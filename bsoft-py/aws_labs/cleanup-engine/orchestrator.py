from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Dict, List, Optional

import boto3

import services
from aws_nuke_runner import AwsNukeRunner
from base.aws_client import AWSClientFactory
from base.base_cleanup import BaseCleanupService
from checkpoint import CheckpointStore
from config import EngineConfig
from inventory import InventoryEngine
from logger import get_logger
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult
from report import ReportGenerator
from utils import Timer
from verification import VerificationEngine


class Orchestrator:
    def __init__(
        self,
        config: EngineConfig,
        session: Optional[boto3.Session] = None,
    ):
        self.config = config
        self.logger = get_logger("orchestrator")

        self.clients = AWSClientFactory(
            config,
            session=session,
        )

        self.checkpoint = CheckpointStore(
            config.checkpoint_db,
        )

        self.aws_nuke = AwsNukeRunner(
        session=self.clients.session,
        binary=self.config.aws_nuke.binary,
        timeout_seconds=self.config.aws_nuke.timeout_seconds,
        max_wait_retries=10,
    )

        self.plugins: Dict[str, BaseCleanupService] = {}

    def discover_plugins(self) -> Dict[str, BaseCleanupService]:
        plugins: Dict[str, BaseCleanupService] = {}

        for module_info in pkgutil.iter_modules(services.__path__):
            module = importlib.import_module(
                f"services.{module_info.name}"
            )

            for _, obj in inspect.getmembers(
                module,
                inspect.isclass,
            ):
                if obj is BaseCleanupService:
                    continue

                if not issubclass(
                    obj,
                    BaseCleanupService,
                ):
                    continue

                if obj.__module__ != module.__name__:
                    continue

                instance = obj(
                    self.clients,
                    self.config,
                    get_logger(
                        f"services.{obj.service_name}"
                    ),
                    self.checkpoint,
                )

                plugins[instance.service_name] = instance

        self.plugins = plugins

        self.logger.info(
            f"Discovered {len(plugins)} service plugins: "
            f"{', '.join(sorted(plugins))}"
        )

        return plugins

    def resolve_services(
        self,
    ) -> Dict[str, List[BaseCleanupService]]:
        if not self.plugins:
            self.discover_plugins()

        candidate_names = (
            self.config.include_services
            or list(self.plugins.keys())
        )

        runnable: List[BaseCleanupService] = []
        excluded: List[BaseCleanupService] = []

        for name in candidate_names:
            plugin = self.plugins.get(name)

            if plugin is None:
                self.logger.warning(
                    f"Service '{name}' is configured "
                    "but no matching plugin was found"
                )
                continue

            if name in self.config.exclude_services:
                excluded.append(plugin)
            else:
                runnable.append(plugin)

        return {
            "runnable": runnable,
            "excluded": excluded,
        }

    def run_full_pipeline(self) -> dict:
        account_id = self._safe_account_id()

        if account_id == "unknown":
            raise RuntimeError(
                "Could not verify target AWS account. "
                "Refusing to continue cleanup."
            )

        self.config.account_id = account_id

        self.logger.info(
            f"Starting cleanup run for account "
            f"{account_id} in {self.config.region}"
        )

        resolved = self.resolve_services()

        runnable = resolved["runnable"]
        excluded = resolved["excluded"]

        all_plugins = runnable + excluded

        # -------------------------------------------------
        # Phase 1: Inventory
        # -------------------------------------------------

        self.logger.info("PHASE 1 - Inventory")

        inventory_engine = InventoryEngine(
            all_plugins,
            self.config,
            self.logger,
        )

        inventories = inventory_engine.run()

        inventory_engine.write(
            inventories,
        )

        for plugin in excluded:
            inv = inventories.get(
                plugin.service_name
            )

            count = (
                inv.resource_count
                if inv
                else 0
            )

            if count:
                self.logger.warning(
                    f"Service '{plugin.service_name}' "
                    "is in exclude_services -- "
                    f"{count} resources will remain untouched"
                )

        # -------------------------------------------------
        # Phase 2: Custom Cleanup
        # -------------------------------------------------

        self.logger.info(
            "PHASE 2 - Custom cleanup"
        )

        cleanup_results = self._run_cleanup(
            runnable,
            inventories,
        )

        # -------------------------------------------------
        # Phase 3: Verification before aws-nuke
        # -------------------------------------------------

        self.logger.info(
            "PHASE 3 - Pre aws-nuke verification"
        )

        verification_engine = VerificationEngine(
            runnable,
            self.config,
            self.logger,
        )

        pre_nuke_verifications = (
            verification_engine.run_until_pass(
                max_passes=(
                    self.config
                    .max_verification_passes
                )
            )
        )

        verification_engine.write(
            pre_nuke_verifications,
            path=(
                f"{self.config.report_dir}/"
                "verification_pre_nuke.json"
            ),
        )

        pre_nuke_passed = (
            verification_engine.overall_passed(
                pre_nuke_verifications
            )
        )

        if pre_nuke_passed:
            self.logger.info(
                "Pre aws-nuke verification: PASS"
            )
        else:
            self.logger.warning(
                "Pre aws-nuke verification: FAIL. "
                "Continuing to aws-nuke so it can "
                "attempt removal of remaining resources."
            )

        # -------------------------------------------------
        # Phase 4: aws-nuke
        # -------------------------------------------------

        nuke_result = None

        if self.config.aws_nuke.enabled:
            self.logger.info(
                "PHASE 4 - aws-nuke"
            )

            nuke_result = self.aws_nuke.run(
                account_id=account_id,
                region=self.config.region,
                config_file=self.config.nuke_config_file,
                dry_run=self.config.dry_run,
            )

            if nuke_result.success:
                self.logger.info(
                    "aws-nuke phase completed successfully"
                )
            else:
                self.logger.error(
                    "aws-nuke phase failed "
                    f"(return_code="
                    f"{nuke_result.return_code}, "
                    f"timed_out="
                    f"{nuke_result.timed_out})"
                )

        else:
            self.logger.info(
                "PHASE 4 - aws-nuke disabled"
            )

        # -------------------------------------------------
        # Phase 5: Final Verification
        # -------------------------------------------------

        self.logger.info(
            "PHASE 5 - Final verification"
        )

        final_verifications = (
            verification_engine.run_until_pass(
                max_passes=(
                    self.config
                    .max_verification_passes
                )
            )
        )

        verification_engine.write(
            final_verifications,
            path=(
                f"{self.config.report_dir}/"
                "verification_final.json"
            ),
        )

        final_passed = (
            verification_engine.overall_passed(
                final_verifications
            )
        )

        if final_passed:
            self.logger.info(
                "Final verification: PASS"
            )
        else:
            self.logger.error(
                "Final verification: FAIL"
            )

        # -------------------------------------------------
        # Phase 6: Final Report
        # -------------------------------------------------

        self.logger.info(
            "PHASE 6 - Report generation"
        )

        report_generator = ReportGenerator(
            self.config,
            account_id,
        )

        report = report_generator.generate(
            inventories,
            cleanup_results,
            final_verifications,
        )

        report["pre_nuke_verification"] = (
            "PASS"
            if pre_nuke_passed
            else "FAIL"
        )

        report["aws_nuke"] = {
            "enabled": self.config.aws_nuke.enabled,
            "executed": nuke_result is not None,
            "success": (
                nuke_result.success
                if nuke_result
                else None
            ),
            "return_code": (
                nuke_result.return_code
                if nuke_result
                else None
            ),
            "timed_out": (
                nuke_result.timed_out
                if nuke_result
                else False
            ),
        }

        report["final_verification"] = (
            "PASS"
            if final_passed
            else "FAIL"
        )

        report_generator.write(
            report,
        )

        return report

    def _run_cleanup(
        self,
        plugins: List[BaseCleanupService],
        inventories: Dict[str, InventoryResult],
    ) -> Dict[str, CleanupResult]:
        results: Dict[str, CleanupResult] = {}

        for plugin in plugins:
            inventory = inventories.get(
                plugin.service_name
            )

            if inventory is None:
                continue

            if inventory.error:
                self.logger.warning(
                    f"Skipping cleanup for "
                    f"'{plugin.service_name}' "
                    "because discovery failed: "
                    f"{inventory.error}"
                )
                continue

            self.logger.info(
                f"Cleaning up service: "
                f"{plugin.service_name}"
            )

            try:
                with Timer() as timer:
                    cleanup_result = (
                        plugin.cleanup(
                            inventory
                        )
                    )

                cleanup_result.duration_seconds = (
                    timer.elapsed
                )

                results[
                    plugin.service_name
                ] = cleanup_result

            except Exception as exc:
                self.logger.error(
                    f"Cleanup for service "
                    f"'{plugin.service_name}' "
                    f"raised: {exc}"
                )

                failed = CleanupResult(
                    service_name=plugin.service_name,
                    discovered=(
                        inventory.resource_count
                    ),
                )

                failed.failed = (
                    inventory.resource_count
                )

                failed.errors.append(
                    str(exc)
                )

                results[
                    plugin.service_name
                ] = failed

        return results

    def _safe_account_id(self) -> str:
        try:
            return self.clients.account_id()

        except Exception as exc:
            self.logger.warning(
                f"Could not resolve AWS "
                f"account id: {exc}"
            )

            return "unknown"

    def close(self) -> None:
        self.checkpoint.close()
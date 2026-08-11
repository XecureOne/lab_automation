from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from config import EngineConfig
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult
from models.verification_result import VerificationResult
from utils import utc_now_iso, write_json


class ReportGenerator:
    """Builds the final cleanup_report.json.

    Includes:
    - account / region / timing
    - discovered / deleted / failed / remaining counts
    - success percentage
    - per-service statistics
    - exact resources that survived final verification
    """

    def __init__(self, config: EngineConfig, account_id: str):
        self.config = config
        self.account_id = account_id
        self.start_time = utc_now_iso()

    def generate(
        self,
        inventories: Dict[str, InventoryResult],
        cleanup_results: Dict[str, CleanupResult],
        verifications: Dict[str, VerificationResult],
    ) -> dict:
        end_time = utc_now_iso()

        elapsed_seconds = round(
            (
                datetime.fromisoformat(end_time)
                - datetime.fromisoformat(self.start_time)
            ).total_seconds(),
            2,
        )

        total_discovered = sum(
            inventory.resource_count
            for inventory in inventories.values()
        )

        total_deleted = sum(
            cleanup.deleted
            for cleanup in cleanup_results.values()
        )

        total_failed = sum(
            cleanup.failed
            for cleanup in cleanup_results.values()
        )

        total_remaining = sum(
            verification.remaining_count
            for verification in verifications.values()
        )

        if total_discovered:
            success_percentage = round(
                (total_deleted / total_discovered) * 100,
                2,
            )
        else:
            success_percentage = 100.0

        # ------------------------------------------------------
        # Exact resources still alive after final verification
        # ------------------------------------------------------

        remaining_resources: List[dict] = []

        for service_name, verification in sorted(verifications.items()):
            resources = getattr(
                verification,
                "remaining_resources",
                [],
            ) or []

            error = getattr(
                verification,
                "error",
                None,
            )

            for resource in resources:
                remaining_resources.append(
                    {
                        "service": service_name,
                        "resource": resource,
                        "verification_error": error,
                    }
                )

            # If verification itself failed but could not identify
            # concrete resources, still expose that failure.
            if not verification.passed and not resources:
                remaining_resources.append(
                    {
                        "service": service_name,
                        "resource": None,
                        "verification_error": (
                            error
                            or "Verification failed but no specific "
                            "remaining resource was reported"
                        ),
                    }
                )

        # ------------------------------------------------------
        # Per-service statistics
        # ------------------------------------------------------

        service_names = sorted(
            set(inventories)
            | set(cleanup_results)
            | set(verifications)
        )

        per_service = {}

        for name in service_names:
            inventory = inventories.get(name)
            cleanup = cleanup_results.get(name)
            verification = verifications.get(name)

            if verification is not None:
                verification_status = (
                    "PASS"
                    if verification.passed
                    else "FAIL"
                )
            else:
                verification_status = "NOT_RUN"

            per_service[name] = {
                "discovered": (
                    inventory.resource_count
                    if inventory
                    else 0
                ),
                "deleted": (
                    cleanup.deleted
                    if cleanup
                    else 0
                ),
                "failed": (
                    cleanup.failed
                    if cleanup
                    else 0
                ),
                "skipped": (
                    cleanup.skipped
                    if cleanup
                    else 0
                ),
                "duration_seconds": (
                    round(
                        cleanup.duration_seconds,
                        2,
                    )
                    if cleanup
                    else 0.0
                ),
                "verification_status": verification_status,
                "remaining": (
                    verification.remaining_count
                    if verification
                    else 0
                ),
                "remaining_resources": (
                    verification.remaining_resources
                    if verification
                    else []
                ),
                "verification_error": (
                    verification.error
                    if verification
                    else None
                ),
                "errors": (
                    cleanup.errors
                    if cleanup
                    else []
                ),
            }

        overall_verification = (
            "PASS"
            if all(
                verification.passed
                for verification in verifications.values()
            )
            else "FAIL"
        )

        return {
            "account_id": self.account_id,
            "region": self.config.region,
            "start_time": self.start_time,
            "end_time": end_time,
            "elapsed_seconds": elapsed_seconds,
            "dry_run": self.config.dry_run,

            "resources_discovered": total_discovered,
            "resources_deleted": total_deleted,
            "resources_failed": total_failed,
            "resources_remaining": total_remaining,

            "success_percentage": success_percentage,
            "overall_verification": overall_verification,

            # New section
            "remaining_resources": remaining_resources,

            "per_service": per_service,
        }

    def write(
        self,
        report: dict,
        path: str = None,
    ) -> str:
        path = (
            path
            or f"{self.config.report_dir}/cleanup_report.json"
        )

        write_json(
            path,
            report,
        )

        return path
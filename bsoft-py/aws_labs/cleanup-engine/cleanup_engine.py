from __future__ import annotations

from typing import Optional

from account_lock import AccountCleanupLock
from nuke_config import MANAGEMENT_ACCOUNT_ID, build_nuke_config
from nuke_config import build_nuke_config
from base.aws_client import build_assumed_session
from config import EngineConfig
from logger import get_logger, setup_logging
from orchestrator import Orchestrator
from utils import ensure_dir, write_json



def _run_one(
    config: EngineConfig,
    region: str,
    account_id: str,
    session,
    nuke_config_file: str,
) -> dict:
    run_config = config.for_run(
        region=region,
        account_id=account_id,
    )

    run_config.nuke_config_file = nuke_config_file

    ensure_dir(run_config.report_dir)

    orchestrator = Orchestrator(
        run_config,
        session=session,
    )

    try:
        return orchestrator.run_full_pipeline()
    finally:
        orchestrator.close()


def cleanup_account(
    account_id: str,
    config_path: str = "config.yaml",
    dry_run: Optional[bool] = None,
) -> dict:
    """
    Run the complete cleanup pipeline for exactly one AWS sandbox account.

    Only one cleanup may execute for a given account on this host at a time.
    """

    account_id = str(account_id).strip()

    if not account_id.isdigit() or len(account_id) != 12:
        raise ValueError(
            f"Invalid AWS account ID: {account_id}"
        )

    if account_id == MANAGEMENT_ACCOUNT_ID:
        raise ValueError(
            f"Refusing cleanup for management account {account_id}"
        )

    config = EngineConfig.load()
    # print(f"[cleanup-engine] loading config from: {path}")

    if dry_run is not None:
        config.dry_run = dry_run

    ensure_dir(config.report_dir)

    setup_logging(
        config.log_level,
        config.log_file,
    )

    logger = get_logger("cleanup_engine")

    role_name = config.assume_role.role_name
    external_id = config.assume_role.external_id

    logger.info(
        f"Attempting to acquire cleanup lock for account {account_id}"
    )

    with AccountCleanupLock(account_id):
        logger.info(
            f"Cleanup lock acquired for account {account_id}"
        )

        logger.info("=" * 70)
        logger.info(
            f"Starting cleanup for account {account_id}"
        )
        logger.info(
        f"Role: {role_name}"
    )
        logger.info(
            f"Regions: {', '.join(config.regions)}"
        )
        logger.info(
            f"Dry run: {config.dry_run}"
        )
        logger.info("=" * 70)

        reports = []
        overall_ok = True
        failed_regions = []

        nuke_config_file = build_nuke_config(account_id)

        logger.info(
            f"Generated aws-nuke config: {nuke_config_file}"
        )

        for region in config.regions:
            logger.info(
                f"--- Account {account_id} / region {region} ---"
            )

            try:
                session = build_assumed_session(
                    account_id,
                    role_name,
                    region,
                    external_id,
                )

                # Safety boundary:
                # never clean unless STS confirms the requested account.
                actual_account_id = (
                    session.client("sts")
                    .get_caller_identity()["Account"]
                )

                if actual_account_id != account_id:
                    raise RuntimeError(
                        "Account safety check failed: "
                        f"requested={account_id}, "
                        f"assumed={actual_account_id}"
                    )

                logger.info(
                    f"STS account verification passed: "
                    f"{actual_account_id}"
                )

                report = _run_one(
                    config,
                    region,
                    account_id,
                    session,
                    nuke_config_file,
                )

                reports.append(report)

                if (
                    report.get("overall_verification")
                    != "PASS"
                ):
                    overall_ok = False
                    failed_regions.append(region)

            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"Cleanup failed for account "
                    f"{account_id} / region {region}: {exc}"
                )

                overall_ok = False
                failed_regions.append(region)

        remaining_resources = []

        for report in reports:
            for resource in report.get(
                "remaining_resources",
                [],
            ):
                remaining_resources.append(
                    {
                        "region": report.get("region"),
                        **resource,
                    }
                )

        status = (
            "PASS"
            if overall_ok
            else "FAIL"
        )

        summary = {
            "account_id": account_id,
            "status": status,
            "reusable": overall_ok,

            "regions": {
                report["region"]:
                    report["overall_verification"]
                for report in reports
            },

            "failed_regions": failed_regions,

            "remaining_resources":
                remaining_resources,

            "runs": reports,
        }

        account_report_dir = (
            f"{config.report_dir}/{account_id}"
        )

        ensure_dir(account_report_dir)

        summary_path = (
            f"{account_report_dir}/"
            "cleanup_report_summary.json"
        )

        write_json(
            summary_path,
            summary,
        )

        summary["report_path"] = summary_path

        logger.info("=" * 70)
        logger.info(
            f"Account cleanup result: {status}"
        )
        logger.info(
            f"Reusable: {overall_ok}"
        )
        logger.info(
            f"Remaining resources: "
            f"{len(remaining_resources)}"
        )
        logger.info("=" * 70)

        return summary
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from botocore.exceptions import ClientError

from base.base_cleanup import BaseCleanupService
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult, ResourceRecord
from models.verification_result import VerificationResult
from retry import retry_with_backoff
from waiters import wait_stack_deleted

ACTIVE_STACK_STATUSES = [
    "CREATE_IN_PROGRESS", "CREATE_COMPLETE", "ROLLBACK_IN_PROGRESS",
    "ROLLBACK_FAILED", "ROLLBACK_COMPLETE", "DELETE_IN_PROGRESS",
    "DELETE_FAILED", "UPDATE_IN_PROGRESS", "UPDATE_COMPLETE_CLEANUP_IN_PROGRESS",
    "UPDATE_COMPLETE", "UPDATE_FAILED", "UPDATE_ROLLBACK_IN_PROGRESS",
    "UPDATE_ROLLBACK_FAILED", "UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS",
    "UPDATE_ROLLBACK_COMPLETE", "REVIEW_IN_PROGRESS",
    "IMPORT_IN_PROGRESS", "IMPORT_COMPLETE", "IMPORT_ROLLBACK_IN_PROGRESS",
    "IMPORT_ROLLBACK_FAILED", "IMPORT_ROLLBACK_COMPLETE",
]


class CloudFormationCleanupService(BaseCleanupService):
    """Deletes every non-terminated CloudFormation stack and waits for each
    deletion to complete. Honors protected_resource_arns, exclude_tags, and
    the checkpoint DB via should_skip_resource(), and gates disabling stack
    termination protection behind force_disable_protection."""

    service_name = "cloudformation"

    def _client(self):
        return self.clients.client("cloudformation")

    @retry_with_backoff()
    def _list_stacks(self) -> List[dict]:
        client = self._client()
        paginator = client.get_paginator("list_stacks")
        stacks: List[dict] = []
        for page in paginator.paginate(StackStatusFilter=ACTIVE_STACK_STATUSES):
            stacks.extend(page.get("StackSummaries", []))
        return stacks

    @retry_with_backoff()
    def _describe_stack(self, stack_name: str) -> dict:
        resp = self._client().describe_stacks(StackName=stack_name)
        stacks = resp.get("Stacks", [])
        return stacks[0] if stacks else {}

    def discover(self) -> InventoryResult:
        self.logger.discover("Scanning CloudFormation stacks ...")
        result = InventoryResult(service_name=self.service_name)
        try:
            summaries = self._list_stacks()
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"CloudFormation discovery failed: {exc}")
            return result

        for summary in summaries:
            tags: Dict[str, str] = {}
            termination_protection = False
            try:
                detail = self._describe_stack(summary["StackName"])
                tags = {t["Key"]: t["Value"] for t in detail.get("Tags", [])}
                termination_protection = bool(detail.get("EnableTerminationProtection", False))
            except ClientError as exc:
                self.logger.warning(f"Could not describe stack {summary['StackName']} for tags: {exc}")

            result.resources.append(
                ResourceRecord(
                    resource_id=summary["StackId"],
                    name=summary["StackName"],
                    arn=summary["StackId"],
                    metadata={
                        "status": summary.get("StackStatus"),
                        "parent_id": summary.get("ParentId", ""),
                        "root_id": summary.get("RootId", ""),
                        "tags": tags,
                        "termination_protection": termination_protection,
                    },
                )
            )
        self.logger.discover(f"Found {result.resource_count} stacks")
        return result

    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No CloudFormation stacks to delete")
            return result

        root_stacks = [r for r in inventory.resources if not r.metadata.get("parent_id")]
        nested_stacks = [r for r in inventory.resources if r.metadata.get("parent_id")]
        for nested in nested_stacks:
            result.add_skipped(
                nested.resource_id, nested.name, "Nested stack; will be removed with its root stack", nested.arn
            )

        client = self._client()

        def _delete_one(record: ResourceRecord) -> None:
            skip_reason = self.should_skip_resource(record)
            if skip_reason:
                self.logger.cleanup(f"Skipping stack {record.name}: {skip_reason}")
                result.add_skipped(record.resource_id, record.name, skip_reason, record.arn)
                return

            if self.config.dry_run:
                self.logger.cleanup(f"[DRY-RUN] Would delete stack {record.name}")
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
                return

            try:
                if record.metadata.get("termination_protection"):
                    if not self.config.force_disable_protection:
                        self.logger.cleanup(
                            f"Skipping termination-protected stack {record.name} "
                            f"(force_disable_protection is false)"
                        )
                        result.add_skipped(record.resource_id, record.name, "termination_protected", record.arn)
                        return
                    self._disable_termination_protection(client, record.resource_id)
                    self.logger.cleanup(f"Disabled termination protection on stack {record.name}")

                self._delete_stack(client, record.resource_id)
                self.logger.cleanup(f"Deleted stack {record.name}")
                self.logger.wait(f"Waiting for stack deletion: {record.name}")
                wait_stack_deleted(
                    client, record.resource_id,
                    timeout=self.config.waiter_default_timeout_seconds,
                    interval=self.config.waiter_default_interval_seconds,
                    logger=self.logger,
                )
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete stack {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        with ThreadPoolExecutor(max_workers=self.config.parallel_workers) as pool:
            futures = [pool.submit(_delete_one, record) for record in root_stacks]
            for future in as_completed(futures):
                future.result()

        return result

    @retry_with_backoff()
    def _delete_stack(self, client, stack_id: str) -> None:
        client.delete_stack(StackName=stack_id)

    @retry_with_backoff()
    def _disable_termination_protection(self, client, stack_id: str) -> None:
        client.update_termination_protection(EnableTerminationProtection=False, StackName=stack_id)

    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying CloudFormation is clean ...")
        try:
            summaries = self._list_stacks()
        except ClientError as exc:
            return VerificationResult(self.service_name, passed=False, error=str(exc))

        remaining = [s["StackName"] for s in summaries]
        passed = len(remaining) == 0
        self.logger.verify(
            "CloudFormation clean" if passed else f"CloudFormation FAIL - {len(remaining)} stacks remain"
        )
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from botocore.exceptions import ClientError

from base.base_cleanup import BaseCleanupService
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult, ResourceRecord
from models.verification_result import VerificationResult
from retry import retry_with_backoff
from waiters import Waiter


class AutoScalingCleanupService(BaseCleanupService):
    """Deletes Auto Scaling groups and launch configurations, in
    dependency order:

    1. Auto Scaling groups: delete with ForceDelete=True. Per AWS's own
       delete_auto_scaling_group docs, ForceDelete deletes the group
       along with all of its instances (without waiting for them to
       individually terminate first), its warm pool if it has one, all
       outstanding lifecycle actions, and its scaling policies and
       scheduled actions -- none of those are tracked as separate
       resources here because AWS guarantees they go with the group.
    2. Launch configurations: delete, after step 1. AWS rejects
       delete_launch_configuration outright while it's still attached to
       an Auto Scaling group ("The launch configuration must not be
       attached to an Auto Scaling group") -- deleting the groups first
       is what clears that block, not a retry or a force flag.

    Launch templates are intentionally OUT of scope for this module, even
    though Auto Scaling groups commonly use them instead of launch
    configurations: a launch template is an EC2 resource (ec2
    CreateLaunchTemplate/DeleteLaunchTemplate), not an autoscaling one,
    can be shared across many ASGs or used standalone with none, and is
    handled by the EC2 service module -- the same reasoning the EKS
    module uses to keep self-managed node groups (plain ASGs) out of its
    own scope.

    Honors protected_resource_arns, exclude_tags, and the checkpoint DB via
    should_skip_resource() for both resource types below.
    """

    service_name = "autoscaling"

    def _client(self):
        return self.clients.client("autoscaling")

    # ------------------------------------------------------------ listing
    @retry_with_backoff()
    def _list_auto_scaling_groups(self) -> List[dict]:
        paginator = self._client().get_paginator("describe_auto_scaling_groups")
        groups: List[dict] = []
        for page in paginator.paginate():
            groups.extend(page.get("AutoScalingGroups", []))
        return groups

    @retry_with_backoff()
    def _describe_auto_scaling_group(self, name: str) -> Optional[dict]:
        resp = self._client().describe_auto_scaling_groups(AutoScalingGroupNames=[name])
        groups = resp.get("AutoScalingGroups", [])
        return groups[0] if groups else None

    @retry_with_backoff()
    def _list_launch_configurations(self) -> List[dict]:
        paginator = self._client().get_paginator("describe_launch_configurations")
        configs: List[dict] = []
        for page in paginator.paginate():
            configs.extend(page.get("LaunchConfigurations", []))
        return configs

    # ---------------------------------------------------------------------
    def discover(self) -> InventoryResult:
        self.logger.discover("Scanning Auto Scaling groups and launch configurations ...")
        result = InventoryResult(service_name=self.service_name)

        try:
            groups = self._list_auto_scaling_groups()
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"Auto Scaling discovery failed: {exc}")
            return result

        for group in groups:
            name = group["AutoScalingGroupName"]
            tags = {t["Key"]: t["Value"] for t in group.get("Tags", [])}
            result.resources.append(
                ResourceRecord(
                    resource_id=name,
                    name=name,
                    arn=group.get("AutoScalingGroupARN", ""),
                    metadata={"type": "auto_scaling_group", "tags": tags},
                    fingerprint=str(group.get("CreatedTime", "")),
                )
            )

        try:
            launch_configs = self._list_launch_configurations()
        except ClientError as exc:
            self.logger.warning(f"Could not list launch configurations: {exc}")
            launch_configs = []

        for lc in launch_configs:
            name = lc["LaunchConfigurationName"]
            result.resources.append(
                ResourceRecord(
                    resource_id=name,
                    name=name,
                    arn=lc.get("LaunchConfigurationARN", ""),
                    # Launch configurations don't support tags at all
                    # (unlike ASGs and launch templates), so there's
                    # nothing to fetch here.
                    metadata={"type": "launch_configuration", "tags": {}},
                    fingerprint=str(lc.get("CreatedTime", "")),
                )
            )

        self.logger.discover(f"Found {len(groups)} Auto Scaling groups, {len(launch_configs)} launch configurations")
        return result

    # --------------------------------------------------------------- clean
    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No Auto Scaling resources to delete")
            return result

        if self.config.dry_run:
            for record in inventory.resources:
                self.logger.cleanup(f"[DRY-RUN] Would delete Auto Scaling {record.metadata.get('type')} {record.name}")
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
            return result

        client = self._client()

        def by_type(t: str) -> List[ResourceRecord]:
            return self._apply_skip_gate([r for r in inventory.resources if r.metadata.get("type") == t], result)

        groups = by_type("auto_scaling_group")
        launch_configs = by_type("launch_configuration")

        self._delete_auto_scaling_groups(client, groups, result)
        self._delete_launch_configurations(client, launch_configs, result)

        return result

    def _apply_skip_gate(self, records: List[ResourceRecord], result: CleanupResult) -> List[ResourceRecord]:
        kept = []
        for record in records:
            reason = self.should_skip_resource(record)
            if reason:
                self.logger.cleanup(f"Skipping Auto Scaling resource {record.name}: {reason}")
                result.add_skipped(record.resource_id, record.name, reason, record.arn)
            else:
                kept.append(record)
        return kept

    def _run_parallel(self, records: List[ResourceRecord], delete_one) -> None:
        if not records:
            return
        with ThreadPoolExecutor(max_workers=self.config.parallel_workers) as pool:
            futures = [pool.submit(delete_one, record) for record in records]
            for future in as_completed(futures):
                future.result()

    # ---- Auto Scaling groups ----
    def _delete_auto_scaling_groups(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            name = record.resource_id
            try:
                self._call_delete_auto_scaling_group(client, name)
                self.logger.wait(f"Waiting for Auto Scaling group deletion: {name}")
                Waiter(self.logger).wait_for(
                    lambda: self._describe_auto_scaling_group(name) is None,
                    f"Auto Scaling group '{name}' deletion",
                    timeout=self.config.waiter_default_timeout_seconds,
                    interval=self.config.waiter_default_interval_seconds,
                )
                self.logger.cleanup(f"Deleted Auto Scaling group {name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete Auto Scaling group {name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_auto_scaling_group(self, client, name: str) -> None:
        client.delete_auto_scaling_group(AutoScalingGroupName=name, ForceDelete=True)

    # ---- launch configurations ----
    def _delete_launch_configurations(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            name = record.resource_id
            try:
                self._call_delete_launch_configuration(client, name)
                self.logger.cleanup(f"Deleted launch configuration {name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete launch configuration {name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_launch_configuration(self, client, name: str) -> None:
        client.delete_launch_configuration(LaunchConfigurationName=name)

    # -------------------------------------------------------------- verify
    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying Auto Scaling is clean ...")
        try:
            groups = self._list_auto_scaling_groups()
        except ClientError as exc:
            return VerificationResult(self.service_name, passed=False, error=str(exc))

        remaining = [f"auto_scaling_group:{g['AutoScalingGroupName']}" for g in groups]

        try:
            remaining += [f"launch_configuration:{lc['LaunchConfigurationName']}" for lc in self._list_launch_configurations()]
        except ClientError:
            pass

        passed = len(remaining) == 0
        self.logger.verify("Auto Scaling clean" if passed else f"Auto Scaling FAIL - {len(remaining)} resources remain")
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )
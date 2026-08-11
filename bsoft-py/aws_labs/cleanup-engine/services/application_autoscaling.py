from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from botocore.exceptions import ClientError

from base.base_cleanup import BaseCleanupService
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult, ResourceRecord
from models.verification_result import VerificationResult
from retry import retry_with_backoff

# Every ServiceNamespace Application Auto Scaling currently supports, per
# the latest boto3/botocore service model checked. DescribeScalableTargets
# (and the scaling-policy/scheduled-action equivalents) require a
# ServiceNamespace and only return results for that one namespace -- there
# is no "all namespaces" call, so discovery has to loop over this list.
# AWS has added namespaces to this enum over time (e.g. 'workspaces' and
# 'elasticache' are both relatively recent additions to it) -- if AWS adds
# another one after this was written, resources under it will silently
# not be discovered. Worth checking this list against the current
# botocore service model (or AWS's ServiceNamespace docs) periodically.
SERVICE_NAMESPACES = (
    "ecs",
    "elasticmapreduce",
    "ec2",
    "appstream",
    "dynamodb",
    "rds",
    "sagemaker",
    "custom-resource",
    "comprehend",
    "lambda",
    "cassandra",
    "kafka",
    "elasticache",
    "neptune",
    "workspaces",
)


class ApplicationAutoScalingCleanupService(BaseCleanupService):
    """Removes Application Auto Scaling configuration -- scalable targets,
    scaling policies, and scheduled actions -- across every supported
    service namespace.

    IMPORTANT SCOPE NOTE: unlike every other module in this tool, nothing
    here deletes infrastructure. A "scalable target" is a pointer to a
    resource owned by another service (a DynamoDB table, an ECS service's
    desired count, a Lambda function's provisioned concurrency, etc.) --
    deregistering it only removes Application Auto Scaling's ability to
    adjust that resource's capacity. The underlying DynamoDB table, ECS
    service, or Lambda function is untouched and must be cleaned up by its
    own service module. This module exists to remove the scaling
    configuration layer, not the resources it was scaling.

    Deletion order, per resource, within each namespace:

    1. Scaling policies: delete. AWS's own docs note that deleting a step
       scaling policy removes its underlying CloudWatch alarm ACTION, but
       explicitly does NOT delete the CloudWatch alarm resource itself,
       even once it has no action left. Those orphaned alarms are a
       CloudWatch resource, out of scope for this module, and will need
       to be swept up by a CloudWatch alarms module if leftover alarms
       matter to this sandbox's cleanliness bar.
    2. Scheduled actions: delete.
    3. Scalable targets: deregister, after steps 1-2. Per AWS's current
       docs, deregistering a scalable target actually deletes any scaling
       policies and scheduled actions still associated with it as a side
       effect -- so steps 1-2 are technically redundant with step 3, not
       a dependency it's blocked by. They're still done explicitly and
       first anyway, for the same reason the EKS module explicitly
       deletes add-ons rather than relying on the cluster-deletion
       cascade: it keeps each resource's own success/failure/skip outcome
       visible in this tool's inventory and results, instead of hiding
       three resources' fates inside one deregister call's outcome.

    Honors protected_resource_arns, exclude_tags, and the checkpoint DB via
    should_skip_resource() for all three resource types below.
    """

    service_name = "application-autoscaling"

    def _client(self):
        return self.clients.client("application-autoscaling")

    # ------------------------------------------------------------ listing
    @retry_with_backoff()
    def _list_scalable_targets(self, namespace: str) -> List[dict]:
        paginator = self._client().get_paginator("describe_scalable_targets")
        targets: List[dict] = []
        for page in paginator.paginate(ServiceNamespace=namespace):
            targets.extend(page.get("ScalableTargets", []))
        return targets

    @retry_with_backoff()
    def _list_scaling_policies(self, namespace: str) -> List[dict]:
        paginator = self._client().get_paginator("describe_scaling_policies")
        policies: List[dict] = []
        for page in paginator.paginate(ServiceNamespace=namespace):
            policies.extend(page.get("ScalingPolicies", []))
        return policies

    @retry_with_backoff()
    def _list_scheduled_actions(self, namespace: str) -> List[dict]:
        paginator = self._client().get_paginator("describe_scheduled_actions")
        actions: List[dict] = []
        for page in paginator.paginate(ServiceNamespace=namespace):
            actions.extend(page.get("ScheduledActions", []))
        return actions

    @retry_with_backoff()
    def _tags_for(self, resource_arn: str) -> Dict[str, str]:
        if not resource_arn:
            return {}
        try:
            resp = self._client().list_tags_for_resource(resourceARN=resource_arn)
            return resp.get("Tags", {})
        except ClientError:
            return {}

    # ---------------------------------------------------------------------
    def discover(self) -> InventoryResult:
        self.logger.discover(
            f"Scanning Application Auto Scaling across {len(SERVICE_NAMESPACES)} service namespaces ..."
        )
        result = InventoryResult(service_name=self.service_name)

        target_count = 0
        policy_count = 0
        action_count = 0

        for namespace in SERVICE_NAMESPACES:
            try:
                targets = self._list_scalable_targets(namespace)
            except ClientError as exc:
                self.logger.warning(f"Could not list scalable targets for namespace {namespace}: {exc}")
                targets = []

            for target in targets:
                target_count += 1
                resource_id = target["ResourceId"]
                dimension = target["ScalableDimension"]
                target_arn = target.get("ScalableTargetARN", "")
                composite_id = f"{namespace}::{resource_id}::{dimension}"
                result.resources.append(
                    ResourceRecord(
                        resource_id=composite_id,
                        name=f"{namespace}/{resource_id}",
                        arn=target_arn,
                        metadata={
                            "type": "scalable_target",
                            "namespace": namespace,
                            "aas_resource_id": resource_id,
                            "scalable_dimension": dimension,
                            "tags": self._tags_for(target_arn),
                        },
                        fingerprint=str(target.get("CreationTime", "")),
                    )
                )

            try:
                policies = self._list_scaling_policies(namespace)
            except ClientError as exc:
                self.logger.warning(f"Could not list scaling policies for namespace {namespace}: {exc}")
                policies = []

            for policy in policies:
                policy_count += 1
                policy_name = policy["PolicyName"]
                resource_id = policy["ResourceId"]
                dimension = policy["ScalableDimension"]
                composite_id = f"{namespace}::{resource_id}::{dimension}::{policy_name}"
                result.resources.append(
                    ResourceRecord(
                        resource_id=composite_id,
                        name=f"{namespace}/{resource_id}/{policy_name}",
                        arn=policy.get("PolicyARN", ""),
                        metadata={
                            "type": "scaling_policy",
                            "namespace": namespace,
                            "policy_name": policy_name,
                            "aas_resource_id": resource_id,
                            "scalable_dimension": dimension,
                            "tags": {},
                        },
                        fingerprint=str(policy.get("CreationTime", "")),
                    )
                )

            try:
                actions = self._list_scheduled_actions(namespace)
            except ClientError as exc:
                self.logger.warning(f"Could not list scheduled actions for namespace {namespace}: {exc}")
                actions = []

            for action in actions:
                action_count += 1
                action_name = action["ScheduledActionName"]
                resource_id = action["ResourceId"]
                dimension = action.get("ScalableDimension", "")
                composite_id = f"{namespace}::{resource_id}::{dimension}::{action_name}"
                result.resources.append(
                    ResourceRecord(
                        resource_id=composite_id,
                        name=f"{namespace}/{resource_id}/{action_name}",
                        arn=action.get("ScheduledActionARN", ""),
                        metadata={
                            "type": "scheduled_action",
                            "namespace": namespace,
                            "action_name": action_name,
                            "aas_resource_id": resource_id,
                            "scalable_dimension": dimension,
                            "tags": {},
                        },
                        fingerprint=str(action.get("CreationTime", "")),
                    )
                )

        self.logger.discover(
            f"Found {target_count} scalable targets, {policy_count} scaling policies, "
            f"{action_count} scheduled actions"
        )
        return result

    # --------------------------------------------------------------- clean
    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No Application Auto Scaling resources to delete")
            return result

        if self.config.dry_run:
            for record in inventory.resources:
                self.logger.cleanup(
                    f"[DRY-RUN] Would delete Application Auto Scaling {record.metadata.get('type')} {record.name}"
                )
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
            return result

        client = self._client()

        def by_type(t: str) -> List[ResourceRecord]:
            return self._apply_skip_gate([r for r in inventory.resources if r.metadata.get("type") == t], result)

        policies = by_type("scaling_policy")
        scheduled_actions = by_type("scheduled_action")
        targets = by_type("scalable_target")

        self._delete_scaling_policies(client, policies, result)
        self._delete_scheduled_actions(client, scheduled_actions, result)
        self._deregister_scalable_targets(client, targets, result)

        return result

    def _apply_skip_gate(self, records: List[ResourceRecord], result: CleanupResult) -> List[ResourceRecord]:
        kept = []
        for record in records:
            reason = self.should_skip_resource(record)
            if reason:
                self.logger.cleanup(f"Skipping Application Auto Scaling resource {record.name}: {reason}")
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

    # ---- scaling policies ----
    def _delete_scaling_policies(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            meta = record.metadata
            try:
                self._call_delete_scaling_policy(
                    client, meta["policy_name"], meta["namespace"], meta["aas_resource_id"], meta["scalable_dimension"]
                )
                self.logger.cleanup(f"Deleted scaling policy {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete scaling policy {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_scaling_policy(self, client, policy_name: str, namespace: str, resource_id: str, dimension: str) -> None:
        client.delete_scaling_policy(
            PolicyName=policy_name, ServiceNamespace=namespace, ResourceId=resource_id, ScalableDimension=dimension
        )

    # ---- scheduled actions ----
    def _delete_scheduled_actions(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            meta = record.metadata
            try:
                self._call_delete_scheduled_action(
                    client, meta["action_name"], meta["namespace"], meta["aas_resource_id"], meta["scalable_dimension"]
                )
                self.logger.cleanup(f"Deleted scheduled action {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete scheduled action {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_scheduled_action(self, client, action_name: str, namespace: str, resource_id: str, dimension: str) -> None:
        client.delete_scheduled_action(
            ScheduledActionName=action_name,
            ServiceNamespace=namespace,
            ResourceId=resource_id,
            ScalableDimension=dimension,
        )

    # ---- scalable targets ----
    def _deregister_scalable_targets(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            meta = record.metadata
            try:
                self._call_deregister_scalable_target(client, meta["namespace"], meta["aas_resource_id"], meta["scalable_dimension"])
                self.logger.cleanup(f"Deregistered scalable target {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to deregister scalable target {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_deregister_scalable_target(self, client, namespace: str, resource_id: str, dimension: str) -> None:
        client.deregister_scalable_target(ServiceNamespace=namespace, ResourceId=resource_id, ScalableDimension=dimension)

    # -------------------------------------------------------------- verify
    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying Application Auto Scaling is clean ...")
        remaining: List[str] = []
        error = None

        for namespace in SERVICE_NAMESPACES:
            try:
                for target in self._list_scalable_targets(namespace):
                    remaining.append(f"scalable_target:{namespace}/{target['ResourceId']}")
            except ClientError as exc:
                error = error or str(exc)
            try:
                for policy in self._list_scaling_policies(namespace):
                    remaining.append(f"scaling_policy:{namespace}/{policy['ResourceId']}/{policy['PolicyName']}")
            except ClientError as exc:
                error = error or str(exc)
            try:
                for action in self._list_scheduled_actions(namespace):
                    remaining.append(
                        f"scheduled_action:{namespace}/{action['ResourceId']}/{action['ScheduledActionName']}"
                    )
            except ClientError as exc:
                error = error or str(exc)

        if error and not remaining:
            return VerificationResult(self.service_name, passed=False, error=error)

        passed = len(remaining) == 0
        self.logger.verify(
            "Application Auto Scaling clean"
            if passed
            else f"Application Auto Scaling FAIL - {len(remaining)} resources remain"
        )
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )
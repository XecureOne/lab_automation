from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from botocore.exceptions import ClientError

from base.base_cleanup import BaseCleanupService
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult, ResourceRecord
from models.verification_result import VerificationResult
from retry import retry_with_backoff
from waiters import wait_rds_cluster_deleted, wait_rds_instance_deleted


class RDSCleanupService(BaseCleanupService):
    """Deletes RDS/Aurora resources in dependency-safe order:
    1. Filter out anything should_skip_resource() rejects (protected ARN,
       excluded tag, already handled per the checkpoint DB).
    2. Filter out anything still deletion-protected when
       force_disable_protection is false (skipped, not deleted).
    3. Disable deletion protection on what's left.
    4. Delete read replicas (they must go before their source instance).
    5. Delete all remaining instances, including cluster members -- a
       cluster cannot be deleted while it still has members.
    6. Delete clusters.
    Final snapshots are skipped unless retain_final_snapshot is configured.
    """

    service_name = "rds"

    def _client(self):
        return self.clients.client("rds")

    @retry_with_backoff()
    def _describe_clusters(self) -> List[dict]:
        client = self._client()
        paginator = client.get_paginator("describe_db_clusters")
        clusters: List[dict] = []
        for page in paginator.paginate():
            clusters.extend(page.get("DBClusters", []))
        return clusters

    @retry_with_backoff()
    def _describe_instances(self) -> List[dict]:
        client = self._client()
        paginator = client.get_paginator("describe_db_instances")
        instances: List[dict] = []
        for page in paginator.paginate():
            instances.extend(page.get("DBInstances", []))
        return instances

    @retry_with_backoff()
    def _resource_tags(self, arn: str) -> Dict[str, str]:
        if not arn:
            return {}
        resp = self._client().list_tags_for_resource(ResourceName=arn)
        return {t["Key"]: t["Value"] for t in resp.get("TagList", [])}

    def discover(self) -> InventoryResult:
        self.logger.discover("Scanning RDS clusters and instances ...")
        result = InventoryResult(service_name=self.service_name)
        try:
            clusters = self._describe_clusters()
            instances = self._describe_instances()
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"RDS discovery failed: {exc}")
            return result

        for cluster in clusters:
            arn = cluster.get("DBClusterArn", "")
            try:
                tags = self._resource_tags(arn)
            except ClientError as exc:
                self.logger.warning(f"Could not read tags for cluster {cluster['DBClusterIdentifier']}: {exc}")
                tags = {}
            result.resources.append(
                ResourceRecord(
                    resource_id=cluster["DBClusterIdentifier"],
                    name=cluster["DBClusterIdentifier"],
                    arn=arn,
                    metadata={
                        "type": "cluster",
                        "status": cluster.get("Status"),
                        "deletion_protection": cluster.get("DeletionProtection", False),
                        "tags": tags,
                    },
                    # ClusterCreateTime distinguishes this cluster from a
                    # different, later-created one that reuses the same
                    # DBClusterIdentifier (e.g. a fresh lab reusing
                    # 'database-1'), so the checkpoint DB doesn't skip a
                    # genuinely new resource as "already deleted".
                    fingerprint=str(cluster.get("ClusterCreateTime", "")),
                )
            )

        for instance in instances:
            arn = instance.get("DBInstanceArn", "")
            try:
                tags = self._resource_tags(arn)
            except ClientError as exc:
                self.logger.warning(f"Could not read tags for instance {instance['DBInstanceIdentifier']}: {exc}")
                tags = {}
            result.resources.append(
                ResourceRecord(
                    resource_id=instance["DBInstanceIdentifier"],
                    name=instance["DBInstanceIdentifier"],
                    arn=arn,
                    metadata={
                        "type": "instance",
                        "status": instance.get("DBInstanceStatus"),
                        "deletion_protection": instance.get("DeletionProtection", False),
                        "is_read_replica": bool(instance.get("ReadReplicaSourceDBInstanceIdentifier")),
                        "cluster_id": instance.get("DBClusterIdentifier", ""),
                        "tags": tags,
                    },
                    # InstanceCreateTime distinguishes this instance from a
                    # different, later-created one that reuses the same
                    # DBInstanceIdentifier.
                    fingerprint=str(instance.get("InstanceCreateTime", "")),
                )
            )

        self.logger.discover(f"Found {len(clusters)} DB clusters and {len(instances)} DB instances")
        return result

    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No RDS clusters or instances to delete")
            return result

        client = self._client()
        clusters = [r for r in inventory.resources if r.metadata.get("type") == "cluster"]
        instances = [r for r in inventory.resources if r.metadata.get("type") == "instance"]

        if self.config.dry_run:
            for record in instances + clusters:
                self.logger.cleanup(f"[DRY-RUN] Would delete RDS resource {record.name}")
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
            return result

        clusters = self._apply_skip_gate(clusters, result)
        instances = self._apply_skip_gate(instances, result)

        instances, clusters = self._apply_protection_gate(instances, clusters, result)

        replicas = [r for r in instances if r.metadata.get("is_read_replica")]
        non_replicas = [r for r in instances if not r.metadata.get("is_read_replica")]

        self._disable_deletion_protection(client, instances, clusters)

        self._delete_instances(client, replicas, result)
        self._delete_instances(client, non_replicas, result)
        self._delete_clusters(client, clusters, result)

        return result

    def _apply_skip_gate(self, records: List[ResourceRecord], result: CleanupResult) -> List[ResourceRecord]:
        kept = []
        for record in records:
            reason = self.should_skip_resource(record)
            if reason:
                self.logger.cleanup(f"Skipping RDS resource {record.name}: {reason}")
                result.add_skipped(record.resource_id, record.name, reason, record.arn)
            else:
                kept.append(record)
        return kept

    def _apply_protection_gate(self, instances, clusters, result: CleanupResult):
        if self.config.force_disable_protection:
            return instances, clusters

        protected_ids = set()
        for record in instances + clusters:
            if record.metadata.get("deletion_protection"):
                protected_ids.add(record.resource_id)
                self.logger.cleanup(
                    f"Skipping deletion-protected RDS resource {record.name} (force_disable_protection is false)"
                )
                result.add_skipped(record.resource_id, record.name, "deletion_protected", record.arn)

        instances = [r for r in instances if r.resource_id not in protected_ids]
        clusters = [r for r in clusters if r.resource_id not in protected_ids]
        return instances, clusters

    def _disable_deletion_protection(self, client, instances: List[ResourceRecord], clusters: List[ResourceRecord]) -> None:
        for record in instances:
            if not record.metadata.get("deletion_protection"):
                continue
            try:
                self._modify_instance_protection(client, record.resource_id, False)
                self.logger.cleanup(f"Disabled deletion protection on instance {record.name}")
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Could not disable deletion protection on {record.name}: {exc}")

        for record in clusters:
            if not record.metadata.get("deletion_protection"):
                continue
            try:
                self._modify_cluster_protection(client, record.resource_id, False)
                self.logger.cleanup(f"Disabled deletion protection on cluster {record.name}")
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Could not disable deletion protection on {record.name}: {exc}")

    @retry_with_backoff()
    def _modify_instance_protection(self, client, instance_id: str, protected: bool) -> None:
        client.modify_db_instance(DBInstanceIdentifier=instance_id, DeletionProtection=protected, ApplyImmediately=True)

    @retry_with_backoff()
    def _modify_cluster_protection(self, client, cluster_id: str, protected: bool) -> None:
        client.modify_db_cluster(DBClusterIdentifier=cluster_id, DeletionProtection=protected, ApplyImmediately=True)

    def _delete_instances(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        if not records:
            return

        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._delete_instance(client, record)
                self.logger.cleanup(f"Deleted DB instance {record.name}")
                self.logger.wait(f"Waiting for DB instance deletion: {record.name}")
                wait_rds_instance_deleted(
                    client, record.resource_id,
                    timeout=self.config.waiter_default_timeout_seconds,
                    interval=self.config.waiter_default_interval_seconds,
                    logger=self.logger,
                )
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete DB instance {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        with ThreadPoolExecutor(max_workers=self.config.parallel_workers) as pool:
            futures = [pool.submit(_delete_one, record) for record in records]
            for future in as_completed(futures):
                future.result()

    @retry_with_backoff()
    def _delete_instance(self, client, record: ResourceRecord) -> None:
        kwargs = {"DBInstanceIdentifier": record.resource_id}
        if not record.metadata.get("cluster_id"):
            kwargs["SkipFinalSnapshot"] = not self.config.retain_final_snapshot
            if self.config.retain_final_snapshot:
                kwargs["FinalDBSnapshotIdentifier"] = f"{record.resource_id}-final-{int(time.time())}"
        client.delete_db_instance(**kwargs)

    def _delete_clusters(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        if not records:
            return

        def _delete_one(record: ResourceRecord) -> None:
            try:
                kwargs = {
                    "DBClusterIdentifier": record.resource_id,
                    "SkipFinalSnapshot": not self.config.retain_final_snapshot,
                }
                if self.config.retain_final_snapshot:
                    kwargs["FinalDBClusterSnapshotIdentifier"] = f"{record.resource_id}-final-{int(time.time())}"
                self._delete_cluster(client, kwargs)
                self.logger.cleanup(f"Deleted DB cluster {record.name}")
                self.logger.wait(f"Waiting for DB cluster deletion: {record.name}")
                wait_rds_cluster_deleted(
                    client, record.resource_id,
                    timeout=self.config.waiter_default_timeout_seconds,
                    interval=self.config.waiter_default_interval_seconds,
                    logger=self.logger,
                )
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete DB cluster {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        with ThreadPoolExecutor(max_workers=self.config.parallel_workers) as pool:
            futures = [pool.submit(_delete_one, record) for record in records]
            for future in as_completed(futures):
                future.result()

    @retry_with_backoff()
    def _delete_cluster(self, client, kwargs: dict) -> None:
        client.delete_db_cluster(**kwargs)

    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying RDS is clean ...")
        try:
            clusters = self._describe_clusters()
            instances = self._describe_instances()
        except ClientError as exc:
            return VerificationResult(self.service_name, passed=False, error=str(exc))

        remaining = [c["DBClusterIdentifier"] for c in clusters] + [i["DBInstanceIdentifier"] for i in instances]
        passed = len(remaining) == 0
        self.logger.verify("RDS clean" if passed else f"RDS FAIL - {len(remaining)} resources remain")
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )

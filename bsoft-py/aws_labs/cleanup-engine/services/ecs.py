from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from botocore.exceptions import ClientError

from base.base_cleanup import BaseCleanupService
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult, ResourceRecord
from models.verification_result import VerificationResult
from retry import retry_with_backoff
from utils import chunked

ACTIVE_CLUSTER_STATUSES = ["ACTIVE", "PROVISIONING", "DEPROVISIONING", "FAILED"]


class ECSCleanupService(BaseCleanupService):
    """Deletes ECS services, standalone tasks, EC2-launch-type container
    instances, task definitions, and clusters, in dependency order:

    1. Force-delete every service (force=True stops its tasks and deletes
       the service without requiring desiredCount to be scaled to 0
       first).
    2. Stop any task not already owned by a service (a service's own tasks
       are already gone from step 1).
    3. Deregister EC2-launch-type container instances (Fargate clusters
       have none of these; a cluster can't be deleted while any remain
       registered).
    4. Deregister every ACTIVE task definition revision, then permanently
       delete the now-INACTIVE revisions.
    5. Delete clusters, once they have no services, tasks, or registered
       container instances left.

    Capacity providers are intentionally out of scope for this module --
    the FARGATE/FARGATE_SPOT providers AWS attaches by default can't be
    deleted at all, and customer-created Auto Scaling Group-backed capacity
    providers are uncommon enough in typical sandbox/training labs that
    they're left for a future module rather than guessed at here.

    Honors protected_resource_arns, exclude_tags, and the checkpoint DB via
    should_skip_resource().
    """

    service_name = "ecs"

    def _client(self):
        return self.clients.client("ecs")

    @retry_with_backoff()
    def _tags_for(self, resource_arn: str) -> Dict[str, str]:
        try:
            resp = self._client().list_tags_for_resource(resourceArn=resource_arn)
            return {t["key"]: t["value"] for t in resp.get("tags", [])}
        except ClientError:
            return {}

    # ------------------------------------------------------------ listing
    @retry_with_backoff()
    def _list_cluster_arns(self) -> List[str]:
        paginator = self._client().get_paginator("list_clusters")
        arns: List[str] = []
        for page in paginator.paginate():
            arns.extend(page.get("clusterArns", []))
        return arns

    @retry_with_backoff()
    def _describe_clusters(self, cluster_arns: List[str]) -> List[dict]:
        client = self._client()
        clusters: List[dict] = []
        for batch in chunked(cluster_arns, 100):
            resp = client.describe_clusters(clusters=batch)
            clusters.extend(resp.get("clusters", []))
        return clusters

    @retry_with_backoff()
    def _list_service_arns(self, cluster_arn: str) -> List[str]:
        paginator = self._client().get_paginator("list_services")
        arns: List[str] = []
        for page in paginator.paginate(cluster=cluster_arn):
            arns.extend(page.get("serviceArns", []))
        return arns

    @retry_with_backoff()
    def _describe_services(self, cluster_arn: str, service_arns: List[str]) -> List[dict]:
        client = self._client()
        services: List[dict] = []
        for batch in chunked(service_arns, 10):
            resp = client.describe_services(cluster=cluster_arn, services=batch)
            services.extend(resp.get("services", []))
        return services

    @retry_with_backoff()
    def _list_task_arns(self, cluster_arn: str) -> List[str]:
        paginator = self._client().get_paginator("list_tasks")
        arns: List[str] = []
        for page in paginator.paginate(cluster=cluster_arn):
            arns.extend(page.get("taskArns", []))
        return arns

    @retry_with_backoff()
    def _describe_tasks(self, cluster_arn: str, task_arns: List[str]) -> List[dict]:
        client = self._client()
        tasks: List[dict] = []
        for batch in chunked(task_arns, 100):
            resp = client.describe_tasks(cluster=cluster_arn, tasks=batch)
            tasks.extend(resp.get("tasks", []))
        return tasks

    @retry_with_backoff()
    def _list_container_instance_arns(self, cluster_arn: str) -> List[str]:
        paginator = self._client().get_paginator("list_container_instances")
        arns: List[str] = []
        for page in paginator.paginate(cluster=cluster_arn):
            arns.extend(page.get("containerInstanceArns", []))
        return arns

    @retry_with_backoff()
    def _list_active_task_definition_arns(self) -> List[str]:
        paginator = self._client().get_paginator("list_task_definitions")
        arns: List[str] = []
        for page in paginator.paginate(status="ACTIVE"):
            arns.extend(page.get("taskDefinitionArns", []))
        return arns

    # ---------------------------------------------------------------------
    def discover(self) -> InventoryResult:
        self.logger.discover("Scanning ECS clusters, services, tasks, container instances, and task definitions ...")
        result = InventoryResult(service_name=self.service_name)
        try:
            cluster_arns = self._list_cluster_arns()
            clusters = self._describe_clusters(cluster_arns) if cluster_arns else []
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"ECS discovery failed: {exc}")
            return result

        service_count = 0
        task_count = 0
        container_instance_count = 0
        for cluster in clusters:
            if cluster.get("status") not in ACTIVE_CLUSTER_STATUSES:
                continue
            cluster_arn = cluster["clusterArn"]
            cluster_name = cluster["clusterName"]

            try:
                service_arns = self._list_service_arns(cluster_arn)
                services = self._describe_services(cluster_arn, service_arns) if service_arns else []
            except ClientError as exc:
                self.logger.warning(f"Could not list services on cluster {cluster_name}: {exc}")
                services = []

            service_managed_task_arns = set()
            for svc in services:
                if svc.get("status") not in ("ACTIVE", "DRAINING"):
                    continue
                service_count += 1
                tags = self._tags_for(svc["serviceArn"])
                result.resources.append(
                    ResourceRecord(
                        resource_id=f"{cluster_arn}::{svc['serviceName']}",
                        name=svc["serviceName"],
                        arn=svc["serviceArn"],
                        metadata={"type": "service", "cluster_arn": cluster_arn, "cluster_name": cluster_name, "tags": tags},
                        fingerprint=str(svc.get("createdAt", "")),
                    )
                )

            try:
                task_arns = self._list_task_arns(cluster_arn)
                tasks = self._describe_tasks(cluster_arn, task_arns) if task_arns else []
            except ClientError as exc:
                self.logger.warning(f"Could not list tasks on cluster {cluster_name}: {exc}")
                tasks = []

            for task in tasks:
                group = task.get("group", "")
                if group.startswith("service:"):
                    service_managed_task_arns.add(task["taskArn"])
                    continue  # will be stopped when its service is force-deleted
                task_count += 1
                result.resources.append(
                    ResourceRecord(
                        resource_id=f"{cluster_arn}::{task['taskArn']}",
                        name=task["taskArn"].rsplit("/", 1)[-1],
                        arn=task["taskArn"],
                        metadata={"type": "task", "cluster_arn": cluster_arn, "cluster_name": cluster_name, "tags": {}},
                        fingerprint=str(task.get("createdAt", "")),
                    )
                )

            try:
                ci_arns = self._list_container_instance_arns(cluster_arn)
            except ClientError as exc:
                self.logger.warning(f"Could not list container instances on cluster {cluster_name}: {exc}")
                ci_arns = []

            for ci_arn in ci_arns:
                container_instance_count += 1
                result.resources.append(
                    ResourceRecord(
                        resource_id=f"{cluster_arn}::{ci_arn}",
                        name=ci_arn.rsplit("/", 1)[-1],
                        arn=ci_arn,
                        metadata={"type": "container_instance", "cluster_arn": cluster_arn, "cluster_name": cluster_name, "tags": {}},
                    )
                )

            result.resources.append(
                ResourceRecord(
                    resource_id=cluster_arn,
                    name=cluster_name,
                    arn=cluster_arn,
                    metadata={"type": "cluster", "tags": self._tags_for(cluster_arn)},
                )
            )

        try:
            task_def_arns = self._list_active_task_definition_arns()
        except ClientError as exc:
            self.logger.warning(f"Could not list task definitions: {exc}")
            task_def_arns = []

        for arn in task_def_arns:
            result.resources.append(
                ResourceRecord(
                    resource_id=arn,
                    name=arn.rsplit("/", 1)[-1],
                    arn=arn,
                    metadata={"type": "task_definition", "tags": self._tags_for(arn)},
                    # family:revision is unique per registration -- a new
                    # register_task_definition call always bumps the
                    # revision number, so no identifier-reuse risk exists.
                )
            )

        cluster_count = sum(1 for r in result.resources if r.metadata.get("type") == "cluster")
        self.logger.discover(
            f"Found {cluster_count} clusters, {service_count} services, {task_count} standalone tasks, "
            f"{container_instance_count} container instances, {len(task_def_arns)} active task definitions"
        )
        return result

    # --------------------------------------------------------------- clean
    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No ECS resources to delete")
            return result

        if self.config.dry_run:
            for record in inventory.resources:
                self.logger.cleanup(f"[DRY-RUN] Would delete ECS {record.metadata.get('type')} {record.name}")
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
            return result

        client = self._client()

        def by_type(t: str) -> List[ResourceRecord]:
            return self._apply_skip_gate([r for r in inventory.resources if r.metadata.get("type") == t], result)

        services = by_type("service")
        tasks = by_type("task")
        container_instances = by_type("container_instance")
        task_definitions = by_type("task_definition")
        clusters = by_type("cluster")

        self._delete_services(client, services, result)
        self._stop_tasks(client, tasks, result)
        self._deregister_container_instances(client, container_instances, result)
        self._delete_task_definitions(client, task_definitions, result)
        self._delete_clusters(client, clusters, result)

        return result

    def _apply_skip_gate(self, records: List[ResourceRecord], result: CleanupResult) -> List[ResourceRecord]:
        kept = []
        for record in records:
            reason = self.should_skip_resource(record)
            if reason:
                self.logger.cleanup(f"Skipping ECS resource {record.name}: {reason}")
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

    # ---- services ----
    def _delete_services(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._force_delete_service(client, record.metadata["cluster_arn"], record.name)
                self.logger.cleanup(f"Deleted service {record.name} (cluster {record.metadata['cluster_name']})")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete service {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _force_delete_service(self, client, cluster_arn: str, service_name: str) -> None:
        client.delete_service(cluster=cluster_arn, service=service_name, force=True)

    # ---- standalone tasks ----
    def _stop_tasks(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _stop_one(record: ResourceRecord) -> None:
            try:
                self._stop_task(client, record.metadata["cluster_arn"], record.arn)
                self.logger.cleanup(f"Stopped task {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to stop task {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _stop_one)

    @retry_with_backoff()
    def _stop_task(self, client, cluster_arn: str, task_arn: str) -> None:
        client.stop_task(cluster=cluster_arn, task=task_arn, reason="sandbox-cleanup-engine")

    # ---- container instances ----
    def _deregister_container_instances(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _deregister_one(record: ResourceRecord) -> None:
            try:
                self._deregister_container_instance(client, record.metadata["cluster_arn"], record.arn)
                self.logger.cleanup(f"Deregistered container instance {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to deregister container instance {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _deregister_one)

    @retry_with_backoff()
    def _deregister_container_instance(self, client, cluster_arn: str, container_instance_arn: str) -> None:
        client.deregister_container_instance(cluster=cluster_arn, containerInstance=container_instance_arn, force=True)

    # ---- task definitions ----
    def _delete_task_definitions(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._deregister_task_definition(client, record.arn)
                self.logger.cleanup(f"Deregistered task definition {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to deregister task definition {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)
        # Permanently delete the now-INACTIVE revisions in a single batched
        # pass (delete_task_definitions accepts up to 10 ARNs per call) --
        # a best-effort cleanup step, not required for cluster deletion.
        deregistered_arns = [r.arn for r in records if r.arn]
        if deregistered_arns:
            try:
                for batch in chunked(deregistered_arns, 10):
                    self._call_delete_task_definitions(client, batch)
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(f"Could not permanently delete some task definition revisions: {exc}")

    @retry_with_backoff()
    def _deregister_task_definition(self, client, task_definition_arn: str) -> None:
        client.deregister_task_definition(taskDefinition=task_definition_arn)

    @retry_with_backoff()
    def _call_delete_task_definitions(self, client, arns: List[str]) -> None:
        client.delete_task_definitions(taskDefinitions=arns)

    # ---- clusters ----
    def _delete_clusters(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._delete_cluster(client, record.resource_id)
                self.logger.cleanup(f"Deleted cluster {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete cluster {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _delete_cluster(self, client, cluster_arn: str) -> None:
        client.delete_cluster(cluster=cluster_arn)

    # -------------------------------------------------------------- verify
    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying ECS is clean ...")
        remaining: List[str] = []

        try:
            cluster_arns = self._list_cluster_arns()
            clusters = self._describe_clusters(cluster_arns) if cluster_arns else []
        except ClientError as exc:
            return VerificationResult(self.service_name, passed=False, error=str(exc))

        for cluster in clusters:
            if cluster.get("status") not in ACTIVE_CLUSTER_STATUSES:
                continue
            remaining.append(f"cluster:{cluster['clusterName']}")
            cluster_arn = cluster["clusterArn"]
            try:
                service_arns = self._list_service_arns(cluster_arn)
                remaining += [f"service:{cluster['clusterName']}/{arn.rsplit('/', 1)[-1]}" for arn in service_arns]
            except ClientError:
                pass
            try:
                task_arns = self._list_task_arns(cluster_arn)
                remaining += [f"task:{cluster['clusterName']}/{arn.rsplit('/', 1)[-1]}" for arn in task_arns]
            except ClientError:
                pass

        try:
            task_def_arns = self._list_active_task_definition_arns()
            remaining += [f"task_definition:{arn.rsplit('/', 1)[-1]}" for arn in task_def_arns]
        except ClientError as exc:
            self.logger.warning(f"Could not verify task definitions: {exc}")

        passed = len(remaining) == 0
        self.logger.verify("ECS clean" if passed else f"ECS FAIL - {len(remaining)} resources remain")
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )
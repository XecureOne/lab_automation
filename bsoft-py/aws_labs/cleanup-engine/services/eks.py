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


class EKSCleanupService(BaseCleanupService):
    """Deletes EKS clusters and everything AWS won't clean up on its own,
    in dependency order:

    1. Node groups: delete, wait for each to disappear (a cluster can't be
       deleted while it still has any).
    2. Fargate profiles: delete, wait for each to disappear (same
       constraint as node groups).
    3. Add-ons: delete (force=True). Not a hard blocker for cluster
       deletion, but left running they'd otherwise just get silently
       swept away with the cluster -- deleting them explicitly keeps the
       inventory/verification story honest about what this tool removed.
    4. Access entries (EKS API-based cluster access control): delete.
       Also not a hard blocker; cleaned up for the same reason as add-ons.
    5. Cluster: delete, wait for it to disappear.
    6. CloudWatch Logs log group for the cluster's control-plane logs
       (/aws/eks/<cluster>/cluster): delete. EKS does NOT delete this
       automatically when the cluster is deleted -- it's one of the most
       common EKS leftovers.
    7. IAM OIDC identity provider backing the cluster's IRSA (IAM Roles
       for Service Accounts) setup, matched by the cluster's OIDC issuer
       URL: delete. EKS does NOT delete this when the cluster is deleted
       either -- clusters created with `eksctl` or Terraform almost always
       have one, and it's an equally common orphaned-resource complaint
       against aws-nuke.

    Self-managed node groups (plain EC2 Auto Scaling Groups tagged for
    Kubernetes, rather than an eks:Nodegroup API object) are out of scope
    here -- they're cleaned up by the autoscaling/ec2 modules instead,
    since EKS has no API awareness of them at all.

    Honors protected_resource_arns, exclude_tags, and the checkpoint DB via
    should_skip_resource() for every resource type below.
    """

    service_name = "eks"

    def _client(self):
        return self.clients.client("eks")

    # ------------------------------------------------------------ listing
    @retry_with_backoff()
    def _list_cluster_names(self) -> List[str]:
        paginator = self._client().get_paginator("list_clusters")
        names: List[str] = []
        for page in paginator.paginate():
            names.extend(page.get("clusters", []))
        return names

    @retry_with_backoff()
    def _describe_cluster(self, name: str) -> Optional[dict]:
        try:
            return self._client().describe_cluster(name=name).get("cluster")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "ResourceNotFoundException":
                return None
            raise

    @retry_with_backoff()
    def _list_nodegroup_names(self, cluster_name: str) -> List[str]:
        paginator = self._client().get_paginator("list_nodegroups")
        names: List[str] = []
        for page in paginator.paginate(clusterName=cluster_name):
            names.extend(page.get("nodegroups", []))
        return names

    @retry_with_backoff()
    def _describe_nodegroup(self, cluster_name: str, nodegroup_name: str) -> Optional[dict]:
        try:
            resp = self._client().describe_nodegroup(clusterName=cluster_name, nodegroupName=nodegroup_name)
            return resp.get("nodegroup")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "ResourceNotFoundException":
                return None
            raise

    @retry_with_backoff()
    def _list_fargate_profile_names(self, cluster_name: str) -> List[str]:
        paginator = self._client().get_paginator("list_fargate_profiles")
        names: List[str] = []
        for page in paginator.paginate(clusterName=cluster_name):
            names.extend(page.get("fargateProfileNames", []))
        return names

    @retry_with_backoff()
    def _describe_fargate_profile(self, cluster_name: str, profile_name: str) -> Optional[dict]:
        try:
            resp = self._client().describe_fargate_profile(clusterName=cluster_name, fargateProfileName=profile_name)
            return resp.get("fargateProfile")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "ResourceNotFoundException":
                return None
            raise

    @retry_with_backoff()
    def _list_addon_names(self, cluster_name: str) -> List[str]:
        paginator = self._client().get_paginator("list_addons")
        names: List[str] = []
        for page in paginator.paginate(clusterName=cluster_name):
            names.extend(page.get("addons", []))
        return names

    @retry_with_backoff()
    def _describe_addon(self, cluster_name: str, addon_name: str) -> Optional[dict]:
        try:
            resp = self._client().describe_addon(clusterName=cluster_name, addonName=addon_name)
            return resp.get("addon")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "ResourceNotFoundException":
                return None
            raise

    @retry_with_backoff()
    def _list_access_entries(self, cluster_name: str) -> List[dict]:
        try:
            paginator = self._client().get_paginator("list_access_entries")
            entries: List[dict] = []
            for page in paginator.paginate(clusterName=cluster_name):
                for principal_arn in page.get("accessEntries", []):
                    resp = self._client().describe_access_entry(clusterName=cluster_name, principalArn=principal_arn)
                    entries.append(resp.get("accessEntry", {}))
            return entries
        except ClientError as exc:
            # Clusters that predate EKS access entries, or that still run
            # ConfigMap-only authentication, reject this call outright --
            # treat that as "nothing to clean up here" rather than a
            # discovery failure.
            if exc.response.get("Error", {}).get("Code", "") in ("ResourceNotFoundException", "InvalidParameterException"):
                return []
            raise

    @retry_with_backoff()
    def _log_group_exists(self, log_group_name: str) -> Optional[dict]:
        logs_client = self.clients.client("logs")
        resp = logs_client.describe_log_groups(logGroupNamePrefix=log_group_name, limit=1)
        for group in resp.get("logGroups", []):
            if group.get("logGroupName") == log_group_name:
                return group
        return None

    @retry_with_backoff()
    def _find_oidc_provider(self, issuer_url: str) -> Optional[dict]:
        if not issuer_url:
            return None
        issuer_host_and_path = issuer_url.replace("https://", "").replace("http://", "")
        iam_client = self.clients.client("iam")
        resp = iam_client.list_open_id_connect_providers()
        for provider in resp.get("OpenIDConnectProviderList", []):
            if provider["Arn"].endswith(issuer_host_and_path):
                try:
                    detail = iam_client.get_open_id_connect_provider(OpenIDConnectProviderArn=provider["Arn"])
                except ClientError:
                    detail = {}
                return {"Arn": provider["Arn"], "CreateDate": detail.get("CreateDate", "")}
        return None

    # ---------------------------------------------------------------------
    def discover(self) -> InventoryResult:
        self.logger.discover("Scanning EKS clusters and their node groups, Fargate profiles, and add-ons ...")
        result = InventoryResult(service_name=self.service_name)
        try:
            cluster_names = self._list_cluster_names()
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"EKS discovery failed: {exc}")
            return result

        nodegroup_count = 0
        fargate_count = 0
        addon_count = 0
        access_entry_count = 0

        for cluster_name in cluster_names:
            cluster = self._describe_cluster(cluster_name)
            if cluster is None:
                continue
            if cluster.get("status") == "DELETING":
                continue

            result.resources.append(
                ResourceRecord(
                    resource_id=cluster_name,
                    name=cluster_name,
                    arn=cluster.get("arn", ""),
                    metadata={"type": "cluster", "tags": cluster.get("tags", {}) or {}},
                    fingerprint=str(cluster.get("createdAt", "")),
                )
            )

            try:
                for ng_name in self._list_nodegroup_names(cluster_name):
                    ng = self._describe_nodegroup(cluster_name, ng_name)
                    if ng is None:
                        continue
                    nodegroup_count += 1
                    result.resources.append(
                        ResourceRecord(
                            resource_id=f"{cluster_name}::{ng_name}",
                            name=ng_name,
                            arn=ng.get("nodegroupArn", ""),
                            metadata={"type": "nodegroup", "cluster_name": cluster_name, "tags": ng.get("tags", {}) or {}},
                            fingerprint=str(ng.get("createdAt", "")),
                        )
                    )
            except ClientError as exc:
                self.logger.warning(f"Could not list node groups for cluster {cluster_name}: {exc}")

            try:
                for fp_name in self._list_fargate_profile_names(cluster_name):
                    fp = self._describe_fargate_profile(cluster_name, fp_name)
                    if fp is None:
                        continue
                    fargate_count += 1
                    result.resources.append(
                        ResourceRecord(
                            resource_id=f"{cluster_name}::{fp_name}",
                            name=fp_name,
                            arn=fp.get("fargateProfileArn", ""),
                            metadata={"type": "fargate_profile", "cluster_name": cluster_name, "tags": fp.get("tags", {}) or {}},
                            fingerprint=str(fp.get("createdAt", "")),
                        )
                    )
            except ClientError as exc:
                self.logger.warning(f"Could not list Fargate profiles for cluster {cluster_name}: {exc}")

            try:
                for addon_name in self._list_addon_names(cluster_name):
                    addon = self._describe_addon(cluster_name, addon_name)
                    if addon is None:
                        continue
                    addon_count += 1
                    result.resources.append(
                        ResourceRecord(
                            resource_id=f"{cluster_name}::{addon_name}",
                            name=addon_name,
                            arn=addon.get("addonArn", ""),
                            metadata={"type": "addon", "cluster_name": cluster_name, "tags": addon.get("tags", {}) or {}},
                            fingerprint=str(addon.get("createdAt", "")),
                        )
                    )
            except ClientError as exc:
                self.logger.warning(f"Could not list add-ons for cluster {cluster_name}: {exc}")

            for entry in self._list_access_entries(cluster_name):
                principal_arn = entry.get("principalArn", "")
                if not principal_arn:
                    continue
                access_entry_count += 1
                result.resources.append(
                    ResourceRecord(
                        resource_id=f"{cluster_name}::{principal_arn}",
                        name=principal_arn.rsplit("/", 1)[-1],
                        arn=entry.get("accessEntryArn", ""),
                        metadata={"type": "access_entry", "cluster_name": cluster_name, "principal_arn": principal_arn, "tags": {}},
                        fingerprint=str(entry.get("createdAt", "")),
                    )
                )

            log_group_name = f"/aws/eks/{cluster_name}/cluster"
            try:
                log_group = self._log_group_exists(log_group_name)
            except ClientError as exc:
                self.logger.warning(f"Could not check log group for cluster {cluster_name}: {exc}")
                log_group = None
            if log_group is not None:
                result.resources.append(
                    ResourceRecord(
                        resource_id=log_group_name,
                        name=log_group_name,
                        arn=log_group.get("arn", ""),
                        metadata={"type": "log_group", "tags": {}},
                        fingerprint=str(log_group.get("creationTime", "")),
                    )
                )

            issuer_url = (cluster.get("identity") or {}).get("oidc", {}).get("issuer", "")
            try:
                oidc_provider = self._find_oidc_provider(issuer_url)
            except ClientError as exc:
                self.logger.warning(f"Could not check OIDC provider for cluster {cluster_name}: {exc}")
                oidc_provider = None
            if oidc_provider is not None:
                result.resources.append(
                    ResourceRecord(
                        resource_id=oidc_provider["Arn"],
                        name=oidc_provider["Arn"].rsplit("/", 1)[-1],
                        arn=oidc_provider["Arn"],
                        metadata={"type": "oidc_provider", "cluster_name": cluster_name, "tags": {}},
                        fingerprint=str(oidc_provider.get("CreateDate", "")),
                    )
                )

        cluster_count = sum(1 for r in result.resources if r.metadata.get("type") == "cluster")
        self.logger.discover(
            f"Found {cluster_count} clusters, {nodegroup_count} node groups, {fargate_count} Fargate profiles, "
            f"{addon_count} add-ons, {access_entry_count} access entries"
        )
        return result

    # --------------------------------------------------------------- clean
    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No EKS resources to delete")
            return result

        if self.config.dry_run:
            for record in inventory.resources:
                self.logger.cleanup(f"[DRY-RUN] Would delete EKS {record.metadata.get('type')} {record.name}")
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
            return result

        client = self._client()

        def by_type(t: str) -> List[ResourceRecord]:
            return self._apply_skip_gate([r for r in inventory.resources if r.metadata.get("type") == t], result)

        nodegroups = by_type("nodegroup")
        fargate_profiles = by_type("fargate_profile")
        addons = by_type("addon")
        access_entries = by_type("access_entry")
        clusters = by_type("cluster")
        log_groups = by_type("log_group")
        oidc_providers = by_type("oidc_provider")

        self._delete_nodegroups(client, nodegroups, result)
        self._delete_fargate_profiles(client, fargate_profiles, result)
        self._delete_addons(client, addons, result)
        self._delete_access_entries(client, access_entries, result)
        self._delete_clusters(client, clusters, result)
        self._delete_log_groups(log_groups, result)
        self._delete_oidc_providers(oidc_providers, result)

        return result

    def _apply_skip_gate(self, records: List[ResourceRecord], result: CleanupResult) -> List[ResourceRecord]:
        kept = []
        for record in records:
            reason = self.should_skip_resource(record)
            if reason:
                self.logger.cleanup(f"Skipping EKS resource {record.name}: {reason}")
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

    # ---- node groups ----
    def _delete_nodegroups(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            cluster_name = record.metadata["cluster_name"]
            ng_name = record.name
            try:
                self._call_delete_nodegroup(client, cluster_name, ng_name)
                self.logger.wait(f"Waiting for node group deletion: {ng_name}")
                Waiter(self.logger).wait_for(
                    lambda: self._describe_nodegroup(cluster_name, ng_name) is None,
                    f"EKS node group '{ng_name}' deletion",
                    timeout=self.config.waiter_default_timeout_seconds,
                    interval=self.config.waiter_default_interval_seconds,
                )
                self.logger.cleanup(f"Deleted node group {ng_name} (cluster {cluster_name})")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete node group {ng_name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_nodegroup(self, client, cluster_name: str, nodegroup_name: str) -> None:
        client.delete_nodegroup(clusterName=cluster_name, nodegroupName=nodegroup_name)

    # ---- Fargate profiles ----
    def _delete_fargate_profiles(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            cluster_name = record.metadata["cluster_name"]
            fp_name = record.name
            try:
                self._call_delete_fargate_profile(client, cluster_name, fp_name)
                self.logger.wait(f"Waiting for Fargate profile deletion: {fp_name}")
                Waiter(self.logger).wait_for(
                    lambda: self._describe_fargate_profile(cluster_name, fp_name) is None,
                    f"EKS Fargate profile '{fp_name}' deletion",
                    timeout=self.config.waiter_default_timeout_seconds,
                    interval=self.config.waiter_default_interval_seconds,
                )
                self.logger.cleanup(f"Deleted Fargate profile {fp_name} (cluster {cluster_name})")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete Fargate profile {fp_name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_fargate_profile(self, client, cluster_name: str, profile_name: str) -> None:
        client.delete_fargate_profile(clusterName=cluster_name, fargateProfileName=profile_name)

    # ---- add-ons ----
    def _delete_addons(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._call_delete_addon(client, record.metadata["cluster_name"], record.name)
                self.logger.cleanup(f"Deleted add-on {record.name} (cluster {record.metadata['cluster_name']})")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete add-on {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_addon(self, client, cluster_name: str, addon_name: str) -> None:
        client.delete_addon(clusterName=cluster_name, addonName=addon_name, preserve=False)

    # ---- access entries ----
    def _delete_access_entries(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._call_delete_access_entry(client, record.metadata["cluster_name"], record.metadata["principal_arn"])
                self.logger.cleanup(f"Deleted access entry {record.name} (cluster {record.metadata['cluster_name']})")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete access entry {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_access_entry(self, client, cluster_name: str, principal_arn: str) -> None:
        client.delete_access_entry(clusterName=cluster_name, principalArn=principal_arn)

    # ---- clusters ----
    def _delete_clusters(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            name = record.resource_id
            try:
                self._call_delete_cluster(client, name)
                self.logger.wait(f"Waiting for cluster deletion: {name}")
                Waiter(self.logger).wait_for(
                    lambda: self._describe_cluster(name) is None,
                    f"EKS cluster '{name}' deletion",
                    timeout=self.config.waiter_default_timeout_seconds,
                    interval=self.config.waiter_default_interval_seconds,
                )
                self.logger.cleanup(f"Deleted cluster {name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete cluster {name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_cluster(self, client, cluster_name: str) -> None:
        client.delete_cluster(name=cluster_name)

    # ---- log groups ----
    def _delete_log_groups(self, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._call_delete_log_group(record.resource_id)
                self.logger.cleanup(f"Deleted log group {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete log group {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_log_group(self, log_group_name: str) -> None:
        self.clients.client("logs").delete_log_group(logGroupName=log_group_name)

    # ---- OIDC providers ----
    def _delete_oidc_providers(self, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._call_delete_oidc_provider(record.resource_id)
                self.logger.cleanup(f"Deleted OIDC provider {record.name} (cluster {record.metadata.get('cluster_name', '')})")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete OIDC provider {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_oidc_provider(self, provider_arn: str) -> None:
        self.clients.client("iam").delete_open_id_connect_provider(OpenIDConnectProviderArn=provider_arn)

    # -------------------------------------------------------------- verify
    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying EKS is clean ...")
        remaining: List[str] = []
        try:
            cluster_names = self._list_cluster_names()
        except ClientError as exc:
            return VerificationResult(self.service_name, passed=False, error=str(exc))

        for cluster_name in cluster_names:
            cluster = self._describe_cluster(cluster_name)
            if cluster is None or cluster.get("status") == "DELETING":
                continue
            remaining.append(f"cluster:{cluster_name}")
            try:
                remaining += [f"nodegroup:{cluster_name}/{n}" for n in self._list_nodegroup_names(cluster_name)]
            except ClientError:
                pass
            try:
                remaining += [f"fargate_profile:{cluster_name}/{n}" for n in self._list_fargate_profile_names(cluster_name)]
            except ClientError:
                pass
            try:
                remaining += [f"addon:{cluster_name}/{n}" for n in self._list_addon_names(cluster_name)]
            except ClientError:
                pass

            log_group_name = f"/aws/eks/{cluster_name}/cluster"
            try:
                if self._log_group_exists(log_group_name) is not None:
                    remaining.append(f"log_group:{log_group_name}")
            except ClientError:
                pass

            issuer_url = (cluster.get("identity") or {}).get("oidc", {}).get("issuer", "")
            try:
                if self._find_oidc_provider(issuer_url) is not None:
                    remaining.append(f"oidc_provider:{cluster_name}")
            except ClientError:
                pass

        passed = len(remaining) == 0
        self.logger.verify("EKS clean" if passed else f"EKS FAIL - {len(remaining)} resources remain")
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )
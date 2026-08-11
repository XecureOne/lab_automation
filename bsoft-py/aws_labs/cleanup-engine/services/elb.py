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


class ELBCleanupService(BaseCleanupService):
    """Deletes both generations of Elastic Load Balancing resources, in
    dependency order:

    1. elbv2 load balancers (ALB/NLB/GWLB): disable deletion protection
       (modify_load_balancer_attributes, deletion_protection.enabled ->
       false) if set, then delete. AWS refuses to delete a load balancer
       with deletion protection on, so this is done unconditionally up
       front rather than only on a caught failure. Deleting a load
       balancer also deletes its listeners automatically -- it does NOT
       delete its target groups, and does NOT affect registered targets
       (e.g. EC2 instances keep running).
    2. elbv2 target groups: delete, after step 1. A target group that is
       still referenced by a listener rejects deletion with
       ResourceInUseException -- deleting the load balancers first (which
       removes their listeners as a side effect) clears that block, so
       target groups are handled as a distinct, later pass rather than
       interleaved with load balancers.
    3. Classic load balancers (ELB "v1" -- a separate API/client from
       elbv2, with no target groups of its own; each classic LB's
       instances/listeners are deleted along with it): delete.

    Honors protected_resource_arns, exclude_tags, and the checkpoint DB via
    should_skip_resource() for every resource type below.
    """

    service_name = "elb"

    def _v2_client(self):
        return self.clients.client("elbv2")

    def _classic_client(self):
        return self.clients.client("elb")

    # ------------------------------------------------------------ listing
    @retry_with_backoff()
    def _list_v2_load_balancers(self) -> List[dict]:
        client = self._v2_client()
        lbs: List[dict] = []
        marker: Optional[str] = None
        while True:
            kwargs = {"Marker": marker} if marker else {}
            resp = client.describe_load_balancers(**kwargs)
            lbs.extend(resp.get("LoadBalancers", []))
            marker = resp.get("NextMarker")
            if not marker:
                break
        return lbs

    @retry_with_backoff()
    def _describe_v2_load_balancer(self, arn: str) -> Optional[dict]:
        try:
            resp = self._v2_client().describe_load_balancers(LoadBalancerArns=[arn])
            lbs = resp.get("LoadBalancers", [])
            return lbs[0] if lbs else None
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "LoadBalancerNotFound":
                return None
            raise

    @retry_with_backoff()
    def _list_target_groups(self) -> List[dict]:
        client = self._v2_client()
        groups: List[dict] = []
        marker: Optional[str] = None
        while True:
            kwargs = {"Marker": marker} if marker else {}
            resp = client.describe_target_groups(**kwargs)
            groups.extend(resp.get("TargetGroups", []))
            marker = resp.get("NextMarker")
            if not marker:
                break
        return groups

    @retry_with_backoff()
    def _describe_target_group(self, arn: str) -> Optional[dict]:
        try:
            resp = self._v2_client().describe_target_groups(TargetGroupArns=[arn])
            groups = resp.get("TargetGroups", [])
            return groups[0] if groups else None
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "TargetGroupNotFound":
                return None
            raise

    @retry_with_backoff()
    def _list_classic_load_balancers(self) -> List[dict]:
        client = self._classic_client()
        lbs: List[dict] = []
        marker: Optional[str] = None
        while True:
            kwargs = {"Marker": marker} if marker else {}
            resp = client.describe_load_balancers(**kwargs)
            lbs.extend(resp.get("LoadBalancerDescriptions", []))
            marker = resp.get("NextMarker")
            if not marker:
                break
        return lbs

    @retry_with_backoff()
    def _describe_classic_load_balancer(self, name: str) -> Optional[dict]:
        try:
            resp = self._classic_client().describe_load_balancers(LoadBalancerNames=[name])
            lbs = resp.get("LoadBalancerDescriptions", [])
            return lbs[0] if lbs else None
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "LoadBalancerNotFound":
                return None
            raise

    @retry_with_backoff()
    def _v2_tags_for(self, resource_arn: str) -> Dict[str, str]:
        try:
            resp = self._v2_client().describe_tags(ResourceArns=[resource_arn])
            descriptions = resp.get("TagDescriptions", [])
            if not descriptions:
                return {}
            return {t["Key"]: t["Value"] for t in descriptions[0].get("Tags", [])}
        except ClientError:
            return {}

    @retry_with_backoff()
    def _classic_tags_for(self, name: str) -> Dict[str, str]:
        try:
            resp = self._classic_client().describe_tags(LoadBalancerNames=[name])
            descriptions = resp.get("TagDescriptions", [])
            if not descriptions:
                return {}
            return {t["Key"]: t["Value"] for t in descriptions[0].get("Tags", [])}
        except ClientError:
            return {}

    # ---------------------------------------------------------------------
    def discover(self) -> InventoryResult:
        self.logger.discover("Scanning ELB (v2 + classic) load balancers and target groups ...")
        result = InventoryResult(service_name=self.service_name)

        try:
            v2_lbs = self._list_v2_load_balancers()
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"elbv2 discovery failed: {exc}")
            v2_lbs = []

        for lb in v2_lbs:
            arn = lb["LoadBalancerArn"]
            name = lb.get("LoadBalancerName", arn)
            result.resources.append(
                ResourceRecord(
                    resource_id=arn,
                    name=name,
                    arn=arn,
                    metadata={"type": "load_balancer_v2", "lb_type": lb.get("Type", ""), "tags": self._v2_tags_for(arn)},
                    fingerprint=str(lb.get("CreatedTime", "")),
                )
            )

        try:
            target_groups = self._list_target_groups()
        except ClientError as exc:
            self.logger.warning(f"Could not list target groups: {exc}")
            target_groups = []

        for tg in target_groups:
            arn = tg["TargetGroupArn"]
            name = tg.get("TargetGroupName", arn)
            result.resources.append(
                ResourceRecord(
                    resource_id=arn,
                    name=name,
                    arn=arn,
                    metadata={"type": "target_group", "tags": self._v2_tags_for(arn)},
                    fingerprint="",
                )
            )

        try:
            classic_lbs = self._list_classic_load_balancers()
        except ClientError as exc:
            self.logger.warning(f"Classic ELB discovery failed: {exc}")
            classic_lbs = []

        for lb in classic_lbs:
            name = lb["LoadBalancerName"]
            result.resources.append(
                ResourceRecord(
                    resource_id=name,
                    name=name,
                    arn="",  # classic ELB has no ARN in its describe response
                    metadata={"type": "load_balancer_classic", "tags": self._classic_tags_for(name)},
                    fingerprint=str(lb.get("CreatedTime", "")),
                )
            )

        self.logger.discover(
            f"Found {len(v2_lbs)} v2 load balancers, {len(target_groups)} target groups, "
            f"{len(classic_lbs)} classic load balancers"
        )
        return result

    # --------------------------------------------------------------- clean
    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No ELB resources to delete")
            return result

        if self.config.dry_run:
            for record in inventory.resources:
                self.logger.cleanup(f"[DRY-RUN] Would delete ELB {record.metadata.get('type')} {record.name}")
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
            return result

        v2_client = self._v2_client()
        classic_client = self._classic_client()

        def by_type(t: str) -> List[ResourceRecord]:
            return self._apply_skip_gate([r for r in inventory.resources if r.metadata.get("type") == t], result)

        v2_load_balancers = by_type("load_balancer_v2")
        target_groups = by_type("target_group")
        classic_load_balancers = by_type("load_balancer_classic")

        self._delete_v2_load_balancers(v2_client, v2_load_balancers, result)
        self._delete_target_groups(v2_client, target_groups, result)
        self._delete_classic_load_balancers(classic_client, classic_load_balancers, result)

        return result

    def _apply_skip_gate(self, records: List[ResourceRecord], result: CleanupResult) -> List[ResourceRecord]:
        kept = []
        for record in records:
            reason = self.should_skip_resource(record)
            if reason:
                self.logger.cleanup(f"Skipping ELB resource {record.name}: {reason}")
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

    # ---- elbv2 load balancers ----
    def _delete_v2_load_balancers(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            arn = record.resource_id
            try:
                self._disable_v2_deletion_protection(client, arn)
                self._call_delete_v2_load_balancer(client, arn)
                self.logger.wait(f"Waiting for load balancer deletion: {record.name}")
                Waiter(self.logger).wait_for(
                    lambda: self._describe_v2_load_balancer(arn) is None,
                    f"elbv2 load balancer '{record.name}' deletion",
                    timeout=self.config.waiter_default_timeout_seconds,
                    interval=self.config.waiter_default_interval_seconds,
                )
                self.logger.cleanup(f"Deleted load balancer {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete load balancer {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _disable_v2_deletion_protection(self, client, arn: str) -> None:
        # Deletion protection blocks delete_load_balancer outright, so
        # it's turned off unconditionally rather than waiting for the
        # delete call to fail first -- this call is a harmless no-op if
        # protection was already off.
        client.modify_load_balancer_attributes(
            LoadBalancerArn=arn, Attributes=[{"Key": "deletion_protection.enabled", "Value": "false"}]
        )

    @retry_with_backoff()
    def _call_delete_v2_load_balancer(self, client, arn: str) -> None:
        client.delete_load_balancer(LoadBalancerArn=arn)

    # ---- target groups ----
    def _delete_target_groups(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._call_delete_target_group(client, record.resource_id)
                self.logger.cleanup(f"Deleted target group {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete target group {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_target_group(self, client, arn: str) -> None:
        client.delete_target_group(TargetGroupArn=arn)

    # ---- classic load balancers ----
    def _delete_classic_load_balancers(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            name = record.resource_id
            try:
                self._call_delete_classic_load_balancer(client, name)
                self.logger.wait(f"Waiting for classic load balancer deletion: {name}")
                Waiter(self.logger).wait_for(
                    lambda: self._describe_classic_load_balancer(name) is None,
                    f"classic load balancer '{name}' deletion",
                    timeout=self.config.waiter_default_timeout_seconds,
                    interval=self.config.waiter_default_interval_seconds,
                )
                self.logger.cleanup(f"Deleted classic load balancer {name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete classic load balancer {name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_classic_load_balancer(self, client, name: str) -> None:
        # delete_load_balancer succeeds even if the load balancer doesn't
        # exist / was already deleted -- no separate not-found handling
        # needed around this call itself.
        client.delete_load_balancer(LoadBalancerName=name)

    # -------------------------------------------------------------- verify
    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying ELB (v2 + classic) is clean ...")
        remaining: List[str] = []
        error: Optional[str] = None

        try:
            for lb in self._list_v2_load_balancers():
                remaining.append(f"load_balancer_v2:{lb['LoadBalancerName']}")
        except ClientError as exc:
            error = str(exc)

        try:
            for tg in self._list_target_groups():
                remaining.append(f"target_group:{tg.get('TargetGroupName', tg.get('TargetGroupArn'))}")
        except ClientError as exc:
            error = error or str(exc)

        try:
            for lb in self._list_classic_load_balancers():
                remaining.append(f"load_balancer_classic:{lb['LoadBalancerName']}")
        except ClientError as exc:
            error = error or str(exc)

        if error and not remaining:
            return VerificationResult(self.service_name, passed=False, error=error)

        passed = len(remaining) == 0
        self.logger.verify("ELB clean" if passed else f"ELB FAIL - {len(remaining)} resources remain")
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )
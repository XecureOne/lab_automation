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
from waiters import Waiter

DEFAULT_SECURITY_GROUP_NAME = "default"


class EC2CleanupService(BaseCleanupService):
    """Deletes EC2 instances, volumes, snapshots, security groups, ENIs,
    VPC endpoints, NAT gateways, and elastic IPs, in dependency order:

    1. Terminate instances, wait for 'terminated'.
    2. Delete NAT gateways, wait for 'deleted' (a NAT gateway holds an
       elastic IP association that must be cleared before that IP can be
       released).
    3. Delete VPC endpoints.
    4. Disassociate and release elastic IPs.
    5. Delete non-default security groups (the retry decorator's built-in
       handling of DependencyViolation lets this succeed once whatever ENI
       or instance was still referencing the group finishes disappearing
       from steps 1-4, without any extra polling logic here).
    6. Delete available (unattached) ENIs.
    7. Delete available (unattached) EBS volumes.
    8. Delete self-owned EBS snapshots.

    Every resource type here uses an AWS-generated, never-reused ID
    (i-, vol-, snap-, sg-, eni-, eipalloc-, nat-, vpce-) rather than a
    user-chosen name, so unlike RDS instances/clusters or S3 buckets there
    is no risk of a later, unrelated resource colliding with a stale
    checkpoint entry -- no fingerprint is needed for correctness here.
    """

    service_name = "ec2"

    def _client(self):
        return self.clients.client("ec2")

    @staticmethod
    def _tags(item: dict) -> Dict[str, str]:
        return {t["Key"]: t["Value"] for t in item.get("Tags", [])}

    # ------------------------------------------------------------ listing
    @retry_with_backoff()
    def _list_instances(self) -> List[dict]:
        paginator = self._client().get_paginator("describe_instances")
        instances: List[dict] = []
        for page in paginator.paginate(
            Filters=[{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
        ):
            for reservation in page.get("Reservations", []):
                instances.extend(reservation.get("Instances", []))
        return instances

    @retry_with_backoff()
    def _list_volumes(self) -> List[dict]:
        paginator = self._client().get_paginator("describe_volumes")
        volumes: List[dict] = []
        for page in paginator.paginate():
            volumes.extend(page.get("Volumes", []))
        return volumes

    @retry_with_backoff()
    def _list_snapshots(self) -> List[dict]:
        paginator = self._client().get_paginator("describe_snapshots")
        snapshots: List[dict] = []
        for page in paginator.paginate(OwnerIds=["self"]):
            snapshots.extend(page.get("Snapshots", []))
        return snapshots

    @retry_with_backoff()
    def _list_security_groups(self) -> List[dict]:
        paginator = self._client().get_paginator("describe_security_groups")
        groups: List[dict] = []
        for page in paginator.paginate():
            groups.extend(page.get("SecurityGroups", []))
        return groups

    @retry_with_backoff()
    def _list_network_interfaces(self) -> List[dict]:
        paginator = self._client().get_paginator("describe_network_interfaces")
        enis: List[dict] = []
        for page in paginator.paginate():
            enis.extend(page.get("NetworkInterfaces", []))
        return enis

    @retry_with_backoff()
    def _list_addresses(self) -> List[dict]:
        return self._client().describe_addresses().get("Addresses", [])

    @retry_with_backoff()
    def _list_nat_gateways(self) -> List[dict]:
        paginator = self._client().get_paginator("describe_nat_gateways")
        gateways: List[dict] = []
        for page in paginator.paginate(
            Filter=[{"Name": "state", "Values": ["pending", "available", "failed"]}]
        ):
            gateways.extend(page.get("NatGateways", []))
        return gateways

    @retry_with_backoff()
    def _list_vpc_endpoints(self) -> List[dict]:
        paginator = self._client().get_paginator("describe_vpc_endpoints")
        endpoints: List[dict] = []
        for page in paginator.paginate(
            Filters=[{"Name": "vpc-endpoint-state", "Values": ["pendingAcceptance", "pending", "available"]}]
        ):
            endpoints.extend(page.get("VpcEndpoints", []))
        return endpoints

    # ---------------------------------------------------------------------
    def discover(self) -> InventoryResult:
        self.logger.discover("Scanning EC2 instances, volumes, snapshots, and networking resources ...")
        result = InventoryResult(service_name=self.service_name)
        try:
            instances = self._list_instances()
            volumes = self._list_volumes()
            snapshots = self._list_snapshots()
            security_groups = self._list_security_groups()
            enis = self._list_network_interfaces()
            addresses = self._list_addresses()
            nat_gateways = self._list_nat_gateways()
            vpc_endpoints = self._list_vpc_endpoints()
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"EC2 discovery failed: {exc}")
            return result

        for instance in instances:
            result.resources.append(
                ResourceRecord(
                    resource_id=instance["InstanceId"],
                    name=instance["InstanceId"],
                    arn="",
                    metadata={"type": "instance", "state": instance["State"]["Name"], "tags": self._tags(instance)},
                    fingerprint=str(instance.get("LaunchTime", "")),
                )
            )

        for volume in volumes:
            result.resources.append(
                ResourceRecord(
                    resource_id=volume["VolumeId"],
                    name=volume["VolumeId"],
                    arn="",
                    metadata={"type": "volume", "state": volume["State"], "tags": self._tags(volume)},
                    fingerprint=str(volume.get("CreateTime", "")),
                )
            )

        for snapshot in snapshots:
            result.resources.append(
                ResourceRecord(
                    resource_id=snapshot["SnapshotId"],
                    name=snapshot["SnapshotId"],
                    arn="",
                    metadata={"type": "snapshot", "tags": self._tags(snapshot)},
                    fingerprint=str(snapshot.get("StartTime", "")),
                )
            )

        for sg in security_groups:
            if sg.get("GroupName") == DEFAULT_SECURITY_GROUP_NAME:
                continue  # AWS-managed per VPC, cannot be deleted
            result.resources.append(
                ResourceRecord(
                    resource_id=sg["GroupId"],
                    name=sg.get("GroupName", sg["GroupId"]),
                    arn="",
                    metadata={"type": "security_group", "tags": self._tags(sg)},
                )
            )

        for eni in enis:
            if eni.get("Status") != "available":
                continue  # still attached to something outside this tool's scope
            result.resources.append(
                ResourceRecord(
                    resource_id=eni["NetworkInterfaceId"],
                    name=eni["NetworkInterfaceId"],
                    arn="",
                    metadata={"type": "eni", "tags": self._tags(eni)},
                )
            )

        for addr in addresses:
            allocation_id = addr.get("AllocationId")
            if not allocation_id:
                continue  # EC2-classic address, not relevant to a VPC-only account
            result.resources.append(
                ResourceRecord(
                    resource_id=allocation_id,
                    name=addr.get("PublicIp", allocation_id),
                    arn="",
                    metadata={
                        "type": "eip",
                        "association_id": addr.get("AssociationId", ""),
                        "tags": self._tags(addr),
                    },
                )
            )

        for nat in nat_gateways:
            result.resources.append(
                ResourceRecord(
                    resource_id=nat["NatGatewayId"],
                    name=nat["NatGatewayId"],
                    arn="",
                    metadata={"type": "nat_gateway", "state": nat["State"], "tags": self._tags(nat)},
                    fingerprint=str(nat.get("CreateTime", "")),
                )
            )

        for vpce in vpc_endpoints:
            result.resources.append(
                ResourceRecord(
                    resource_id=vpce["VpcEndpointId"],
                    name=vpce["VpcEndpointId"],
                    arn="",
                    metadata={"type": "vpc_endpoint", "tags": self._tags(vpce)},
                    fingerprint=str(vpce.get("CreationTimestamp", "")),
                )
            )

        self.logger.discover(
            f"Found {len(instances)} instances, {len(volumes)} volumes, {len(snapshots)} snapshots, "
            f"{result.resource_count - len(instances) - len(volumes) - len(snapshots)} networking resources"
        )
        return result

    # --------------------------------------------------------------- clean
    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No EC2 resources to delete")
            return result

        if self.config.dry_run:
            for record in inventory.resources:
                self.logger.cleanup(f"[DRY-RUN] Would delete EC2 {record.metadata.get('type')} {record.name}")
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
            return result

        client = self._client()

        def by_type(t: str) -> List[ResourceRecord]:
            return self._apply_skip_gate([r for r in inventory.resources if r.metadata.get("type") == t], result)

        instances = by_type("instance")
        nat_gateways = by_type("nat_gateway")
        vpc_endpoints = by_type("vpc_endpoint")
        eips = by_type("eip")
        security_groups = by_type("security_group")
        enis = by_type("eni")
        volumes = by_type("volume")
        snapshots = by_type("snapshot")

        self._terminate_instances(client, instances, result)
        self._delete_nat_gateways(client, nat_gateways, result)
        self._delete_vpc_endpoints(client, vpc_endpoints, result)
        self._release_eips(client, eips, result)
        self._delete_security_groups(client, security_groups, result)
        self._delete_enis(client, enis, result)
        self._delete_volumes(client, volumes, result)
        self._delete_snapshots(client, snapshots, result)

        return result

    def _apply_skip_gate(self, records: List[ResourceRecord], result: CleanupResult) -> List[ResourceRecord]:
        kept = []
        for record in records:
            reason = self.should_skip_resource(record)
            if reason:
                self.logger.cleanup(f"Skipping EC2 resource {record.name}: {reason}")
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

    # ---- instances ----
    def _terminate_instances(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        if not records:
            return
        ids = [r.resource_id for r in records]
        try:
            self._call_terminate_instances(client, ids)
        except Exception as exc:  # noqa: BLE001 - fall through to per-instance waits/failures below
            self.logger.error(f"terminate_instances call failed: {exc}")

        def _wait_one(record: ResourceRecord) -> None:
            try:
                self.logger.wait(f"Waiting for instance termination: {record.name}")
                Waiter(self.logger).wait_for(
                    lambda: self._instance_terminated(client, record.resource_id),
                    f"EC2 instance '{record.name}' termination",
                    timeout=self.config.waiter_default_timeout_seconds,
                    interval=self.config.waiter_default_interval_seconds,
                )
                self.logger.cleanup(f"Terminated instance {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to terminate instance {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _wait_one)

    @retry_with_backoff()
    def _call_terminate_instances(self, client, instance_ids: List[str]) -> None:
        for batch in chunked(instance_ids, 100):
            client.terminate_instances(InstanceIds=batch)

    @retry_with_backoff()
    def _instance_terminated(self, client, instance_id: str) -> bool:
        try:
            resp = client.describe_instances(InstanceIds=[instance_id])
            for reservation in resp.get("Reservations", []):
                for instance in reservation.get("Instances", []):
                    return instance["State"]["Name"] == "terminated"
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "InvalidInstanceID.NotFound":
                return True
            raise

    # ---- NAT gateways ----
    def _delete_nat_gateways(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._call_delete_nat_gateway(client, record.resource_id)
                self.logger.wait(f"Waiting for NAT gateway deletion: {record.name}")
                Waiter(self.logger).wait_for(
                    lambda: self._nat_gateway_deleted(client, record.resource_id),
                    f"NAT gateway '{record.name}' deletion",
                    timeout=self.config.waiter_default_timeout_seconds,
                    interval=self.config.waiter_default_interval_seconds,
                )
                self.logger.cleanup(f"Deleted NAT gateway {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete NAT gateway {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_nat_gateway(self, client, nat_gateway_id: str) -> None:
        client.delete_nat_gateway(NatGatewayId=nat_gateway_id)

    @retry_with_backoff()
    def _nat_gateway_deleted(self, client, nat_gateway_id: str) -> bool:
        resp = client.describe_nat_gateways(NatGatewayIds=[nat_gateway_id])
        gateways = resp.get("NatGateways", [])
        if not gateways:
            return True
        return gateways[0]["State"] in ("deleted", "failed")

    # ---- VPC endpoints ----
    def _delete_vpc_endpoints(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        if not records:
            return
        ids = [r.resource_id for r in records]
        by_id = {r.resource_id: r for r in records}
        try:
            for batch in chunked(ids, 1000):
                resp = self._call_delete_vpc_endpoints(client, batch)
                errors = {e["VpcEndpointId"]: e.get("Error", {}).get("Message", "") for e in resp.get("Unsuccessful", [])}
                for endpoint_id in batch:
                    record = by_id[endpoint_id]
                    if endpoint_id in errors:
                        self.logger.error(f"Failed to delete VPC endpoint {record.name}: {errors[endpoint_id]}")
                        result.add_failure(record.resource_id, record.name, errors[endpoint_id], record.arn)
                    else:
                        self.logger.cleanup(f"Deleted VPC endpoint {record.name}")
                        result.add_success(record.resource_id, record.name, record.arn)
                        self.record_deleted(record)
        except Exception as exc:  # noqa: BLE001
            for record in records:
                self.logger.error(f"Failed to delete VPC endpoint {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

    @retry_with_backoff()
    def _call_delete_vpc_endpoints(self, client, endpoint_ids: List[str]) -> dict:
        return client.delete_vpc_endpoints(VpcEndpointIds=endpoint_ids)

    # ---- elastic IPs ----
    def _release_eips(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                if record.metadata.get("association_id"):
                    self._disassociate_address(client, record.metadata["association_id"])
                self._release_address(client, record.resource_id)
                self.logger.cleanup(f"Released elastic IP {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to release elastic IP {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _disassociate_address(self, client, association_id: str) -> None:
        client.disassociate_address(AssociationId=association_id)

    @retry_with_backoff()
    def _release_address(self, client, allocation_id: str) -> None:
        client.release_address(AllocationId=allocation_id)

    # ---- security groups ----
    def _delete_security_groups(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._delete_security_group(client, record.resource_id)
                self.logger.cleanup(f"Deleted security group {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete security group {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _delete_security_group(self, client, group_id: str) -> None:
        client.delete_security_group(GroupId=group_id)

    # ---- ENIs ----
    def _delete_enis(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._delete_network_interface(client, record.resource_id)
                self.logger.cleanup(f"Deleted network interface {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete network interface {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _delete_network_interface(self, client, eni_id: str) -> None:
        client.delete_network_interface(NetworkInterfaceId=eni_id)

    # ---- volumes ----
    def _delete_volumes(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._delete_volume(client, record.resource_id)
                self.logger.cleanup(f"Deleted volume {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete volume {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _delete_volume(self, client, volume_id: str) -> None:
        client.delete_volume(VolumeId=volume_id)

    # ---- snapshots ----
    def _delete_snapshots(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._delete_snapshot(client, record.resource_id)
                self.logger.cleanup(f"Deleted snapshot {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete snapshot {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _delete_snapshot(self, client, snapshot_id: str) -> None:
        client.delete_snapshot(SnapshotId=snapshot_id)

    # -------------------------------------------------------------- verify
    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying EC2 is clean ...")
        try:
            instances = self._list_instances()
            volumes = self._list_volumes()
            snapshots = self._list_snapshots()
            security_groups = self._list_security_groups()
            enis = self._list_network_interfaces()
            addresses = self._list_addresses()
            nat_gateways = self._list_nat_gateways()
            vpc_endpoints = self._list_vpc_endpoints()
        except ClientError as exc:
            return VerificationResult(self.service_name, passed=False, error=str(exc))

        remaining: List[str] = []
        remaining += [f"instance:{i['InstanceId']}" for i in instances]
        remaining += [f"volume:{v['VolumeId']}" for v in volumes]
        remaining += [f"snapshot:{s['SnapshotId']}" for s in snapshots]
        remaining += [
            f"security_group:{g['GroupId']}" for g in security_groups if g.get("GroupName") != DEFAULT_SECURITY_GROUP_NAME
        ]
        remaining += [f"eni:{e['NetworkInterfaceId']}" for e in enis if e.get("Status") == "available"]
        remaining += [f"eip:{a['AllocationId']}" for a in addresses if a.get("AllocationId")]
        remaining += [f"nat_gateway:{n['NatGatewayId']}" for n in nat_gateways]
        remaining += [f"vpc_endpoint:{e['VpcEndpointId']}" for e in vpc_endpoints]

        passed = len(remaining) == 0
        self.logger.verify("EC2 clean" if passed else f"EC2 FAIL - {len(remaining)} resources remain")
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from botocore.exceptions import ClientError

from base.base_cleanup import BaseCleanupService
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult, ResourceRecord
from models.verification_result import VerificationResult
from retry import retry_with_backoff
from utils import chunked

DEFAULT_EVENT_BUS_NAME = "default"
DEFAULT_SCHEMA_REGISTRY_NAME = "default"
AWS_MANAGED_REGISTRY_PREFIX = "aws."
MAX_TARGET_IDS_PER_REMOVE_CALL = 10


class EventBridgeCleanupService(BaseCleanupService):
    """Deletes EventBridge rules (and their targets), custom event buses,
    event archives, and Schemas registries/schemas, in dependency order:

    1. Remove every target from a rule, then delete the rule. Rules on the
       'default' bus are cleaned up too (labs commonly add rules there),
       but a rule owned by another AWS service (ManagedBy set -- e.g. a
       GuardDuty or SecurityHub integration) is left alone: deleting it
       would break that integration and AWS rejects the call anyway.
    2. Delete schemas before the registry that holds them.
    3. Delete custom registries. The account's built-in 'default' registry
       and any AWS-managed registry (aws.events, aws.partner-*) are never
       deleted -- but schemas inside 'default' are still cleaned.
    4. Delete archives before the event bus they reference (an archive
       holds a reference to its source bus's ARN).
    5. Delete custom event buses. The 'default' bus is never deleted -- AWS
       doesn't allow it and every account always has exactly one.

    Honors protected_resource_arns, exclude_tags, and the checkpoint DB via
    should_skip_resource().
    """

    service_name = "eventbridge"

    def _client(self):
        return self.clients.client("events")

    def _schemas_client(self):
        return self.clients.client("schemas")

    def _account_id(self) -> str:
        if not hasattr(self, "_cached_account_id"):
            self._cached_account_id = self.clients.account_id()
        return self._cached_account_id

    def _arn(self, service: str, resource: str) -> str:
        return f"arn:aws:{service}:{self.config.region}:{self._account_id()}:{resource}"

    # ---------------------------------------------------------------- tags
    @retry_with_backoff()
    def _events_tags(self, arn: str) -> Dict[str, str]:
        try:
            resp = self._client().list_tags_for_resource(ResourceARN=arn)
            return {t["Key"]: t["Value"] for t in resp.get("Tags", [])}
        except ClientError:
            return {}

    @retry_with_backoff()
    def _schemas_tags(self, arn: str) -> Dict[str, str]:
        try:
            resp = self._schemas_client().list_tags_for_resource(ResourceArn=arn)
            return dict(resp.get("Tags", {}) or {})
        except ClientError:
            return {}

    # ------------------------------------------------------------ listing
    @retry_with_backoff()
    def _list_event_buses(self) -> List[dict]:
        client = self._client()
        buses: List[dict] = []
        next_token: Optional[str] = None
        while True:
            kwargs = {"NextToken": next_token} if next_token else {}
            resp = client.list_event_buses(**kwargs)
            buses.extend(resp.get("EventBuses", []))
            next_token = resp.get("NextToken")
            if not next_token:
                return buses

    @retry_with_backoff()
    def _list_rules(self, bus_name: str) -> List[dict]:
        client = self._client()
        rules: List[dict] = []
        next_token: Optional[str] = None
        while True:
            kwargs = {"EventBusName": bus_name}
            if next_token:
                kwargs["NextToken"] = next_token
            resp = client.list_rules(**kwargs)
            rules.extend(resp.get("Rules", []))
            next_token = resp.get("NextToken")
            if not next_token:
                return rules

    @retry_with_backoff()
    def _list_targets(self, bus_name: str, rule_name: str) -> List[dict]:
        client = self._client()
        targets: List[dict] = []
        next_token: Optional[str] = None
        while True:
            kwargs = {"Rule": rule_name, "EventBusName": bus_name}
            if next_token:
                kwargs["NextToken"] = next_token
            resp = client.list_targets_by_rule(**kwargs)
            targets.extend(resp.get("Targets", []))
            next_token = resp.get("NextToken")
            if not next_token:
                return targets

    @retry_with_backoff()
    def _list_archives(self) -> List[dict]:
        client = self._client()
        archives: List[dict] = []
        next_token: Optional[str] = None
        while True:
            kwargs = {"NextToken": next_token} if next_token else {}
            resp = client.list_archives(**kwargs)
            archives.extend(resp.get("Archives", []))
            next_token = resp.get("NextToken")
            if not next_token:
                return archives

    @retry_with_backoff()
    def _list_registries(self) -> List[dict]:
        client = self._schemas_client()
        registries: List[dict] = []
        next_token: Optional[str] = None
        while True:
            kwargs = {"NextToken": next_token} if next_token else {}
            resp = client.list_registries(**kwargs)
            registries.extend(resp.get("Registries", []))
            next_token = resp.get("NextToken")
            if not next_token:
                return registries

    @retry_with_backoff()
    def _list_schemas(self, registry_name: str) -> List[dict]:
        client = self._schemas_client()
        schemas: List[dict] = []
        next_token: Optional[str] = None
        while True:
            kwargs = {"RegistryName": registry_name}
            if next_token:
                kwargs["NextToken"] = next_token
            resp = client.list_schemas(**kwargs)
            schemas.extend(resp.get("Schemas", []))
            next_token = resp.get("NextToken")
            if not next_token:
                return schemas

    # ---------------------------------------------------------------------
    def discover(self) -> InventoryResult:
        self.logger.discover("Scanning EventBridge buses, rules, archives, and schema registries ...")
        result = InventoryResult(service_name=self.service_name)
        try:
            buses = self._list_event_buses()
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"EventBridge discovery failed: {exc}")
            return result

        bus_count = 0
        rule_count = 0
        for bus in buses:
            bus_name = bus["Name"]
            is_default_bus = bus_name == DEFAULT_EVENT_BUS_NAME
            bus_arn = bus.get("Arn") or self._arn("events", f"event-bus/{bus_name}")

            if not is_default_bus:
                bus_count += 1
                try:
                    tags = self._events_tags(bus_arn)
                except ClientError as exc:
                    self.logger.warning(f"Could not read tags for event bus {bus_name}: {exc}")
                    tags = {}
                result.resources.append(
                    ResourceRecord(
                        resource_id=bus_name,
                        name=bus_name,
                        arn=bus_arn,
                        metadata={"type": "bus", "tags": tags},
                    )
                )

            try:
                rules = self._list_rules(bus_name)
            except ClientError as exc:
                self.logger.warning(f"Could not list rules on bus {bus_name}: {exc}")
                continue

            for rule in rules:
                rule_name = rule["Name"]
                rule_arn = rule.get("Arn") or self._arn(
                    "events", f"rule/{rule_name}" if is_default_bus else f"rule/{bus_name}/{rule_name}"
                )
                managed_by = rule.get("ManagedBy", "")
                try:
                    target_ids = [t["Id"] for t in self._list_targets(bus_name, rule_name)]
                except ClientError as exc:
                    self.logger.warning(f"Could not list targets for rule {rule_name}: {exc}")
                    target_ids = []
                try:
                    tags = self._events_tags(rule_arn)
                except ClientError as exc:
                    self.logger.warning(f"Could not read tags for rule {rule_name}: {exc}")
                    tags = {}

                rule_count += 1
                result.resources.append(
                    ResourceRecord(
                        resource_id=f"{bus_name}::{rule_name}",
                        name=rule_name,
                        arn=rule_arn,
                        metadata={
                            "type": "rule",
                            "bus_name": bus_name,
                            "managed_by": managed_by,
                            "target_ids": target_ids,
                            "tags": tags,
                        },
                        # EventBridge does not expose a rule creation
                        # timestamp via ListRules, so a same-named rule
                        # recreated after deletion can't be distinguished
                        # from the one recorded in the checkpoint DB. This
                        # is a known limitation -- see README.
                    )
                )

        try:
            archives = self._list_archives()
        except ClientError as exc:
            self.logger.warning(f"Could not list EventBridge archives: {exc}")
            archives = []

        for archive in archives:
            archive_name = archive["ArchiveName"]
            archive_arn = archive.get("ArchiveArn") or self._arn("events", f"archive/{archive_name}")
            try:
                tags = self._events_tags(archive_arn)
            except ClientError as exc:
                self.logger.warning(f"Could not read tags for archive {archive_name}: {exc}")
                tags = {}
            result.resources.append(
                ResourceRecord(
                    resource_id=archive_name,
                    name=archive_name,
                    arn=archive_arn,
                    metadata={
                        "type": "archive",
                        "event_source_arn": archive.get("EventSourceArn", ""),
                        "tags": tags,
                    },
                    fingerprint=str(archive.get("CreationTime", "")),
                )
            )

        try:
            registries = self._list_registries()
        except ClientError as exc:
            self.logger.warning(f"Could not list Schemas registries: {exc}")
            registries = []

        registry_count = 0
        schema_count = 0
        for registry in registries:
            registry_name = registry["RegistryName"]
            if registry_name.startswith(AWS_MANAGED_REGISTRY_PREFIX):
                # aws.events, aws.partner-* -- not created by the account,
                # not deletable, and not worth enumerating schemas for.
                continue

            is_default_registry = registry_name == DEFAULT_SCHEMA_REGISTRY_NAME
            registry_arn = registry.get("RegistryArn") or self._arn("schemas", f"registry/{registry_name}")

            if not is_default_registry:
                registry_count += 1
                try:
                    registry_tags = self._schemas_tags(registry_arn)
                except ClientError as exc:
                    self.logger.warning(f"Could not read tags for schema registry {registry_name}: {exc}")
                    registry_tags = {}
                result.resources.append(
                    ResourceRecord(
                        resource_id=registry_name,
                        name=registry_name,
                        arn=registry_arn,
                        metadata={"type": "registry", "tags": registry_tags},
                    )
                )

            try:
                schemas = self._list_schemas(registry_name)
            except ClientError as exc:
                self.logger.warning(f"Could not list schemas in registry {registry_name}: {exc}")
                continue

            for schema in schemas:
                schema_name = schema["SchemaName"]
                schema_arn = schema.get("SchemaArn") or self._arn(
                    "schemas", f"schema/{registry_name}/{schema_name}"
                )
                schema_count += 1
                result.resources.append(
                    ResourceRecord(
                        resource_id=f"{registry_name}::{schema_name}",
                        name=schema_name,
                        arn=schema_arn,
                        metadata={"type": "schema", "registry_name": registry_name, "tags": {}},
                    )
                )

        self.logger.discover(
            f"Found {bus_count} custom event buses, {rule_count} rules, {len(archives)} archives, "
            f"{registry_count} custom schema registries, {schema_count} schemas"
        )
        return result

    # --------------------------------------------------------------- clean
    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No EventBridge resources to delete")
            return result

        if self.config.dry_run:
            for record in inventory.resources:
                self.logger.cleanup(
                    f"[DRY-RUN] Would delete EventBridge {record.metadata.get('type')} {record.name}"
                )
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
            return result

        client = self._client()
        schemas_client = self._schemas_client()

        rules = self._apply_skip_gate(
            [r for r in inventory.resources if r.metadata.get("type") == "rule"], result
        )
        buses = self._apply_skip_gate(
            [r for r in inventory.resources if r.metadata.get("type") == "bus"], result
        )
        archives = self._apply_skip_gate(
            [r for r in inventory.resources if r.metadata.get("type") == "archive"], result
        )
        registries = self._apply_skip_gate(
            [r for r in inventory.resources if r.metadata.get("type") == "registry"], result
        )
        schemas = self._apply_skip_gate(
            [r for r in inventory.resources if r.metadata.get("type") == "schema"], result
        )

        manageable_rules: List[ResourceRecord] = []
        for record in rules:
            managed_by = record.metadata.get("managed_by")
            if managed_by:
                self.logger.cleanup(
                    f"Skipping rule {record.name} on bus {record.metadata.get('bus_name')}: "
                    f"managed by {managed_by}"
                )
                result.add_skipped(record.resource_id, record.name, f"aws_managed_rule:{managed_by}", record.arn)
            else:
                manageable_rules.append(record)

        # Dependency order: targets off rules -> rules gone -> schemas gone
        # -> registries gone -> archives gone -> buses gone.
        self._delete_rules(client, manageable_rules, result)
        self._delete_schemas(schemas_client, schemas, result)
        self._delete_registries(schemas_client, registries, result)
        self._delete_archives(client, archives, result)
        self._delete_buses(client, buses, result)

        return result

    def _apply_skip_gate(self, records: List[ResourceRecord], result: CleanupResult) -> List[ResourceRecord]:
        kept = []
        for record in records:
            reason = self.should_skip_resource(record)
            if reason:
                self.logger.cleanup(f"Skipping EventBridge resource {record.name}: {reason}")
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

    # ---- rules ----
    def _delete_rules(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            bus_name = record.metadata.get("bus_name", DEFAULT_EVENT_BUS_NAME)
            rule_name = record.name
            try:
                target_ids = record.metadata.get("target_ids") or []
                if target_ids:
                    self._remove_targets(client, bus_name, rule_name, target_ids)
                    self.logger.cleanup(f"Removed {len(target_ids)} targets from rule {rule_name}")
                self._delete_rule(client, bus_name, rule_name)
                self.logger.cleanup(f"Deleted rule {rule_name} (bus {bus_name})")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete rule {rule_name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _remove_targets(self, client, bus_name: str, rule_name: str, target_ids: List[str]) -> None:
        for batch in chunked(target_ids, MAX_TARGET_IDS_PER_REMOVE_CALL):
            client.remove_targets(Rule=rule_name, EventBusName=bus_name, Ids=batch)

    @retry_with_backoff()
    def _delete_rule(self, client, bus_name: str, rule_name: str) -> None:
        client.delete_rule(Name=rule_name, EventBusName=bus_name)

    # ---- schemas / registries ----
    def _delete_schemas(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            registry_name = record.metadata.get("registry_name", DEFAULT_SCHEMA_REGISTRY_NAME)
            try:
                self._delete_schema(client, registry_name, record.name)
                self.logger.cleanup(f"Deleted schema {record.name} (registry {registry_name})")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete schema {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _delete_schema(self, client, registry_name: str, schema_name: str) -> None:
        client.delete_schema(RegistryName=registry_name, SchemaName=schema_name)

    def _delete_registries(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._delete_registry(client, record.name)
                self.logger.cleanup(f"Deleted schema registry {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete schema registry {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _delete_registry(self, client, registry_name: str) -> None:
        client.delete_registry(RegistryName=registry_name)

    # ---- archives / buses ----
    def _delete_archives(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._delete_archive(client, record.name)
                self.logger.cleanup(f"Deleted archive {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete archive {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _delete_archive(self, client, archive_name: str) -> None:
        client.delete_archive(ArchiveName=archive_name)

    def _delete_buses(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._delete_bus(client, record.name)
                self.logger.cleanup(f"Deleted event bus {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete event bus {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _delete_bus(self, client, bus_name: str) -> None:
        client.delete_event_bus(Name=bus_name)

    # -------------------------------------------------------------- verify
    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying EventBridge is clean ...")
        remaining: List[str] = []

        try:
            buses = self._list_event_buses()
        except ClientError as exc:
            return VerificationResult(self.service_name, passed=False, error=str(exc))

        for bus in buses:
            bus_name = bus["Name"]
            if bus_name != DEFAULT_EVENT_BUS_NAME:
                remaining.append(f"bus:{bus_name}")
            try:
                rules = self._list_rules(bus_name)
            except ClientError:
                continue  # bus is very likely already gone
            for rule in rules:
                if not rule.get("ManagedBy"):
                    remaining.append(f"rule:{bus_name}/{rule['Name']}")

        try:
            archives = self._list_archives()
            remaining += [f"archive:{a['ArchiveName']}" for a in archives]
        except ClientError as exc:
            self.logger.warning(f"Could not verify EventBridge archives: {exc}")

        try:
            registries = self._list_registries()
        except ClientError as exc:
            self.logger.warning(f"Could not verify Schemas registries: {exc}")
            registries = []

        for registry in registries:
            name = registry["RegistryName"]
            if name.startswith(AWS_MANAGED_REGISTRY_PREFIX):
                continue
            if name != DEFAULT_SCHEMA_REGISTRY_NAME:
                remaining.append(f"registry:{name}")
            try:
                schemas = self._list_schemas(name)
                remaining += [f"schema:{name}/{s['SchemaName']}" for s in schemas]
            except ClientError:
                continue

        passed = len(remaining) == 0
        self.logger.verify(
            "EventBridge clean" if passed else f"EventBridge FAIL - {len(remaining)} resources remain"
        )
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )

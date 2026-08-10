from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from botocore.exceptions import ClientError

from base.base_cleanup import BaseCleanupService
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult, ResourceRecord
from models.verification_result import VerificationResult
from retry import retry_with_backoff


class LambdaCleanupService(BaseCleanupService):
    """Deletes Lambda functions and the two resource types Lambda does
    NOT clean up automatically alongside them, in dependency order:

    1. Event source mappings: delete first. AWS's own delete_function
       documentation is explicit that deleting a function does NOT delete
       its event source mappings -- "To delete Lambda event source
       mappings that invoke a function, use DeleteEventSourceMapping."
       Left behind, a mapping to a deleted function just sits in a
       degraded state rather than disappearing -- one of the most common
       Lambda leftovers, same category as the EKS log group / OIDC
       provider cases.
    2. Functions: delete. A function's own URL config (if any) is
       deleted proactively first via delete_function_url_config -- AWS's
       docs don't explicitly confirm this is (or isn't) auto-removed with
       the function, so rather than assume either way, it's deleted
       up front and any "doesn't exist" response is treated as a
       no-op. delete_function with no Qualifier deletes every version
       and alias of the function as part of the same call -- no separate
       version/alias/reserved-concurrency/provisioned-concurrency cleanup
       is needed, those all go with it.
    3. Layer versions: delete. Layers are independent, standalone
       resources (published separately from any function, and can be
       shared across many functions or none) -- they have no ordering
       dependency on functions and are handled as a distinct pass. Each
       version of a layer is deleted individually; AWS keeps a version
       usable by any function still referencing it even after
       DeleteLayerVersion, so this does not risk breaking a function
       this tool decided to skip.

    Honors protected_resource_arns, exclude_tags, and the checkpoint DB via
    should_skip_resource() for functions, event source mappings, and layer
    versions.
    """

    service_name = "lambda"

    def _client(self):
        return self.clients.client("lambda")

    # ------------------------------------------------------------ listing
    @retry_with_backoff()
    def _list_functions(self) -> List[dict]:
        paginator = self._client().get_paginator("list_functions")
        functions: List[dict] = []
        for page in paginator.paginate():
            functions.extend(page.get("Functions", []))
        return functions

    @retry_with_backoff()
    def _list_event_source_mappings(self) -> List[dict]:
        paginator = self._client().get_paginator("list_event_source_mappings")
        mappings: List[dict] = []
        for page in paginator.paginate():
            mappings.extend(page.get("EventSourceMappings", []))
        return mappings

    @retry_with_backoff()
    def _list_layers(self) -> List[dict]:
        paginator = self._client().get_paginator("list_layers")
        layers: List[dict] = []
        for page in paginator.paginate():
            layers.extend(page.get("Layers", []))
        return layers

    @retry_with_backoff()
    def _list_layer_versions(self, layer_name: str) -> List[dict]:
        paginator = self._client().get_paginator("list_layer_versions")
        versions: List[dict] = []
        for page in paginator.paginate(LayerName=layer_name):
            versions.extend(page.get("LayerVersions", []))
        return versions

    @retry_with_backoff()
    def _tags_for(self, function_arn: str) -> Dict[str, str]:
        try:
            resp = self._client().list_tags(Resource=function_arn)
            return resp.get("Tags", {})
        except ClientError:
            return {}

    # ---------------------------------------------------------------------
    def discover(self) -> InventoryResult:
        self.logger.discover("Scanning Lambda functions, event source mappings, and layer versions ...")
        result = InventoryResult(service_name=self.service_name)

        try:
            functions = self._list_functions()
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"Lambda discovery failed: {exc}")
            return result

        for fn in functions:
            arn = fn["FunctionArn"]
            name = fn["FunctionName"]
            result.resources.append(
                ResourceRecord(
                    resource_id=name,
                    name=name,
                    arn=arn,
                    metadata={"type": "function", "tags": self._tags_for(arn)},
                    fingerprint=str(fn.get("LastModified", "")),
                )
            )

        try:
            mappings = self._list_event_source_mappings()
        except ClientError as exc:
            self.logger.warning(f"Could not list event source mappings: {exc}")
            mappings = []

        for mapping in mappings:
            uuid = mapping["UUID"]
            result.resources.append(
                ResourceRecord(
                    resource_id=uuid,
                    name=uuid,
                    arn=mapping.get("EventSourceArn", ""),
                    metadata={
                        "type": "event_source_mapping",
                        "function_arn": mapping.get("FunctionArn", ""),
                        "tags": {},
                    },
                    fingerprint=str(mapping.get("LastModified", "")),
                )
            )

        layer_version_count = 0
        try:
            layers = self._list_layers()
        except ClientError as exc:
            self.logger.warning(f"Could not list layers: {exc}")
            layers = []

        for layer in layers:
            layer_name = layer["LayerName"]
            try:
                versions = self._list_layer_versions(layer_name)
            except ClientError as exc:
                self.logger.warning(f"Could not list versions for layer {layer_name}: {exc}")
                continue
            for version in versions:
                layer_version_count += 1
                version_number = version["Version"]
                result.resources.append(
                    ResourceRecord(
                        resource_id=f"{layer_name}::{version_number}",
                        name=f"{layer_name}:{version_number}",
                        arn=version.get("LayerVersionArn", ""),
                        metadata={"type": "layer_version", "layer_name": layer_name, "version": version_number, "tags": {}},
                        fingerprint=str(version.get("CreatedDate", "")),
                    )
                )

        self.logger.discover(
            f"Found {len(functions)} functions, {len(mappings)} event source mappings, "
            f"{layer_version_count} layer versions"
        )
        return result

    # --------------------------------------------------------------- clean
    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No Lambda resources to delete")
            return result

        if self.config.dry_run:
            for record in inventory.resources:
                self.logger.cleanup(f"[DRY-RUN] Would delete Lambda {record.metadata.get('type')} {record.name}")
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
            return result

        client = self._client()

        def by_type(t: str) -> List[ResourceRecord]:
            return self._apply_skip_gate([r for r in inventory.resources if r.metadata.get("type") == t], result)

        event_source_mappings = by_type("event_source_mapping")
        functions = by_type("function")
        layer_versions = by_type("layer_version")

        self._delete_event_source_mappings(client, event_source_mappings, result)
        self._delete_functions(client, functions, result)
        self._delete_layer_versions(client, layer_versions, result)

        return result

    def _apply_skip_gate(self, records: List[ResourceRecord], result: CleanupResult) -> List[ResourceRecord]:
        kept = []
        for record in records:
            reason = self.should_skip_resource(record)
            if reason:
                self.logger.cleanup(f"Skipping Lambda resource {record.name}: {reason}")
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

    # ---- event source mappings ----
    def _delete_event_source_mappings(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._call_delete_event_source_mapping(client, record.resource_id)
                self.logger.cleanup(f"Deleted event source mapping {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete event source mapping {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_event_source_mapping(self, client, uuid: str) -> None:
        client.delete_event_source_mapping(UUID=uuid)

    # ---- functions ----
    def _delete_functions(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            function_name = record.resource_id
            try:
                self._delete_function_url_config(client, function_name)
                self._call_delete_function(client, function_name)
                self.logger.cleanup(f"Deleted function {function_name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete function {function_name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _delete_function_url_config(self, client, function_name: str) -> None:
        try:
            client.delete_function_url_config(FunctionName=function_name)
            self.logger.cleanup(f"Removed function URL config on {function_name}")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "ResourceNotFoundException":
                # No function URL configured -- nothing to remove.
                return
            raise

    @retry_with_backoff()
    def _call_delete_function(self, client, function_name: str) -> None:
        client.delete_function(FunctionName=function_name)

    # ---- layer versions ----
    def _delete_layer_versions(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            layer_name = record.metadata["layer_name"]
            version_number = record.metadata["version"]
            try:
                self._call_delete_layer_version(client, layer_name, version_number)
                self.logger.cleanup(f"Deleted layer version {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete layer version {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_layer_version(self, client, layer_name: str, version_number: int) -> None:
        client.delete_layer_version(LayerName=layer_name, VersionNumber=version_number)

    # -------------------------------------------------------------- verify
    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying Lambda is clean ...")
        remaining: List[str] = []

        try:
            remaining += [f"function:{fn['FunctionName']}" for fn in self._list_functions()]
        except ClientError as exc:
            return VerificationResult(self.service_name, passed=False, error=str(exc))

        try:
            remaining += [f"event_source_mapping:{m['UUID']}" for m in self._list_event_source_mappings()]
        except ClientError:
            pass

        try:
            for layer in self._list_layers():
                layer_name = layer["LayerName"]
                try:
                    remaining += [
                        f"layer_version:{layer_name}:{v['Version']}" for v in self._list_layer_versions(layer_name)
                    ]
                except ClientError:
                    pass
        except ClientError:
            pass

        passed = len(remaining) == 0
        self.logger.verify("Lambda clean" if passed else f"Lambda FAIL - {len(remaining)} resources remain")
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )
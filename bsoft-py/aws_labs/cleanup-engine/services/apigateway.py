from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from botocore.exceptions import ClientError

from base.base_cleanup import BaseCleanupService
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult, ResourceRecord
from models.verification_result import VerificationResult
from retry import retry_with_backoff


class ApiGatewayCleanupService(BaseCleanupService):
    """Deletes both API Gateway generations -- REST APIs (the original
    "v1" apigateway client) and HTTP/WebSocket APIs (the newer
    apigatewayv2 client) -- along with their supporting resources, in
    dependency order:

    1. Base path mappings (v1) / API mappings (v2): delete first. This
       order is not a style choice -- AWS rejects deleting a REST or HTTP
       API outright while any custom-domain mapping still points at it.
       A real AWS error message for this is "Please remove all base path
       mappings related to the RestApi in your domains" -- confirmed from
       an actual support thread, not assumed. Mappings are deleted before
       the APIs they point at, not just before the domain names that own
       them.
    2. REST APIs (v1) and HTTP/WebSocket APIs (v2): delete. Deleting
       either kind of API cascades to delete everything defined inside
       it -- resources, methods, models, authorizers, deployments, and
       stages for a REST API; routes, integrations, deployments, and
       stages for an HTTP/WebSocket API. None of those are tracked as
       separate resources here because they cannot outlive their parent
       API.
    3. Domain names (v1 and v2, separate resource pools even though both
       are literally called "domain names"): delete, now that step 1
       cleared the mappings blocking it.
    4. Usage plans (v1 only -- HTTP/WebSocket APIs don't support usage
       plans or API keys at all): delete. Deleting a usage plan removes
       its association with any API keys along with it; the keys
       themselves are untouched and handled separately in step 5.
    5. API keys (v1 only): delete. Independent of any single API or usage
       plan -- a key can be shared across many usage plans, or have none.
    6. VPC Links (v1 and v2 -- these are NOT the same resource type
       despite the shared name: v1 VPC Links back REST API private
       integrations directly; v2 VPC Links are a distinct resource used
       by HTTP API private integrations): delete, independently.
    7. Client certificates (v1 only; used for backend mutual TLS on REST
       API stages): delete, independently.

    Honors protected_resource_arns, exclude_tags, and the checkpoint DB via
    should_skip_resource() for every resource type below.
    """

    service_name = "apigateway"

    def _v1_client(self):
        return self.clients.client("apigateway")

    def _v2_client(self):
        return self.clients.client("apigatewayv2")

    # ------------------------------------------------------------ listing
    @retry_with_backoff()
    def _list_rest_apis(self) -> List[dict]:
        paginator = self._v1_client().get_paginator("get_rest_apis")
        apis: List[dict] = []
        for page in paginator.paginate():
            apis.extend(page.get("items", []))
        return apis

    @retry_with_backoff()
    def _list_http_apis(self) -> List[dict]:
        paginator = self._v2_client().get_paginator("get_apis")
        apis: List[dict] = []
        for page in paginator.paginate():
            apis.extend(page.get("Items", []))
        return apis

    @retry_with_backoff()
    def _list_domain_names_v1(self) -> List[dict]:
        paginator = self._v1_client().get_paginator("get_domain_names")
        names: List[dict] = []
        for page in paginator.paginate():
            names.extend(page.get("items", []))
        return names

    @retry_with_backoff()
    def _list_base_path_mappings(self, domain_name: str) -> List[dict]:
        paginator = self._v1_client().get_paginator("get_base_path_mappings")
        mappings: List[dict] = []
        for page in paginator.paginate(domainName=domain_name):
            mappings.extend(page.get("items", []))
        return mappings

    @retry_with_backoff()
    def _list_domain_names_v2(self) -> List[dict]:
        paginator = self._v2_client().get_paginator("get_domain_names")
        names: List[dict] = []
        for page in paginator.paginate():
            names.extend(page.get("Items", []))
        return names

    @retry_with_backoff()
    def _list_api_mappings(self, domain_name: str) -> List[dict]:
        paginator = self._v2_client().get_paginator("get_api_mappings")
        mappings: List[dict] = []
        for page in paginator.paginate(DomainName=domain_name):
            mappings.extend(page.get("Items", []))
        return mappings

    @retry_with_backoff()
    def _list_usage_plans(self) -> List[dict]:
        paginator = self._v1_client().get_paginator("get_usage_plans")
        plans: List[dict] = []
        for page in paginator.paginate():
            plans.extend(page.get("items", []))
        return plans

    @retry_with_backoff()
    def _list_api_keys(self) -> List[dict]:
        paginator = self._v1_client().get_paginator("get_api_keys")
        keys: List[dict] = []
        for page in paginator.paginate():
            keys.extend(page.get("items", []))
        return keys

    @retry_with_backoff()
    def _list_vpc_links_v1(self) -> List[dict]:
        client = self._v1_client()
        links: List[dict] = []

        response = client.get_vpc_links(limit=500)
        links.extend(response.get("items", []))

        while response.get("position"):
            response = client.get_vpc_links(
                limit=500,
                position=response["position"],
            )
            links.extend(response.get("items", []))

        return links

    @retry_with_backoff()
    def _list_vpc_links_v2(self) -> List[dict]:
        client = self._v2_client()
        links: List[dict] = []

        response = client.get_vpc_links(MaxResults="500")
        links.extend(response.get("Items", []))

        while response.get("NextToken"):
            response = client.get_vpc_links(
                MaxResults="500",
                NextToken=response["NextToken"],
            )
            links.extend(response.get("Items", []))

        return links

    @retry_with_backoff()
    def _list_client_certificates(self) -> List[dict]:
        paginator = self._v1_client().get_paginator("get_client_certificates")
        certs: List[dict] = []
        for page in paginator.paginate():
            certs.extend(page.get("items", []))
        return certs

    # ---------------------------------------------------------------------
    def discover(self) -> InventoryResult:
        self.logger.discover("Scanning API Gateway (REST + HTTP/WebSocket) resources ...")
        result = InventoryResult(service_name=self.service_name)
        counts: Dict[str, int] = {}

        def add(resource_type: str, resource_id: str, name: str, arn: str, extra_meta: dict, fingerprint: str = "") -> None:
            counts[resource_type] = counts.get(resource_type, 0) + 1
            meta = {"type": resource_type, "tags": extra_meta.pop("tags", {})}
            meta.update(extra_meta)
            result.resources.append(
                ResourceRecord(resource_id=resource_id, name=name, arn=arn, metadata=meta, fingerprint=fingerprint)
            )

        try:
            rest_apis = self._list_rest_apis()
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"API Gateway (REST) discovery failed: {exc}")
            rest_apis = []
        for api in rest_apis:
            api_id = api["id"]
            add("rest_api", api_id, api.get("name", api_id), "", {"tags": api.get("tags", {})}, str(api.get("createdDate", "")))

        try:
            http_apis = self._list_http_apis()
        except ClientError as exc:
            self.logger.warning(f"API Gateway (HTTP/WebSocket) discovery failed: {exc}")
            http_apis = []
        for api in http_apis:
            api_id = api["ApiId"]
            add(
                "http_api",
                api_id,
                api.get("Name", api_id),
                api.get("ApiEndpoint", ""),
                {"tags": api.get("Tags", {})},
                str(api.get("CreatedDate", "")),
            )

        try:
            domains_v1 = self._list_domain_names_v1()
        except ClientError as exc:
            self.logger.warning(f"Could not list v1 domain names: {exc}")
            domains_v1 = []
        for domain in domains_v1:
            domain_name = domain["domainName"]
            try:
                mappings = self._list_base_path_mappings(domain_name)
            except ClientError as exc:
                self.logger.warning(f"Could not list base path mappings for {domain_name}: {exc}")
                mappings = []
            for mapping in mappings:
                base_path = mapping.get("basePath", "(none)")
                add(
                    "base_path_mapping",
                    f"{domain_name}::{base_path}",
                    f"{domain_name}{'' if base_path == '(none)' else '/' + base_path}",
                    "",
                    {"tags": {}, "domain_name": domain_name, "base_path": base_path},
                )
            add("domain_name_v1", domain_name, domain_name, "", {"tags": domain.get("tags", {})})

        try:
            domains_v2 = self._list_domain_names_v2()
        except ClientError as exc:
            self.logger.warning(f"Could not list v2 domain names: {exc}")
            domains_v2 = []
        for domain in domains_v2:
            domain_name = domain["DomainName"]
            try:
                mappings = self._list_api_mappings(domain_name)
            except ClientError as exc:
                self.logger.warning(f"Could not list API mappings for {domain_name}: {exc}")
                mappings = []
            for mapping in mappings:
                mapping_id = mapping["ApiMappingId"]
                add(
                    "api_mapping",
                    f"{domain_name}::{mapping_id}",
                    f"{domain_name} -> {mapping.get('ApiId', '')}",
                    "",
                    {"tags": {}, "domain_name": domain_name, "api_mapping_id": mapping_id},
                )
            add("domain_name_v2", domain_name, domain_name, "", {"tags": domain.get("Tags", {})})

        try:
            usage_plans = self._list_usage_plans()
        except ClientError as exc:
            self.logger.warning(f"Could not list usage plans: {exc}")
            usage_plans = []
        for plan in usage_plans:
            plan_id = plan["id"]
            add("usage_plan", plan_id, plan.get("name", plan_id), "", {"tags": plan.get("tags", {})})

        try:
            api_keys = self._list_api_keys()
        except ClientError as exc:
            self.logger.warning(f"Could not list API keys: {exc}")
            api_keys = []
        for key in api_keys:
            key_id = key["id"]
            add("api_key", key_id, key.get("name", key_id), "", {"tags": key.get("tags", {})})

        try:
            vpc_links_v1 = self._list_vpc_links_v1()
        except ClientError as exc:
            self.logger.warning(f"Could not list v1 VPC links: {exc}")
            vpc_links_v1 = []
        for link in vpc_links_v1:
            link_id = link["id"]
            add("vpc_link_v1", link_id, link.get("name", link_id), "", {"tags": link.get("tags", {})})

        try:
            vpc_links_v2 = self._list_vpc_links_v2()
        except ClientError as exc:
            self.logger.warning(f"Could not list v2 VPC links: {exc}")
            vpc_links_v2 = []
        for link in vpc_links_v2:
            link_id = link["VpcLinkId"]
            add("vpc_link_v2", link_id, link.get("Name", link_id), "", {"tags": link.get("Tags", {})})

        try:
            certs = self._list_client_certificates()
        except ClientError as exc:
            self.logger.warning(f"Could not list client certificates: {exc}")
            certs = []
        for cert in certs:
            cert_id = cert["clientCertificateId"]
            add("client_certificate", cert_id, cert_id, "", {"tags": cert.get("tags", {})})

        self.logger.discover(f"Found API Gateway resources: {counts}")
        return result

    # --------------------------------------------------------------- clean
    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No API Gateway resources to delete")
            return result

        if self.config.dry_run:
            for record in inventory.resources:
                self.logger.cleanup(f"[DRY-RUN] Would delete API Gateway {record.metadata.get('type')} {record.name}")
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
            return result

        v1 = self._v1_client()
        v2 = self._v2_client()

        def by_type(t: str) -> List[ResourceRecord]:
            return self._apply_skip_gate([r for r in inventory.resources if r.metadata.get("type") == t], result)

        self._delete_base_path_mappings(v1, by_type("base_path_mapping"), result)
        self._delete_api_mappings(v2, by_type("api_mapping"), result)

        self._delete_rest_apis(v1, by_type("rest_api"), result)
        self._delete_http_apis(v2, by_type("http_api"), result)

        self._delete_domain_names_v1(v1, by_type("domain_name_v1"), result)
        self._delete_domain_names_v2(v2, by_type("domain_name_v2"), result)

        self._delete_usage_plans(v1, by_type("usage_plan"), result)
        self._delete_api_keys(v1, by_type("api_key"), result)
        self._delete_vpc_links_v1(v1, by_type("vpc_link_v1"), result)
        self._delete_vpc_links_v2(v2, by_type("vpc_link_v2"), result)
        self._delete_client_certificates(v1, by_type("client_certificate"), result)

        return result

    def _apply_skip_gate(self, records: List[ResourceRecord], result: CleanupResult) -> List[ResourceRecord]:
        kept = []
        for record in records:
            reason = self.should_skip_resource(record)
            if reason:
                self.logger.cleanup(f"Skipping API Gateway resource {record.name}: {reason}")
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

    def _generic_delete(self, records: List[ResourceRecord], result: CleanupResult, delete_call, label: str) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                delete_call(record)
                self.logger.cleanup(f"Deleted {label} {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete {label} {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    # ---- base path mappings (v1) / API mappings (v2) ----
    def _delete_base_path_mappings(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        @retry_with_backoff()
        def _call(record: ResourceRecord) -> None:
            client.delete_base_path_mapping(
                domainName=record.metadata["domain_name"], basePath=record.metadata["base_path"]
            )

        self._generic_delete(records, result, _call, "base path mapping")

    def _delete_api_mappings(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        @retry_with_backoff()
        def _call(record: ResourceRecord) -> None:
            client.delete_api_mapping(
                DomainName=record.metadata["domain_name"], ApiMappingId=record.metadata["api_mapping_id"]
            )

        self._generic_delete(records, result, _call, "API mapping")

    # ---- APIs ----
    def _delete_rest_apis(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        @retry_with_backoff()
        def _call(record: ResourceRecord) -> None:
            client.delete_rest_api(restApiId=record.resource_id)

        self._generic_delete(records, result, _call, "REST API")

    def _delete_http_apis(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        @retry_with_backoff()
        def _call(record: ResourceRecord) -> None:
            client.delete_api(ApiId=record.resource_id)

        self._generic_delete(records, result, _call, "HTTP/WebSocket API")

    # ---- domain names ----
    def _delete_domain_names_v1(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        @retry_with_backoff()
        def _call(record: ResourceRecord) -> None:
            client.delete_domain_name(domainName=record.resource_id)

        self._generic_delete(records, result, _call, "domain name (v1)")

    def _delete_domain_names_v2(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        @retry_with_backoff()
        def _call(record: ResourceRecord) -> None:
            client.delete_domain_name(DomainName=record.resource_id)

        self._generic_delete(records, result, _call, "domain name (v2)")

    # ---- usage plans / API keys ----
    def _delete_usage_plans(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        @retry_with_backoff()
        def _call(record: ResourceRecord) -> None:
            client.delete_usage_plan(usagePlanId=record.resource_id)

        self._generic_delete(records, result, _call, "usage plan")

    def _delete_api_keys(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        @retry_with_backoff()
        def _call(record: ResourceRecord) -> None:
            client.delete_api_key(apiKey=record.resource_id)

        self._generic_delete(records, result, _call, "API key")

    # ---- VPC links ----
    def _delete_vpc_links_v1(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        @retry_with_backoff()
        def _call(record: ResourceRecord) -> None:
            client.delete_vpc_link(vpcLinkId=record.resource_id)

        self._generic_delete(records, result, _call, "VPC link (v1)")

    def _delete_vpc_links_v2(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        @retry_with_backoff()
        def _call(record: ResourceRecord) -> None:
            client.delete_vpc_link(VpcLinkId=record.resource_id)

        self._generic_delete(records, result, _call, "VPC link (v2)")

    # ---- client certificates ----
    def _delete_client_certificates(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        @retry_with_backoff()
        def _call(record: ResourceRecord) -> None:
            client.delete_client_certificate(clientCertificateId=record.resource_id)

        self._generic_delete(records, result, _call, "client certificate")

    # -------------------------------------------------------------- verify
    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying API Gateway is clean ...")
        remaining: List[str] = []

        try:
            remaining += [f"rest_api:{a['id']}" for a in self._list_rest_apis()]
        except ClientError as exc:
            return VerificationResult(self.service_name, passed=False, error=str(exc))

        try:
            remaining += [f"http_api:{a['ApiId']}" for a in self._list_http_apis()]
        except ClientError:
            pass
        try:
            remaining += [f"domain_name_v1:{d['domainName']}" for d in self._list_domain_names_v1()]
        except ClientError:
            pass
        try:
            remaining += [f"domain_name_v2:{d['DomainName']}" for d in self._list_domain_names_v2()]
        except ClientError:
            pass
        try:
            remaining += [f"usage_plan:{p['id']}" for p in self._list_usage_plans()]
        except ClientError:
            pass
        try:
            remaining += [f"api_key:{k['id']}" for k in self._list_api_keys()]
        except ClientError:
            pass
        try:
            remaining += [f"vpc_link_v1:{v['id']}" for v in self._list_vpc_links_v1()]
        except ClientError:
            pass
        try:
            remaining += [f"vpc_link_v2:{v['VpcLinkId']}" for v in self._list_vpc_links_v2()]
        except ClientError:
            pass
        try:
            remaining += [f"client_certificate:{c['clientCertificateId']}" for c in self._list_client_certificates()]
        except ClientError:
            pass

        passed = len(remaining) == 0
        self.logger.verify("API Gateway clean" if passed else f"API Gateway FAIL - {len(remaining)} resources remain")
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )
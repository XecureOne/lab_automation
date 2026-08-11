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

# Environment statuses that mean "still exists and hasn't finished
# terminating" -- Terminated environments are excluded from discovery the
# same way EKS excludes clusters mid-DELETING.
ACTIVE_ENV_STATUSES = {"Launching", "Updating", "Ready", "Terminating"}


class BeanstalkCleanupService(BaseCleanupService):
    """Deletes Elastic Beanstalk applications and everything under them, in
    dependency order:

    1. Environments: terminate (TerminateResources=True, so the underlying
       ASG/EC2/ELB/RDS stack the environment created is torn down too),
       wait for each to disappear/reach Terminated -- an application can't
       be deleted, and most of its versions can't be deleted, while any
       environment referencing them is still running.
    2. Application versions: delete with DeleteSourceBundle=True. Elastic
       Beanstalk does NOT delete the version's zip/war from its S3 source
       bucket by default -- left alone, these accumulate as one of the
       most common Beanstalk leftovers (mirrors the EKS module's
       CloudWatch-log-group reasoning: AWS won't clean it up for you).
    3. Configuration (saved) templates: delete. Not a hard blocker for
       application deletion, but left running they'd otherwise just get
       silently swept away with the application -- deleting them
       explicitly keeps the inventory/verification story honest about
       what this tool removed, same reasoning as EKS add-ons/access
       entries.
    4. Application: delete (TerminateEnvByForce=False -- environments were
       already explicitly terminated and waited on in step 1, so forcing
       here would only mask a bug if one somehow survived that step).

    Elastic Beanstalk is a regional service (unlike IAM) -- discover() /
    cleanup() / verify() only see applications and environments in the
    configured region.

    Honors protected_resource_arns, exclude_tags, and the checkpoint DB via
    should_skip_resource() for every resource type below.

    NOTE: an environment stuck mid in-progress update (Status="Updating"
    with AbortableOperationInProgress=True) may need
    abort_environment_update() before terminate_environment() succeeds
    cleanly -- that case isn't handled here and hasn't been tested against
    a live stuck environment; treat it as a known gap if termination hangs
    or fails on an "Updating" environment.
    """

    service_name = "elasticbeanstalk"

    def _client(self):
        return self.clients.client("elasticbeanstalk")

    # ------------------------------------------------------------ listing
    @retry_with_backoff()
    def _list_applications(self) -> List[dict]:
        resp = self._client().describe_applications()
        return resp.get("Applications", [])

    @retry_with_backoff()
    def _list_environments(self, application_name: str) -> List[dict]:
        resp = self._client().describe_environments(ApplicationName=application_name, IncludeDeleted=False)
        return resp.get("Environments", [])

    @retry_with_backoff()
    def _describe_environment(self, environment_name: str) -> Optional[dict]:
        resp = self._client().describe_environments(EnvironmentNames=[environment_name], IncludeDeleted=False)
        envs = resp.get("Environments", [])
        return envs[0] if envs else None

    @retry_with_backoff()
    def _list_application_versions(self, application_name: str) -> List[dict]:
        resp = self._client().describe_application_versions(ApplicationName=application_name)
        return resp.get("ApplicationVersions", [])

    @retry_with_backoff()
    def _tags_for(self, resource_arn: str) -> Dict[str, str]:
        if not resource_arn:
            return {}
        try:
            resp = self._client().list_tags_for_resource(ResourceArn=resource_arn)
            return {t["Key"]: t["Value"] for t in resp.get("ResourceTags", [])}
        except ClientError:
            return {}

    # ---------------------------------------------------------------------
    def discover(self) -> InventoryResult:
        self.logger.discover(
            "Scanning Elastic Beanstalk applications, environments, versions, and configuration templates ..."
        )
        result = InventoryResult(service_name=self.service_name)

        try:
            applications = self._list_applications()
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"Elastic Beanstalk discovery failed: {exc}")
            return result

        env_count = 0
        version_count = 0
        template_count = 0

        for app in applications:
            app_name = app["ApplicationName"]
            app_arn = app.get("ApplicationArn", "")
            result.resources.append(
                ResourceRecord(
                    resource_id=app_name,
                    name=app_name,
                    arn=app_arn,
                    metadata={"type": "application", "tags": self._tags_for(app_arn)},
                    fingerprint=str(app.get("DateCreated", "")),
                )
            )

            try:
                for env in self._list_environments(app_name):
                    if env.get("Status") not in ACTIVE_ENV_STATUSES:
                        continue
                    env_count += 1
                    env_name = env["EnvironmentName"]
                    env_arn = env.get("EnvironmentArn", "")
                    result.resources.append(
                        ResourceRecord(
                            resource_id=env_name,
                            name=env_name,
                            arn=env_arn,
                            metadata={
                                "type": "environment",
                                "application_name": app_name,
                                "tags": self._tags_for(env_arn),
                            },
                            fingerprint=str(env.get("DateCreated", "")),
                        )
                    )
            except ClientError as exc:
                self.logger.warning(f"Could not list environments for application {app_name}: {exc}")

            try:
                for version in self._list_application_versions(app_name):
                    version_count += 1
                    version_label = version["VersionLabel"]
                    version_arn = version.get("ApplicationVersionArn", "")
                    result.resources.append(
                        ResourceRecord(
                            resource_id=f"{app_name}::{version_label}",
                            name=version_label,
                            arn=version_arn,
                            metadata={
                                "type": "application_version",
                                "application_name": app_name,
                                "tags": self._tags_for(version_arn),
                            },
                            fingerprint=str(version.get("DateCreated", "")),
                        )
                    )
            except ClientError as exc:
                self.logger.warning(f"Could not list application versions for {app_name}: {exc}")

            for template_name in app.get("ConfigurationTemplates", []) or []:
                template_count += 1
                result.resources.append(
                    ResourceRecord(
                        resource_id=f"{app_name}::{template_name}",
                        name=template_name,
                        arn="",
                        metadata={"type": "configuration_template", "application_name": app_name, "tags": {}},
                        fingerprint="",
                    )
                )

        self.logger.discover(
            f"Found {len(applications)} applications, {env_count} environments, "
            f"{version_count} application versions, {template_count} configuration templates"
        )
        return result

    # --------------------------------------------------------------- clean
    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No Elastic Beanstalk resources to delete")
            return result

        if self.config.dry_run:
            for record in inventory.resources:
                self.logger.cleanup(
                    f"[DRY-RUN] Would delete Elastic Beanstalk {record.metadata.get('type')} {record.name}"
                )
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
            return result

        client = self._client()

        def by_type(t: str) -> List[ResourceRecord]:
            return self._apply_skip_gate([r for r in inventory.resources if r.metadata.get("type") == t], result)

        environments = by_type("environment")
        versions = by_type("application_version")
        templates = by_type("configuration_template")
        applications = by_type("application")

        self._delete_environments(client, environments, result)
        self._delete_application_versions(client, versions, result)
        self._delete_configuration_templates(client, templates, result)
        self._delete_applications(client, applications, result)

        return result

    def _apply_skip_gate(self, records: List[ResourceRecord], result: CleanupResult) -> List[ResourceRecord]:
        kept = []
        for record in records:
            reason = self.should_skip_resource(record)
            if reason:
                self.logger.cleanup(f"Skipping Elastic Beanstalk resource {record.name}: {reason}")
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

    # ---- environments ----
    def _delete_environments(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            env_name = record.resource_id
            try:
                self._call_terminate_environment(client, env_name)
                self.logger.wait(f"Waiting for environment termination: {env_name}")
                Waiter(self.logger).wait_for(
                    lambda: self._is_environment_gone(env_name),
                    f"Elastic Beanstalk environment '{env_name}' termination",
                    timeout=self.config.waiter_default_timeout_seconds,
                    interval=self.config.waiter_default_interval_seconds,
                )
                self.logger.cleanup(f"Terminated environment {env_name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to terminate environment {env_name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_terminate_environment(self, client, env_name: str) -> None:
        client.terminate_environment(EnvironmentName=env_name, TerminateResources=True)

    def _is_environment_gone(self, env_name: str) -> bool:
        env = self._describe_environment(env_name)
        return env is None or env.get("Status") == "Terminated"

    # ---- application versions ----
    def _delete_application_versions(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            app_name = record.metadata["application_name"]
            version_label = record.name
            try:
                self._call_delete_application_version(client, app_name, version_label)
                self.logger.cleanup(f"Deleted application version {version_label} (app {app_name})")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete application version {version_label}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_application_version(self, client, app_name: str, version_label: str) -> None:
        client.delete_application_version(
            ApplicationName=app_name, VersionLabel=version_label, DeleteSourceBundle=True
        )

    # ---- configuration templates ----
    def _delete_configuration_templates(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            app_name = record.metadata["application_name"]
            template_name = record.name
            try:
                self._call_delete_configuration_template(client, app_name, template_name)
                self.logger.cleanup(f"Deleted configuration template {template_name} (app {app_name})")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete configuration template {template_name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_configuration_template(self, client, app_name: str, template_name: str) -> None:
        client.delete_configuration_template(ApplicationName=app_name, TemplateName=template_name)

    # ---- applications ----
    def _delete_applications(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            app_name = record.resource_id
            try:
                self._call_delete_application(client, app_name)
                self.logger.cleanup(f"Deleted application {app_name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete application {app_name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_application(self, client, app_name: str) -> None:
        # TerminateEnvByForce=False: environments were already explicitly
        # terminated and waited on above -- if one somehow survives that,
        # it should surface here as a failure, not be silently
        # force-terminated as a side effect of deleting the application.
        client.delete_application(ApplicationName=app_name, TerminateEnvByForce=False)

    # -------------------------------------------------------------- verify
    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying Elastic Beanstalk is clean ...")
        remaining: List[str] = []

        try:
            applications = self._list_applications()
        except ClientError as exc:
            return VerificationResult(self.service_name, passed=False, error=str(exc))

        for app in applications:
            app_name = app["ApplicationName"]
            remaining.append(f"application:{app_name}")

            try:
                for env in self._list_environments(app_name):
                    if env.get("Status") in ACTIVE_ENV_STATUSES:
                        remaining.append(f"environment:{env['EnvironmentName']}")
            except ClientError:
                pass

            try:
                remaining += [
                    f"application_version:{app_name}/{v['VersionLabel']}"
                    for v in self._list_application_versions(app_name)
                ]
            except ClientError:
                pass

            remaining += [
                f"configuration_template:{app_name}/{t}" for t in app.get("ConfigurationTemplates", []) or []
            ]

        passed = len(remaining) == 0
        self.logger.verify(
            "Elastic Beanstalk clean" if passed else f"Elastic Beanstalk FAIL - {len(remaining)} resources remain"
        )
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )
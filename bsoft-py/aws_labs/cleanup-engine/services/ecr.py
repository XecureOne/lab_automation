from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from botocore.exceptions import ClientError

from base.base_cleanup import BaseCleanupService
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult, ResourceRecord
from models.verification_result import VerificationResult
from retry import retry_with_backoff


class ECRCleanupService(BaseCleanupService):
    """Deletes ECR (private) repositories, in dependency order:

    1. Repository policy: delete, if one exists. Not required by
       delete_repository itself, but a resource-based policy with an
       explicit deny (cross-account lockout, deny-all, etc.) could block
       it the same way an S3 bucket policy can block bucket deletion --
       removed proactively for the same reason as the S3 module's
       bucket-policy fix, rather than waiting to find out the hard way.
    2. Repository: delete with force=True. Unlike S3, ECR does the
       "empty the contents first" step for you -- force=True has ECR
       delete every image in the repository as part of the same call, so
       there's no separate batch_delete_image step here.

    A repository's lifecycle policy is not tracked as a separate
    resource: it lives on the repository and disappears with it, and
    there's nothing left to verify independently once the repository is
    gone.

    ECR is a regional service (like Beanstalk, unlike IAM) -- discover()/
    cleanup()/verify() only see repositories in the configured region and
    the default registry (this account's registry, not a cross-account
    one).

    NOTE: unlike EKS cluster deletion or S3 bucket deletion, ECR's
    delete_repository has not been observed here to be eventually
    consistent -- no waiter is used below. If verify() or a later run
    ever shows a repository still listed immediately after a reported
    success, that assumption needs revisiting.

    Honors protected_resource_arns, exclude_tags, and the checkpoint DB via
    should_skip_resource().
    """

    service_name = "ecr"

    def _client(self):
        return self.clients.client("ecr")

    # ------------------------------------------------------------ listing
    @retry_with_backoff()
    def _list_repositories(self) -> List[dict]:
        paginator = self._client().get_paginator("describe_repositories")
        repos: List[dict] = []
        for page in paginator.paginate():
            repos.extend(page.get("repositories", []))
        return repos

    @retry_with_backoff()
    def _tags_for(self, resource_arn: str) -> Dict[str, str]:
        if not resource_arn:
            return {}
        try:
            resp = self._client().list_tags_for_resource(resourceArn=resource_arn)
            return {t["Key"]: t["Value"] for t in resp.get("tags", [])}
        except ClientError:
            return {}

    # ---------------------------------------------------------------------
    def discover(self) -> InventoryResult:
        self.logger.discover("Scanning ECR repositories ...")
        result = InventoryResult(service_name=self.service_name)

        try:
            repositories = self._list_repositories()
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"ECR discovery failed: {exc}")
            return result

        for repo in repositories:
            repo_name = repo["repositoryName"]
            repo_arn = repo.get("repositoryArn", "")
            result.resources.append(
                ResourceRecord(
                    resource_id=repo_name,
                    name=repo_name,
                    arn=repo_arn,
                    metadata={"type": "repository", "tags": self._tags_for(repo_arn)},
                    fingerprint=str(repo.get("createdAt", "")),
                )
            )

        self.logger.discover(f"Found {len(repositories)} repositories")
        return result

    # --------------------------------------------------------------- clean
    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No ECR resources to delete")
            return result

        if self.config.dry_run:
            for record in inventory.resources:
                self.logger.cleanup(f"[DRY-RUN] Would delete ECR repository {record.name}")
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
            return result

        client = self._client()
        repositories = self._apply_skip_gate(inventory.resources, result)
        self._delete_repositories(client, repositories, result)

        return result

    def _apply_skip_gate(self, records: List[ResourceRecord], result: CleanupResult) -> List[ResourceRecord]:
        kept = []
        for record in records:
            reason = self.should_skip_resource(record)
            if reason:
                self.logger.cleanup(f"Skipping ECR repository {record.name}: {reason}")
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

    # ---- repositories ----
    def _delete_repositories(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            repo_name = record.resource_id
            try:
                self._delete_repository_policy(client, repo_name)
                self._call_delete_repository(client, repo_name)
                self.logger.cleanup(f"Deleted repository {repo_name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete repository {repo_name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _delete_repository_policy(self, client, repo_name: str) -> None:
        try:
            client.delete_repository_policy(repositoryName=repo_name)
            self.logger.cleanup(f"Removed repository policy on {repo_name}")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "RepositoryPolicyNotFoundException":
                # No policy attached -- nothing to remove, not an error.
                return
            # Anything else (AccessDenied, etc.) is a real permissions
            # problem with this tool's own credentials, not something to
            # paper over -- let it surface and fail the repository.
            raise

    @retry_with_backoff()
    def _call_delete_repository(self, client, repo_name: str) -> None:
        client.delete_repository(repositoryName=repo_name, force=True)

    # -------------------------------------------------------------- verify
    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying ECR is clean ...")
        try:
            repositories = self._list_repositories()
        except ClientError as exc:
            return VerificationResult(self.service_name, passed=False, error=str(exc))

        remaining = [f"repository:{repo['repositoryName']}" for repo in repositories]

        passed = len(remaining) == 0
        self.logger.verify("ECR clean" if passed else f"ECR FAIL - {len(remaining)} resources remain")
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )
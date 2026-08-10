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
from waiters import wait_bucket_deleted


class S3CleanupService(BaseCleanupService):
    """Empties and deletes every S3 bucket in scope. Handles versioned
    buckets by deleting every object version and delete marker; this also
    correctly empties non-versioned buckets since list_object_versions
    returns their current objects as a single 'null' version.

    Deletes any bucket policy FIRST, before touching objects or the
    bucket itself. A restrictive policy (explicit deny on
    s3:DeleteObject/s3:DeleteBucket, a deny-all, a cross-account deny,
    etc.) can block object deletion just as easily as it blocks
    delete_bucket -- removing the policy only right before delete_bucket
    would still leave _delete_all_object_versions failing partway
    through on a deny-heavy policy. DeleteBucketPolicy is one of the few
    S3 actions the bucket-owner root principal can always call even
    against a policy that explicitly denies it -- as long as this tool
    runs as (or assumes a role belonging to) the bucket-owning account's
    root/an identity in that account, removing the policy up front is
    safe and unconditional. It does NOT bypass Object Lock in Compliance
    mode -- that has no override, by design on AWS's side, and isn't
    handled here.

    Honors protected_resource_arns, exclude_tags, and the checkpoint DB via
    should_skip_resource()."""

    service_name = "s3"

    def _client(self):
        return self.clients.client("s3")

    @retry_with_backoff()
    def _list_buckets(self) -> List[dict]:
        return self._client().list_buckets().get("Buckets", [])

    @retry_with_backoff()
    def _bucket_region(self, bucket_name: str) -> str:
        location = self._client().get_bucket_location(Bucket=bucket_name)
        constraint = location.get("LocationConstraint")
        return constraint or "us-east-1"

    @retry_with_backoff()
    def _versioning_status(self, bucket_name: str) -> str:
        resp = self._client().get_bucket_versioning(Bucket=bucket_name)
        return resp.get("Status", "Disabled")

    @retry_with_backoff()
    def _bucket_tags(self, bucket_name: str) -> Dict[str, str]:
        try:
            resp = self._client().get_bucket_tagging(Bucket=bucket_name)
            return {t["Key"]: t["Value"] for t in resp.get("TagSet", [])}
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "NoSuchTagSet":
                return {}
            raise

    def discover(self) -> InventoryResult:
        self.logger.discover("Scanning S3 buckets ...")
        result = InventoryResult(service_name=self.service_name)
        try:
            buckets = self._list_buckets()
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"S3 discovery failed: {exc}")
            return result

        for bucket in buckets:
            name = bucket["Name"]
            try:
                region = self._bucket_region(name)
            except ClientError as exc:
                self.logger.warning(f"Could not determine region for bucket {name}: {exc}")
                continue

            if self.config.s3_scope_current_region_only and region != self.config.region:
                continue

            try:
                versioning = self._versioning_status(name)
            except ClientError:
                versioning = "Unknown"

            try:
                tags = self._bucket_tags(name)
            except ClientError as exc:
                self.logger.warning(f"Could not read tags for bucket {name}: {exc}")
                tags = {}

            result.resources.append(
                ResourceRecord(
                    resource_id=name,
                    name=name,
                    arn=f"arn:aws:s3:::{name}",
                    metadata={"region": region, "versioning": versioning, "tags": tags},
                    # CreationDate distinguishes this bucket from a
                    # different one that later reuses the same name (S3
                    # allows a deleted bucket name to become available
                    # again after a retention window).
                    fingerprint=str(bucket.get("CreationDate", "")),
                )
            )
        self.logger.discover(f"Found {result.resource_count} buckets")
        return result

    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No S3 buckets to delete")
            return result

        client = self._client()

        def _empty_and_delete(record: ResourceRecord) -> None:
            bucket = record.resource_id

            skip_reason = self.should_skip_resource(record)
            if skip_reason:
                self.logger.cleanup(f"Skipping bucket {bucket}: {skip_reason}")
                result.add_skipped(record.resource_id, record.name, skip_reason, record.arn)
                return

            if self.config.dry_run:
                self.logger.cleanup(f"[DRY-RUN] Would empty and delete bucket {bucket}")
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
                return

            try:
                self._delete_bucket_policy(client, bucket)
                self._abort_multipart_uploads(client, bucket)
                self._delete_all_object_versions(client, bucket)
                self._delete_bucket(client, bucket)
                self.logger.cleanup(f"Deleted bucket {bucket}")
                self.logger.wait(f"Waiting for bucket deletion: {bucket}")
                wait_bucket_deleted(client, bucket, logger=self.logger)
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete bucket {bucket}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        with ThreadPoolExecutor(max_workers=self.config.parallel_workers) as pool:
            futures = [pool.submit(_empty_and_delete, record) for record in inventory.resources]
            for future in as_completed(futures):
                future.result()

        return result

    @retry_with_backoff()
    def _delete_bucket_policy(self, client, bucket: str) -> None:
        try:
            client.delete_bucket_policy(Bucket=bucket)
            self.logger.cleanup(f"Removed bucket policy on {bucket}")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "NoSuchBucketPolicy":
                # No policy attached -- nothing to remove, not an error.
                return
            # Anything else (AccessDenied, 405 Method Not Allowed for a
            # cross-account caller, etc.) is a real permissions problem
            # this tool's own credentials have, not something to paper
            # over -- let it surface and fail the bucket.
            raise

    @retry_with_backoff()
    def _abort_multipart_uploads(self, client, bucket: str) -> None:
        paginator = client.get_paginator("list_multipart_uploads")
        count = 0
        for page in paginator.paginate(Bucket=bucket):
            for upload in page.get("Uploads", []):
                client.abort_multipart_upload(Bucket=bucket, Key=upload["Key"], UploadId=upload["UploadId"])
                count += 1
        if count:
            self.logger.cleanup(f"Aborted {count} multipart uploads in {bucket}")

    def _delete_all_object_versions(self, client, bucket: str) -> None:
        paginator = client.get_paginator("list_object_versions")
        pending: List[dict] = []
        total = 0
        for page in paginator.paginate(Bucket=bucket):
            for version in page.get("Versions", []):
                pending.append({"Key": version["Key"], "VersionId": version["VersionId"]})
            for marker in page.get("DeleteMarkers", []):
                pending.append({"Key": marker["Key"], "VersionId": marker["VersionId"]})
            if len(pending) >= 1000:
                total += self._batch_delete(client, bucket, pending[:1000])
                pending = pending[1000:]
        if pending:
            total += self._batch_delete(client, bucket, pending)
        if total:
            self.logger.cleanup(f"Deleted {total} object versions/markers from {bucket}")

    @retry_with_backoff()
    def _batch_delete(self, client, bucket: str, objects: List[dict]) -> int:
        deleted = 0
        for batch in chunked(objects, 1000):
            resp = client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
            errors = resp.get("Errors", [])
            deleted += len(batch) - len(errors)
            for err in errors:
                self.logger.warning(
                    f"Failed to delete {bucket}/{err.get('Key')} "
                    f"version {err.get('VersionId')}: {err.get('Message')}"
                )
        return deleted

    @retry_with_backoff()
    def _delete_bucket(self, client, bucket: str) -> None:
        client.delete_bucket(Bucket=bucket)

    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying S3 is clean ...")
        try:
            buckets = self._list_buckets()
        except ClientError as exc:
            return VerificationResult(self.service_name, passed=False, error=str(exc))

        remaining: List[str] = []
        for bucket in buckets:
            name = bucket["Name"]
            try:
                region = self._bucket_region(name)
            except ClientError:
                continue
            if self.config.s3_scope_current_region_only and region != self.config.region:
                continue
            remaining.append(name)

        passed = len(remaining) == 0
        self.logger.verify("S3 clean" if passed else f"S3 FAIL - {len(remaining)} buckets remain")
        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )
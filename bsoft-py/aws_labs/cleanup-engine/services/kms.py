from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from botocore.exceptions import ClientError

from base.base_cleanup import BaseCleanupService
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult, ResourceRecord
from models.verification_result import VerificationResult
from retry import retry_with_backoff

# Key states that mean "already scheduled for deletion" -- re-calling
# schedule_key_deletion on a key already in one of these states raises
# KMSInvalidStateException, so cleanup() treats them as already-done
# rather than attempting (and failing) the call again.
PENDING_DELETION_STATES = {"PendingDeletion", "PendingReplicaDeletion"}

# Key states describe_key can return that are NOT safe/meaningful targets
# for schedule_key_deletion as part of a generic sweep (Creating/Updating
# are transient; PendingImport concerns externally-sourced key material;
# Unavailable applies to external key stores that may be disconnected).
# Keys in these states are inventoried but skipped at cleanup time rather
# than guessed at.
NON_ACTIONABLE_STATES = {"Creating", "Updating", "PendingImport", "Unavailable"}


class KMSCleanupService(BaseCleanupService):
    """Schedules deletion of customer managed KMS keys and deletes their
    customer-created aliases.

    THIS MODULE'S COMPLETION MODEL IS DIFFERENT FROM EVERY OTHER SERVICE
    MODULE IN THIS TOOL, AND THAT DIFFERENCE IS BY AWS DESIGN, NOT A GAP
    IN THIS CODE:

    - AWS KMS keys cannot be deleted immediately, by anyone, under any
      permission level. The only deletion API is ScheduleKeyDeletion,
      which starts a mandatory waiting period of 7-30 days (this module
      uses the minimum, 7, to limit sandbox lifetime) before AWS itself
      deletes the key. There is no force/immediate-delete option -- unlike
      the S3 bucket-policy, EventBridge managed-rule, or elbv2
      deletion-protection cases elsewhere in this tool, there is nothing
      to override here. A key scheduled for deletion will still appear in
      list_keys() until its waiting period elapses.
    - Because of that, verify() below does NOT require zero keys remaining
      to pass. A customer managed key counts as successfully handled once
      it's in PendingDeletion (or PendingReplicaDeletion, for multi-Region
      keys awaiting their replicas) -- it only counts as a real failure if
      it's still Enabled/Disabled and was never scheduled at all.
    - Unlike S3 bucket policies (where the bucket-owner root can always
      call DeleteBucketPolicy even against an explicit deny), a KMS key
      policy is NOT overridden by account root: no AWS principal,
      including root, has any permission on a key unless the key policy
      itself explicitly grants it. If a key's policy doesn't grant this
      tool's principal kms:ScheduleKeyDeletion, that key will fail with
      AccessDeniedException and there is no bypass -- recovering it
      requires either fixing the key policy from a principal it does
      trust, or an AWS Support request. This module does not attempt to
      work around that; it surfaces it as a failure.
    - AWS managed keys (KeyManager == "AWS", e.g. aws/s3, aws/ebs) cannot
      be scheduled for deletion by a customer at all -- AWS owns their
      lifecycle. These are filtered out during discover() and never
      appear in inventory, the same way self-managed EKS node groups are
      filtered out of the EKS module's scope.
    - A multi-Region primary key will not actually be deleted while
      replica keys still exist elsewhere, even after its waiting period
      is reached -- its state moves to PendingReplicaDeletion and stays
      there indefinitely. Replica keys themselves are region-scoped like
      any other key and are scheduled for deletion normally by this
      module when this tool runs against their region.

    Aliases (customer-created ones; aliases under the reserved alias/aws/
    prefix belong to AWS managed keys and are filtered out for the same
    reason as their keys) are deleted outright -- alias deletion is
    immediate and does not affect the underlying key.

    Honors protected_resource_arns, exclude_tags, and the checkpoint DB via
    should_skip_resource() for both resource types below.
    """

    service_name = "kms"

    def _client(self):
        return self.clients.client("kms")

    # ------------------------------------------------------------ listing
    @retry_with_backoff()
    def _list_key_ids(self) -> List[str]:
        paginator = self._client().get_paginator("list_keys")
        key_ids: List[str] = []
        for page in paginator.paginate():
            key_ids.extend(k["KeyId"] for k in page.get("Keys", []))
        return key_ids

    @retry_with_backoff()
    def _describe_key(self, key_id: str) -> Optional[dict]:
        try:
            return self._client().describe_key(KeyId=key_id).get("KeyMetadata")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code", "") == "NotFoundException":
                return None
            raise

    @retry_with_backoff()
    def _tags_for(self, key_id: str) -> Dict[str, str]:
        try:
            paginator = self._client().get_paginator("list_resource_tags")
            tags: Dict[str, str] = {}
            for page in paginator.paginate(KeyId=key_id):
                for tag in page.get("Tags", []):
                    tags[tag["TagKey"]] = tag["TagValue"]
            return tags
        except ClientError:
            return {}

    @retry_with_backoff()
    def _list_aliases(self) -> List[dict]:
        paginator = self._client().get_paginator("list_aliases")
        aliases: List[dict] = []
        for page in paginator.paginate():
            aliases.extend(page.get("Aliases", []))
        return aliases

    # ---------------------------------------------------------------------
    def discover(self) -> InventoryResult:
        self.logger.discover("Scanning KMS customer managed keys and aliases ...")
        result = InventoryResult(service_name=self.service_name)

        try:
            key_ids = self._list_key_ids()
        except ClientError as exc:
            result.error = str(exc)
            self.logger.error(f"KMS discovery failed: {exc}")
            return result

        customer_key_count = 0
        aws_managed_skipped = 0

        for key_id in key_ids:
            metadata = self._describe_key(key_id)
            if metadata is None:
                continue
            if metadata.get("KeyManager") == "AWS":
                aws_managed_skipped += 1
                continue

            customer_key_count += 1
            key_arn = metadata.get("Arn", "")
            result.resources.append(
                ResourceRecord(
                    resource_id=key_id,
                    name=key_id,
                    arn=key_arn,
                    metadata={
                        "type": "key",
                        "key_state": metadata.get("KeyState", ""),
                        "multi_region": metadata.get("MultiRegion", False),
                        "tags": self._tags_for(key_id),
                    },
                    fingerprint=str(metadata.get("CreationDate", "")),
                )
            )

        try:
            aliases = self._list_aliases()
        except ClientError as exc:
            self.logger.warning(f"Could not list KMS aliases: {exc}")
            aliases = []

        alias_count = 0
        for alias in aliases:
            alias_name = alias["AliasName"]
            if alias_name.startswith("alias/aws/"):
                continue  # reserved for AWS managed keys, not ours to touch
            alias_count += 1
            result.resources.append(
                ResourceRecord(
                    resource_id=alias_name,
                    name=alias_name,
                    arn=alias.get("AliasArn", ""),
                    metadata={"type": "alias", "target_key_id": alias.get("TargetKeyId", ""), "tags": {}},
                    fingerprint=str(alias.get("CreationDate", "")),
                )
            )

        self.logger.discover(
            f"Found {customer_key_count} customer managed keys, {alias_count} aliases "
            f"({aws_managed_skipped} AWS managed keys excluded -- not customer-deletable)"
        )
        return result

    # --------------------------------------------------------------- clean
    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        result = CleanupResult(service_name=self.service_name, discovered=inventory.resource_count)
        if inventory.resource_count == 0:
            self.logger.cleanup("No KMS resources to delete")
            return result

        if self.config.dry_run:
            for record in inventory.resources:
                verb = "schedule deletion of" if record.metadata.get("type") == "key" else "delete"
                self.logger.cleanup(f"[DRY-RUN] Would {verb} KMS {record.metadata.get('type')} {record.name}")
                result.add_skipped(record.resource_id, record.name, "dry_run", record.arn)
            return result

        client = self._client()

        def by_type(t: str) -> List[ResourceRecord]:
            return self._apply_skip_gate([r for r in inventory.resources if r.metadata.get("type") == t], result)

        aliases = by_type("alias")
        keys = by_type("key")

        self._delete_aliases(client, aliases, result)
        self._schedule_key_deletions(client, keys, result)

        return result

    def _apply_skip_gate(self, records: List[ResourceRecord], result: CleanupResult) -> List[ResourceRecord]:
        kept = []
        for record in records:
            reason = self.should_skip_resource(record)
            if reason:
                self.logger.cleanup(f"Skipping KMS resource {record.name}: {reason}")
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

    # ---- aliases ----
    def _delete_aliases(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            try:
                self._call_delete_alias(client, record.resource_id)
                self.logger.cleanup(f"Deleted alias {record.name}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to delete alias {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_delete_alias(self, client, alias_name: str) -> None:
        client.delete_alias(AliasName=alias_name)

    # ---- keys ----
    def _schedule_key_deletions(self, client, records: List[ResourceRecord], result: CleanupResult) -> None:
        def _delete_one(record: ResourceRecord) -> None:
            key_state = record.metadata.get("key_state", "")

            if key_state in PENDING_DELETION_STATES:
                self.logger.cleanup(f"Key {record.name} is already {key_state} -- not re-scheduling")
                result.add_skipped(record.resource_id, record.name, f"already {key_state}", record.arn)
                return

            if key_state in NON_ACTIONABLE_STATES:
                self.logger.cleanup(f"Key {record.name} is in state {key_state} -- skipping, not a safe deletion target")
                result.add_skipped(record.resource_id, record.name, f"key_state={key_state}", record.arn)
                return

            try:
                deletion_date = self._call_schedule_key_deletion(client, record.resource_id)
                self.logger.cleanup(f"Scheduled key {record.name} for deletion on {deletion_date}")
                result.add_success(record.resource_id, record.name, record.arn)
                self.record_deleted(record)
            except Exception as exc:  # noqa: BLE001
                self.logger.error(f"Failed to schedule deletion for key {record.name}: {exc}")
                result.add_failure(record.resource_id, record.name, str(exc), record.arn)

        self._run_parallel(records, _delete_one)

    @retry_with_backoff()
    def _call_schedule_key_deletion(self, client, key_id: str) -> str:
        resp = client.schedule_key_deletion(KeyId=key_id, PendingWindowInDays=7)
        return str(resp.get("DeletionDate", ""))

    # -------------------------------------------------------------- verify
    def verify(self) -> VerificationResult:
        self.logger.verify("Verifying KMS is clean (or all customer keys scheduled for deletion) ...")

        try:
            key_ids = self._list_key_ids()
        except ClientError as exc:
            return VerificationResult(self.service_name, passed=False, error=str(exc))

        remaining: List[str] = []
        scheduled_count = 0

        for key_id in key_ids:
            metadata = self._describe_key(key_id)
            if metadata is None or metadata.get("KeyManager") == "AWS":
                continue
            state = metadata.get("KeyState", "")
            if state in PENDING_DELETION_STATES:
                # Not a failure -- this is the only "deleted" a KMS key
                # can be immediately after a cleanup run. See class
                # docstring.
                scheduled_count += 1
                continue
            remaining.append(f"key:{key_id} (state={state})")

        try:
            for alias in self._list_aliases():
                alias_name = alias["AliasName"]
                if not alias_name.startswith("alias/aws/"):
                    remaining.append(f"alias:{alias_name}")
        except ClientError:
            pass

        passed = len(remaining) == 0
        if passed:
            self.logger.verify(
                f"KMS clean ({scheduled_count} customer keys scheduled for deletion, pending their waiting period)"
            )
        else:
            self.logger.verify(f"KMS FAIL - {len(remaining)} resources remain unscheduled/undeleted")

        return VerificationResult(
            self.service_name, passed=passed, remaining_count=len(remaining), remaining_resources=remaining
        )
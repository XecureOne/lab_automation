from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from botocore.exceptions import ClientError

from base.base_cleanup import BaseCleanupService
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult, ResourceRecord
from models.verification_result import VerificationResult
from retry import retry_with_backoff


SERVICE_LINKED_PREFIX = "/aws-service-role/"
RESERVED_PREFIX = "/aws-reserved/"


class IAMCleanupService(BaseCleanupService):
    """Deletes IAM resources in dependency-safe order."""

    service_name = "iam"

    def _client(self):
        return self.clients.client("iam")

    def _current_identity_role_name(self) -> str:
        """
        Return the role backing the credentials currently used by the
        cleanup engine.

        The currently-assumed execution role must never be deleted.
        """
        if not hasattr(self, "_cached_identity_role"):
            try:
                arn = self.clients.client(
                    "sts"
                ).get_caller_identity()["Arn"]

                if ":assumed-role/" in arn:
                    self._cached_identity_role = (
                        arn
                        .split(":assumed-role/", 1)[1]
                        .split("/", 1)[0]
                    )
                else:
                    self._cached_identity_role = ""

            except ClientError:
                self._cached_identity_role = ""

        return self._cached_identity_role

    # ------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------

    @retry_with_backoff()
    def _list_roles(self) -> List[dict]:
        paginator = self._client().get_paginator("list_roles")

        roles: List[dict] = []

        for page in paginator.paginate():
            roles.extend(page.get("Roles", []))

        return roles

    @retry_with_backoff()
    def _list_policies(self) -> List[dict]:
        paginator = self._client().get_paginator("list_policies")

        policies: List[dict] = []

        for page in paginator.paginate(Scope="Local"):
            policies.extend(page.get("Policies", []))

        return policies

    @retry_with_backoff()
    def _list_instance_profiles(self) -> List[dict]:
        paginator = self._client().get_paginator(
            "list_instance_profiles"
        )

        profiles: List[dict] = []

        for page in paginator.paginate():
            profiles.extend(
                page.get("InstanceProfiles", [])
            )

        return profiles

    @retry_with_backoff()
    def _list_users(self) -> List[dict]:
        paginator = self._client().get_paginator("list_users")

        users: List[dict] = []

        for page in paginator.paginate():
            users.extend(page.get("Users", []))

        return users

    @retry_with_backoff()
    def _list_groups(self) -> List[dict]:
        paginator = self._client().get_paginator("list_groups")

        groups: List[dict] = []

        for page in paginator.paginate():
            groups.extend(page.get("Groups", []))

        return groups

    @retry_with_backoff()
    def _tags_for(
        self,
        list_tags_fn,
        **kwargs,
    ) -> Dict[str, str]:
        try:
            response = list_tags_fn(**kwargs)

            return {
                tag["Key"]: tag["Value"]
                for tag in response.get("Tags", [])
            }

        except ClientError:
            return {}

    # ------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------

    def discover(self) -> InventoryResult:
        self.logger.discover(
            "Scanning IAM roles, policies, instance profiles, "
            "users, and groups ..."
        )

        result = InventoryResult(
            service_name=self.service_name
        )

        client = self._client()

        self_role_name = (
            self._current_identity_role_name()
        )

        protected_policy_names = set(
            self.config.protected_iam_policy_names
        )

        self.logger.info(
            "IAM protected policy names: "
            f"{sorted(protected_policy_names)}"
        )

        try:
            roles = self._list_roles()
            policies = self._list_policies()
            profiles = self._list_instance_profiles()
            users = self._list_users()
            groups = self._list_groups()

        except ClientError as exc:
            result.error = str(exc)

            self.logger.error(
                f"IAM discovery failed: {exc}"
            )

            return result

        # --------------------------------------------------------
        # Roles
        # --------------------------------------------------------

        role_count = 0

        for role in roles:
            name = role["RoleName"]
            path = role.get("Path", "/")

            is_service_linked = (
                path.startswith(SERVICE_LINKED_PREFIX)
                or path.startswith(RESERVED_PREFIX)
            )

            is_self = (
                name == self_role_name
            )

            if is_service_linked:
                continue

            if is_self:
                continue

            role_count += 1

            tags = self._tags_for(
                client.list_role_tags,
                RoleName=name,
            )

            result.resources.append(
                ResourceRecord(
                    resource_id=name,
                    name=name,
                    arn=role.get("Arn", ""),
                    metadata={
                        "type": "role",
                        "tags": tags,
                    },
                    fingerprint=str(
                        role.get("CreateDate", "")
                    ),
                )
            )

        # --------------------------------------------------------
        # Customer-managed policies
        # --------------------------------------------------------

        policy_count = 0

        for policy in policies:
            policy_arn = policy["Arn"]
            policy_name = policy.get(
                "PolicyName",
                policy_arn,
            )

            if policy_name in protected_policy_names:
                self.logger.discover(
                    f"Protecting IAM policy "
                    f"'{policy_name}' from deletion"
                )
                continue

            policy_count += 1

            tags = self._tags_for(
                client.list_policy_tags,
                PolicyArn=policy_arn,
            )

            result.resources.append(
                ResourceRecord(
                    resource_id=policy_arn,
                    name=policy_name,
                    arn=policy_arn,
                    metadata={
                        "type": "policy",
                        "tags": tags,
                    },
                    fingerprint=str(
                        policy.get("CreateDate", "")
                    ),
                )
            )

        # --------------------------------------------------------
        # Instance profiles
        # --------------------------------------------------------

        for profile in profiles:
            name = profile["InstanceProfileName"]

            tags = self._tags_for(
                client.list_instance_profile_tags,
                InstanceProfileName=name,
            )

            result.resources.append(
                ResourceRecord(
                    resource_id=name,
                    name=name,
                    arn=profile.get("Arn", ""),
                    metadata={
                        "type": "instance_profile",
                        "tags": tags,
                        "role_names": [
                            role["RoleName"]
                            for role in profile.get(
                                "Roles",
                                [],
                            )
                        ],
                    },
                    fingerprint=str(
                        profile.get("CreateDate", "")
                    ),
                )
            )

        # --------------------------------------------------------
        # Users
        # --------------------------------------------------------

        for user in users:
            name = user["UserName"]

            tags = self._tags_for(
                client.list_user_tags,
                UserName=name,
            )

            result.resources.append(
                ResourceRecord(
                    resource_id=name,
                    name=name,
                    arn=user.get("Arn", ""),
                    metadata={
                        "type": "user",
                        "tags": tags,
                    },
                    fingerprint=str(
                        user.get("CreateDate", "")
                    ),
                )
            )

        # --------------------------------------------------------
        # Groups
        # --------------------------------------------------------

        for group in groups:
            name = group["GroupName"]

            result.resources.append(
                ResourceRecord(
                    resource_id=name,
                    name=name,
                    arn=group.get("Arn", ""),
                    metadata={
                        "type": "group",
                        "tags": {},
                    },
                    fingerprint=str(
                        group.get("CreateDate", "")
                    ),
                )
            )

        if self_role_name:
            self.logger.discover(
                "Protecting currently-assumed role "
                f"'{self_role_name}' from deletion"
            )

        self.logger.discover(
            f"Found {role_count} deletable roles, "
            f"{policy_count} deletable customer-managed policies, "
            f"{len(profiles)} instance profiles, "
            f"{len(users)} users, "
            f"{len(groups)} groups"
        )

        return result

    # ------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------

    def cleanup(
        self,
        inventory: InventoryResult,
    ) -> CleanupResult:

        result = CleanupResult(
            service_name=self.service_name,
            discovered=inventory.resource_count,
        )

        if inventory.resource_count == 0:
            self.logger.cleanup(
                "No IAM resources to delete"
            )
            return result

        if self.config.dry_run:
            for record in inventory.resources:
                self.logger.cleanup(
                    "[DRY-RUN] Would delete IAM "
                    f"{record.metadata.get('type')} "
                    f"{record.name}"
                )

                result.add_skipped(
                    record.resource_id,
                    record.name,
                    "dry_run",
                    record.arn,
                )

            return result

        client = self._client()

        roles = self._apply_skip_gate(
            [
                r
                for r in inventory.resources
                if r.metadata.get("type") == "role"
            ],
            result,
        )

        profiles = self._apply_skip_gate(
            [
                r
                for r in inventory.resources
                if r.metadata.get("type")
                == "instance_profile"
            ],
            result,
        )

        users = self._apply_skip_gate(
            [
                r
                for r in inventory.resources
                if r.metadata.get("type") == "user"
            ],
            result,
        )

        groups = self._apply_skip_gate(
            [
                r
                for r in inventory.resources
                if r.metadata.get("type") == "group"
            ],
            result,
        )

        policies = self._apply_skip_gate(
            [
                r
                for r in inventory.resources
                if r.metadata.get("type") == "policy"
            ],
            result,
        )

        self._delete_roles(
            client,
            roles,
            result,
        )

        self._delete_instance_profiles(
            client,
            profiles,
            result,
        )

        self._delete_users(
            client,
            users,
            result,
        )

        self._delete_groups(
            client,
            groups,
            result,
        )

        self._delete_policies(
            client,
            policies,
            result,
        )

        return result

    def _apply_skip_gate(
        self,
        records: List[ResourceRecord],
        result: CleanupResult,
    ) -> List[ResourceRecord]:

        kept = []

        for record in records:
            reason = self.should_skip_resource(
                record
            )

            if reason:
                self.logger.cleanup(
                    "Skipping IAM resource "
                    f"{record.name}: {reason}"
                )

                result.add_skipped(
                    record.resource_id,
                    record.name,
                    reason,
                    record.arn,
                )

            else:
                kept.append(record)

        return kept

    def _run_parallel(
        self,
        records: List[ResourceRecord],
        delete_one,
    ) -> None:

        if not records:
            return

        with ThreadPoolExecutor(
            max_workers=self.config.parallel_workers
        ) as pool:

            futures = [
                pool.submit(
                    delete_one,
                    record,
                )
                for record in records
            ]

            for future in as_completed(futures):
                future.result()

    # ------------------------------------------------------------
    # Roles
    # ------------------------------------------------------------

    def _delete_roles(
        self,
        client,
        records: List[ResourceRecord],
        result: CleanupResult,
    ) -> None:

        def _delete_one(
            record: ResourceRecord,
        ) -> None:

            name = record.resource_id

            try:
                self._detach_role_policies(
                    client,
                    name,
                )

                self._delete_role_inline_policies(
                    client,
                    name,
                )

                self._remove_role_from_instance_profiles(
                    client,
                    name,
                )

                self._delete_role(
                    client,
                    name,
                )

                self.logger.cleanup(
                    f"Deleted role {name}"
                )

                result.add_success(
                    record.resource_id,
                    record.name,
                    record.arn,
                )

                self.record_deleted(record)

            except Exception as exc:
                self.logger.error(
                    f"Failed to delete role "
                    f"{name}: {exc}"
                )

                result.add_failure(
                    record.resource_id,
                    record.name,
                    str(exc),
                    record.arn,
                )

        self._run_parallel(
            records,
            _delete_one,
        )

    @retry_with_backoff()
    def _detach_role_policies(
        self,
        client,
        role_name: str,
    ) -> None:

        paginator = client.get_paginator(
            "list_attached_role_policies"
        )

        for page in paginator.paginate(
            RoleName=role_name
        ):
            for policy in page.get(
                "AttachedPolicies",
                [],
            ):
                client.detach_role_policy(
                    RoleName=role_name,
                    PolicyArn=policy["PolicyArn"],
                )

    @retry_with_backoff()
    def _delete_role_inline_policies(
        self,
        client,
        role_name: str,
    ) -> None:

        paginator = client.get_paginator(
            "list_role_policies"
        )

        for page in paginator.paginate(
            RoleName=role_name
        ):
            for policy_name in page.get(
                "PolicyNames",
                [],
            ):
                client.delete_role_policy(
                    RoleName=role_name,
                    PolicyName=policy_name,
                )

    @retry_with_backoff()
    def _remove_role_from_instance_profiles(
        self,
        client,
        role_name: str,
    ) -> None:

        paginator = client.get_paginator(
            "list_instance_profiles_for_role"
        )

        for page in paginator.paginate(
            RoleName=role_name
        ):
            for profile in page.get(
                "InstanceProfiles",
                [],
            ):
                self._disassociate_ec2_instance_profile(
                    profile.get("Arn", "")
                )

                client.remove_role_from_instance_profile(
                    InstanceProfileName=profile[
                        "InstanceProfileName"
                    ],
                    RoleName=role_name,
                )

    @retry_with_backoff()
    def _disassociate_ec2_instance_profile(
        self,
        profile_arn: str,
    ) -> None:

        if not profile_arn:
            return

        ec2_client = self.clients.client(
            "ec2"
        )

        response = (
            ec2_client
            .describe_iam_instance_profile_associations(
                Filters=[
                    {
                        "Name": "instance-profile-arn",
                        "Values": [
                            profile_arn
                        ],
                    },
                    {
                        "Name": "state",
                        "Values": [
                            "associating",
                            "associated",
                        ],
                    },
                ]
            )
        )

        for association in response.get(
            "IamInstanceProfileAssociations",
            [],
        ):
            ec2_client.disassociate_iam_instance_profile(
                AssociationId=association[
                    "AssociationId"
                ]
            )

    @retry_with_backoff()
    def _delete_role(
        self,
        client,
        role_name: str,
    ) -> None:

        client.delete_role(
            RoleName=role_name
        )

    # ------------------------------------------------------------
    # Instance profiles
    # ------------------------------------------------------------

    def _delete_instance_profiles(
        self,
        client,
        records: List[ResourceRecord],
        result: CleanupResult,
    ) -> None:

        def _delete_one(
            record: ResourceRecord,
        ) -> None:

            name = record.resource_id

            try:
                for role_name in (
                    record.metadata.get(
                        "role_names"
                    )
                    or []
                ):
                    self._remove_role_from_profile(
                        client,
                        name,
                        role_name,
                        record.arn,
                    )

                self._delete_instance_profile(
                    client,
                    name,
                )

                self.logger.cleanup(
                    "Deleted instance profile "
                    f"{name}"
                )

                result.add_success(
                    record.resource_id,
                    record.name,
                    record.arn,
                )

                self.record_deleted(record)

            except Exception as exc:
                self.logger.error(
                    "Failed to delete instance "
                    f"profile {name}: {exc}"
                )

                result.add_failure(
                    record.resource_id,
                    record.name,
                    str(exc),
                    record.arn,
                )

        self._run_parallel(
            records,
            _delete_one,
        )

    @retry_with_backoff()
    def _remove_role_from_profile(
        self,
        client,
        profile_name: str,
        role_name: str,
        profile_arn: str = "",
    ) -> None:

        self._disassociate_ec2_instance_profile(
            profile_arn
        )

        try:
            client.remove_role_from_instance_profile(
                InstanceProfileName=profile_name,
                RoleName=role_name,
            )

        except ClientError as exc:
            if (
                exc.response
                .get("Error", {})
                .get("Code", "")
                != "NoSuchEntity"
            ):
                raise

    @retry_with_backoff()
    def _delete_instance_profile(
        self,
        client,
        profile_name: str,
    ) -> None:

        client.delete_instance_profile(
            InstanceProfileName=profile_name
        )

    # ------------------------------------------------------------
    # Users
    # ------------------------------------------------------------

    def _delete_users(
        self,
        client,
        records: List[ResourceRecord],
        result: CleanupResult,
    ) -> None:

        def _delete_one(
            record: ResourceRecord,
        ) -> None:

            name = record.resource_id

            try:
                self._delete_access_keys(
                    client,
                    name,
                )

                self._delete_login_profile(
                    client,
                    name,
                )

                self._remove_mfa_devices(
                    client,
                    name,
                )

                self._detach_user_policies(
                    client,
                    name,
                )

                self._delete_user_inline_policies(
                    client,
                    name,
                )

                self._remove_user_from_groups(
                    client,
                    name,
                )

                self._delete_user(
                    client,
                    name,
                )

                self.logger.cleanup(
                    f"Deleted user {name}"
                )

                result.add_success(
                    record.resource_id,
                    record.name,
                    record.arn,
                )

                self.record_deleted(record)

            except Exception as exc:
                self.logger.error(
                    f"Failed to delete user "
                    f"{name}: {exc}"
                )

                result.add_failure(
                    record.resource_id,
                    record.name,
                    str(exc),
                    record.arn,
                )

        self._run_parallel(
            records,
            _delete_one,
        )

    @retry_with_backoff()
    def _delete_access_keys(
        self,
        client,
        user_name: str,
    ) -> None:

        response = client.list_access_keys(
            UserName=user_name
        )

        for key in response.get(
            "AccessKeyMetadata",
            [],
        ):
            client.delete_access_key(
                UserName=user_name,
                AccessKeyId=key[
                    "AccessKeyId"
                ],
            )

    @retry_with_backoff()
    def _delete_login_profile(
        self,
        client,
        user_name: str,
    ) -> None:

        try:
            client.delete_login_profile(
                UserName=user_name
            )

        except ClientError as exc:
            if (
                exc.response
                .get("Error", {})
                .get("Code", "")
                != "NoSuchEntity"
            ):
                raise

    @retry_with_backoff()
    def _remove_mfa_devices(
        self,
        client,
        user_name: str,
    ) -> None:

        response = client.list_mfa_devices(
            UserName=user_name
        )

        for device in response.get(
            "MFADevices",
            [],
        ):
            serial = device[
                "SerialNumber"
            ]

            client.deactivate_mfa_device(
                UserName=user_name,
                SerialNumber=serial,
            )

            try:
                client.delete_virtual_mfa_device(
                    SerialNumber=serial
                )

            except ClientError as exc:
                if (
                    exc.response
                    .get("Error", {})
                    .get("Code", "")
                    != "NoSuchEntity"
                ):
                    raise

    @retry_with_backoff()
    def _detach_user_policies(
        self,
        client,
        user_name: str,
    ) -> None:

        paginator = client.get_paginator(
            "list_attached_user_policies"
        )

        for page in paginator.paginate(
            UserName=user_name
        ):
            for policy in page.get(
                "AttachedPolicies",
                [],
            ):
                client.detach_user_policy(
                    UserName=user_name,
                    PolicyArn=policy[
                        "PolicyArn"
                    ],
                )

    @retry_with_backoff()
    def _delete_user_inline_policies(
        self,
        client,
        user_name: str,
    ) -> None:

        paginator = client.get_paginator(
            "list_user_policies"
        )

        for page in paginator.paginate(
            UserName=user_name
        ):
            for policy_name in page.get(
                "PolicyNames",
                [],
            ):
                client.delete_user_policy(
                    UserName=user_name,
                    PolicyName=policy_name,
                )

    @retry_with_backoff()
    def _remove_user_from_groups(
        self,
        client,
        user_name: str,
    ) -> None:

        response = client.list_groups_for_user(
            UserName=user_name
        )

        for group in response.get(
            "Groups",
            [],
        ):
            client.remove_user_from_group(
                UserName=user_name,
                GroupName=group[
                    "GroupName"
                ],
            )

    @retry_with_backoff()
    def _delete_user(
        self,
        client,
        user_name: str,
    ) -> None:

        client.delete_user(
            UserName=user_name
        )

    # ------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------

    def _delete_groups(
        self,
        client,
        records: List[ResourceRecord],
        result: CleanupResult,
    ) -> None:

        def _delete_one(
            record: ResourceRecord,
        ) -> None:

            name = record.resource_id

            try:
                self._detach_group_policies(
                    client,
                    name,
                )

                self._delete_group_inline_policies(
                    client,
                    name,
                )

                self._delete_group(
                    client,
                    name,
                )

                self.logger.cleanup(
                    f"Deleted group {name}"
                )

                result.add_success(
                    record.resource_id,
                    record.name,
                    record.arn,
                )

                self.record_deleted(record)

            except Exception as exc:
                self.logger.error(
                    f"Failed to delete group "
                    f"{name}: {exc}"
                )

                result.add_failure(
                    record.resource_id,
                    record.name,
                    str(exc),
                    record.arn,
                )

        self._run_parallel(
            records,
            _delete_one,
        )

    @retry_with_backoff()
    def _detach_group_policies(
        self,
        client,
        group_name: str,
    ) -> None:

        paginator = client.get_paginator(
            "list_attached_group_policies"
        )

        for page in paginator.paginate(
            GroupName=group_name
        ):
            for policy in page.get(
                "AttachedPolicies",
                [],
            ):
                client.detach_group_policy(
                    GroupName=group_name,
                    PolicyArn=policy[
                        "PolicyArn"
                    ],
                )

    @retry_with_backoff()
    def _delete_group_inline_policies(
        self,
        client,
        group_name: str,
    ) -> None:

        paginator = client.get_paginator(
            "list_group_policies"
        )

        for page in paginator.paginate(
            GroupName=group_name
        ):
            for policy_name in page.get(
                "PolicyNames",
                [],
            ):
                client.delete_group_policy(
                    GroupName=group_name,
                    PolicyName=policy_name,
                )

    @retry_with_backoff()
    def _delete_group(
        self,
        client,
        group_name: str,
    ) -> None:

        client.delete_group(
            GroupName=group_name
        )

    # ------------------------------------------------------------
    # Customer-managed policies
    # ------------------------------------------------------------

    def _delete_policies(
        self,
        client,
        records: List[ResourceRecord],
        result: CleanupResult,
    ) -> None:

        protected_policy_names = set(
            self.config.protected_iam_policy_names
        )

        def _delete_one(
            record: ResourceRecord,
        ) -> None:

            arn = record.resource_id

            # Second-line safety gate.
            if record.name in protected_policy_names:
                self.logger.cleanup(
                    "Skipping protected IAM policy "
                    f"{record.name}"
                )

                result.add_skipped(
                    record.resource_id,
                    record.name,
                    "protected_iam_policy_name",
                    record.arn,
                )

                return

            try:
                self._detach_policy_from_all_entities(
                    client,
                    arn,
                )

                self._delete_non_default_policy_versions(
                    client,
                    arn,
                )

                self._delete_policy(
                    client,
                    arn,
                )

                self.logger.cleanup(
                    f"Deleted policy {record.name}"
                )

                result.add_success(
                    record.resource_id,
                    record.name,
                    record.arn,
                )

                self.record_deleted(record)

            except Exception as exc:
                self.logger.error(
                    "Failed to delete policy "
                    f"{record.name}: {exc}"
                )

                result.add_failure(
                    record.resource_id,
                    record.name,
                    str(exc),
                    record.arn,
                )

        self._run_parallel(
            records,
            _delete_one,
        )

    @retry_with_backoff()
    def _detach_policy_from_all_entities(
        self,
        client,
        policy_arn: str,
    ) -> None:

        paginator = client.get_paginator(
            "list_entities_for_policy"
        )

        for page in paginator.paginate(
            PolicyArn=policy_arn
        ):
            for role in page.get(
                "PolicyRoles",
                [],
            ):
                client.detach_role_policy(
                    RoleName=role[
                        "RoleName"
                    ],
                    PolicyArn=policy_arn,
                )

            for user in page.get(
                "PolicyUsers",
                [],
            ):
                client.detach_user_policy(
                    UserName=user[
                        "UserName"
                    ],
                    PolicyArn=policy_arn,
                )

            for group in page.get(
                "PolicyGroups",
                [],
            ):
                client.detach_group_policy(
                    GroupName=group[
                        "GroupName"
                    ],
                    PolicyArn=policy_arn,
                )

    @retry_with_backoff()
    def _delete_non_default_policy_versions(
        self,
        client,
        policy_arn: str,
    ) -> None:

        response = client.list_policy_versions(
            PolicyArn=policy_arn
        )

        for version in response.get(
            "Versions",
            [],
        ):
            if not version.get(
                "IsDefaultVersion"
            ):
                client.delete_policy_version(
                    PolicyArn=policy_arn,
                    VersionId=version[
                        "VersionId"
                    ],
                )

    @retry_with_backoff()
    def _delete_policy(
        self,
        client,
        policy_arn: str,
    ) -> None:

        client.delete_policy(
            PolicyArn=policy_arn
        )

    # ------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------

    def verify(
        self,
    ) -> VerificationResult:

        self.logger.verify(
            "Verifying IAM is clean ..."
        )

        self_role_name = (
            self._current_identity_role_name()
        )

        protected_policy_names = set(
            self.config.protected_iam_policy_names
        )

        remaining: List[str] = []

        try:
            roles = self._list_roles()
            policies = self._list_policies()
            profiles = self._list_instance_profiles()
            users = self._list_users()
            groups = self._list_groups()

        except ClientError as exc:
            return VerificationResult(
                self.service_name,
                passed=False,
                error=str(exc),
            )

        for role in roles:
            path = role.get("Path", "/")

            if (
                path.startswith(
                    SERVICE_LINKED_PREFIX
                )
                or path.startswith(
                    RESERVED_PREFIX
                )
            ):
                continue

            if (
                role["RoleName"]
                == self_role_name
            ):
                continue

            remaining.append(
                f"role:{role['RoleName']}"
            )

        for policy in policies:
            policy_name = policy.get(
                "PolicyName",
                "",
            )

            if (
                policy_name
                in protected_policy_names
            ):
                continue

            remaining.append(
                f"policy:{policy_name}"
            )

        remaining += [
            "instance_profile:"
            f"{profile['InstanceProfileName']}"
            for profile in profiles
        ]

        remaining += [
            f"user:{user['UserName']}"
            for user in users
        ]

        remaining += [
            f"group:{group['GroupName']}"
            for group in groups
        ]

        passed = (
            len(remaining) == 0
        )

        self.logger.verify(
            "IAM clean"
            if passed
            else (
                "IAM FAIL - "
                f"{len(remaining)} "
                "resources remain"
            )
        )

        return VerificationResult(
            self.service_name,
            passed=passed,
            remaining_count=len(
                remaining
            ),
            remaining_resources=remaining,
        )
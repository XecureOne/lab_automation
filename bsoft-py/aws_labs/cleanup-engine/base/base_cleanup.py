from __future__ import annotations

from abc import abstractmethod
from typing import Optional

from base.base_inventory import BaseInventoryService
from base.base_verifier import BaseVerifier
from models.cleanup_result import CleanupResult
from models.inventory_result import InventoryResult, ResourceRecord
from models.verification_result import VerificationResult


class BaseCleanupService(BaseInventoryService, BaseVerifier):
    is_implemented: bool = True

    @abstractmethod
    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        raise NotImplementedError

    def should_skip_resource(self, record: ResourceRecord) -> Optional[str]:
        if record.arn and record.arn in self.config.protected_resource_arns:
            return "protected_resource_arn"

        tags = record.metadata.get("tags") or {}
        matched_tag = next((key for key in self.config.exclude_tags if key in tags), None)
        if matched_tag:
            return f"excluded_tag:{matched_tag}"

        if self.checkpoint is not None and self.checkpoint.is_deleted(
            self.config.account_id, self.config.region, self.service_name, record.resource_id, record.fingerprint
        ):
            return "already_deleted_checkpoint"

        return None

    def record_deleted(self, record: ResourceRecord) -> None:
        if self.checkpoint is not None:
            self.checkpoint.mark_deleted(
                self.config.account_id,
                self.config.region,
                self.service_name,
                record.resource_id,
                record.name,
                record.fingerprint,
            )


class NotImplementedCleanupService(BaseCleanupService):
    is_implemented = False

    def discover(self) -> InventoryResult:
        raise NotImplementedError(
            f"Service '{self.service_name}' is a placeholder and has not been implemented yet."
        )

    def cleanup(self, inventory: InventoryResult) -> CleanupResult:
        raise NotImplementedError(
            f"Service '{self.service_name}' is a placeholder and has not been implemented yet."
        )

    def verify(self) -> VerificationResult:
        raise NotImplementedError(
            f"Service '{self.service_name}' is a placeholder and has not been implemented yet."
        )

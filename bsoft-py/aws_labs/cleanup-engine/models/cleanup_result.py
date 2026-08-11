from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ResourceCleanupOutcome:
    """Outcome of attempting to delete a single resource."""

    resource_id: str
    name: str
    arn: str = ""
    deleted: bool = False
    skipped: bool = False
    error: str = ""


@dataclass
class CleanupResult:
    """Aggregate result of a cleanup() call for one service."""

    service_name: str
    discovered: int = 0
    deleted: int = 0
    failed: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    outcomes: List[ResourceCleanupOutcome] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def add_success(self, resource_id: str, name: str, arn: str = "") -> None:
        self.outcomes.append(ResourceCleanupOutcome(resource_id, name, arn, deleted=True))
        self.deleted += 1

    def add_failure(self, resource_id: str, name: str, error: str, arn: str = "") -> None:
        self.outcomes.append(ResourceCleanupOutcome(resource_id, name, arn, error=error))
        self.failed += 1
        self.errors.append(f"{self.service_name}:{name or resource_id}: {error}")

    def add_skipped(self, resource_id: str, name: str, reason: str, arn: str = "") -> None:
        self.outcomes.append(ResourceCleanupOutcome(resource_id, name, arn, skipped=True, error=reason))
        self.skipped += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service": self.service_name,
            "discovered": self.discovered,
            "deleted": self.deleted,
            "failed": self.failed,
            "skipped": self.skipped,
            "duration_seconds": round(self.duration_seconds, 2),
            "outcomes": [
                {
                    "id": o.resource_id,
                    "name": o.name,
                    "arn": o.arn,
                    "deleted": o.deleted,
                    "skipped": o.skipped,
                    "error": o.error,
                }
                for o in self.outcomes
            ],
            "errors": self.errors,
        }

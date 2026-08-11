from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class ResourceRecord:
    """A single discovered AWS resource."""

    resource_id: str
    name: str
    arn: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Distinguishes this physical resource from a different one that later
    # reuses the same resource_id (e.g. a same-named RDS instance/cluster
    # recreated by a new lab run). When a service can supply one (RDS's
    # InstanceCreateTime/ClusterCreateTime, S3's bucket CreationDate), the
    # checkpoint DB only treats a resource as "already deleted" if both the
    # resource_id AND this fingerprint match a prior recorded deletion.
    # Empty string means the service has no such signal (e.g.
    # CloudFormation, whose StackId is already unique per creation) and the
    # checkpoint falls back to resource_id alone.
    fingerprint: str = ""


@dataclass
class InventoryResult:
    """Result of a discover() call for one service."""

    service_name: str
    resources: List[ResourceRecord] = field(default_factory=list)
    error: str = ""

    @property
    def resource_count(self) -> int:
        return len(self.resources)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service": self.service_name,
            "resource_count": self.resource_count,
            "resources": [
                {
                    "id": r.resource_id,
                    "name": r.name,
                    "arn": r.arn,
                    "metadata": r.metadata,
                    "fingerprint": r.fingerprint,
                }
                for r in self.resources
            ],
            "error": self.error,
        }

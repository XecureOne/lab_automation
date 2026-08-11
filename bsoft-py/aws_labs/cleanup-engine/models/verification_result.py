from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class VerificationResult:
    """Result of a verify() call for one service."""

    service_name: str
    passed: bool
    remaining_count: int = 0
    remaining_resources: List[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service": self.service_name,
            "status": "PASS" if self.passed else "FAIL",
            "remaining_count": self.remaining_count,
            "remaining_resources": self.remaining_resources,
            "error": self.error,
        }

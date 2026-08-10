from __future__ import annotations

from abc import ABC, abstractmethod

from models.verification_result import VerificationResult


class BaseVerifier(ABC):
    """Base class every verification-capable module inherits from."""

    @abstractmethod
    def verify(self) -> VerificationResult:
        """Confirm no resources of this service remain in the account/region
        and return a VerificationResult. Must not raise -- capture failures
        on VerificationResult.error and mark passed=False."""
        raise NotImplementedError

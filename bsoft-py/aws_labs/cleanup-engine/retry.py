from __future__ import annotations

import functools
import random
import time
from typing import Callable, Iterable, Optional, TypeVar

from botocore.exceptions import BotoCoreError, ClientError

RETRYABLE_ERROR_CODES = {
    "ThrottlingException", "Throttling", "TooManyRequestsException",
    "RequestLimitExceeded", "ConcurrentModificationException",
    "DependencyViolation", "LimitExceededException", "InternalFailure",
    "InternalError", "ServiceUnavailable", "SlowDown",
    "OperationAbortedException", "ResourceInUseException",
}

T = TypeVar("T")


def retry_with_backoff(
    max_attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    retryable_errors: Optional[Iterable[str]] = None,
    logger=None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator that retries a boto3 call with exponential backoff on
    transient AWS errors. When wrapping a bound method whose instance
    exposes a `config` attribute with retry_attempts/retry_base_delay_seconds/
    retry_max_delay_seconds fields, those instance-level, config-driven
    values take precedence over the decorator's own defaults."""
    retryable = set(retryable_errors) if retryable_errors else RETRYABLE_ERROR_CODES

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            cfg = getattr(args[0], "config", None) if args else None
            eff_max_attempts = getattr(cfg, "retry_attempts", None) or max_attempts
            eff_base_delay = getattr(cfg, "retry_base_delay_seconds", None) or base_delay
            eff_max_delay = getattr(cfg, "retry_max_delay_seconds", None) or max_delay

            attempt = 0
            while True:
                attempt += 1
                try:
                    return func(*args, **kwargs)
                except ClientError as exc:
                    code = exc.response.get("Error", {}).get("Code", "")
                    if code in retryable and attempt < eff_max_attempts:
                        _sleep(attempt, eff_base_delay, eff_max_delay, jitter, logger, f"{func.__name__} ({code})")
                        continue
                    raise
                except BotoCoreError:
                    if attempt < eff_max_attempts:
                        _sleep(attempt, eff_base_delay, eff_max_delay, jitter, logger, f"{func.__name__} (connection error)")
                        continue
                    raise

        return wrapper

    return decorator


def _sleep(attempt: int, base_delay: float, max_delay: float, jitter: bool, logger, reason: str) -> None:
    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
    if jitter:
        delay = delay * (0.5 + random.random())
    if logger is not None:
        logger.warning(f"Retryable error on attempt {attempt} for {reason}; sleeping {delay:.1f}s")
    time.sleep(delay)

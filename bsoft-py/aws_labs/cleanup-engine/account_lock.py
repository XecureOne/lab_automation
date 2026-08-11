from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Optional


class AccountLockError(RuntimeError):
    pass


class AccountCleanupLock:
    """
    Cross-process lock for a single AWS account.

    Prevents two cleanup executions on the same machine from operating
    on the same sandbox account simultaneously.

    Lock file:
        logs/locks/<account_id>.lock
    """

    def __init__(
        self,
        account_id: str,
        lock_dir: str = "logs/locks",
    ):
        self.account_id = str(account_id)
        self.lock_dir = Path(lock_dir)
        self.lock_path = self.lock_dir / f"{self.account_id}.lock"
        self._file: Optional[object] = None

    def acquire(self) -> None:
        self.lock_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        self._file = open(
            self.lock_path,
            "a+",
            encoding="utf-8",
        )

        try:
            fcntl.flock(
                self._file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )

        except BlockingIOError:
            self._file.close()
            self._file = None

            raise AccountLockError(
                f"Cleanup already running for AWS account "
                f"{self.account_id}"
            )

        self._file.seek(0)
        self._file.truncate()

        self._file.write(
            f"pid={os.getpid()}\n"
            f"account_id={self.account_id}\n"
        )

        self._file.flush()

    def release(self) -> None:
        if self._file is None:
            return

        try:
            fcntl.flock(
                self._file.fileno(),
                fcntl.LOCK_UN,
            )
        finally:
            self._file.close()
            self._file = None

    def __enter__(self) -> "AccountCleanupLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ) -> None:
        self.release()
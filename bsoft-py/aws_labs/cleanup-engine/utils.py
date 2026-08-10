from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Iterator, List, TypeVar

T = TypeVar("T")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dir(path: str) -> None:
    if path:
        os.makedirs(path, exist_ok=True)


def write_json(path: str, data: Any) -> None:
    ensure_dir(os.path.dirname(path) or ".")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)


def chunked(items: List[T], size: int) -> Iterator[List[T]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class Timer:
    """Context manager that records elapsed wall-clock time in self.elapsed."""

    def __enter__(self) -> "Timer":
        self._start = time.monotonic()
        self.elapsed = 0.0
        return self

    def __exit__(self, *exc) -> None:
        self.elapsed = time.monotonic() - self._start

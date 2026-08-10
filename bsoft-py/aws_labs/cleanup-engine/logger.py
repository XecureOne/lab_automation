from __future__ import annotations

import logging
import sys
from typing import Optional

_STAGE_WIDTH = 10


def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Configure the root logger once at process start. Every StageLogger
    created afterward via get_logger() shares this configuration."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)


def get_logger(name: str) -> "StageLogger":
    return StageLogger(logging.getLogger(name))


class StageLogger:
    """Wraps a stdlib logger with the [STAGE] tagging convention used across
    the engine, e.g. '[DISCOVER] Found 5 buckets', '[CLEANUP] Deleted bucket
    student-bucket', '[WAIT] Waiting for stack deletion', '[VERIFY] S3 clean'.
    """

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _emit(self, stage: str, message: str, level: int = logging.INFO) -> None:
        tag = f"[{stage.upper()}]".ljust(_STAGE_WIDTH + 2)
        self._logger.log(level, f"{tag}{message}")

    def discover(self, message: str) -> None:
        self._emit("discover", message)

    def cleanup(self, message: str) -> None:
        self._emit("cleanup", message)

    def wait(self, message: str) -> None:
        self._emit("wait", message)

    def verify(self, message: str) -> None:
        self._emit("verify", message)

    def report(self, message: str) -> None:
        self._emit("report", message)

    def info(self, message: str) -> None:
        self._logger.info(message)

    def warning(self, message: str) -> None:
        self._emit("warn", message, logging.WARNING)

    def error(self, message: str) -> None:
        self._emit("error", message, logging.ERROR)

    def debug(self, message: str) -> None:
        self._logger.debug(message)

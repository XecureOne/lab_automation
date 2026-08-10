from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from logger import get_logger


@dataclass
class AwsNukeResult:
    success: bool
    return_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class AwsNukeRunner:
    def __init__(
        self,
        session,
        binary: str = "aws-nuke",
        timeout_seconds: int = 3600,
        max_wait_retries: int = 10,
    ):
        self.session = session
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self.max_wait_retries = max_wait_retries
        self.logger = get_logger("aws_nuke")

    def _build_env(self) -> dict:
        env = os.environ.copy()

        creds = self.session.get_credentials()
        frozen = creds.get_frozen_credentials()

        env["AWS_ACCESS_KEY_ID"] = frozen.access_key
        env["AWS_SECRET_ACCESS_KEY"] = frozen.secret_key

        if frozen.token:
            env["AWS_SESSION_TOKEN"] = frozen.token

        return env

    def run(
        self,
        account_id: str,
        region: str,
        config_file: str,
        dry_run: bool = False,
    ) -> AwsNukeResult:
        config_path = Path(config_file)

        if not config_path.exists():
            raise FileNotFoundError(
                f"aws-nuke config file not found: {config_path}"
            )

        command = [
            self.binary,
            "run",
            "--config",
            str(config_path),
            "--default-region",
            region,
            "--no-prompt",
            "--max-wait-retries",
            str(self.max_wait_retries),
        ]

        if not dry_run:
            command.append("--no-dry-run")

        mode = "DRY RUN" if dry_run else "DESTRUCTIVE"

        self.logger.info(
            f"Starting aws-nuke [{mode}] for account "
            f"{account_id} in region {region}"
        )

        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                env=self._build_env(),
            )

            success = process.returncode == 0

            if success:
                self.logger.info(
                    "aws-nuke completed successfully"
                )
            else:
                self.logger.error(
                    f"aws-nuke failed with return code "
                    f"{process.returncode}"
                )

            return AwsNukeResult(
                success=success,
                return_code=process.returncode,
                stdout=process.stdout,
                stderr=process.stderr,
            )

        except subprocess.TimeoutExpired as exc:
            self.logger.error(
                f"aws-nuke timed out after "
                f"{self.timeout_seconds} seconds"
            )

            return AwsNukeResult(
                success=False,
                return_code=-1,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "",
                timed_out=True,
            )
import subprocess
import sys
import logging
import argparse
import os
import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def _redacted_command(cmd):
    redacted_flags = {
        "--access-key-id",
        "--secret-access-key",
        "--session-token",
    }
    safe = []
    redact_next = False
    for part in cmd:
        if redact_next:
            safe.append("<redacted>")
            redact_next = False
            continue
        safe.append(part)
        if part in redacted_flags:
            redact_next = True
    return safe


def build_config(account_id: str, alias: str, output_path: str = "aws-nuke-final.yaml"):
    """Build the aws-nuke config using f-string templating and write to disk."""

    config = f"""regions:
  - ap-south-1
  - global

blocklist:
  - "880690594512"  # management account ID

accounts:
  "{account_id}":
    filters:
      IAMRole:
        - "OrganizationAccountAccessRole"
      IAMRolePolicyAttachment:
        - "OrganizationAccountAccessRole -> AdministratorAccess"
      IAMRolePolicy:
        - property: role
          value: "OrganizationAccountAccessRole"
      IAMPolicy:
        - "arn:aws:iam::{account_id}:policy/CoderCreatedIdentityBoundary"
        - "arn:aws:iam::{account_id}:policy/DefaultIamPolicy"
"""

    with open(output_path, "w") as f:
        f.write(config.strip())

    logger.info("Config written to %s account_id=%s alias=%s", output_path, account_id, alias)


def run_aws_nuke(config_path: str,creds: dict , dry_run: bool = False, profile: str = None) -> None:
    cmd = ["aws-nuke","nuke", "-c", config_path,"--access-key-id",creds["AccessKeyId"],"--secret-access-key",creds["SecretAccessKey"],"--session-token",creds["SessionToken"], "--force"]
    if not dry_run:
        cmd.append("--no-dry-run")
    if profile:
        cmd.extend(["--profile", profile])

    logger.info("Command: %s", " ".join(_redacted_command(cmd)))

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        for line in process.stdout:
            line = line.rstrip()
            logger.info(line)
            if "ERROR" in line:
                logger.error("aws-nuke error: %s", line)
            if "Nuke complete" in line:
                logger.info("aws-nuke finished successfully")

        process.wait()

        if process.returncode != 0:
            logger.error("aws-nuke exited with code %s", process.returncode)
            sys.exit(process.returncode)

    except FileNotFoundError:
        logger.error("aws-nuke binary not found. Ensure it is installed and in your PATH.")
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error: %s", e)
        sys.exit(1)

def test_run(config_path: str,creds: dict , dry_run: bool = False, profile: str = None) -> None:
    cmd = ["aws-nuke","nuke", "-c", config_path,"--access-key-id",creds["AccessKeyId"],"--secret-access-key",creds["SecretAccessKey"],"--session-token",creds["SessionToken"]]
    if not dry_run:
        cmd.append("--no-dry-run")
    if profile:
        cmd.extend(["--profile", profile])

    logger.info("Command: %s", " ".join(_redacted_command(cmd)))

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        for line in process.stdout:
            line = line.rstrip()
            logger.info(line)
            if "ERROR" in line:
                logger.error("aws-nuke error: %s", line)
            if "Nuke complete" in line:
                logger.info("aws-nuke finished successfully")

        process.wait()

        if process.returncode != 0:
            logger.error("aws-nuke exited with code %s", process.returncode)
            sys.exit(process.returncode)

    except FileNotFoundError:
        logger.error("aws-nuke binary not found. Ensure it is installed and in your PATH.")
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error: %s", e)
        sys.exit(1)


def nuke(account_id,account_alias,creds):
    build_config(
        account_id=account_id,
        alias=account_alias,
        output_path="nuke_config.yaml"
    )
    run_aws_nuke(
        config_path="nuke_config.yaml",
        creds=creds,
        dry_run=True
    )
    # test_run(
    #     config_path="nuke_config.yaml",
    #     creds=creds,
    #     dry_run=True
    # )

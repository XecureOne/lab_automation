import subprocess
import sys
import logging
import argparse
import os
import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def build_config(account_id: str, alias: str, output_path: str = "aws-nuke-final.yaml"):
    """Build the aws-nuke config using f-string templating and write to disk."""

    config = f"""
regions:
  - ap-south-1
  - global

blocklist:
  - "959782869917"  # Replace with your management account ID

accounts:
  "{account_id}":
    alias: "{alias}"
    filters:
      IAMRole:
        - "OrganizationAccountAccessRole"
      IAMRolePolicyAttachment:
        - "OrganizationAccountAccessRole -> AdministratorAccess"
      IAMRolePolicy:
        - property: role
          value: "OrganizationAccountAccessRole"
      IAMUser:
        - "Coder"
      IAMUserLoginProfile:
        - "Coder"
"""

    with open(output_path, "w") as f:
        f.write(config.strip())

    logger.info(f"Config written → {output_path} | ID: {account_id} | Alias: {alias}")


def run_aws_nuke(config_path: str,creds: dict , dry_run: bool = False, profile: str = None) -> None:
    cmd = ["aws-nuke","nuke", "-c", config_path,"--access-key-id",creds["AccessKeyId"],"--secret-access-key",creds["SecretAccessKey"],"--session-token",creds["SessionToken"], "--force"]
    if not dry_run:
        cmd.append("--no-dry-run")
    if profile:
        cmd.extend(["--profile", profile])

    logger.info(f"Command: {' '.join(cmd)}")

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
                logger.error(f"aws-nuke error: {line}")
            if "Nuke complete" in line:
                logger.info("✅ aws-nuke finished successfully.")

        process.wait()

        if process.returncode != 0:
            logger.error(f"aws-nuke exited with code {process.returncode}")
            sys.exit(process.returncode)

    except FileNotFoundError:
        logger.error("aws-nuke binary not found. Ensure it is installed and in your PATH.")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


def nuke(account_id,account_alias,creds):
    build_config(
        account_id=account_id,
        alias=account_alias,
        output_path="nuke_config.yaml"
    )
    run_aws_nuke(
        config_path="nuke_config.yaml",
        creds=creds
    )

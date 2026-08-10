from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from typing import List, Optional


import yaml
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.yaml"

ALL_SERVICE_MODULES = [
    "cloudformation", "s3", "rds", "eventbridge", "iam", "eks", "ec2",
    "autoscaling", "application_autoscaling", "elb", "ecs", "ecr",
    "elasticbeanstalk", "lambda", "apigateway", "kms",
]

@dataclass
class AwsNukeConfig:
    enabled: bool = False
    binary: str = "aws-nuke"
    config_file: str = "nuke-config.yaml"
    timeout_seconds: int = 3600
    max_retries: int = 2


@dataclass
class AssumeRoleConfig:
    role_name: str = "OrganizationAccountAccessRole"
    external_id: Optional[str] = None


@dataclass
class EngineConfig:
    regions: List[str] = field(default_factory=lambda: ["ap-south-1"])
    aws_profile: Optional[str] = None
    region: str = ""
    account_id: str = ""
    nuke_config_file: str = ""

    protected_iam_policy_names: List[str] = field(default_factory=list)
    aws_nuke: AwsNukeConfig = field(default_factory=AwsNukeConfig)

    exclude_services: List[str] = field(default_factory=list)
    include_services: List[str] = field(default_factory=list)

    exclude_tags: List[str] = field(default_factory=list)
    protected_resource_arns: List[str] = field(default_factory=list)

    parallel_workers: int = 20
    retry_attempts: int = 5
    retry_base_delay_seconds: float = 2.0
    retry_max_delay_seconds: float = 60.0
    botocore_max_attempts: int = 3
    connect_timeout_seconds: int = 10
    read_timeout_seconds: int = 30

    waiter_default_timeout_seconds: int = 1800
    waiter_default_interval_seconds: int = 15

    force_disable_protection: bool = True
    kms_pending_window_days: int = 7
    retain_final_snapshot: bool = False
    s3_scope_current_region_only: bool = True

    dry_run: bool = True
    require_confirmation_phrase: str = "DELETE ACCOUNT"

    checkpoint_db: str = "logs/checkpoint.sqlite3"
    report_dir: str = "logs/reports"
    log_level: str = "INFO"
    log_file: str = "logs/cleanup_engine.log"

    max_verification_passes: int = 5


    assume_role: AssumeRoleConfig = field(default_factory=AssumeRoleConfig)

    @staticmethod
    def load(path: str | None = None) -> "EngineConfig":
        if path is None:
            path = str(DEFAULT_CONFIG_PATH)

        data = {}

        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}

        cfg = EngineConfig()


        if "region" in data and "regions" not in data:
            data["regions"] = [data.pop("region")]
        if "enabled_services" in data and "include_services" not in data:
            data["include_services"] = data.pop("enabled_services")
        if "max_workers" in data and "parallel_workers" not in data:
            data["parallel_workers"] = data.pop("max_workers")
        if "retry_max_attempts" in data and "retry_attempts" not in data:
            data["retry_attempts"] = data.pop("retry_max_attempts")
        if "output_dir" in data and "report_dir" not in data:
            data["report_dir"] = data.pop("output_dir")

        raw_assume_role = data.pop("assume_role", None) or {}

        cfg.assume_role = AssumeRoleConfig(
            role_name=raw_assume_role.get(
                "role_name",
                "OrganizationAccountAccessRole",
            ),
            external_id=raw_assume_role.get("external_id"),
        )

        raw_aws_nuke = data.pop("aws_nuke", None) or {}

        cfg.aws_nuke = AwsNukeConfig(
            enabled=raw_aws_nuke.get("enabled", False),
            binary=raw_aws_nuke.get("binary", "aws-nuke"),
            config_file=raw_aws_nuke.get("config_file", "nuke-config.yaml"),
            timeout_seconds=int(raw_aws_nuke.get("timeout_seconds", 3600)),
            max_retries=int(raw_aws_nuke.get("max_retries", 2)),
        )

        recognized = {f.name for f in dataclasses.fields(cfg)}
        unknown_keys = [k for k in data if k not in recognized]

        for key, value in data.items():
            if hasattr(cfg, key) and value is not None:
                setattr(cfg, key, value)

        env_map = {
            "CLEANUP_ENGINE_REGIONS": "regions",
            "AWS_REGION": "regions",
            "AWS_DEFAULT_REGION": "regions",
            "CLEANUP_ENGINE_PROFILE": "aws_profile",
            "AWS_PROFILE": "aws_profile",
            "CLEANUP_ENGINE_DRY_RUN": "dry_run",
            "CLEANUP_ENGINE_PARALLEL_WORKERS": "parallel_workers",
            "CLEANUP_ENGINE_RETAIN_SNAPSHOT": "retain_final_snapshot",
            "CLEANUP_ENGINE_LOG_LEVEL": "log_level",
            "CLEANUP_ENGINE_REPORT_DIR": "report_dir",
            "CLEANUP_ENGINE_CONFIRMATION_PHRASE": "require_confirmation_phrase",
        }
        for env_key, attr in env_map.items():
            raw = os.environ.get(env_key)
            if raw is None:
                continue
            current = getattr(cfg, attr)
            if attr == "regions":
                setattr(cfg, attr, [r.strip() for r in raw.split(",") if r.strip()])
            elif isinstance(current, bool):
                setattr(cfg, attr, raw.strip().lower() in ("1", "true", "yes", "on"))
            elif isinstance(current, int):
                setattr(cfg, attr, int(raw))
            else:
                setattr(cfg, attr, raw)

        if not cfg.regions:
            cfg.regions = ["ap-south-1"]

        cfg._unknown_keys = unknown_keys
        return cfg

    def for_run(self, region: str, account_id: str = "") -> "EngineConfig":
        suffix = f"{account_id}/{region}" if account_id else region
        return dataclasses.replace(
            self, region=region, account_id=account_id, report_dir=f"{self.report_dir}/{suffix}"
        )

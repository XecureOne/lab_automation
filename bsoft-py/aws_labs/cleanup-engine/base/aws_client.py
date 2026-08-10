from __future__ import annotations

import threading
from typing import Dict, Optional

import boto3
from botocore.client import BaseClient
from botocore.config import Config as BotoConfig

from config import EngineConfig


class AWSClientFactory:
    def __init__(self, config: EngineConfig, session: Optional[boto3.Session] = None):
        self._config = config
        self._session = session or boto3.Session(
            region_name=config.region or None,
            profile_name=config.aws_profile or None,
        )
        self._clients: Dict[str, BaseClient] = {}
        self._lock = threading.Lock()

    @property
    def session(self) -> boto3.Session:
        return self._session

    def _boto_config(self, region_name: str) -> BotoConfig:
        return BotoConfig(
            region_name=region_name,
            retries={"max_attempts": self._config.botocore_max_attempts, "mode": "standard"},
            connect_timeout=self._config.connect_timeout_seconds,
            read_timeout=self._config.read_timeout_seconds,
        )

    def client(self, service_name: str, region_name: Optional[str] = None) -> BaseClient:
        region = region_name or self._config.region
        key = f"{service_name}:{region}"
        with self._lock:
            if key not in self._clients:
                self._clients[key] = self._session.client(service_name, config=self._boto_config(region))
            return self._clients[key]

    def account_id(self) -> str:
        sts = self.client("sts")
        return sts.get_caller_identity()["Account"]


def build_assumed_session(
    account_id: str, role_name: str, region: str, external_id: Optional[str] = None
) -> boto3.Session:
    sts = boto3.client("sts")
    kwargs = {
        "RoleArn": f"arn:aws:iam::{account_id}:role/{role_name}",
        "RoleSessionName": "sandbox-cleanup-engine",
    }
    if external_id:
        kwargs["ExternalId"] = external_id
    creds = sts.assume_role(**kwargs)["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )

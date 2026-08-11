from __future__ import annotations

import time
from typing import Callable, Optional

from botocore.exceptions import ClientError

from logger import StageLogger, get_logger


class WaiterTimeoutError(Exception):
    """Raised when a resource does not reach the expected terminal state
    within the configured timeout."""


class Waiter:
    """Generic polling waiter. Specific wait_* functions below build on this
    with resource-specific predicates so new ones can be added without
    duplicating the polling loop."""

    def __init__(self, logger: Optional[StageLogger] = None):
        self._logger = logger or get_logger(__name__)

    def wait_for(
        self,
        predicate: Callable[[], bool],
        description: str,
        timeout: int = 1800,
        interval: int = 15,
    ) -> None:
        start = time.monotonic()
        while True:
            if predicate():
                return
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                raise WaiterTimeoutError(f"Timed out after {int(elapsed)}s waiting for: {description}")
            self._logger.wait(f"{description} ... ({int(elapsed)}s elapsed)")
            time.sleep(interval)


def wait_stack_deleted(cf_client, stack_name: str, timeout: int = 1800, interval: int = 15, logger=None) -> None:
    """Poll CloudFormation until the given stack is gone (DELETE_COMPLETE or
    no longer describable). Raises if the stack enters DELETE_FAILED."""
    waiter = Waiter(logger)

    def predicate() -> bool:
        try:
            resp = cf_client.describe_stacks(StackName=stack_name)
            stacks = resp.get("Stacks", [])
            if not stacks:
                return True
            status = stacks[0]["StackStatus"]
            if status == "DELETE_FAILED":
                raise RuntimeError(
                    f"Stack {stack_name} entered DELETE_FAILED: "
                    f"{stacks[0].get('StackStatusReason', 'unknown reason')}"
                )
            return status == "DELETE_COMPLETE"
        except ClientError as exc:
            if "does not exist" in str(exc):
                return True
            raise

    waiter.wait_for(predicate, f"CloudFormation stack '{stack_name}' deletion", timeout, interval)


def wait_bucket_deleted(s3_client, bucket_name: str, timeout: int = 600, interval: int = 10, logger=None) -> None:
    """Poll S3 via head_bucket until the bucket returns 404/NoSuchBucket."""
    waiter = Waiter(logger)

    def predicate() -> bool:
        try:
            s3_client.head_bucket(Bucket=bucket_name)
            return False
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in ("404", "NoSuchBucket") or status == 404:
                return True
            raise

    waiter.wait_for(predicate, f"S3 bucket '{bucket_name}' deletion", timeout, interval)


def wait_rds_instance_deleted(rds_client, db_instance_id: str, timeout: int = 1800, interval: int = 20, logger=None) -> None:
    """Poll RDS until the DB instance no longer exists. Raises if the
    instance enters the 'failed' state."""
    waiter = Waiter(logger)

    def predicate() -> bool:
        try:
            resp = rds_client.describe_db_instances(DBInstanceIdentifier=db_instance_id)
            instances = resp.get("DBInstances", [])
            if not instances:
                return True
            status = instances[0]["DBInstanceStatus"]
            if status == "failed":
                raise RuntimeError(f"DB instance {db_instance_id} entered failed state")
            return False
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "DBInstanceNotFound":
                return True
            raise

    waiter.wait_for(predicate, f"RDS instance '{db_instance_id}' deletion", timeout, interval)


def wait_rds_cluster_deleted(rds_client, db_cluster_id: str, timeout: int = 1800, interval: int = 20, logger=None) -> None:
    """Poll RDS until the DB cluster no longer exists. Raises if the cluster
    enters the 'failed' state."""
    waiter = Waiter(logger)

    def predicate() -> bool:
        try:
            resp = rds_client.describe_db_clusters(DBClusterIdentifier=db_cluster_id)
            clusters = resp.get("DBClusters", [])
            if not clusters:
                return True
            status = clusters[0]["Status"]
            if status == "failed":
                raise RuntimeError(f"DB cluster {db_cluster_id} entered failed state")
            return False
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "DBClusterNotFoundFault":
                return True
            raise

    waiter.wait_for(predicate, f"RDS cluster '{db_cluster_id}' deletion", timeout, interval)

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

from utils import ensure_dir


class CheckpointStore:
    """SQLite-backed record of resources this engine has already deleted,
    keyed by (account, region, service, resource_id) plus an optional
    fingerprint. Lets a run that crashes or is restarted skip work it
    already completed, and gives operators a durable audit trail across
    runs.

    The fingerprint guards against identifier reuse: a resource_id like an
    RDS DB instance identifier or an S3 bucket name can be reused by a
    completely different, later-created resource (e.g. every lab in a
    training environment provisioning a DB called 'database-1'). When a
    service can supply a fingerprint that changes across recreations (RDS's
    InstanceCreateTime/ClusterCreateTime, S3's bucket CreationDate), a
    checkpoint only counts as a match if the fingerprint matches too --
    otherwise the newer resource is treated as never having been deleted.
    Services with no such signal (CloudFormation, whose StackId already
    embeds a unique suffix per creation) pass an empty fingerprint, and
    matching falls back to resource_id alone, same as before.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        ensure_dir(os.path.dirname(db_path) or ".")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS deleted_resources (
                    account_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    service TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    resource_name TEXT,
                    fingerprint TEXT NOT NULL DEFAULT '',
                    deleted_at TEXT NOT NULL,
                    PRIMARY KEY (account_id, region, service, resource_id)
                )
                """
            )
            # Migrate checkpoint DBs created before the fingerprint column
            # existed -- ALTER TABLE ADD COLUMN is a no-op error if the
            # column is already there, which is fine to swallow.
            try:
                self._conn.execute(
                    "ALTER TABLE deleted_resources ADD COLUMN fingerprint TEXT NOT NULL DEFAULT ''"
                )
            except sqlite3.OperationalError:
                pass

    def is_deleted(self, account_id: str, region: str, service: str, resource_id: str, fingerprint: str = "") -> bool:
        """True only if this exact resource_id was previously recorded as
        deleted AND, when both sides have a fingerprint, the fingerprints
        match. A stored row with no fingerprint (legacy row, or a service
        that doesn't supply one) still matches on resource_id alone, so
        CloudFormation/older checkpoint data behave exactly as before."""
        account_id = account_id or "unknown"
        with self._lock:
            cur = self._conn.execute(
                "SELECT fingerprint FROM deleted_resources WHERE account_id=? AND region=? AND service=? AND resource_id=?",
                (account_id, region, service, resource_id),
            )
            row = cur.fetchone()
        if row is None:
            return False
        stored_fingerprint = row[0] or ""
        if stored_fingerprint and fingerprint:
            return stored_fingerprint == fingerprint
        return True

    def mark_deleted(
        self,
        account_id: str,
        region: str,
        service: str,
        resource_id: str,
        resource_name: str = "",
        fingerprint: str = "",
    ) -> None:
        account_id = account_id or "unknown"
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO deleted_resources
                    (account_id, region, service, resource_id, resource_name, fingerprint, deleted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    region,
                    service,
                    resource_id,
                    resource_name,
                    fingerprint,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

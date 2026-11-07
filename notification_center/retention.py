"""Bounded retention for disposable Notice Place audit detail.

The events and resolution_events tables are replay guards and are deliberately
never pruned here. Incident rows, delivery keys/receipts, STOP state, mutes and
runtime settings are also outside this module's deletion scope.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

DEFAULT_RETENTION_DAYS = 7
DEFAULT_BATCH_SIZE = 100
RETENTION_SETTING_KEY = "history_retention_days"


@dataclass(frozen=True)
class RetentionResult:
    cutoff: float
    retention_days: int
    eligible: int
    protected: int
    deleted: int
    dry_run: bool


class RetentionWorker:
    """One short transaction per invocation; call from the existing worker loop."""

    def __init__(self, connection: Any, *, batch_size: int = DEFAULT_BATCH_SIZE,
                 retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        if not 1 <= int(batch_size) <= 1000:
            raise ValueError("batch_size must be between 1 and 1000")
        if not 1 <= int(retention_days) <= 3650:
            raise ValueError("retention_days must be between 1 and 3650")
        self.connection = connection
        self.batch_size = int(batch_size)
        self.retention_days = int(retention_days)

    def _effective_days(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM runtime_settings WHERE key = ?", (RETENTION_SETTING_KEY,)
        ).fetchone()
        if row is None:
            return self.retention_days
        value = row[0]
        try:
            days = int(value)
        except (TypeError, ValueError):
            return self.retention_days
        return days if 1 <= days <= 3650 else self.retention_days

    def run_batch(self, *, now: float | None = None, dry_run: bool = True) -> RetentionResult:
        days = self._effective_days()
        cutoff = (time.time() if now is None else float(now)) - days * 86400
        eligible_sql = """
            SELECT a.id
            FROM audit_events a
            JOIN incidents i ON i.id = a.incident_id
            WHERE a.created_at < ?
              AND i.state IN ('resolved', 'closed', 'expired', 'archived')
              AND NOT EXISTS (
                  SELECT 1 FROM deliveries d
                  WHERE d.incident_id = i.id
                    AND lower(d.status) IN ('pending', 'queued', 'sending', 'unknown', 'inflight', 'claimed')
              )
              AND NOT EXISTS (
                  SELECT 1 FROM human_requests h
                  WHERE h.incident_id = i.id
                    AND (lower(h.state) IN ('pending', 'sending', 'unknown', 'open')
                         OR lower(coalesce(h.task_control_state, 'clear')) IN
                            ('pending', 'sending', 'unknown', 'requested', 'stop'))
              )
            ORDER BY a.created_at, a.id
        """
        eligible = int(self.connection.execute(
            f"SELECT count(*) FROM ({eligible_sql})", (cutoff,)
        ).fetchone()[0])
        total_old = int(self.connection.execute(
            "SELECT count(*) FROM audit_events WHERE created_at < ?", (cutoff,)
        ).fetchone()[0])
        protected = max(0, total_old - eligible)
        deleted = 0
        if not dry_run and eligible:
            # Recheck all protections in the DELETE statement itself so an
            # incident becoming active after preview cannot race the purge.
            with self.connection:
                result = self.connection.execute(
                    f"DELETE FROM audit_events WHERE id IN ({eligible_sql} LIMIT ?)",
                    (cutoff, self.batch_size),
                )
            deleted = max(0, int(result.rowcount))
        return RetentionResult(cutoff, days, eligible, protected, deleted, bool(dry_run))

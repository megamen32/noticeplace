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
CURSOR_SETTING_KEY = "history_retention_cursor"

# These audit rows are queried as durable behavioral guards by Core/lifecycle.
REPLAY_GUARD_AUDIT_TYPES = (
    "telegram_ai_requested",
    "health_notification_decision",
    "health_notification_waiting_ai",
    "health_controller_archive",
)


@dataclass(frozen=True)
class RetentionResult:
    cutoff: float
    retention_days: int
    eligible: int
    protected: int
    deleted: int
    dry_run: bool
    has_more: bool = False
    full_preview: bool = False


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

    def run_batch(self, *, now: float | None = None, dry_run: bool = True,
                  full_preview: bool = False) -> RetentionResult:
        days = self._effective_days()
        cutoff = (time.time() if now is None else float(now)) - days * 86400
        replay_types = ",".join("?" for _ in REPLAY_GUARD_AUDIT_TYPES)
        cursor_row = self.connection.execute(
            "SELECT value FROM runtime_settings WHERE key = ?", (CURSOR_SETTING_KEY,)
        ).fetchone()
        cursor = str(cursor_row[0]) if cursor_row else ""

        def sample_after(value: str, limit: int):
            return self.connection.execute(
                "SELECT id, created_at, incident_id, type FROM audit_events "
                "WHERE id > ? ORDER BY id LIMIT ?", (value, limit)
            ).fetchall()

        if full_preview:
            # Intentionally exhaustive operator preview; never used by apply.
            old_total = int(self.connection.execute(
                "SELECT count(*) FROM audit_events WHERE created_at < ?", (cutoff,)
            ).fetchone()[0])
            eligible = int(self.connection.execute(
                "SELECT count(*) FROM audit_events a JOIN incidents i ON i.id=a.incident_id "
                "WHERE a.created_at < ? AND a.type NOT IN (" + replay_types + ") "
                "AND i.state IN ('resolved','closed','expired','archived') "
                "AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.incident_id=i.id "
                "AND lower(d.status) NOT IN ('sent','failed','cancelled','superseded')) "
                "AND NOT EXISTS (SELECT 1 FROM human_requests h WHERE h.incident_id=i.id "
                "AND (lower(h.state) IN ('pending','sending','unknown','uncertain','open') "
                "OR lower(coalesce(h.task_control_state,'clear')) IN ('pending','sending','unknown','uncertain','requested','stop') "
                "OR (lower(coalesce(h.task_control_state,'clear'))='acknowledged' "
                "AND lower(coalesce(h.task_control_kind,''))='hold' AND h.task_resume_ref IS NULL)))",
                (cutoff, *REPLAY_GUARD_AUDIT_TYPES),
            ).fetchone()[0])
            return RetentionResult(cutoff, days, eligible, max(0, old_total - eligible), 0, True,
                                   full_preview=True)

        # Keyset sampling bounds every ordinary preview/apply read to one page.
        rows = sample_after(cursor, self.batch_size + 1)
        if not rows and cursor:
            # Start the next bounded sweep; do not scan from the beginning here.
            if not dry_run:
                with self.connection:
                    self.connection.execute(
                        "INSERT INTO runtime_settings(key,value,updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                        (CURSOR_SETTING_KEY, "", time.time()),
                    )
            return RetentionResult(cutoff, days, 0, 0, 0, bool(dry_run), has_more=False)
        has_more = len(rows) > self.batch_size
        page = rows[:self.batch_size]
        ids = [str(row[0]) for row in page]
        if not ids:
            return RetentionResult(cutoff, days, 0, 0, 0, bool(dry_run))
        marks = ",".join("?" for _ in ids)
        sample_old = sum(1 for row in page if float(row[1]) < cutoff)
        eligible_in_page = self.connection.execute(
            "SELECT count(*) FROM audit_events a JOIN incidents i ON i.id=a.incident_id "
            f"WHERE a.id IN ({marks}) AND a.created_at < ? AND a.type NOT IN ({replay_types}) "
            "AND i.state IN ('resolved','closed','expired','archived') "
            "AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.incident_id=i.id "
            "AND lower(d.status) NOT IN ('sent','failed','cancelled','superseded')) "
            "AND NOT EXISTS (SELECT 1 FROM human_requests h WHERE h.incident_id=i.id "
            "AND (lower(h.state) IN ('pending','sending','unknown','uncertain','open') "
            "OR lower(coalesce(h.task_control_state,'clear')) IN ('pending','sending','unknown','uncertain','requested','stop') "
            "OR (lower(coalesce(h.task_control_state,'clear'))='acknowledged' "
            "AND lower(coalesce(h.task_control_kind,''))='hold' AND h.task_resume_ref IS NULL)))",
            (*ids, cutoff, *REPLAY_GUARD_AUDIT_TYPES),
        ).fetchone()[0]
        eligible = int(eligible_in_page)
        protected = max(0, sample_old - eligible)
        deleted = 0
        next_cursor = str(page[-1][0])
        if not dry_run:
            # Recheck the safety predicates in the DELETE itself and persist
            # cursor progress in the same bounded transaction.
            with self.connection:
                result = self.connection.execute(
                    "DELETE FROM audit_events WHERE id IN (" + marks + ") "
                    "AND created_at < ? AND type NOT IN (" + replay_types + ") "
                    "AND incident_id IN (SELECT i.id FROM incidents i WHERE i.state IN ('resolved','closed','expired','archived') "
                    "AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.incident_id=i.id "
                    "AND lower(d.status) NOT IN ('sent','failed','cancelled','superseded')) "
                    "AND NOT EXISTS (SELECT 1 FROM human_requests h WHERE h.incident_id=i.id "
                    "AND (lower(h.state) IN ('pending','sending','unknown','uncertain','open') "
                    "OR lower(coalesce(h.task_control_state,'clear')) IN ('pending','sending','unknown','uncertain','requested','stop') "
                    "OR (lower(coalesce(h.task_control_state,'clear'))='acknowledged' "
                    "AND lower(coalesce(h.task_control_kind,''))='hold' AND h.task_resume_ref IS NULL))))",
                    (*ids, cutoff, *REPLAY_GUARD_AUDIT_TYPES),
                )
                deleted = max(0, int(result.rowcount))
                self.connection.execute(
                    "INSERT INTO runtime_settings(key,value,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                    (CURSOR_SETTING_KEY, next_cursor, time.time()),
                )
        return RetentionResult(cutoff, days, eligible, protected, deleted, bool(dry_run), has_more=has_more)

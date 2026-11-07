import sqlite3

from notification_center.retention import RetentionWorker

NOW = 2_000_000_000.0


def database():
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE runtime_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE incidents(id TEXT PRIMARY KEY, state TEXT NOT NULL);
        CREATE TABLE audit_events(id TEXT PRIMARY KEY, incident_id TEXT, created_at REAL NOT NULL, type TEXT NOT NULL);
        CREATE TABLE deliveries(incident_id TEXT, status TEXT NOT NULL);
        CREATE TABLE human_requests(incident_id TEXT, state TEXT, task_control_state TEXT,
                                    task_control_kind TEXT, task_resume_ref TEXT);
        CREATE TABLE events(idempotency_key TEXT PRIMARY KEY, payload_json TEXT);
        CREATE TABLE resolution_events(idempotency_key TEXT PRIMARY KEY, payload_json TEXT);
        CREATE TABLE notification_mutes(dedup_key TEXT PRIMARY KEY);
    """)
    return db


def add_incident(db, name, state, *, audit=True, audit_type="ordinary"):
    db.execute("INSERT INTO incidents VALUES (?, ?)", (name, state))
    if audit:
        db.execute("INSERT INTO audit_events VALUES (?, ?, ?, ?)",
                   ("audit-" + name, name, NOW - 9 * 86400, audit_type))


def test_preview_and_bounded_purge_preserve_uncertain_stop_and_replay_guards():
    db = database()
    add_incident(db, "done1", "resolved")
    add_incident(db, "done2", "closed")
    add_incident(db, "active", "open")
    add_incident(db, "uncertain", "resolved")
    add_incident(db, "stopped", "resolved")
    add_incident(db, "guarded", "resolved", audit_type="telegram_ai_requested")
    db.execute("INSERT INTO deliveries VALUES ('uncertain', 'uncertain')")
    db.execute("INSERT INTO human_requests VALUES ('stopped', 'resolved', 'acknowledged', 'hold', NULL)")
    db.execute("INSERT INTO events VALUES ('accepted-event', '{\"once\":true}')")
    db.execute("INSERT INTO resolution_events VALUES ('accepted-resolution', '{\"once\":true}')")
    db.execute("INSERT INTO notification_mutes VALUES ('mute-1')")
    worker = RetentionWorker(db, batch_size=1)

    preview = worker.run_batch(now=NOW)
    assert preview.dry_run and preview.eligible <= 1 and preview.protected <= 1
    assert preview.has_more
    assert db.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 6

    deleted = 0
    for _ in range(10):
        result = worker.run_batch(now=NOW, dry_run=False)
        deleted += result.deleted
        if not result.has_more and result.deleted == 0:
            break
    assert deleted == 2
    assert db.execute("SELECT count(*) FROM audit_events WHERE id IN ('audit-active','audit-uncertain','audit-stopped','audit-guarded')").fetchone()[0] == 4
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM resolution_events").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM notification_mutes").fetchone()[0] == 1


def test_runtime_retention_setting_and_future_rows():
    db = database()
    add_incident(db, "resolved", "resolved")
    db.execute("INSERT INTO audit_events VALUES ('fresh', 'resolved', ?, 'ordinary')", (NOW - 2 * 86400,))
    db.execute("INSERT INTO runtime_settings VALUES ('history_retention_days', '3', ?)", (NOW,))
    result = RetentionWorker(db).run_batch(now=NOW)
    assert result.retention_days == 3
    assert result.eligible == 1


def test_apply_uses_bounded_keyset_page_without_full_history_count():
    db = database()
    for index in range(500):
        add_incident(db, f"i{index:04}", "resolved")
    statements = []
    db.set_trace_callback(statements.append)
    result = RetentionWorker(db, batch_size=7).run_batch(now=NOW, dry_run=False)
    assert result.deleted <= 7
    assert any("SELECT id, created_at, incident_id, type FROM audit_events" in sql and "LIMIT 8" in sql
               for sql in statements)
    assert not any("COUNT(*) FROM audit_events WHERE created_at" in sql for sql in statements)


def test_full_preview_is_explicit_and_invalid_batches_are_rejected():
    db = database()
    add_incident(db, "resolved", "resolved")
    full = RetentionWorker(db).run_batch(now=NOW, full_preview=True)
    assert full.full_preview and full.eligible == 1 and full.deleted == 0
    for size in (0, 1001):
        try:
            RetentionWorker(db, batch_size=size)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid batch size accepted")

import sqlite3

from notification_center.retention import RetentionWorker


NOW = 2_000_000_000.0


def database():
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE runtime_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);
        CREATE TABLE incidents(id TEXT PRIMARY KEY, state TEXT NOT NULL);
        CREATE TABLE audit_events(id TEXT PRIMARY KEY, incident_id TEXT, created_at REAL NOT NULL);
        CREATE TABLE deliveries(incident_id TEXT, status TEXT NOT NULL);
        CREATE TABLE human_requests(incident_id TEXT, state TEXT, task_control_state TEXT);
        CREATE TABLE events(idempotency_key TEXT PRIMARY KEY, payload_json TEXT);
        CREATE TABLE resolution_events(idempotency_key TEXT PRIMARY KEY, payload_json TEXT);
        CREATE TABLE notification_mutes(dedup_key TEXT PRIMARY KEY);
    """)
    return db


def add_incident(db, name, state, *, audit=True):
    db.execute("INSERT INTO incidents VALUES (?, ?)", (name, state))
    if audit:
        db.execute("INSERT INTO audit_events VALUES (?, ?, ?)", ("audit-" + name, name, NOW - 9 * 86400))


def test_preview_and_bounded_purge_preserve_state_and_replay_guards():
    db = database()
    add_incident(db, "done1", "resolved")
    add_incident(db, "done2", "closed")
    add_incident(db, "active", "open")
    add_incident(db, "unknown", "resolved")
    add_incident(db, "stopped", "resolved")
    db.execute("INSERT INTO deliveries VALUES ('unknown', 'unknown')")
    db.execute("INSERT INTO human_requests VALUES ('stopped', 'resolved', 'stop')")
    db.execute("INSERT INTO events VALUES ('accepted-event', '{\"once\":true}')")
    db.execute("INSERT INTO resolution_events VALUES ('accepted-resolution', '{\"once\":true}')")
    db.execute("INSERT INTO notification_mutes VALUES ('mute-1')")
    worker = RetentionWorker(db, batch_size=1)

    preview = worker.run_batch(now=NOW)
    assert preview.dry_run and preview.eligible == 2 and preview.protected == 3
    assert db.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 5

    first = worker.run_batch(now=NOW, dry_run=False)
    assert first.deleted == 1
    assert db.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 4
    second = worker.run_batch(now=NOW, dry_run=False)
    assert second.deleted == 1
    assert db.execute("SELECT count(*) FROM audit_events WHERE id IN ('audit-active','audit-unknown','audit-stopped')").fetchone()[0] == 3
    assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM resolution_events").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM notification_mutes").fetchone()[0] == 1


def test_runtime_retention_setting_and_future_rows():
    db = database()
    add_incident(db, "resolved", "resolved")
    db.execute("INSERT INTO audit_events VALUES ('fresh', 'resolved', ?)", (NOW - 2 * 86400,))
    db.execute("INSERT INTO runtime_settings VALUES ('history_retention_days', '3', ?)", (NOW,))
    result = RetentionWorker(db).run_batch(now=NOW)
    assert result.retention_days == 3
    assert result.eligible == 1
    assert result.protected == 0


def test_rejects_unbounded_or_invalid_batch_configuration():
    db = database()
    for size in (0, 1001):
        try:
            RetentionWorker(db, batch_size=size)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid batch size accepted")

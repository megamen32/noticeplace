"""Archive selection must not monopolize the center on accumulated history."""
import json
from unittest.mock import patch
from notification_center.core import NotificationCenter
from notification_center.health_session_lifecycle import HealthSessionArchiver


def test_archive_selection_has_finite_work_on_unrelated_audit_history(tmp_path):
    center = NotificationCenter(tmp_path / "history.sqlite", {})
    try:
        center._connection.execute("INSERT INTO incidents(id,project,recipient,kind,severity,title,body,dedup_key,state,occurrences,created_at,updated_at,event_type) VALUES ('resolved','qa','me','incident','notice','test','','qa','resolved',1,1,1,'health.degraded')")
        center._connection.executemany("INSERT INTO events(idempotency_key,event_id,incident_id,payload_json,created_at,event_type) VALUES (?,?,?,?,?,?)", [
            (str(i),str(i),'resolved',json.dumps({'harness':'codex','session_id':f'native-{i}'}),i,'health.diagnosis_session_started') for i in range(80)])
        center._connection.executemany("INSERT INTO audit_events(id,incident_id,type,actor,payload_json,created_at) VALUES (?,?,?,?,?,?)", [
            (str(i),'unrelated','health_controller_archive','qa','{}',i) for i in range(8000)])
        center._connection.commit()
        steps = 0
        def bounded():
            nonlocal steps
            steps += 1000
            return steps > 80000
        center._connection.set_progress_handler(bounded, 1000)
        # No native action is needed to exercise the production candidate SQL.
        with patch.object(HealthSessionArchiver, '_eligible', return_value=False):
            assert HealthSessionArchiver(center).run_once() == 0
        assert steps <= 80000
    finally:
        center._connection.set_progress_handler(None, 0)
        center._connection.close()

"""A lost executor receipt is observation work, never a new launch."""
import time
from unittest.mock import Mock

import pytest

from notification_center.core import NotificationCenter
from notification_center.gptadmin_agent import GptAdminAdmissionUnavailable
from notification_center.http_api import DeliveryWorker


@pytest.mark.parametrize('error,outcome', [
    (TimeoutError('receipt lost after admission'), 'uncertain'),
    (RuntimeError('poll returned invalid JSON'), 'uncertain'),
    (GptAdminAdmissionUnavailable('Hub rejected admission'), 'queued'),
])
def test_agent_exception_preserves_admission_boundary(tmp_path, error, outcome):
    center = NotificationCenter(tmp_path / 'notice.db',
        {'source': {'project': 'agent-fixture', 'max_severity': 'critical',
                    'agent_jobs': ['health-diagnosis']}}, default_quiet_hours=[])
    try:
        created = center.create_event('source', 'event', {
            'schema': 'notify.event.v1', 'kind': 'incident', 'project': 'agent-fixture',
            'recipient': 'me', 'severity': 'critical', 'title': 'Agent fixture',
            'dedup_key': 'one', 'event_type': 'health.degraded', 'source_id': 'probe',
            'host_id': 'fixture', 'signal_type': 'failure', 'correlation_id': 'episode'})
        delivery_id = created['agent_job_delivery_id']
        adapter = Mock()
        adapter.send_with_progress.side_effect = error
        worker = DeliveryWorker(center, Mock(), agent_jobs={'health-diagnosis': adapter})
        worker.deliver(center.claim_due_deliveries(delivery_id=delivery_id)[0])
        row = center._connection.execute('SELECT status FROM deliveries WHERE id=?',
                                        (delivery_id,)).fetchone()
        assert row['status'] == outcome
        if outcome == 'uncertain':
            assert center.claim_due_deliveries(now_epoch=time.time()+3600, delivery_id=delivery_id) == []
            adapter.send_with_progress.assert_called_once()
    finally:
        center._connection.close()

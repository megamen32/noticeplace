from notification_center.incident_model_policy import DEFAULT_INCIDENT_MODEL, PROJECT_ROUTE_INTENT
from notification_center.health_workflow import HEALTH_EXECUTION_PROFILE, health_launch_execution
from notification_center.agent_job_helper import _health_model_route


def test_no_local_execution_model():
    assert DEFAULT_INCIDENT_MODEL == ''
    assert HEALTH_EXECUTION_PROFILE['model'] == ''
    assert PROJECT_ROUTE_INTENT == 'herder/project-route'


def test_multisegment_receipt_roundtrip():
    route = 'omniroute/cx/gpt-6-luna'
    assert _health_model_route(health_launch_execution(route)) == route

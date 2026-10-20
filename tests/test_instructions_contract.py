"""Инструкции /v1/instructions обязаны быть машинно-точными для ИИ-продюсеров.

Инцидент 10.10: агент прочитал «отправьте health.degraded» и послал
kind='health.degraded' (отклонён), а «POST /health/verification» вёл на
несуществующие эндпоинты. Текст ниже фиксирует точный контракт.
"""

from notification_center.instructions import noticeplace_instructions


def test_instructions_state_exact_event_contract():
    data = noticeplace_instructions()
    contract = data["api"]["event_contract"]
    assert "kind='incident'" in contract
    assert "event_type='health.degraded'" in contract
    assert "НЕ kind='health.degraded'" in contract


def test_instructions_recovery_names_real_resolve_flow():
    recovery = data_recovery = noticeplace_instructions()["api"]["recovery"]
    assert "action='resolve'" in data_recovery
    assert "НЕТ" in recovery  # для обычных инцидентов /health/* эндпоинтов нет — явно
    # Repair-first: an executor resolves a health incident only with an
    # independent source verification, never with the plan-selection gate.
    assert "независимая проверка источника" in recovery
    assert "source_id и verifier_id различаются" in recovery
    assert "requires explicit plan selection" not in recovery


def test_instructions_name_the_repair_first_question_and_receipt_seams():
    api = noticeplace_instructions()["api"]
    assert "GET /v1/human-requests?state=pending" in api["pending_questions"]
    assert "POST /v1/human-requests/{request_id}/resolve" in api["answer_question"]
    assert "POST /v1/incidents/{incident_id}/choice-result" in api["answer_choice"]
    assert "/health/progress" in api["incident_progress"]
    repair = noticeplace_instructions()["automatic_repair"]
    assert "human_request_create" in repair
    assert "автопилот" in repair


def test_instructions_automatic_repair_names_wire_fields():
    repair = noticeplace_instructions()["automatic_repair"]
    assert "kind='incident'" in repair and "event_type='health.degraded'" in repair
    for field in ("source_id", "host_id", "signal_type", "correlation_id"):
        assert field in repair


def test_instructions_name_target_transport_owners_and_capability_dependent_channels():
    data = noticeplace_instructions()
    boundary = data["communications_boundary"]
    target = boundary["target"]
    assert "Notice Place API/MCP" in target
    assert "UserIO" in target
    assert "Agent Herder alone resumes" in target
    assert {row["name"] for row in boundary["channels"]} == {
        "Telegram", "Matrix", "Telemost", "SIM/GSM2SIP"
    }
    assert all(row["capability_dependent"] is True for row in boundary["channels"])
    assert "do not route through Asterisk" in boundary["routing"]
    assert "legacy" in boundary["installed_compatibility"]
    states = data["delivery_state_semantics"]
    for state in ("accepted/stored", "ringing", "answered", "confirmed human answer", "resumed"):
        assert state in states
    assert "unknown is not success" in states

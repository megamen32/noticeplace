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
    assert "НЕТ" in recovery  # /health/* эндпоинтов нет — явно
    assert "requires explicit plan selection" in recovery


def test_instructions_automatic_repair_names_wire_fields():
    repair = noticeplace_instructions()["automatic_repair"]
    assert "kind='incident'" in repair and "event_type='health.degraded'" in repair
    for field in ("source_id", "host_id", "signal_type", "correlation_id"):
        assert field in repair

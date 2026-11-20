"""Bounded policy editor for the SSO operator console."""
from __future__ import annotations

import math
import re
from typing import Any

from .core import IdempotencyConflict, NotificationCenter, ValidationError


MAX_STEPS = 32
_TARGET_FIELDS = {"matrix": ("room_id", "target"), "telegram": ("chat_id", "topic_id")}
_LEGACY_FIELDS = ("chat_id", "topic_id", "delay_seconds")
_GENERIC_FIELDS = {"id", "platform", "action", "target", "previous_step_id", "retry_interval_seconds", "max_repeats"}
_MATRIX_ROOM = re.compile(r"![^\s\x00-\x1f\x7f]{1,254}")
_MATRIX_USER = re.compile(r"@[^:\s\x00-\x1f\x7f]+:[^\s\x00-\x1f\x7f]+")
_TELEGRAM_CHAT = re.compile(r"-?[1-9][0-9]{0,18}|@[a-zA-Z0-9_]{5,32}")


class ConsumerNotFound(ValidationError):
    """The consumer no longer exists."""


def _editable_target(platform: str, target: dict[str, Any]) -> dict[str, Any]:
    return {
        key: target[key] for key in _TARGET_FIELDS.get(platform, ())
        if type(target.get(key)) in (str, int) and target[key] != ""
    }


def _view(consumer: dict[str, Any], policy: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    steps = []
    for entry in consumer["policy"] if policy is None else policy:
        target = entry["target"]
        if entry.get("platform"):
            step = {
                "id": entry.get("id") or entry["step_id"], "platform": entry["platform"],
                "action": entry["action"], "previous_step_id": entry.get("previous_step_id"),
                "retry_interval_seconds": entry["retry_interval_seconds"], "max_repeats": entry["max_repeats"],
                "target": _editable_target(entry["platform"], target),
            }
        else:
            step = {"kind": entry["kind"], "enabled": entry["enabled"]}
            step.update({key: target[key] for key in _LEGACY_FIELDS if type(target.get(key)) in (int, float)})
        steps.append(step)
    root = next((step for step in steps if "platform" in step and step["previous_step_id"] is None), None)
    if root is not None and all("platform" in step for step in steps):
        by_parent = {step["previous_step_id"]: step for step in steps}
        ordered = [root]
        while len(ordered) < len(steps) and ordered[-1]["id"] in by_parent:
            ordered.append(by_parent[ordered[-1]["id"]])
        if len({step["id"] for step in ordered}) == len(steps):
            steps = ordered
    return {
        **{key: consumer[key] for key in ("id", "project", "name", "revision", "profile_type", "profile_key", "quiet_hours")},
        "editable": consumer["profile_type"] != "builtin",
        "policy": steps,
    }


def _timer(value: Any, title: str) -> None:
    if type(value) not in (int, float) or not 0 < value <= 31_536_000 or not math.isfinite(value):
        raise ValidationError(f"{title}: укажите положительное число секунд в пределах года.")


def _target(platform: str, value: Any, old: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - set(_TARGET_FIELDS.get(platform, ())):
        raise ValidationError("У шага есть неподдерживаемые параметры адресата.")
    public_old = _editable_target(platform, old)
    for field, supplied in value.items():
        if type(supplied) is type(public_old.get(field)) and supplied == public_old.get(field):
            continue
        if field == "room_id":
            if not isinstance(supplied, str) or not _MATRIX_ROOM.fullmatch(supplied):
                raise ValidationError("Укажите идентификатор комнаты Matrix, начинающийся с !.")
        elif field == "target":
            if not isinstance(supplied, str) or len(supplied) > 255 or not _MATRIX_USER.fullmatch(supplied):
                raise ValidationError("Укажите пользователя Matrix в формате @имя:сервер.")
        elif field == "chat_id":
            if type(supplied) not in (int, str) or not _TELEGRAM_CHAT.fullmatch(str(supplied)):
                raise ValidationError("Укажите числовой ID или @имя чата Telegram.")
        elif type(supplied) is not int or not 0 < supplied <= 2**53 - 1:
            raise ValidationError("Укажите положительный ID темы Telegram.")

    if platform == "matrix":
        if "target" in value and "room_id" not in value:
            raise ValidationError("Для адресата Matrix укажите комнату.")
        if value.get("room_id") != public_old.get("room_id"):
            if set(old) - set(_TARGET_FIELDS["matrix"]):
                raise ValidationError("Комната связана с настройками коннектора. Добавьте отдельный шаг для другой комнаты.")
            return dict(value)
        if old.get("participants") and value.get("target") and value["target"] not in old["participants"]:
            raise ValidationError("Текущий маршрут Matrix не разрешает этого адресата.")
    if platform == "telegram":
        if "topic_id" in value and "chat_id" not in value:
            raise ValidationError("Для темы Telegram укажите чат.")
        if str(value.get("chat_id")) != str(public_old.get("chat_id")):
            return dict(value)
        if value.get("topic_id") != public_old.get("topic_id"):
            old = {key: item for key, item in old.items() if key != "message_thread_id"}
    return {**{key: item for key, item in old.items() if key not in public_old}, **value}


def _draft(policy: Any, consumer: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(policy, list) or not 1 <= len(policy) <= MAX_STEPS or any(not isinstance(step, dict) for step in policy):
        raise ValidationError(f"В цепочке должно быть от 1 до {MAX_STEPS} шагов.")
    existing = {
        (step["id"], step["platform"], step["action"]): step["target"]
        for step in consumer["policy"] if step.get("platform")
    }
    if all("kind" in step for step in policy):
        for step in policy:
            if set(step) - {"kind", "enabled", *_LEGACY_FIELDS} or type(step.get("enabled", True)) is not bool:
                raise ValidationError("Проверьте поля шага прежнего формата.")
            for field in ("chat_id", "topic_id"):
                if field in step and (type(step[field]) is not int or not -(2**63) <= step[field] < 2**63):
                    raise ValidationError("ID чата и темы должны быть целыми числами.")
            if "delay_seconds" in step:
                _timer(step["delay_seconds"], "Задержка")
        return policy

    result = []
    previous = None
    for step in policy:
        step_id, platform, action = (step.get(key) for key in ("id", "platform", "action"))
        if (set(step) - _GENERIC_FIELDS or not isinstance(step_id, str) or not 1 <= len(step_id) <= 128
                or any(ord(char) < 32 for char in step_id) or not isinstance(platform, str)
                or not isinstance(action, str) or step.get("previous_step_id") != previous):
            raise ValidationError("Проверьте порядок и поля шагов цепочки.")
        _timer(step.get("retry_interval_seconds"), "Интервал")
        repeats = step.get("max_repeats")
        if type(repeats) is not int or not 1 <= repeats <= 100:
            raise ValidationError("Число попыток на шаг должно быть от 1 до 100.")
        destination = _target(platform, step.get("target"), existing.get((step_id, platform, action), {}))
        result.append({**step, "target": destination})
        previous = step_id
    root = result[0]
    if root["platform"] == "matrix" and (room := root["target"].get("room_id")):
        for step in result[1:]:
            if step["platform"] != "matrix":
                continue
            members = root["target"].get("participants") or []
            callee = step["target"].get("target")
            if step["target"].get("room_id") != room or (members and callee and callee not in members):
                raise ValidationError("Первый шаг Matrix задаёт комнату и допустимых адресатов для всей цепочки.")
    return result


def edit_policy(
    center: NotificationCenter, consumer_id: str, form: dict[str, Any] | None = None,
    *, preview: bool = False, actor: str = "sso:operator",
) -> dict[str, Any]:
    consumer = center.get_consumer(consumer_id, nested_targets=True)
    if consumer is None:
        raise ConsumerNotFound("Консьюмер не найден.")
    if form is None:
        return _view(consumer)
    if consumer["profile_type"] == "builtin":
        raise ValidationError("Цепочка встроенного профиля доступна для просмотра.")
    if not isinstance(form, dict) or set(form) != {"revision", "policy"} or type(form["revision"]) is not int:
        raise ValidationError("Некорректный запрос редактора цепочки.")
    revision = form["revision"]
    if revision != consumer["revision"]:
        raise IdempotencyConflict("Цепочка уже изменена. Загрузите сохранённую версию.")
    draft = _draft(form["policy"], consumer)
    normalized = center._validate_consumer_policy(draft)
    if preview:
        return _view(consumer, normalized)
    updated = center.replace_consumer_policy(consumer_id, draft, actor, expected_revision=revision)
    return _view(updated, normalized)

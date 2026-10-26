"""Pinned subscription route for NoticePlace health-incident analysis.

NoticePlace sends incident analysis through the OpenCode MiniMax subscription
provider. It does not use OmniRoute or provider/model fallbacks. Agent Herder
stays the launch and admission runtime and keeps its own allow/deny decision.

This module is the single source of truth for the route contract:

- the settings key, the one permitted route and the console suggestion;
- a one-way migration for the former OmniRoute combo default when reading the
  saved preference for future incidents; and
- the strict normalizer used by settings writes and delivery snapshots.
  Existing delivery snapshots are never migrated.

Nothing here resolves credentials, talks to a provider, or starts a session.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

INCIDENT_MODEL_SETTING_KEY = "health_incident_model"

# This NoticePlace project is explicitly pinned to its MiniMax subscription.
DEFAULT_INCIDENT_MODEL = "minimax-coding-plan/MiniMax-M3.1-Flash"
LEGACY_INCIDENT_MODELS = (
    "omniroute/minimax-combo.m3.1",
    "omniroute/minimax-combo-m3.1",
)

MAX_INCIDENT_MODEL_LENGTH = 256
# The signed health context carries the route in a bounded 128-character
# field. A longer route would reach the launch truncated and would run a
# different model than the operator saved, so it is rejected instead.
MAX_LAUNCH_ROUTE_LENGTH = 128

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

# The console shows the same single route that the launch contract permits.
KNOWN_INCIDENT_MODELS: tuple[dict[str, Any], ...] = (
    {
        "value": DEFAULT_INCIDENT_MODEL,
        "label": "OpenCode · MiniMax по подписке · M3.1 Flash",
        "default": True,
        "manual_only": False,
    },
)


def incident_model_options() -> list[dict[str, Any]]:
    """Return the known routes as independent display-safe copies."""
    return [dict(option) for option in KNOWN_INCIDENT_MODELS]


def default_incident_model() -> str:
    """Return the route used for new health analyses when nothing is saved."""
    return DEFAULT_INCIDENT_MODEL


def incident_model_error(value: Any) -> str | None:
    """Return why a route is not allowed, or None when the route is usable.

    The route contract is identical at every boundary (write path, read path,
    delivery snapshot, signed event):

    - the value is not empty and carries no control characters;
    - it is trimmed and at most 256 characters long;
    - it is exactly the project-approved MiniMax subscription route.
    """
    raw = "" if value is None else str(value)
    if not raw.strip():
        return "Введите маршрут модели анализа инцидентов"
    if _CONTROL_CHARACTERS.search(raw):
        return "Маршрут модели анализа инцидентов не должен содержать управляющие символы"
    route = raw.strip()
    if len(route) > MAX_INCIDENT_MODEL_LENGTH:
        return f"Маршрут модели анализа инцидентов длиннее {MAX_INCIDENT_MODEL_LENGTH} символов"
    if len(route) > MAX_LAUNCH_ROUTE_LENGTH:
        return (
            f"Маршрут модели анализа инцидентов длиннее {MAX_LAUNCH_ROUTE_LENGTH} символов: "
            "более длинное значение обрезается в подписанном контексте запуска"
        )
    if route != DEFAULT_INCIDENT_MODEL:
        return (
            "Для диагностики инцидентов разрешён только маршрут "
            f"{DEFAULT_INCIDENT_MODEL}; OmniRoute и другие маршруты запрещены"
        )
    return None


def is_incident_model(value: Any) -> bool:
    """Return whether a value is a usable incident-analysis route."""
    return incident_model_error(value) is None


def normalize_incident_model(value: Any) -> str:
    """Return the exact route to store and launch, or reject it with the reason."""
    error = incident_model_error(value)
    if error is not None:
        raise ValueError(error)
    return str(value).strip()


def incident_model_label(value: Any) -> str:
    """Return the human label of a known route, or the raw route for anything else."""
    candidate = str(value or "").strip()
    for option in KNOWN_INCIDENT_MODELS:
        if candidate == str(option["value"]):
            return str(option["label"])
    return candidate


def read_incident_model(center: Any) -> str:
    """Return the effective route for future health analyses.

    The exact former default is migrated in memory for future incidents only.
    Existing delivery snapshots still use the strict normalizer and fail
    closed, so this cannot reroute an already scheduled incident.
    """
    stored = center.get_runtime_setting(INCIDENT_MODEL_SETTING_KEY)
    if stored is None or not str(stored).strip():
        return default_incident_model()
    if str(stored).strip() in LEGACY_INCIDENT_MODELS:
        return default_incident_model()
    return normalize_incident_model(stored)


def saved_incident_model(center: Any) -> str | None:
    """Return the raw stored value, or None when the operator never saved one."""
    stored = center.get_runtime_setting(INCIDENT_MODEL_SETTING_KEY)
    if stored is None or not str(stored).strip():
        return None
    return str(stored).strip()


def write_incident_model(center: Any, value: Any) -> str:
    """Persist one hand-picked route for future health analyses."""
    normalized = normalize_incident_model(value)
    center.set_runtime_setting(INCIDENT_MODEL_SETTING_KEY, normalized)
    return normalized


def incident_model_state(center: Any) -> dict[str, Any]:
    """Return the display-safe model state for the operator console."""
    stored = saved_incident_model(center)
    migration_pending = stored in LEGACY_INCIDENT_MODELS
    error = incident_model_error(stored) if stored is not None and not migration_pending else None
    effective = default_incident_model() if stored is None or migration_pending else ("" if error else normalize_incident_model(stored))
    return {
        "value": effective,
        "label": incident_model_label(effective),
        "route": effective,
        "effective": effective,
        "default": default_incident_model(),
        "stored": stored,
        "stored_recognized": error is None,
        "stored_error": error,
        "migration_pending": migration_pending,
        "migration_message": (
            f"Старый маршрут {stored} заменён для новых разборов на "
            f"{DEFAULT_INCIDENT_MODEL}; сохранённые события не перенаправляются."
            if migration_pending else ""
        ),
        "options": incident_model_options(),
    }


def model_route_text(value: Any) -> str:
    """Return a short human route description without leaking internals."""
    route = str(value or "").strip()
    return route or "неизвестно"


def bounded_option_values(options: Any = None) -> list[str]:
    """Return the exact route strings of a bounded option list."""
    source = options if isinstance(options, (list, tuple)) else KNOWN_INCIDENT_MODELS
    values: list[str] = []
    for option in source:
        if isinstance(option, Mapping):
            value = str(option.get("value") or "").strip()
            if value:
                values.append(value)
    return values

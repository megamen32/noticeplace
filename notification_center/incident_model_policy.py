"""Hand-picked operator choice for the model that analyses health incidents.

The operator types the route by hand: any OmniRoute gateway route of the form
``omniroute/<model>`` or ``omniroute/<provider>/<model>``. Notice Place stores
that exact string and launches it with the next incident analysis. Agent
Herder stays the launch and admission runtime and keeps its own allow/deny
decision.

This module is the single source of truth for the route contract:

- the settings key, the default route and the known-route suggestions;
- the forbidden paid MiniMax subscription routes;
- the one normalizer used by the write path, the read path, the delivery
  snapshot and the signed launch event.

Nothing here resolves credentials, talks to a provider, or starts a session.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

INCIDENT_MODEL_SETTING_KEY = "health_incident_model"

# The canonical free route: the only route that both the Agent Herder
# health-launch gate and the OpenCode catalog accept.
DEFAULT_INCIDENT_MODEL = "omniroute/minimax-combo-m3.1"

ROUTE_PREFIX = "omniroute/"
MAX_INCIDENT_MODEL_LENGTH = 256
# The signed health context carries the route in a bounded 128-character
# field. A longer route would reach the launch truncated and would run a
# different model than the operator saved, so it is rejected instead.
MAX_LAUNCH_ROUTE_LENGTH = 128

# The paid MiniMax subscription stays forbidden: M3.1 is free inside MM Code.
# The check runs on the route id behind the optional gateway prefix, so the
# paid route cannot be smuggled in as "omniroute/minimax-coding-plan/...".
FORBIDDEN_ROUTE_PROVIDERS = ("minimax-coding-plan",)
FORBIDDEN_ROUTE_IDS = ("minimax/minimax-m3.1-flash",)

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

# Known routes offered as suggestions. The operator is not limited to them:
# the console field accepts every route the contract below allows.
KNOWN_INCIDENT_MODELS: tuple[dict[str, Any], ...] = (
    {
        "value": DEFAULT_INCIDENT_MODEL,
        "label": "OpenCode · OmniRoute · MiniMax M3.1 (combo)",
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

    The rules are identical at every boundary (write path, read path, delivery
    snapshot, signed event):

    - the value is not empty and carries no control characters;
    - it is trimmed and at most 256 characters long;
    - it starts with the ``omniroute/`` gateway prefix;
    - it names one model or one provider and one model;
    - it is not a forbidden paid MiniMax subscription route.
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
    # The paid-route rule runs on the route id behind the optional gateway
    # prefix, so the paid route is named as paid and not as a malformed one.
    route_id = route[len(ROUTE_PREFIX):] if route.startswith(ROUTE_PREFIX) else route
    parts = [part.strip() for part in route_id.split("/")]
    if parts and parts[0].lower() in FORBIDDEN_ROUTE_PROVIDERS:
        return "Платный маршрут MiniMax по подписке (minimax-coding-plan) запрещён"
    if "/".join(part.lower() for part in parts) in FORBIDDEN_ROUTE_IDS:
        return "Платный маршрут MiniMax по подписке (minimax/MiniMax-M3.1-Flash) запрещён"
    if not route.startswith(ROUTE_PREFIX):
        return (
            "Маршрут модели анализа инцидентов должен начинаться с "
            f"{ROUTE_PREFIX} (например {DEFAULT_INCIDENT_MODEL})"
        )
    if not all(parts) or len(parts) > 2:
        return (
            "Маршрут модели анализа инцидентов должен быть вида "
            "omniroute/<model> или omniroute/<provider>/<model>"
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

    A stored route is used exactly as the operator saved it. An invalid stored
    value is never silently replaced: the read fails with the operator reason
    so the launch fails closed and the console can show what is wrong.
    """
    stored = center.get_runtime_setting(INCIDENT_MODEL_SETTING_KEY)
    if stored is None or not str(stored).strip():
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
    error = incident_model_error(stored) if stored is not None else None
    effective = default_incident_model() if stored is None else ("" if error else stored)
    return {
        "value": effective,
        "label": incident_model_label(effective),
        "route": effective,
        "effective": effective,
        "default": default_incident_model(),
        "stored": stored,
        "stored_recognized": error is None,
        "stored_error": error,
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

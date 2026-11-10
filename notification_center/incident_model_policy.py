"""Model intent and Herder-backed display for health incident execution."""

from __future__ import annotations

import re
from typing import Any, Mapping

INCIDENT_MODEL_SETTING_KEY = "health_incident_model"

# No local default: Herder owns model selection.
DEFAULT_INCIDENT_MODEL = ""
PROJECT_ROUTE_INTENT = "herder/project-route"
LEGACY_INCIDENT_MODELS = (
    "minimax-coding-plan/MiniMax-M3.1-Flash",
    "omniroute/minimax-combo.m3.1",
    "omniroute/minimax-combo-m3.1",
    "omniroute/glm-5.3-flash-minimax-m3.1",
)

MAX_INCIDENT_MODEL_LENGTH = 256
# The signed health context carries the route in a bounded 128-character
# field. A longer route would reach the launch truncated and would run a
# different model than the operator saved, so it is rejected instead.
MAX_LAUNCH_ROUTE_LENGTH = 128

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

# The console shows the same single route that the launch contract permits.
KNOWN_INCIDENT_MODELS: tuple[dict[str, Any], ...] = ()


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
    - it is exactly the project-approved direct MiniMax route.
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
    if "/" not in route or any(c.isspace() for c in route):
        return "Некорректный маршрут модели анализа инцидентов"
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
    """Read the authoritative Herder policy, never the local setting."""
    state = incident_model_state(center)
    if not state.get("available"):
        raise ValueError(state.get("stored_error") or "Herder недоступен")
    return str(state["effective"])


def saved_incident_model(center: Any) -> str | None:
    return None


def write_incident_model(center: Any, value: Any, expected_revision: str = "") -> str:
    """Update Herder's policy with its current ETag; never write SQLite."""
    normalized = normalize_incident_model(value)
    from .herder_policy_client import HerderPolicyClient
    HerderPolicyClient().set_incident_model(normalized, expected_revision)
    return normalized


def incident_model_state(center: Any) -> dict[str, Any]:
    """Return the display-safe model state for the operator console."""
    from .herder_policy_client import HerderPolicyClient, HerderPolicyUnavailable
    try:
        policy, revision = HerderPolicyClient().read()
        execution = policy.get('incidentExecution')
        if not isinstance(execution, Mapping):
            raise HerderPolicyUnavailable('Настройки модели Herder некорректны')
        if not isinstance(execution.get('model'), str) or not isinstance(execution.get('provider'), str):
            raise HerderPolicyUnavailable('Настройки модели Herder некорректны')
        value = execution['model'].strip()
        provider = execution['provider'].strip()
        if provider and value and not value.startswith(provider + '/'):
            value = provider + '/' + value
        if not value:
            raise HerderPolicyUnavailable('В настройках Herder не указана модель')
        return {'value': value, 'label': value, 'route': value, 'effective': value,
                'default': '', 'stored': None, 'stored_recognized': True,
                'stored_error': None, 'migration_pending': False,
                'migration_message': '', 'options': [], 'available': True, 'revision': revision}
    except HerderPolicyUnavailable as error:
        return {'value': '', 'label': 'Herder недоступен', 'route': '', 'effective': '',
                'default': '', 'stored': None, 'stored_recognized': False,
                'stored_error': str(error), 'migration_pending': False,
                'migration_message': '', 'options': [], 'available': False}

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

"""Dispatch tier/effort defaults from the task-class table (PRD-CORE-290-FR03).

Belongs to the ``trw_mcp.dispatch`` package. Precedence for each of effort and
model is explicit request > operator config > table default, where the table
default is the row of the role's task class (``agents/task_policy.py``, the one
source bundled agents also use). The resolver records which source won; the
result records what was requested versus what the client was actually given.
"""

from __future__ import annotations

from trw_mcp.agents.task_policy import TASK_POLICY
from trw_mcp.agents.tier_resolver import resolve_tier
from trw_mcp.dispatch._client_spec_types import EFFORT_LEVELS
from trw_mcp.dispatch._client_specs import CLIENT_SPECS, UnknownClientError, client_spec_for
from trw_mcp.dispatch._commands import _client_effort
from trw_mcp.dispatch._roles import role_task_class
from trw_mcp.dispatch._types import DispatchRequest
from trw_mcp.models.config._fields_dispatch import DEFAULT_DISPATCH_MAX_TURNS

__all__ = ["operator_set", "policy_record", "resolve_effort", "resolve_max_turns", "resolve_model"]


def _checked(value: str, where: str) -> str:
    if value not in EFFORT_LEVELS:
        raise ValueError(f"{where} effort {value!r} is not one of {', '.join(EFFORT_LEVELS)}")
    return value


def resolve_effort(explicit: str | None, role: str | None, config_default: object) -> tuple[str | None, str]:
    """``(effort, source)``; raises ``ValueError`` for a value outside the portable ladder."""
    if explicit is not None:
        return _checked(explicit, "requested"), "request"
    if isinstance(config_default, str) and config_default:
        return _checked(config_default, "configured"), "config"
    task_class = role_task_class(role)
    if task_class:
        return TASK_POLICY[task_class].effort, "table"
    return None, "none"


def resolve_model(explicit: str | None, client: str, role: str | None, config_models: object) -> tuple[str | None, str]:
    """``(model, source)``. ``unsupported``: the role has a tier, the client has no verified map."""
    if explicit:
        return explicit, "request"
    configured = config_models.get(client) if isinstance(config_models, dict) else None
    if configured:
        return str(configured), "config"
    task_class = role_task_class(role)
    if not task_class:
        return None, "none"
    # A client without a verified tier map gets no table model: a raw tier passed as
    # --model would be an invalid model name, so TRW passes nothing and says so.
    try:
        profile = client_spec_for(client).tier_profile
    except UnknownClientError:  # trw-fail-silent-allow: an unknown client has no verified tier map
        profile = None
    if profile is None:
        return None, "unsupported"
    return resolve_tier(TASK_POLICY[task_class].tier, client=profile), "table"


def operator_set(dispatch_cfg: object, name: str) -> bool:
    """Whether the operator set *name* (config.yaml or env).

    ``DispatchConfig.operator_set`` says which fields the operator set, so a value
    equal to its default still counts as the operator's. A config object without
    that marker (a caller's stand-in) counts whatever fields it carries.
    """
    marked = getattr(dispatch_cfg, "operator_set", None)
    if isinstance(marked, frozenset):
        return name in marked
    return hasattr(dispatch_cfg, name)


def resolve_max_turns(configured: object, role: str | None) -> tuple[int | None, str]:
    """``(max_turns, source)`` (PRD-CORE-290-FR04): operator policy > the role's row > default.

    0 from the operator disables the cap; a row of 0 exempts its class (review and
    security audits are never cut off mid-evidence).
    """
    if isinstance(configured, int) and not isinstance(configured, bool):
        return (configured, "config") if configured > 0 else (None, "disabled")
    task_class = role_task_class(role)
    row = TASK_POLICY[task_class].max_turns if task_class else None
    if row is None:
        return DEFAULT_DISPATCH_MAX_TURNS, "default"
    return (row, "table") if row > 0 else (None, "exempt")


def policy_record(req: DispatchRequest) -> dict[str, dict[str, object]]:
    """What was asked for versus what the child's command line actually carries."""
    spec = CLIENT_SPECS.get(req.client)
    applied = _client_effort(spec, req) if spec is not None else None
    # The model reaches the child only through the client's model flag.
    model_flag = spec is not None and spec.model_flag is not None
    model_applied = req.model if model_flag else None
    model_source = req.model_source if model_flag or req.model is None else "unsupported"
    turn_flag = spec is not None and spec.max_turns_flag is not None
    turns_applied = req.max_turns if turn_flag else None
    turns_source = req.max_turns_source if turn_flag or req.max_turns is None else "unsupported"
    return {
        "effort": {"requested": req.effort, "applied": applied, "source": req.effort_source},
        "model": {"requested": req.model, "applied": model_applied, "source": model_source},
        "turns": {"requested": req.max_turns, "applied": turns_applied, "source": turns_source},
    }

"""Dispatch tier/effort defaults from the task-class table (PRD-CORE-290-FR03).

Belongs to the ``trw_mcp.dispatch`` package. Precedence for each of effort and
model is explicit request > operator config > client default > table default, where the table
default is the row of the role's task class (``agents/task_policy.py``, the one
source bundled agents also use). The resolver records which source won; the
result records what was requested versus what the client was actually given.
"""

from __future__ import annotations

from itertools import pairwise

from trw_mcp.agents.task_policy import TASK_POLICY
from trw_mcp.agents.tier_resolver import resolve_tier
from trw_mcp.dispatch._client_spec_types import EFFORT_LEVELS, ClientSpec
from trw_mcp.dispatch._client_specs import CLIENT_SPECS, UnknownClientError, client_spec_for
from trw_mcp.dispatch._commands import _client_effort, build_command
from trw_mcp.dispatch._roles import role_task_class
from trw_mcp.dispatch._slots import slot_settings
from trw_mcp.dispatch._types import DispatchRequest
from trw_mcp.models.config._fields_dispatch import DEFAULT_DISPATCH_MAX_TURNS

__all__ = [
    "operator_set",
    "policy_record",
    "require_effort",
    "resolve_effort",
    "resolve_max_turns",
    "resolve_model",
]


def _spec_for_policy(client: str | None) -> ClientSpec | None:
    if client is None:
        return None
    try:
        return client_spec_for(client)
    except UnknownClientError:  # trw-fail-silent-allow: an unknown client has no provider policy; resolution rejects it
        return None


def _checked(value: str, where: str) -> str:
    if value not in EFFORT_LEVELS:
        raise ValueError(f"{where} effort {value!r} is not one of {', '.join(EFFORT_LEVELS)}")
    return value


def resolve_effort(
    explicit: str | None, role: str | None, config_default: object, *, client: str | None = None
) -> tuple[str | None, str]:
    """``(effort, source)``; raises ``ValueError`` for a value outside the portable ladder."""
    if explicit is not None:
        return _checked(explicit, "requested"), "request"
    if isinstance(config_default, str) and config_default:
        return _checked(config_default, "configured"), "config"
    spec = _spec_for_policy(client)
    if spec is not None and spec.default_effort is not None:
        return spec.default_effort, "default"
    task_class = role_task_class(role)
    if task_class:
        return TASK_POLICY[task_class].effort, "table"
    return None, "none"


def require_effort(client: str, model: str | None, effort_source: str) -> str | None:
    """PRD-CORE-355-FR06: the refusal text when no effort source won for a client that carries one, else None.

    A client with no effort flag or config key, or a Haiku model (which takes no effort), has nothing to
    require, so it is never refused for lacking one.
    """
    if effort_source != "none":
        return None
    try:
        spec = client_spec_for(client)
    except UnknownClientError:  # trw-fail-silent-allow: resolution already refused an unknown client
        return None
    if not (spec.effort_flag or spec.effort_config_key):
        return None
    if model and "haiku" in model.lower():
        return None
    return (
        f"dispatch_require_effort is on and no effort resolved for {client!r}: pass --effort "
        f"({', '.join(EFFORT_LEVELS)}) or set dispatch_default_effort in .trw/config.yaml"
    )


def resolve_model(explicit: str | None, client: str, role: str | None, config_models: object) -> tuple[str | None, str]:
    """``(model, source)``. ``unsupported``: the role has a tier, the client has no verified map."""
    if explicit:
        return explicit, "request"
    configured = config_models.get(client) if isinstance(config_models, dict) else None
    if configured:
        return str(configured), "config"
    spec = _spec_for_policy(client)
    if spec is not None and spec.default_model:
        return spec.default_model, "default"
    task_class = role_task_class(role)
    if not task_class:
        return None, "none"
    # A client without a verified tier map gets no table model: a raw tier passed as
    # --model would be an invalid model name, so TRW passes nothing and says so.
    profile = spec.tier_profile if spec is not None else None
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


def _argv_flags(spec: ClientSpec, req: DispatchRequest) -> tuple[str | None, str | None] | None:
    """``(model, effort)`` as the child's own argv carries them (last occurrence wins, as the CLI reads it).

    Reads the argv :func:`build_command` produces, prompt excluded, so a caller's ``extra_args`` flag
    after TRW's is what gets reported. ``None`` when the argv cannot be built (the dispatch then fails
    anyway) so the caller falls back to the computed value.
    """
    try:
        argv = build_command(req)
    except Exception:  # trw-fail-silent-allow: an unbuildable argv fails the dispatch itself; the record falls back
        return None
    argv = argv[: -2 if spec.prompt_flag else -1]
    model_flag, effort_flag, effort_key = spec.model_flag, spec.effort_flag, spec.effort_config_key
    model = effort = None
    for flag, value in pairwise(argv):
        if model_flag and flag == model_flag:
            model = value
        elif effort_flag and flag == effort_flag:
            effort = value
        elif effort_key and flag == "-c" and value.startswith(f"{effort_key}="):
            effort = value.split("=", 1)[1].strip("\"'")
    return model, effort


def policy_record(req: DispatchRequest) -> dict[str, dict[str, object]]:
    """Requested (the literal ask), resolved (after precedence) and applied (the child's real argv).

    ``requested`` is non-null only when the request itself chose the value; ``resolved`` is what
    request > operator config > table produced; ``applied`` is read from the built command line
    (CODEX-P0-A), so an ``extra_args`` override is reported rather than hidden.
    """
    spec = CLIENT_SPECS.get(req.client)
    computed_effort = _client_effort(spec, req) if spec is not None else None
    model_flag = spec is not None and spec.model_flag is not None
    flags = _argv_flags(spec, req) if spec is not None else None
    applied_model, applied_effort = flags if flags is not None else (req.model if model_flag else None, computed_effort)
    model_source = req.model_source if model_flag or req.model is None else "unsupported"
    turn_flag = spec is not None and spec.max_turns_flag is not None
    turns_applied = req.max_turns if turn_flag else None
    turns_source = req.max_turns_source if turn_flag or req.max_turns is None else "unsupported"

    def knob(value: object, source: str, applied: object) -> dict[str, object]:
        return {
            "requested": value if source == "request" else None,
            "resolved": value,
            "applied": applied,
            "source": source,
        }

    record: dict[str, dict[str, object]] = {
        "effort": knob(req.effort, req.effort_source, applied_effort),
        "model": knob(req.model, model_source, applied_model),
        "turns": {"requested": req.max_turns, "applied": turns_applied, "source": turns_source},
    }
    slots = slot_settings()
    if slots.cap > 0:  # PRD-CORE-355-FR07; absent when the cap is off (NFR01)
        record["slot"] = {"cap": slots.cap, "wait_limit_s": slots.wait_s}
    return record

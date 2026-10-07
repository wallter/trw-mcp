"""The one structured dispatch-setup result and its two renderers (PRD-INFRA-210 FR09/FR10).

``trw-mcp config dispatch`` builds the result once; the human line (:func:`render_line`) and the headless
installer's JSON ``dispatch`` object both read it, so the two cannot disagree. It carries the decision's
provenance, the write outcomes, and the per-client defaults a launch would use, including the effort the
launch would really carry (:func:`trw_mcp.dispatch._commands.applied_effort`), which can be clamped or
omitted, not just the resolver's value.
"""

from __future__ import annotations

from collections.abc import Sequence

from trw_mcp.dispatch._targets import resolved_dispatch_defaults

__all__ = ["DECIDED_BY", "describe_dispatch", "render_line"]

#: How the enable decision was reached: an explicit flag, an earlier operator answer, the prompt, or no answer.
DECIDED_BY = ("flag", "prior", "prompt", "default")
_ENABLE = "trw-mcp config set dispatch_tools_exposed true --scope machine"
_PIN = "trw-mcp config set dispatch_default_efforts.<client> <level> --scope machine"


def describe_dispatch(
    dispatch_cfg: object,
    decided_by: str,
    writes: Sequence[dict[str, object]] = (),
    warnings: Sequence[str] = (),
) -> dict[str, object]:
    """The setup result for the effective *dispatch_cfg* (a ``DispatchConfig``)."""
    if decided_by not in DECIDED_BY:
        raise ValueError(f"decided_by must be one of {DECIDED_BY}, got {decided_by!r}")
    result: dict[str, object] = {
        "enabled": bool(getattr(dispatch_cfg, "dispatch_tools_exposed", False)),
        "decided_by": decided_by,
        "default_client": getattr(dispatch_cfg, "dispatch_default_client", None),
        "clients": resolved_dispatch_defaults(dispatch_cfg),
        "writes": [dict(w) for w in writes],
        "warnings": list(warnings),
    }
    result["line"] = render_line(result)
    return result


def _described(row: dict[str, object]) -> str:
    model = f"model {row['model']} [{row['model_source']}]" if row["model"] else "model: the client's own"
    applied, resolved = row.get("applied_effort"), row.get("effort")
    if applied is None:
        effort = "effort none" + (f" (resolved {resolved} is not carried)" if resolved else "")
    else:
        effort = f"effort {applied} [{row['effort_source']}]" + (
            f" (resolved {resolved}, clamped)" if applied != resolved else ""
        )
    return f"{model}, {effort}"


def render_line(result: dict[str, object]) -> str:
    """The single ``Dispatch:`` line both installers print."""
    if not result["enabled"]:
        return f"Dispatch: off (enable: {_ENABLE})"
    client = result["default_client"]
    if not client:
        return "Dispatch: on, no default client (pass --client)"
    clients = result["clients"]
    row = next((r for r in clients if r["client"] == client), None) if isinstance(clients, list) else None
    if row is None:
        return f"Dispatch: on, default client {client} (not a supported client); pin: {_PIN}"
    return f"Dispatch: on, default client {client} ({_described(row)}); pin: {_PIN}"

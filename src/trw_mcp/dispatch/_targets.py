"""Frictionless target + role selection for ``trw_dispatch`` (PUBLIC, BSL-1.1).

Belongs to the ``trw_mcp.dispatch`` package. One place turns the loose names an
agent types ("codex", "grok:grok-4.7", "sonnet", "codex, agy") into
``(client, model)`` pairs, turns a plain-English role ("review", "critique",
"plan", "implement") into a registry role plus its write stance, lists what can
be dispatched to, and shapes fan-out results into a compact list.

Everything here is pure except :func:`list_clients` (a PATH lookup), so the
tool layer stays a thin adapter.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from types import SimpleNamespace

from trw_mcp.dispatch._client_aliases import CLIENT_ALIASES, MODEL_SHORTHANDS
from trw_mcp.dispatch._client_specs import CLIENT_SPECS, SUPPORTED_CLIENTS
from trw_mcp.dispatch._commands import applied_effort
from trw_mcp.dispatch._policy import configured_effort, configured_models, resolve_effort, resolve_model
from trw_mcp.dispatch._roles import ROLE_TABLE

#: Plain-English roles -> (registry role or None, writes allowed).
ROLE_ALIASES: dict[str, tuple[str | None, bool]] = {
    "review": ("code-review", False),
    "critique": ("adversarial-audit", False),
    "audit": ("design-audit", False),
    "implement": (None, True),
}


class TargetError(ValueError):
    """A target or role name that cannot be resolved; the message says what to use."""


@dataclass(frozen=True)
class Target:
    client: str  # "" = the configured default client
    model: str | None
    variant: int = 0  # 1..N: which of the caller's prompt variants this lane runs (0: the only prompt)

    @property
    def label(self) -> str:
        base = f"{self.client or 'default'}:{self.model}" if self.model else (self.client or "default")
        return f"{base}#{self.variant}" if self.variant else base


def variant_lanes(targets: list[Target] | None, model: str | None, prompts: int) -> list[Target]:
    """One lane per (target, prompt variant); no targets means the configured default client."""
    base = targets or [Target("", model)]
    if prompts <= 1:
        return base
    return [Target(t.client, t.model, i) for t in base for i in range(1, prompts + 1)]


def _valid_names() -> str:
    names = sorted({*SUPPORTED_CLIENTS, *CLIENT_ALIASES, *MODEL_SHORTHANDS})
    return ", ".join(names)


def parse_target(raw: str, default_model: str | None = None) -> Target:
    """Parse one ``client`` / ``client:model`` / model-shorthand name."""
    name, _, model = raw.strip().partition(":")
    name = name.strip().lower()
    model_or_default = model.strip() or default_model
    if name in MODEL_SHORTHANDS and not model.strip():
        short_client, short_model = MODEL_SHORTHANDS[name]
        return Target(short_client, short_model)
    client: str = CLIENT_ALIASES.get(name, name)
    if client not in SUPPORTED_CLIENTS:
        raise TargetError(
            f"unknown dispatch target {raw.strip()!r}; use one of: {_valid_names()} "
            "(optionally 'client:model', e.g. 'grok:grok-4.7'); action='clients' lists them"
        )
    return Target(client, model_or_default)


def parse_targets(client: str | None, model: str | None) -> list[Target] | None:
    """Split a comma-separated ``client`` into targets; None when no client given.

    ``model`` applies to every target that does not name its own model. Duplicate
    labels are dropped so a fan-out never launches the same lane twice.
    """
    if client is None or not client.strip():
        return None
    out: list[Target] = []
    for part in client.split(","):
        if not part.strip():
            continue
        target = parse_target(part, model)
        if target not in out:
            out.append(target)
    if not out:
        return None
    return out


def resolve_role(role: str | None) -> tuple[str | None, bool | None]:
    """Map a role name to ``(registry_role, writes)``.

    ``writes`` is False for a read-only role, True for ``implement``, None when no
    role was given (caller's own ``allow_writes`` decides). Unknown roles raise
    instead of silently running the bare prompt.
    """
    if role is None or not role.strip():
        return None, None
    key = role.strip().lower()
    if key in ROLE_ALIASES:
        return ROLE_ALIASES[key]
    if key in ROLE_TABLE:
        return key, False
    valid = sorted({*ROLE_ALIASES, *ROLE_TABLE})
    raise TargetError(f"unknown role {role!r}; use one of: {', '.join(valid)}")


def resolved_dispatch_defaults(dispatch_cfg: object) -> list[dict[str, object]]:
    """Per supported client: what a dispatch with no ``--model``/``--effort``/``--role`` would launch with.

    ``installed`` is a PATH lookup only (no client process is started). ``model``/``effort`` and their
    sources come from the same resolvers a real dispatch uses, with ``role=None`` (PRD-INFRA-210-FR03).
    ``applied_effort`` is what the launch would really carry: the resolved level clamped to what the
    client accepts, or None when the client has no effort carrier (or the model takes none).
    """
    rows: list[dict[str, object]] = []
    for cid in SUPPORTED_CLIENTS:
        spec = CLIENT_SPECS[cid]
        model, model_source = resolve_model(None, cid, None, configured_models(dispatch_cfg))
        effort, effort_source = resolve_effort(None, None, configured_effort(dispatch_cfg, cid), client=cid)
        rows.append(
            {
                "client": cid,
                "installed": any(shutil.which(b) for b in spec.binary_names),
                "model": model,
                "model_source": model_source,
                "effort": effort,
                "effort_source": effort_source,
                "applied_effort": applied_effort(spec, effort, model),
            }
        )
    return rows


def list_clients(default_models: dict[str, str] | None = None, dispatch_cfg: object | None = None) -> dict[str, object]:
    """Every dispatchable client with install state, resolved defaults, aliases, and roles."""
    cfg = dispatch_cfg if dispatch_cfg is not None else SimpleNamespace(dispatch_default_models=default_models or {})
    clients = []
    for row in resolved_dispatch_defaults(cfg):
        cid = str(row["client"])
        entry: dict[str, object] = {"client": cid, "installed": row["installed"]}
        if row["model"]:
            entry["default_model"] = row["model"]
        if row["effort"]:
            entry["default_effort"] = row["effort"]
            entry["applied_effort"] = row["applied_effort"]  # what a launch carries: clamped, or None without a carrier
        entry["model_source"], entry["effort_source"] = row["model_source"], row["effort_source"]
        aliases = sorted(a for a, c in CLIENT_ALIASES.items() if c == cid)
        aliases += sorted(s for s, (c, _m) in MODEL_SHORTHANDS.items() if c == cid)
        if aliases:
            entry["aliases"] = aliases
        clients.append(entry)
    return {
        "clients": clients,
        "roles": sorted({*ROLE_ALIASES, *ROLE_TABLE}),
        "syntax": "client='codex' | 'grok:grok-4.7' | 'sonnet' | 'codex,agy,grok:grok-4.7' (fan-out)",
    }


_ERROR_TAIL_CHARS = 1500


def compact_result(label: str, payload: dict[str, object]) -> dict[str, object]:
    """One fan-out lane as ``{target, ok, text, duration_s}`` plus error context on failure."""
    ok = bool(payload.get("ok"))
    out: dict[str, object] = {
        "target": label,
        "ok": ok,
        "text": payload.get("text", ""),
        "duration_s": payload.get("duration_s"),
    }
    if payload.get("posture_note"):  # the requested posture did not take: say so on every lane
        out["posture_enforced"], out["posture_note"] = False, payload["posture_note"]
    if not ok:
        stderr = str(payload.get("raw_stderr") or "")
        stdout = str(payload.get("raw_stdout") or "")
        out["error"] = (stderr or stdout)[-_ERROR_TAIL_CHARS:]
        out["reason"] = payload.get("silence_reason") or ("timed_out" if payload.get("timed_out") else "failed")
        out["exit_code"] = payload.get("exit_code")
    return out

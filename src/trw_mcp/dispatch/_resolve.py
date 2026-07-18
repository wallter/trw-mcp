"""Shared dispatch-request resolution.

Belongs to the ``trw_mcp.dispatch`` package. The CLI handler (``_cli.py``) and
the MCP tools (``trw_mcp.tools.dispatch``) must resolve a caller's loose inputs
(optional client/model/timeout/role + ``config.dispatch`` defaults) into the
SAME typed :class:`DispatchRequest`. Centralizing the precedence here is the one
source of truth — a divergence between the CLI and the MCP path would silently
apply different security/posture defaults depending on the entry point.

Precedence (highest wins):

- client: explicit ``client`` > ``dispatch_role_client[role]`` (when ``role``
  set) > ``dispatch_default_client``. ``None`` after that -> error.
- model: explicit ``model`` > ``dispatch_default_models[client]``.
- timeout: explicit ``timeout_s`` (not None) > ``dispatch_default_timeout_s``.
- read_only: an EXPLICIT ``read_only`` (True or False) is honored; ``None`` ->
  the ``dispatch_default_read_only`` config baseline. (The caller is responsible
  for turning an ``--allow-writes`` request into ``read_only=False``.)

``gemini`` is rejected (EOL) BEFORE the enabled-clients check so the redirect to
``agy`` always wins. A resolved client absent from ``dispatch_enabled_clients``
is rejected. All rejection paths raise :class:`DispatchResolutionError` carrying
an ``exit_code`` so the CLI can translate it to ``sys.exit`` and the MCP tool can
surface it as a structured ``{"error", "exit_code"}`` payload.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.dispatch._roles import apply_role
from trw_mcp.dispatch._types import DispatchRequest

_GEMINI_EOL_MSG = (
    "Gemini CLI was retired (EOL 2026-06-18) and is not a dispatch target. "
    "Use '--client agy' (Antigravity CLI) instead."
)


class DispatchResolutionError(ValueError):
    """A dispatch request could not be resolved into a valid target.

    Carries ``exit_code`` so the CLI maps it directly to ``sys.exit`` and the MCP
    tool can echo the same code in its structured error payload. ``2`` is used
    for every resolution failure (unresolved / disabled / gemini-EOL) to mirror
    the CLI's pre-existing exit conventions.
    """

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _resolve_client(
    *,
    client: str | None,
    role: str | None,
    dispatch_cfg: object,
) -> str:
    """Resolve the target client by precedence, raising on failure.

    explicit > role mapping > default. ``gemini`` is rejected BEFORE the
    enabled-clients check so the EOL redirect always wins. A resolved client not
    in ``dispatch_enabled_clients`` is rejected.
    """
    default_client = getattr(dispatch_cfg, "dispatch_default_client", None)
    role_client = getattr(dispatch_cfg, "dispatch_role_client", {})

    resolved = client
    if resolved is None and role is not None and isinstance(role_client, dict):
        resolved = role_client.get(role)
    if resolved is None:
        resolved = default_client
    if resolved is None:
        raise DispatchResolutionError(
            "No dispatch client resolved: pass a client or set "
            "dispatch.default_client (or dispatch.role_client) in .trw/config.yaml.",
            exit_code=2,
        )

    resolved = str(resolved)
    if resolved == "gemini":
        raise DispatchResolutionError(_GEMINI_EOL_MSG, exit_code=2)

    enabled = getattr(dispatch_cfg, "dispatch_enabled_clients", [])
    if isinstance(enabled, list) and resolved not in enabled:
        raise DispatchResolutionError(
            f"client {resolved!r} is disabled (set dispatch.enabled_clients in .trw/config.yaml)",
            exit_code=2,
        )
    return resolved


def resolve_dispatch_request(
    *,
    client: str | None,
    prompt: str,
    role: str | None,
    model: str | None,
    cwd: Path | None,
    timeout_s: int | None,
    read_only: bool | None = None,
    isolate: bool,
    use_pty: bool,
    dispatch_cfg: object,
) -> DispatchRequest:
    """Build a validated :class:`DispatchRequest` from loose inputs + config.

    Raises :class:`DispatchResolutionError` (``exit_code=2``) when no client
    resolves, the resolved client is disabled, or ``gemini`` is requested.
    """
    resolved_client = _resolve_client(client=client, role=role, dispatch_cfg=dispatch_cfg)

    # Model: explicit wins; otherwise the per-client config override.
    resolved_model = model
    if resolved_model is None:
        default_models = getattr(dispatch_cfg, "dispatch_default_models", {})
        if isinstance(default_models, dict):
            resolved_model = default_models.get(resolved_client)

    # Timeout: an explicit value (any int) wins; None -> config default.
    resolved_timeout = timeout_s
    if resolved_timeout is None:
        resolved_timeout = int(getattr(dispatch_cfg, "dispatch_default_timeout_s", 600))

    # Read-only: an explicit caller value (True or False) is AUTHORITATIVE; only
    # ``None`` falls back to the config default. This is the F-03 safety fix — an
    # explicit ``read_only=True`` must never be silently overridden by a config
    # default of False.
    if read_only is None:
        effective_read_only = bool(getattr(dispatch_cfg, "dispatch_default_read_only", True))
    else:
        effective_read_only = read_only

    resolved_prompt = apply_role(role, prompt)

    return DispatchRequest(
        client=resolved_client,  # type: ignore[arg-type]  # validated against the Literal by Pydantic
        prompt=resolved_prompt,
        model=resolved_model,
        cwd=cwd,
        timeout_s=int(resolved_timeout),
        read_only=effective_read_only,
        isolate=isolate,
        use_pty=use_pty,
    )

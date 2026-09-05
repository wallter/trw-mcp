"""Cross-client dispatch (``trw-mcp dispatch``) operator-default fields.

Belongs to the ``_TRWConfigFields`` MI assembly in ``_main_fields.py`` and is
projected into the :class:`~trw_mcp.models.config._sub_models.DispatchConfig`
view model via ``TRWConfig.dispatch``.

These flat fields let an operator set defaults for the cross-client dispatch
CLI (``trw_mcp.dispatch``) in ``.trw/config.yaml`` so a bare
``trw-mcp dispatch --prompt ...`` resolves a target client, model, timeout, and
read-only posture without repeating flags. Every field is additive with a
documented default — omitting the ``dispatch:`` block keeps the CLI's prior
behavior byte-identical.
"""

from __future__ import annotations

from pydantic import Field

from trw_mcp.dispatch._types import SUPPORTED_CLIENTS, DispatchClient

# The dispatch layer's hard wall-clock timeout default (seconds). Mirrors the
# inline ``DispatchRequest.timeout_s`` default so the config-resolved and
# API-direct paths agree on the same documented ceiling.
DEFAULT_DISPATCH_TIMEOUT_SECS: int = 600

# Per-probe bound for the doctor readiness version probe (seconds). 5 s is an
# order of magnitude above the ~0.3 s a warm CLI takes to print a banner, and an
# order of magnitude below the dispatch timeout above — it exists to bound a hung
# binary, not to trim a working one.
DEFAULT_DISPATCH_VERSION_PROBE_TIMEOUT_SECS: int = 5


class _DispatchFields:
    """Cross-client dispatch domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Cross-client dispatch defaults --

    # Allowed dispatch targets. A resolved client absent from this list is
    # rejected (exit 2) so an operator can disable a target (e.g. drop the
    # weaker ``agy``/``opencode`` isolation targets) without code changes.
    # Typed as ``list[DispatchClient]`` (not ``list[str]``) so a typo (e.g.
    # "codexx") fails LOUD with a pydantic ValidationError at config load instead
    # of silently disabling every client. Default derives from SUPPORTED_CLIENTS
    # (the single source) so the allowed set cannot drift from the builder.
    dispatch_enabled_clients: list[DispatchClient] = Field(
        default_factory=lambda: list(SUPPORTED_CLIENTS),
        description="Dispatch targets the CLI may launch; a client outside this list is rejected.",
    )
    # Default target when ``--client`` is omitted. ``codex`` is the operator's
    # primary second-opinion client (cleanest host-config isolation). Set to
    # ``None`` to require an explicit ``--client`` (or a matching role default).
    dispatch_default_client: str | None = Field(
        default="codex",
        description="Client used when --client is omitted; None requires an explicit --client.",
    )
    # Per-client model override applied when ``--model`` is omitted, e.g.
    # ``{"codex": "gpt-5.5"}``. Empty by default (the client's own default model).
    dispatch_default_models: dict[str, str] = Field(
        default_factory=dict,
        description="Per-client model override applied when --model is omitted (e.g. {'codex': 'gpt-5.5'}).",
    )
    # Per-probe wall-clock bound for the doctor ``formation_readiness`` version
    # probe (PRD-CORE-266-NFR01). A typed field rather than a literal because it
    # is the ONLY thing standing between a hung client binary and a hung doctor
    # run, and an operator on a slow box must be able to raise it without a code
    # edit. It bounds each probe INDIVIDUALLY, so the worst case for N enabled
    # clients is N times this value; the ge/le bounds keep it low enough to stay
    # a diagnostic and high enough for a cold node-based CLI to start.
    dispatch_version_probe_timeout_s: int = Field(
        default=DEFAULT_DISPATCH_VERSION_PROBE_TIMEOUT_SECS,
        ge=1,
        le=60,
        description=(
            "Per-client wall-clock bound (seconds) on the doctor formation-readiness "
            "version probe; N enabled clients are bounded by N times this value."
        ),
    )
    # Hard wall-clock timeout (seconds) applied when ``--timeout`` is omitted.
    dispatch_default_timeout_s: int = Field(
        default=DEFAULT_DISPATCH_TIMEOUT_SECS,
        gt=0,
        description="Wall-clock timeout (seconds) applied when --timeout is omitted.",
    )
    # Default read-only posture. True forbids child writes; ``--allow-writes``
    # always overrides this to False (authoritative) regardless of config.
    dispatch_default_read_only: bool = Field(
        default=True,
        description="Default read-only posture for dispatched children; --allow-writes overrides to False.",
    )
    # Optional per-role default client, e.g. ``{"adversarial-audit": "codex"}``.
    # Consulted only when neither ``--client`` nor ``dispatch_default_client``
    # resolves a target and a ``--role`` was supplied.
    dispatch_role_client: dict[str, str] = Field(
        default_factory=dict,
        description="Per-role default client used only when --client and default_client do not apply.",
    )

"""FastMCP application creation and configuration.

Creates the ``mcp`` FastMCP instance with middleware, instructions,
and structured logging.

PRD-CORE-001: Base MCP tool suite.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import structlog
from fastmcp import FastMCP

from trw_mcp.meta_tune.boot_checks import validate_defaults as validate_meta_tune_defaults
from trw_mcp.middleware.ceremony import CeremonyMiddleware
from trw_mcp.models.config import TRWConfig
from trw_mcp.server._boot_timeline import emit_boot_phase

logger = structlog.get_logger(__name__)

# Minimum trw-memory version that includes the concurrent-writer corruption fix
# (warm-tier sidecar lock + hot-tier sweep race shipped in 0.9.5).
_TRW_MEMORY_MIN_VERSION = "0.9.5"


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a PEP-440-style version string into a comparable integer tuple.

    Only the numeric prefix (MAJOR.MINOR.PATCH) is considered; pre/post/dev
    suffixes are stripped so the comparison stays simple and dependency-free.
    """
    import re as _re

    match = _re.match(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", version_str)
    if not match:
        return (0,)
    return tuple(int(g) for g in match.groups() if g is not None)


def _check_memory_version() -> None:
    """Emit a warning when the installed trw-memory is below the minimum safe version.

    trw-memory <0.9.5 exposes a concurrent-write corruption bug (reference_memory_db_walreset_fix).
    This check is fail-open — a missing or unparseable version is logged but does not abort startup.
    """
    try:
        installed = importlib.metadata.version("trw-memory")
        if _parse_version(installed) < _parse_version(_TRW_MEMORY_MIN_VERSION):
            logger.warning(
                "trw_memory_version_below_minimum",
                installed=installed,
                minimum=_TRW_MEMORY_MIN_VERSION,
                action="upgrade trw-memory to avoid concurrent-write corruption",
            )
    except importlib.metadata.PackageNotFoundError:
        logger.warning(
            "trw_memory_version_check_failed",
            reason="trw-memory package not found in environment",
        )
    except Exception:  # justified: fail-open, boot-time check must never abort startup
        logger.debug("trw_memory_version_check_failed", reason="unexpected error during version check")


_DEFAULT_INSTRUCTIONS = (
    "TRW turns session history into reusable engineering context. "
    "Call trw_session_start() first: it restores prior learnings and any active run, "
    "so you start from what the team already learned instead of re-deriving it. "
    "Workflow: plan, implement, verify, deliver. "
    "Read .trw/frameworks/FRAMEWORK-CORE.md after startup or compaction for phase gates. "
    "Use trw_learn() when you discover a root cause or durable pattern. "
    "Preserve material unfinished work with a checkpoint or durable native handoff and a next-read pointer. Nothing material to preserve: do not manufacture artifacts. Use trw_deliver only for completed-work acceptance under unchanged delivery gates; recorded learnings already persist."
)


def _load_server_instructions() -> str:
    """Load MCP server instructions from centralized messages, with fallback."""
    try:
        from trw_mcp.prompts.messaging import get_message_or_default

        return get_message_or_default("server_instructions", _DEFAULT_INSTRUCTIONS)
    except Exception:  # justified: fail-open, message registry failure falls back to inline default
        return _DEFAULT_INSTRUCTIONS


def _try_init_ceremony() -> CeremonyMiddleware | None:
    """Try to initialize CeremonyMiddleware. Returns None on failure (fail-open)."""
    try:
        return CeremonyMiddleware()
    except Exception:  # justified: fail-open, middleware init failure must not crash server startup
        logger.warning("middleware_init_failed", component="CeremonyMiddleware")
        return None


def _try_load_config() -> TRWConfig | None:
    """Try to load TRWConfig. Returns None on failure (fail-open)."""
    try:
        from trw_mcp.models.config import get_config

        return get_config()
    except Exception:  # justified: fail-open, config load failure must not crash server startup
        logger.warning("middleware_config_load_failed", component="get_config")
        return None


def _try_init_observation_masking(config: TRWConfig) -> object | None:
    """Try to initialize ContextBudgetMiddleware. Returns None on failure."""
    if not config.observation_masking:
        return None
    try:
        from trw_mcp.middleware.context_budget import ContextBudgetMiddleware

        return ContextBudgetMiddleware()
    except Exception:  # justified: fail-open, observation masking is optional enhancement
        logger.warning("middleware_init_failed", component="ContextBudgetMiddleware")
        return None


def _try_init_mcp_security(config: TRWConfig) -> object | None:
    """Initialize the mounted MCP security middleware."""
    from trw_mcp.startup import init_security

    return init_security(config.security.mcp)


def _try_init_surface_authority() -> object | None:
    """Try to initialize SurfaceAuthorityMiddleware. Returns None on failure (fail-open).

    PRD-CORE-218 FR03/FR04 activation: the kernel/pack resolver is the production
    tool-exposure authority (replacing the removed PRD-CORE-125 preset filter).
    Registered BEFORE PhaseExposureMiddleware so phase masking composes WITHIN the
    CORE-218 surface. The middleware self-resolves ``tool_resolution_mode`` from
    config at request time (default ``standard``; ``all`` is a strict no-op
    operator escape), so it is always appended — a broken init is fail-open.

    PRD-SEC-015 round-2 audit (Row 1): this fail-open contract is INVERTED under
    the reviewer role. SurfaceAuthorityMiddleware is the sole server-side control
    that bounds a reviewer to ``REVIEWER_TOOLS`` — a reviewer session that lost
    it would serve its full, unmasked surface to a stateless, unattributed lane.
    Ceremony still exempts a reviewer from the compaction gate regardless, so
    "degrade and keep serving" is worse here than refusing to boot: an operator
    who sees the server fail to start investigates; one who gets an unbounded
    reviewer with no error at all does not.
    """
    try:
        from trw_mcp.middleware.surface_authority import SurfaceAuthorityMiddleware

        return SurfaceAuthorityMiddleware()
    except Exception:
        from trw_mcp.state._surface_role import reviewer_role_active

        if reviewer_role_active():
            logger.exception("middleware_init_failed_reviewer_abort", component="SurfaceAuthorityMiddleware")
            raise
        logger.warning("middleware_init_failed", component="SurfaceAuthorityMiddleware")  # justified: fail-open
        return None


def _try_init_phase_exposure() -> object | None:
    """Try to initialize PhaseExposureMiddleware. Returns None on failure (fail-open).

    PRD-INTENT-002 FR08: inserted immediately after CeremonyMiddleware (session
    state resolved first) and before ContextBudgetMiddleware (phase filtering
    precedes context/observation masking). The middleware self-resolves its
    ``enabled`` flag from ``phase_exposure_enabled`` config (default false for
    the v1 rollout), so it is always appended — a disabled flag is a no-op
    pass-through, not a missing chain entry.
    """
    try:
        from trw_mcp.middleware.phase_exposure import PhaseExposureMiddleware

        return PhaseExposureMiddleware()
    except Exception:  # justified: fail-open, middleware init failure must not crash startup
        logger.warning("middleware_init_failed", component="PhaseExposureMiddleware")
        return None


def _try_init_version_drift() -> object | None:
    """Try to initialize VersionDriftMiddleware. Returns None on failure (fail-open).

    PRD-CORE-215-FR02: annotates tool results with a non-blocking booted-vs-installed
    version-drift advisory. Placed after CeremonyMiddleware (session context resolved)
    and before ResponseOptimizerMiddleware so its plain-text advisory block is not
    re-serialized; the check is cached off the hot path (NFR01).
    """
    try:
        from trw_mcp.middleware.version_drift import VersionDriftMiddleware

        return VersionDriftMiddleware()
    except Exception:  # justified: fail-open, middleware init failure must not crash startup
        logger.warning("middleware_init_failed", component="VersionDriftMiddleware")
        return None


def _try_init_boot_deferral() -> object | None:
    """Initialize BootDeferralMiddleware (PRD-CORE-248 FR01). None on failure.

    Registered FIRST in the chain so its ``on_initialize`` hook wraps the whole
    handshake and its ``on_call_tool`` fallback runs before any other middleware
    can act on a session whose sync configuration is still unresolved.
    """
    try:
        from trw_mcp.middleware.boot_deferral import BootDeferralMiddleware

        return BootDeferralMiddleware(_resolve_deferred_budget_ms())
    except Exception:  # justified: fail-open, middleware init failure must not crash startup
        logger.warning("middleware_init_failed", component="BootDeferralMiddleware")
        return None


def _try_init_response_optimizer() -> object | None:
    """Try to initialize ResponseOptimizerMiddleware. Returns None on failure."""
    try:
        from trw_mcp.middleware.response_optimizer import ResponseOptimizerMiddleware

        return ResponseOptimizerMiddleware()
    except Exception:  # justified: fail-open, response optimizer is optional enhancement
        logger.warning("middleware_init_failed", component="ResponseOptimizerMiddleware")
        return None


def _run_meta_tune_boot_validation(config: TRWConfig) -> None:
    """Fail-loud SAFE-001 boot validation when meta-tune is enabled."""
    if config.meta_tune.enabled:
        validate_meta_tune_defaults(config)


def _build_middleware() -> list[object]:
    """Build the middleware list, conditionally including progressive disclosure.

    Each middleware component is initialized by a dedicated helper that
    returns None on failure (fail-open). This keeps the orchestration
    logic readable while isolating error handling per component.
    """
    _check_memory_version()
    config = _try_load_config()
    if config is None:
        config = TRWConfig()
    _run_meta_tune_boot_validation(config)

    middleware: list[object] = []

    # PRD-CORE-248 FR01: first in the chain — its on_initialize hook must wrap
    # the whole handshake, and its first-tool-call fallback must resolve backend
    # sync before any other middleware inspects session state.
    boot_deferral = _try_init_boot_deferral()
    if boot_deferral is not None:
        middleware.append(boot_deferral)

    global _mcp_security
    _mcp_security = _try_init_mcp_security(config)
    if _mcp_security is not None:
        middleware.append(_mcp_security)

    ceremony = _try_init_ceremony()
    if ceremony is not None:
        middleware.append(ceremony)

    # PRD-CORE-218 FR03/FR04: surface-authority masking sits AFTER Ceremony
    # (session state first) and BEFORE PhaseExposure so phase masking composes
    # WITHIN the resolved CORE-218 surface (task packs first, then phase subset).
    surface_authority = _try_init_surface_authority()
    if surface_authority is not None:
        middleware.append(surface_authority)

    # PRD-INTENT-002 FR08: phase masking sits AFTER Ceremony (session state
    # first) and BEFORE ContextBudget (phase filtering precedes context/
    # observation masking). Appended here so the relative order holds.
    phase_exposure = _try_init_phase_exposure()
    if phase_exposure is not None:
        middleware.append(phase_exposure)

    # PRD-CORE-215-FR02: version-drift advisory. Outer relative to the response
    # optimizer so its plain-text advisory block is appended after re-serialization.
    version_drift = _try_init_version_drift()
    if version_drift is not None:
        middleware.append(version_drift)

    middleware.extend(
        mw
        for mw in (
            _try_init_observation_masking(config),
            _try_init_response_optimizer(),
        )
        if mw is not None
    )

    return middleware


@asynccontextmanager
async def _build_sync_lifespan(_: FastMCP) -> AsyncIterator[None]:
    """Own the background sync task's lifecycle. Resolves NOTHING before yield.

    PRD-CORE-248 FR01: this lifespan used to resolve backend-sync config, sync
    targets and — through ``BackendSyncClient.__init__`` ->
    ``resolve_sync_client_id()`` -> ``cfg.client_profile`` — the client profile,
    all of it before the lowlevel server began processing messages, so all of it
    preceded the ``initialize`` reply (measured 1111.0-1114.9 ms against a reply
    at 1116.3 ms). That work now lives in
    :mod:`trw_mcp.server._boot_deferred`, scheduled after the client's
    ``initialized`` notification, with a first-tool-call inline fallback.

    What remains here is exactly task lifecycle: record the serving loop so the
    deferred step can create its task on it, and cancel that task at shutdown.
    Anything added before ``yield`` goes back on the handshake critical path,
    which is what ``tests/test_boot_initialize_ordering.py`` exists to catch.
    """
    from trw_mcp.server._boot_deferred import cancel_sync_task, remember_serving_loop

    remember_serving_loop(asyncio.get_running_loop())
    try:
        yield
    finally:
        sync_task = cancel_sync_task()
        if sync_task is not None:
            with suppress(asyncio.CancelledError):
                await sync_task


def _resolve_deferred_budget_ms() -> int:
    """Read ``boot_deferred_work_budget_ms``, falling back to its field default."""
    config = _try_load_config()
    if config is not None:
        return int(config.boot_deferred_work_budget_ms)
    return int(TRWConfig.model_fields["boot_deferred_work_budget_ms"].default)


def create_app(
    *,
    instructions: str | None = None,
    middleware: list[object] | None = None,
) -> FastMCP:
    """Create a new FastMCP application instance.

    Args:
        instructions: Override server instructions. Uses centralized messages by default.
        middleware: Override middleware list. Uses default chain by default.

    Returns:
        Configured FastMCP instance. The default middleware chain carries
        ``BootDeferralMiddleware``, so the object this returns is the one the
        FR01/FR02 contract tests drive the ordering invariant through.
    """
    return FastMCP(
        "trw",
        instructions=instructions or _load_server_instructions(),
        middleware=middleware if middleware is not None else _build_middleware(),  # type: ignore[arg-type]
        lifespan=_build_sync_lifespan,
    )


# ── Module-level singletons ─────────────────────────────────────────
# _mcp_security holds the MCPSecurityMiddleware instance (PRD-INFRA-SEC-001
# FR-6/FR-9) when startup succeeds; it is None otherwise (observe-mode
# fail-open). Transports consult this for per-dispatch security events.
_mcp_security: object | None = None
# PRD-CORE-248 FR02: everything above this line is dependency import — fastmcp,
# trw_memory, config, middleware. 99% of the pre-`initialize` window is spent
# here, and until now nothing said so.
emit_boot_phase("import_complete")
mcp = create_app()

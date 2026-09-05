"""Freeze the connected MCP process's live fingerprint at startup (PRD-INFRA-164 FR07).

This is the FastMCP adapter for the standard-library-only
``trw_mcp.canons.fingerprint`` core: it resolves the loaded package/canon/
template versions, reads the registry-managed bundled source digests, and lists
the *registered* MCP surface, then freezes an immutable process fingerprint.
Volatile metadata (timestamp, PID, checkout path, discovery order, secrets) is
excluded by the core, so two identical surfaces in different locations/orders
produce the same digest.

The freeze runs once, after all tools/resources/prompts are registered
(``server/_tools.py``), and enumerates them with ``run_middleware=False`` —
this call happens at MODULE IMPORT time, before any real client has connected,
and FastMCP cannot distinguish that self-check from a genuine request (it
synthesizes its own ``Context`` either way), so running the full middleware
chain here made merely IMPORTING the package write a real
``MCPSecurityMiddleware`` audit event to disk for every registered tool. The
per-session exposure masks (``SurfaceAuthorityMiddleware``,
``PhaseExposureMiddleware``) are consequently not reflected in this
fingerprint's surface — acceptable because its purpose is DRIFT DETECTION
(does this process's deployed code match a prior digest), which needs the
registered surface to be deterministic across restarts of the same code, not
an authoritative statement about what one particular session can see.

It is fully fail-safe: any failure leaves the frozen fingerprint UNSET so
currentness comparison reports UNKNOWN (never a false-green), and server boot
is never blocked.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

import structlog
from fastmcp import FastMCP

from trw_mcp.canons.fingerprint import (
    ProcessFingerprint,
    PublicPromptDecl,
    PublicResourceDecl,
    PublicToolDecl,
    RealizedSurface,
    digest_loaded_modules,
    freeze_fingerprint,
    get_frozen_fingerprint,
    set_frozen_fingerprint,
)
from trw_mcp.canons.registry import (
    bundled_source_version,
    load_registry,
    managed_source_digests,
    template_artifact,
)

logger = structlog.get_logger(__name__)

_UNKNOWN = "unknown"
_AsyncResultT = TypeVar("_AsyncResultT")


def _run_async(coro: Coroutine[object, object, _AsyncResultT]) -> _AsyncResultT:
    """Run an async coroutine from sync startup code (mirror of _tools._run_async)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _package_version() -> str:
    try:
        from importlib.metadata import version as pkg_version

        return pkg_version("trw-mcp")
    except Exception:  # justified: editable/uninstalled -> fall back to __version__
        try:
            from trw_mcp import __version__

            return __version__
        except Exception:  # justified: last-resort, never crash boot
            return _UNKNOWN


def resolve_loaded_versions() -> tuple[str, str, str, str]:
    """Return (trw_mcp_version, framework_version, aaref_version, template_version).

    Framework/AARE-F come from the effective loaded config (the values the
    process actually runs under); the template version is read deterministically
    from the bundled template body. A missing token is ``"unknown"`` — never a
    borrowed/plausible value (NFR07).
    """
    from trw_mcp.models.config import get_config

    config = get_config()
    framework_version = str(getattr(config, "framework_version", "") or _UNKNOWN)
    aaref_version = str(getattr(config, "aaref_version", "") or _UNKNOWN)
    try:
        registry = load_registry()
        template_version = bundled_source_version(template_artifact(registry)) or _UNKNOWN
    except Exception:  # justified: unreadable template -> unknown, never crash
        template_version = _UNKNOWN
    return _package_version(), framework_version, aaref_version, template_version


def _tool_decls(server: FastMCP) -> tuple[PublicToolDecl, ...]:
    decls: list[PublicToolDecl] = []
    # ``run_middleware=False``: this call runs once, at MODULE IMPORT time
    # (``server/_tools.py``'s ``_register_tools()``), before any real client
    # has connected. FastMCP synthesizes its own ``Context`` for a
    # middleware-enabled ``list_tools()`` call whether or not a genuine
    # client is asking, so ``MCPSecurityMiddleware`` could not tell this
    # self-check apart from a real request — it wrote a real audit event to
    # disk for every registered tool on every process start (merely
    # importing the package wrote to ``.trw/context/``). This returns the
    # REGISTERED surface (session transforms + enabled-filter still apply,
    # just not the per-session masking middlewares add) — sufficient for
    # this fingerprint's purpose, which is DRIFT DETECTION via digest
    # comparison across restarts of the same deployed code, not an
    # authoritative statement about what one particular session can see.
    for tool in _run_async(server.list_tools(run_middleware=False)):
        input_schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {}
        output_schema = getattr(tool, "outputSchema", None) or getattr(tool, "output_schema", None) or {}
        decls.append(
            PublicToolDecl(
                name=str(getattr(tool, "name", "")),
                description=str(getattr(tool, "description", "") or ""),
                input_schema=dict(input_schema) if isinstance(input_schema, dict) else {},
                output_schema=dict(output_schema) if isinstance(output_schema, dict) else {},
            )
        )
    return tuple(decls)


def _resource_decls(server: FastMCP) -> tuple[PublicResourceDecl, ...]:
    return tuple(
        PublicResourceDecl(
            uri=str(getattr(res, "uri", "")),
            name=str(getattr(res, "name", "") or ""),
            description=str(getattr(res, "description", "") or ""),
        )
        for res in _run_async(server.list_resources(run_middleware=False))
    )


def _prompt_decls(server: FastMCP) -> tuple[PublicPromptDecl, ...]:
    return tuple(
        PublicPromptDecl(
            name=str(getattr(prompt, "name", "")),
            description=str(getattr(prompt, "description", "") or ""),
        )
        for prompt in _run_async(server.list_prompts(run_middleware=False))
    )


def build_realized_surface(server: FastMCP) -> RealizedSurface:
    """Read the registered public tool/resource/prompt surface (module docstring: why
    this is ``run_middleware=False``, not the per-session exposure-filtered surface).
    """
    return RealizedSurface(
        tools=_tool_decls(server),
        resources=_resource_decls(server),
        prompts=_prompt_decls(server),
    )


def freeze_live_process_fingerprint(server: FastMCP) -> ProcessFingerprint | None:
    """Freeze the live-process fingerprint and store it for the process lifetime.

    Returns the frozen fingerprint, or ``None`` if construction failed (in which
    case the frozen fingerprint stays UNSET and currentness reports UNKNOWN).
    Never raises — server boot must not be blocked by fingerprint construction.
    """
    # Registration is intentionally idempotent. Once startup has frozen a
    # generation, a repeated registrar call must return that same identity
    # rather than recomputing it from a later/larger ``sys.modules`` set and
    # falsely reporting mixed bytes inside one healthy process.
    existing = get_frozen_fingerprint()
    if existing is not None:
        return existing

    try:
        registry = load_registry()
        trw_mcp_version, framework_version, aaref_version, template_version = resolve_loaded_versions()
        surface = build_realized_surface(server)
        fingerprint = freeze_fingerprint(
            trw_mcp_version=trw_mcp_version,
            framework_version=framework_version,
            aaref_version=aaref_version,
            template_version=template_version,
            registry_digest=registry.digest,
            source_digests=managed_source_digests(registry),
            loaded_module_digest=digest_loaded_modules(),
            surface=surface,
        )
    except Exception:  # justified: fail-safe, unknown fingerprint > false-current
        logger.warning("live_process_fingerprint_freeze_failed", exc_info=True)
        return None

    set_frozen_fingerprint(fingerprint)
    logger.info(
        "live_process_fingerprint_frozen",
        digest=fingerprint.digest,
        loaded_module_digest=fingerprint.loaded_module_digest,
        framework_version=framework_version,
        tool_count=len(surface.tools),
        resource_count=len(surface.resources),
        prompt_count=len(surface.prompts),
    )
    return fingerprint


__all__ = [
    "build_realized_surface",
    "freeze_live_process_fingerprint",
    "resolve_loaded_versions",
]

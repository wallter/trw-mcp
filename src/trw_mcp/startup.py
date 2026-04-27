"""Security bootstrap helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import structlog

from trw_mcp.middleware.mcp_security import MCPSecurityMiddleware
from trw_mcp.models.config._sub_models import MCPSecurityConfig
from trw_mcp.security.anomaly_detector import AnomalyDetector, AnomalyDetectorConfig
from trw_mcp.security.capability_scope import scope_from_allowed_tool
from trw_mcp.security.mcp_registry import (
    MCPRegistry,
    MCPSecurityConfigError,
    bundled_allowlist_path,
    bundled_public_key_path,
)
from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir

logger = structlog.get_logger(__name__)


def _resolve_security_anchor(start: Path | None = None) -> Path:
    candidate = (start or Path.cwd()).resolve()
    configured_root = resolve_project_root()
    if configured_root.exists() and ((configured_root / ".trw").exists() or (configured_root / ".git").exists()):
        return configured_root
    for parent in (candidate, *candidate.parents):
        if (parent / ".trw").exists():
            return parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607 — git is on PATH; partial-path is intentional
            check=True,
            capture_output=True,
            text=True,
            cwd=candidate,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        raise MCPSecurityConfigError(
            f"relative MCP security path requires an anchored project root: {candidate}"
        ) from None
    return Path(result.stdout.strip()).resolve()


def _resolve_repo_anchored_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path.resolve()
    if path.parts and path.parts[0] == ".trw":
        return (_resolve_security_anchor() / path).resolve()
    if path.parts and path.parts[0] == "data":
        return (bundled_allowlist_path().parent / path.relative_to("data")).resolve()
    return (_resolve_security_anchor() / path).resolve()


def init_security(config: MCPSecurityConfig | None = None) -> MCPSecurityMiddleware:
    """Build the mounted MCP security middleware or raise fail-loud."""

    resolved = config or MCPSecurityConfig()
    canonical_path = _resolve_repo_anchored_path(resolved.allowlist_path)
    if not canonical_path.exists():
        raise MCPSecurityConfigError(f"MCP allowlist not found: {canonical_path}")
    overlay_path = _resolve_repo_anchored_path(resolved.operator_overlay_path)
    operator_key_path = (
        _resolve_repo_anchored_path(resolved.operator_public_key) if resolved.operator_public_key else None
    )
    registry = MCPRegistry.load(
        canonical_path=canonical_path,
        canonical_public_key_path=bundled_public_key_path(),
        overlay_path=overlay_path,
        operator_public_key_path=operator_key_path,
        allow_unsigned=resolved.allow_unsigned,
    )
    if resolved.allow_unsigned:
        logger.warning("mcp_allow_unsigned_enabled", outcome="audit_required")
    scopes = {
        tool.name: scope_from_allowed_tool(server.name, tool)
        for server in registry.allowlist.servers
        for tool in server.allowed_tools
    }
    trw_dir = resolve_trw_dir()
    audit_dir = _resolve_repo_anchored_path(resolved.audit_log_path)
    return MCPSecurityMiddleware(
        registry=registry,
        scopes=scopes,
        anomaly_detector=AnomalyDetector(
            config=AnomalyDetectorConfig(
                mode=resolved.anomaly.mode,
                sigma_threshold=resolved.anomaly.sigma_threshold,
                window_seconds=resolved.anomaly.window_seconds,
                shadow_clock_path=trw_dir / "security" / "mcp_shadow_start.yaml",
                baseline_store_path=trw_dir / "security" / "mcp_arg_baseline.jsonl",
            ),
            run_dir=None,
            fallback_dir=audit_dir,
        ),
        run_dir=None,
        fallback_dir=audit_dir,
        default_server_name="trw",
        enforce=resolved.enforce,
        quarantine_auto_release=resolved.quarantine.auto_release,
    )


__all__ = ["init_security"]

"""Deployed framework integrity check for ``trw-mcp doctor``.

This adapter runs as a FRESH CLI process: it inspects deployed disk state and
the registry-bound generation, but it CANNOT attest to a separately connected,
already-running stdio MCP process — that process's frozen live fingerprint is
the evidence for its own currentness (PRD-INFRA-164 FR09). Bundled sources and
the registry digest derive from the typed ``trw_mcp.canons.registry`` view so
the doctor shares one canon authority with source parity and runtime checks.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.canons.registry import (
    CanonRegistryError,
    bundled_source_bytes,
    load_registry,
)
from trw_mcp.framework_integrity import inspect_framework_runtime


def check_framework_integrity(
    target: Path,
    *,
    framework_version: str,
    aaref_version: str,
) -> tuple[str, str]:
    """Return doctor status/message without mutating deployed state."""
    trw_dir = target / ".trw"
    if not trw_dir.exists():
        return "SKIP", "runtime frameworks not installed yet; run 'trw-mcp init-project .'."

    try:
        registry = load_registry()
        framework_source = bundled_source_bytes(registry.artifact("framework")).decode("utf-8")
        aaref_source = bundled_source_bytes(registry.artifact("aaref")).decode("utf-8")
    except (CanonRegistryError, KeyError, OSError, UnicodeError) as exc:
        return "FAIL", f"bundled framework source/registry unreadable: {exc}"

    report = inspect_framework_runtime(
        target,
        framework_source=framework_source,
        aaref_source=aaref_source,
        framework_version=framework_version,
        aaref_version=aaref_version,
        registry_digest=registry.digest,
    )
    if report.errors:
        detail = "; ".join(report.errors[:4])
        extra = f" (+{len(report.errors) - 4} more)" if len(report.errors) > 4 else ""
        return (
            "FAIL",
            f"deployed framework integrity mismatch: {detail}{extra}. "
            "Update explicit version pins, then run 'trw-mcp update-project .' or start a new trw_init.",
        )
    if report.warnings:
        return "WARN", "; ".join(report.warnings)
    from trw_mcp.server._version_status_layers import FRESH_CLI_ATTEST_NOTE

    return (
        "PASS",
        f"effective config, deployed bodies, and VERSION.yaml agree ({framework_version}; AARE-F {aaref_version}). "
        f"{FRESH_CLI_ATTEST_NOTE}",
    )


__all__ = ["check_framework_integrity"]

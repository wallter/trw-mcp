"""Codex T2 tool-return enrichment for trw_before_edit_hint.

# Managed by TRW — no trw_distill imports permitted.

Provides T2 payload construction when TRW_CLIENT_PROFILE=codex is set.
Detection MUST use TRW_CLIENT_PROFILE env var only — NOT ctx.client_profile
(audit P0-01: FastMCP ctx.client_profile not yet propagated).

Wired into before_edit_hint.py via resolve_client_profile() from
tools/_client_detection.py.

PRD-DIST-2402 FR05, FR06.
"""

from __future__ import annotations

from typing import Any

import structlog

from trw_mcp.channels._distill_telemetry import resolve_client_profile

log = structlog.get_logger(__name__)

__all__ = [
    "build_t2_payload",
    "get_default_tier_for_codex",
]


def get_default_tier_for_codex() -> str:
    """Return the default tier for Codex client.

    T2 is the default for Codex (token-budget-aware, includes importers,
    co-change neighbors, inferred tests, risk score).

    Returns:
        ``"T2"``
    """
    return "T2"


def is_codex_client() -> bool:
    """Return True when TRW_CLIENT_PROFILE=codex is set.

    Uses env-var detection only per audit P0-01.

    Returns:
        True if the active client profile is codex.
    """
    profile = resolve_client_profile()
    return profile == "codex"


def build_t2_payload(
    file_path: str,
    sidecar_data: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build a T2 enrichment payload for the given file path.

    T2 payload includes: importers, co_change_neighbors, inferred_tests,
    risk_score, and hotspot_warnings — sourced from the sidecar.

    When sidecar_data is None or the file is not found in the hotspot
    index, returns None (caller should fall back to T1 behavior).

    Args:
        file_path: The file path being edited.
        sidecar_data: Parsed sidecar payload, or None if absent.

    Returns:
        T2 payload dict or None if unavailable.
    """
    if sidecar_data is None:
        log.debug(
            "codex_t2_payload_sidecar_absent",
            file_path=file_path,
            outcome="sidecar_absent",
        )
        return None

    hotspots: list[dict[str, Any]] = sidecar_data.get("hotspots", [])
    file_map: dict[str, dict[str, Any]] = sidecar_data.get("file_map", {})

    # Try direct file_map lookup first (O(1)), then scan hotspots list.
    entry: dict[str, Any] | None = file_map.get(file_path)
    if entry is None:
        for h in hotspots:
            h_path = h.get("file", h.get("path", ""))
            if h_path == file_path:
                entry = h
                break

    if entry is None:
        log.debug(
            "codex_t2_payload_file_not_in_sidecar",
            file_path=file_path,
            outcome="not_in_sidecar",
        )
        return None

    importers: list[str] = entry.get("importers", [])
    co_change_neighbors: list[str] = entry.get("co_change_neighbors", [])
    inferred_tests: list[str] = entry.get("inferred_tests", [])
    risk_score: float | None = entry.get("risk_score", entry.get("score"))
    hotspot_warnings: list[str] = entry.get("warnings", [])

    payload: dict[str, Any] = {
        "file_path": file_path,
        "importers": importers,
        "co_change_neighbors": co_change_neighbors,
        "inferred_tests": inferred_tests,
        "risk_score": risk_score,
        "hotspot_warnings": hotspot_warnings,
        "tier": "T2",
    }

    log.debug(
        "codex_t2_payload_built",
        file_path=file_path,
        importers_count=len(importers),
        neighbors_count=len(co_change_neighbors),
        outcome="ok",
    )

    return payload

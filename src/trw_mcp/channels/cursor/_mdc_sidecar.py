"""Sidecar data extraction helpers for the Cursor MDC emitter.

Extracted from _mdc_emitter.py to satisfy the 350 effective-LOC gate.
All functions are pure data transformations over the sidecar dict.

PRD-DIST-2401 Phase B.
"""

from __future__ import annotations

from typing import Any

from trw_mcp.channels.cursor._mdc_templates import (
    ConventionRecord,
    EdgeCaseRecord,
    HotspotRecord,
)

__all__ = [
    "extract_conventions",
    "extract_edge_cases",
    "extract_edge_cases_for_dir",
    "extract_hotspots",
    "get_sidecar_sha",
    "get_sidecar_ts",
]


def get_sidecar_sha(sidecar: dict[str, Any]) -> str:
    """Extract the git SHA from a sidecar dict."""
    return str(sidecar.get("sha", "unknown"))


def get_sidecar_ts(sidecar: dict[str, Any]) -> str:
    """Extract the generated_at timestamp from a sidecar dict."""
    payload = sidecar.get("payload") or sidecar
    return str(payload.get("generated_at", sidecar.get("generated_at", "unknown")))


def extract_conventions(sidecar: dict[str, Any]) -> list[ConventionRecord]:
    """Extract convention records from a sidecar dict."""
    payload = sidecar.get("payload") or sidecar
    raw = payload.get("conventions", [])
    return [
        ConventionRecord(
            slug=str(item.get("slug", "")),
            title=str(item.get("title", "")),
            body=str(item.get("body", "")),
        )
        for item in raw
        if isinstance(item, dict)
    ]


def extract_hotspots(sidecar: dict[str, Any]) -> list[HotspotRecord]:
    """Extract hotspot records from a sidecar dict."""
    payload = sidecar.get("payload") or sidecar
    raw = payload.get("hotspots", [])
    return [
        HotspotRecord(
            file_path=str(item.get("file_path", "")),
            risk_score=float(item.get("risk_score", 0.0)),
            reason=str(item.get("reason", "")),
        )
        for item in raw
        if isinstance(item, dict)
    ]


def extract_edge_cases(sidecar: dict[str, Any]) -> list[EdgeCaseRecord]:
    """Extract both survivor and undocumented edge-case records from a sidecar."""
    payload = sidecar.get("payload") or sidecar
    records: list[EdgeCaseRecord] = []
    for key, survived in [("edge_case_survivors", True), ("edge_case_undocumented", False)]:
        records.extend(
            EdgeCaseRecord(
                file_path=str(item.get("file_path", "")),
                description=str(item.get("description", "")),
                survived=survived,
            )
            for item in payload.get(key, [])
            if isinstance(item, dict)
        )
    return records


def extract_edge_cases_for_dir(
    sidecar: dict[str, Any], directory: str
) -> list[EdgeCaseRecord]:
    """Extract edge-case records that belong to a specific directory."""
    return [
        ec
        for ec in extract_edge_cases(sidecar)
        if ec.file_path.replace("\\", "/").rsplit("/", 1)[0] == directory
    ]

"""Shared helpers for split telemetry publisher tests."""

from __future__ import annotations

from pathlib import Path

import ruamel.yaml

from trw_mcp.models.config import TRWConfig


def _make_config(
    *,
    platform_url: str = "https://api.example.com",
    platform_urls: list[str] | None = None,
    platform_telemetry_enabled: bool = True,
) -> TRWConfig:
    kwargs: dict[str, object] = {
        "platform_telemetry_enabled": platform_telemetry_enabled,
    }
    if platform_urls is not None:
        kwargs["platform_urls"] = platform_urls
    else:
        kwargs["platform_url"] = platform_url
    return TRWConfig(**kwargs)


def _write_learning(entries_dir: Path, filename: str, data: dict[str, object]) -> None:
    """Write a learning YAML file to the entries directory."""
    entries_dir.mkdir(parents=True, exist_ok=True)
    yaml = ruamel.yaml.YAML()
    with (entries_dir / filename).open("w", encoding="utf-8") as fh:
        yaml.dump(data, fh)


def _make_learning(
    *,
    status: str = "active",
    impact: float = 0.9,
    summary: str = "Test learning summary",
    detail: str = "Test learning detail",
    tags: list[str] | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "impact": impact,
        "summary": summary,
        "detail": detail,
        "tags": tags or ["testing"],
    }

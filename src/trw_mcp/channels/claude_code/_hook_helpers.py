"""CC-03/CC-04 hook helper utilities for the Claude Code channel.

Belongs to the ``channels/claude_code`` package (PRD-DIST-2405 FR06, FR29).

Provides:
- ``_CEREMONY_MODE_FIELD``: authoritative config field name (FR06).
- ``read_cc03_config()``: reads CC-03 opt-in flag and skip extensions.
- ``format_t0_beacon()``, ``format_t1_hint()``, ``format_t2_hint()``:
  tier-appropriate hook output formatters.
- ``write_hint_file()``: writes per-hint context JSON keyed on tool_use_id (FR29).
- ``prune_hint_files()``: removes hint files older than TTL (FR35).

Authoritative field name (P1-02 fix):
  ``python -c "from trw_mcp.models.config._fields_ceremony import _CeremonyFields;
  print(list(_CeremonyFields.model_fields))"`` confirms ``ceremony_mode`` exists
  (values: ``"full"`` | ``"light"``).  ``enforcement_variant`` is a SEPARATE field
  (values are per-client baseline strings, unrelated to the ceremony gate).
  The CC-02 gate uses ``ceremony_mode``.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

__all__ = [
    "CC03_HINTS_DIR",
    "DEFAULT_SKIP_EXTENSIONS",
    "_CEREMONY_MODE_FIELD",
    "format_t0_beacon",
    "format_t1_hint",
    "format_t2_hint",
    "prune_hint_files",
    "read_cc03_config",
    "write_hint_file",
]

# --- P1-02 resolution: authoritative field name ---
# Verified by inspecting trw_mcp.models.config._fields_ceremony.model_fields:
#   ceremony_mode: Literal["full", "light"] = "full"
# enforcement_variant is a separate, per-baseline string field.
# This constant is referenced by tests and the CC-02 segment gate.
_CEREMONY_MODE_FIELD: str = "ceremony_mode"

# Directory for per-hint context files (P1-04: tool_use_id-keyed)
CC03_HINTS_DIR: str = ".trw/context/cc03-hints"

# P0-10 fix: safe-to-skip extensions ALLOWLIST (extensions that never get hint)
DEFAULT_SKIP_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".txt", ".rst", ".lock", ".log", ".gitignore"}
)

# Hint file TTL for pruning
_HINT_FILE_TTL_SECONDS: int = 86400  # 24 hours


def read_cc03_config(repo_root: Path) -> dict[str, Any]:
    """Read CC-03 configuration from ``.trw/config.yaml``.

    Returns a dict with:
      - ``cc03_hook_enabled``: bool (default False — opt-in)
      - ``skip_extensions``: set of extensions to skip
      - ``cc03_t0_silent``: bool (default False)
      - ``debounce_seconds``: int (default 180)

    Fail-open: any read/parse error returns safe defaults.
    """
    config_path = repo_root / ".trw" / "config.yaml"
    defaults: dict[str, Any] = {
        "cc03_hook_enabled": False,
        "skip_extensions": set(DEFAULT_SKIP_EXTENSIONS),
        "cc03_t0_silent": False,
        "debounce_seconds": 180,
    }

    if not config_path.exists():
        return defaults

    try:
        # Use ruamel or simple yaml parse
        try:
            from ruamel.yaml import YAML

            yaml_parser = YAML(typ="safe")
            raw = yaml_parser.load(config_path.read_text(encoding="utf-8"))
        except ImportError:
            import yaml

            raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))

        if not isinstance(raw, dict):
            return defaults

        # Top-level cc03 key or channels.cc03 nesting
        channels_cfg = raw.get("channels", {})
        if isinstance(channels_cfg, dict):
            cc03_cfg = channels_cfg.get("cc03", {})
            if isinstance(cc03_cfg, dict):
                defaults["cc03_hook_enabled"] = bool(
                    channels_cfg.get("cc03_hook_enabled", cc03_cfg.get("enabled", False))
                )
                custom_exts = cc03_cfg.get("skip_extensions")
                if isinstance(custom_exts, list):
                    defaults["skip_extensions"] = set(custom_exts)
                defaults["cc03_t0_silent"] = bool(cc03_cfg.get("t0_silent", False))
                defaults["debounce_seconds"] = int(cc03_cfg.get("debounce_seconds", 180))
            else:
                defaults["cc03_hook_enabled"] = bool(
                    channels_cfg.get("cc03_hook_enabled", False)
                )

        # Also check top-level cc03_hook_enabled (FR09)
        if "cc03_hook_enabled" in raw:
            defaults["cc03_hook_enabled"] = bool(raw["cc03_hook_enabled"])

    except Exception:
        pass

    return defaults


def format_t0_beacon() -> str:
    """Format T0 presence beacon output (≤ 20 tokens)."""
    return "[TRW] Distill intelligence available — run trw_before_edit_hint for details."


def format_t1_hint(learnings: list[dict[str, Any]]) -> str:
    """Format T1 hint output from learnings (≤ 60 tokens).

    Args:
        learnings: List of learning dicts with ``summary`` and optionally ``detail``.

    Returns:
        Formatted hint string ≤ 60 tokens.
    """
    if not learnings:
        return "[TRW] No learnings found. Call trw_before_edit_hint for more context."

    lines: list[str] = ["[TRW Distill Hint — T1]"]
    for learning in learnings[:2]:
        summary = str(learning.get("summary", ""))
        if summary:
            lines.append(f"  - {summary[:80]}")
    lines.append("  Call trw_before_edit_hint for full context.")

    return "\n".join(lines)


def format_t2_hint(
    *,
    file_path: str,
    risk_score: float | None,
    hotspot_warnings: list[str],
    co_change_neighbors: list[str],
    inferred_tests: list[str],
) -> str:
    """Format T2 hint output (≤ 80 tokens / ~320 chars).

    Args:
        file_path: The file being edited.
        risk_score: Distill risk score (0.0-1.0) or None.
        hotspot_warnings: List of hotspot warning strings.
        co_change_neighbors: Related files that co-change.
        inferred_tests: Inferred test file paths.

    Returns:
        Formatted hint string ≤ 80 tokens.
    """
    lines: list[str] = ["[TRW Distill Hint — T2]"]

    if risk_score is not None:
        lines.append(f"  RISK: {risk_score:.2f}")

    lines.extend(f"  WARN: {warn[:60]}" for warn in hotspot_warnings[:3])

    if co_change_neighbors:
        neighbors_str = ", ".join(co_change_neighbors[:2])
        lines.append(f"  CO-CHANGE: {neighbors_str}")

    if inferred_tests:
        lines.append(f"  TESTS: {inferred_tests[0]}")

    content = "\n".join(lines)

    # Hard cap enforcement (FR32: ~320 chars ≈ 80 tokens)
    if len(content) > 320:
        content = content[:317] + "..."

    return content


def write_hint_file(
    *,
    hints_dir: Path,
    tool_use_id: str,
    file_path: str,
    tier: str,
    hint_emitted: bool,
    tokens_emitted: int,
    distill_status: str,
) -> None:
    """Write a per-hint context file keyed on *tool_use_id* (P1-04 fix).

    Args:
        hints_dir: Directory to write hint files into (created if absent).
        tool_use_id: The PreToolUse tool_use_id from Claude Code stdin.
        file_path: Absolute path of the file being hinted.
        tier: Tier string ("T0", "T1", "T2").
        hint_emitted: Whether a hint was actually emitted.
        tokens_emitted: Estimated token count of the hint.
        distill_status: Distill status string from BeforeEditHintResult.
    """
    hints_dir.mkdir(parents=True, exist_ok=True)
    hint_file = hints_dir / f"{tool_use_id}.json"
    record = {
        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "file_path": file_path,
        "tier": tier,
        "hint_emitted": hint_emitted,
        "tokens_emitted": tokens_emitted,
        "distill_status": distill_status,
        "tool_use_id": tool_use_id,
    }
    hint_file.write_text(json.dumps(record), encoding="utf-8")


def prune_hint_files(hints_dir: Path, ttl_seconds: int = _HINT_FILE_TTL_SECONDS) -> int:
    """Remove hint files older than *ttl_seconds* from *hints_dir*.

    Args:
        hints_dir: Directory containing per-hint context files.
        ttl_seconds: Maximum age in seconds before pruning.

    Returns:
        Number of files removed.
    """
    if not hints_dir.exists():
        return 0

    now = time.time()
    removed = 0
    for hint_file in hints_dir.glob("*.json"):
        try:
            if (now - hint_file.stat().st_mtime) > ttl_seconds:
                hint_file.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass

    return removed

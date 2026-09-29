"""The ``distill_ingest`` row of ``trw-mcp doctor``: last incremental-ingest outcome.

Belongs to the ``_subcommands_doctor.py`` catalogue; kept in a sibling for the
eLOC gate, and separate from ``_doctor_distill.py`` (install-state row) and
``_doctor_hook_channel.py`` (owned by a different lane) so each stays a
single-file, single-concern change.

2026-09-27 audit (touchpoint #3): ``post_commit_distill_incremental`` spawns
``trw-distill run --incremental --live-ingest`` fully detached. When the
default model isn't pulled or the embedding isn't cached, that child exits at
its doctor preflight and the failure went ONLY to ``continuous.jsonl`` — an
append log nobody scans — while this project's own doctor stayed silent.
``trw_distill.cli._ingest_status`` now writes a current-state receipt at
``<repo>/.trw/distill/ingest-status.json`` on every preflight outcome; this
row reads that JSON file directly. Zero ``trw_distill`` import (AGENTS.md IP
boundary) — the relative path and field names ARE the contract.

Never FAILs: an ingest failure here is the SAME optional, proprietary,
opt-in feature the ``distill`` row already treats as non-fatal (default off;
PRD says the lesson-quality gate is a pending operator decision, not this
row's job).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

__all__ = ["INGEST_STATUS_REL", "distill_ingest_row"]

#: Mirrors ``trw_distill.cli._ingest_status.INGEST_STATUS_REL`` relative to
#: ``<repo>/.trw`` (the default ``--trw-dir``). Kept as a plain literal, not
#: an import, per the zero-``trw_distill``-import boundary this row exists to
#: respect.
INGEST_STATUS_REL: Path = Path(".trw") / "distill" / "ingest-status.json"


def distill_ingest_row(target: Path, _config: object) -> tuple[Literal["PASS", "WARN", "SKIP"], str]:
    """SKIP when no ingest has run yet; WARN on a preflight failure; PASS otherwise."""
    status_path = target / INGEST_STATUS_REL
    try:
        raw = status_path.read_text(encoding="utf-8")
    except OSError:
        return "SKIP", "no distill incremental-ingest run has recorded an outcome yet"
    try:
        payload = json.loads(raw)
    except ValueError:
        return "WARN", f"{status_path} is not valid JSON"
    if not isinstance(payload, dict):
        return "WARN", f"{status_path} did not contain a JSON object"

    outcome = payload.get("outcome")
    if outcome == "preflight_failed":
        flag = payload.get("flag", "post_commit_distill_incremental")
        checked_at = payload.get("checked_at", "unknown time")
        fix_commands = payload.get("fix_commands") or []
        missing = payload.get("missing_models") or []
        fix_text = "; ".join(str(c) for c in fix_commands) or "see `trw-distill doctor` for the failing checks"
        missing_text = f" (missing: {', '.join(str(m) for m in missing)})" if missing else ""
        return (
            "WARN",
            f"last incremental ingest ({checked_at}) failed its preflight{missing_text}. "
            f"Fix: {fix_text}. Disable ingest with `{flag}: false` in .trw/config.yaml.",
        )
    if outcome == "ok":
        checked_at = payload.get("checked_at", "unknown time")
        return "PASS", f"last incremental-ingest preflight ({checked_at}) passed"
    return "WARN", f"{status_path} has an unrecognized outcome: {outcome!r}"

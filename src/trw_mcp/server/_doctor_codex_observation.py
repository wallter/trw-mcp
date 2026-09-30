"""``codex_observation`` doctor row: can TRW read what codex reports it ran? (CODEX-P0-A S3).

Belongs to the ``_subcommands_doctor.py`` catalogue; a sibling so that module stays under the
effective-LOC gate. It reads the NEWEST codex session rollout with the same bounded, keys-only reader
the dispatch record uses, so a codex release that changes the rollout format shows here as a WARN
before every dispatch's ``observed`` silently turns ``unknown``. Client-reported evidence only.
"""

from __future__ import annotations

from typing import Literal

__all__ = ["codex_observation_row"]

RowStatus = Literal["PASS", "WARN", "SKIP"]  # never FAIL: evidence availability, not a defect


def codex_observation_row() -> tuple[RowStatus, str]:
    """``(status, message)``: SKIP without a sessions dir, WARN when the newest rollout lacks the keys."""
    from trw_mcp.dispatch._codex_observed import codex_home_dir, newest_rollout, read_rollout

    home = codex_home_dir()
    if home is None:
        return "WARN", "CODEX_HOME is relative or contains '..'; refused, so observed model/effort will be unknown."
    if not (home / "sessions").is_dir():
        return "SKIP", f"no codex sessions dir under {home}; a codex dispatch's observed model/effort will be unknown."
    newest = newest_rollout(home)
    if newest is None:
        return "SKIP", "no codex rollout yet; a codex dispatch's observed model/effort will be unknown."
    seen = read_rollout(home, newest)
    if seen.get("reason") == "rollout_unreadable":
        return "WARN", "newest codex rollout is not a readable regular file inside the sessions dir; refused."
    missing = [key for key in ("model", "effort") if seen[key] == "unknown"]
    if missing:
        return "WARN", (
            f"newest codex rollout (cli {seen['cli_version']}) has no {'/'.join(missing)}; "
            "observed values will be unknown (codex rollout format may have changed)."
        )
    return "PASS", f"codex rollout readable (cli {seen['cli_version']}); observed model/effort are client-reported."

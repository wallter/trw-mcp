"""Degenerate-result advisory tunables (PRD-CORE-250-FR10).

Domain mixin. Split from ``_fields_ceremony.py`` because that file sat against
the 200-raw-line domain-mixin ceiling
(``tests/test_config_fields.py::test_domain_mixin_files_under_200_lines``), and
because these five knobs are one subject: the shipped PostToolUse advisory
that flags truncated/undated/stale-looking tool results.

Read by the shipped PostToolUse adapter (data/hooks/post-tool-degenerate-result.sh)
through one lib-trw.sh accessor, `trw_degenerate_result_setting`, which
applies env override -> .trw/config.yaml -> the default below and falls back
to the default on an unparseable value rather than silently disabling the
rule. They are tunables, not switches: the adapter has no disable knob
beyond the global HOOKS_ENABLED contract.
"""

from __future__ import annotations

from pydantic import Field


class _DegenerateResultFields:
    """Typed tunables for PRD-CORE-250 degenerate-result advisory."""

    degenerate_result_cooldown_calls: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Matching tool results to skip after an advisory fires, per session (NFR06 noise budget).",
    )
    degenerate_result_max_read_bytes: int = Field(
        default=65536,
        ge=1024,
        le=1048576,
        description="Cap on bytes read from a PostToolUse tool_response before shape classification (NFR03).",
    )
    degenerate_result_deadline_ms: int = Field(
        default=50,
        ge=5,
        le=500,
        description="Self-imposed wall-clock deadline; past it the adapter exits 0 without emitting (NFR01).",
    )
    degenerate_result_truncation_markers: list[str] = Field(
        # Harvested from real PostToolUse tool_result bodies, not guessed:
        # `.trw/compliance/degenerate-result-calibration.json` records the hit
        # counts with their N and date, including the candidates that came to
        # mind and scored ZERO ("<response clipped>", "PARTIAL view",
        # "[truncated]", "lines truncated"), which are deliberately absent.
        # Regenerate with scripts/measure_degenerate_result_calibration.py.
        default_factory=lambda: ["more lines]", "Output too large"],
        description="Literal markers whose presence means the result was capped rather than complete.",
    )
    degenerate_result_freshness_commands: list[str] = Field(
        # The noisiest rule, so it is gated on an explicit command allowlist
        # rather than a heuristic, matched as a first-line command PREFIX. That
        # distinction is what makes rule 3 shippable at all: the substring
        # matching it was drafted with blew the NFR06 budget several times over
        # on the same seven entries. Both measurements, with their N and date,
        # are in `.trw/compliance/degenerate-result-calibration.json`.
        default_factory=lambda: ["git log", "date", "ls -l", "stat", "curl", "gh api", "gh run list"],
        description="Command prefixes whose undated output cannot distinguish 'nothing there' from 'could not look'.",
    )

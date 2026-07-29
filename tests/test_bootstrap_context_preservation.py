"""`update-project` must not destroy durable `.trw/context/` artifacts (PRD-FIX-120).

`_cleanup_context_transients` swept `.trw/context/` deny-by-default: anything
not named in an 11-entry hand-maintained `_CONTEXT_ALLOWLIST` was unlinked. The
allowlist was written once and never kept in step with the writers, so a routine
`update-project` — the standard upgrade path for every installed project —
deleted production-managed state including the MCP trust registry, the
deliver-gate override audit trail, and the unified security event stream that
`trw_mcp_security_status` and the anomaly detector read.

PRD-FIX-031 asked for both halves and shipped one. Its Goals required an
allowlist "covering all active TRW context files referenced by the codebase"
(11 of ~27 covered), and FR03 named three transient globs — but phrased the
predicate as an OR, so clause (a) "not in the allowlist" deleted everything
unlisted on its own and the three patterns never changed an outcome. They were
decorative: unreachable prose in a checked-off `[x]` requirement.

The direction is now the one FIX-031's own user story asked for — "my analytics
history, build cache, and session state are **never lost**": delete what is
*known* transient, preserve everything else. Leftover junk from an old version
is cosmetic; an unlinked audit trail is unrecoverable.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.bootstrap import update_project

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401

# Durable artifacts, each grounded in the production writer that creates it.
# Names alone would rot the same way the allowlist did, so the structural test
# at the bottom re-derives this set from the source instead of trusting it.
_DURABLE: dict[str, str] = {
    "trust-registry.yaml": "state/trust.py — MCP trust-boundary decisions",
    "deliver-override-audit.jsonl": "data/hooks/pre-tool-deliver-gate.sh — record of every truthfulness-gate override",
    "events-2026-07-27.jsonl": "state/persistence.py — unified security event stream read by mcp_security_status",
    "tool_call_events.jsonl": "legacy projection, still read by mcp_security_status",
    "session-events.jsonl": "state/_session_events.py",
    "ceremony-overrides.yaml": "state/_ceremony_escalation.py — operator escalation overrides",
    "file_ownership.yaml": "tools/checkpoint.py — multi-agent coordination state",
    "nudge-analysis.json": "state/nudge_analysis.py",
    "claude_md_hash.txt": "state/claude_md/_sync_hash.py — render cache",
    "architecture.yaml": "context pack",
    "conventions.yaml": "context pack",
    "analytics-report.yaml": "analytics reporting",
    "compact_instructions.txt": "tools/checkpoint.py",
    ".sanitized_ceremony_v1": "state/_ceremony_sanitize.py — migration sentinel",
    ".trust-registry.lock": "state/_trust_outcome.py — live lock",
}


class TestDurableArtifactsSurvive:
    """The regression that matters: an upgrade must not eat production state."""

    def test_update_project_preserves_every_durable_context_artifact(self, initialized_repo: Path) -> None:
        context = initialized_repo / ".trw" / "context"
        for name in _DURABLE:
            (context / name).write_text("durable", encoding="utf-8")

        result = update_project(initialized_repo)

        destroyed = {name: why for name, why in _DURABLE.items() if not (context / name).exists()}
        assert not destroyed, (
            "update-project deleted production-managed context state; each of "
            f"these is written by live code and is not reconstructable: {destroyed}"
        )
        assert result["cleaned"] == [], (
            f"nothing here is transient, so nothing should be reported cleaned; got {result['cleaned']}"
        )

    def test_audit_trail_content_is_not_truncated(self, initialized_repo: Path) -> None:
        """Surviving as an empty file would be the same loss, quieter."""
        context = initialized_repo / ".trw" / "context"
        audit = context / "deliver-override-audit.jsonl"
        audit.write_text('{"override":"one"}\n{"override":"two"}\n', encoding="utf-8")

        update_project(initialized_repo)

        assert audit.read_text(encoding="utf-8").count("override") == 2


class TestTransientsStillRemoved:
    """The cleanup must keep doing its job — this is a redirection, not a repeal."""

    def test_globbed_transients_are_removed(self, initialized_repo: Path) -> None:
        """FR03's three patterns, observable for the first time.

        Under the old OR-predicate these names were deleted by clause (a) — for
        being unlisted, not for matching a pattern — so the patterns could have
        been deleted from the source with every test still green.
        """
        context = initialized_repo / ".trw" / "context"
        for name in (
            "tc_block_session123",
            "idle_block_lead",
            "sprint-34-findings.yaml",
        ):
            (context / name).write_text("", encoding="utf-8")

        update_project(initialized_repo)

        for name in ("tc_block_session123", "idle_block_lead", "sprint-34-findings.yaml"):
            assert not (context / name).exists(), f"{name} matches a transient pattern"

    def test_retired_exact_names_are_removed(self, initialized_repo: Path) -> None:
        context = initialized_repo / ".trw" / "context"
        (context / "velocity.yaml").write_text("", encoding="utf-8")
        (context / "tool-telemetry.jsonl").write_text("", encoding="utf-8")

        update_project(initialized_repo)

        assert not (context / "velocity.yaml").exists()
        assert not (context / "tool-telemetry.jsonl").exists()

    def test_unknown_files_are_preserved_not_guessed_at(self, initialized_repo: Path) -> None:
        """The core inversion: unrecognised means *leave alone*.

        A user's own note, or state written by a TRW version newer than the
        installer doing the update, is exactly what deny-by-default destroyed.
        """
        context = initialized_repo / ".trw" / "context"
        (context / "my-own-notes.md").write_text("mine", encoding="utf-8")

        update_project(initialized_repo)

        assert (context / "my-own-notes.md").exists()


class TestPatternsAreReachable:
    """Guard against FR03 regressing into decoration a second time."""

    def test_every_transient_pattern_can_delete_something(self, initialized_repo: Path) -> None:
        """Each declared pattern must be individually load-bearing.

        A pattern that no name can reach is prose, not behaviour — the exact
        defect class this file exists to close.
        """
        from trw_mcp.bootstrap._version_migration import _TRANSIENT_PATTERNS

        context = initialized_repo / ".trw" / "context"
        samples = {p: p.replace("*", "zz") for p in _TRANSIENT_PATTERNS}
        for name in samples.values():
            (context / name).write_text("", encoding="utf-8")

        update_project(initialized_repo)

        unreachable = {pattern: name for pattern, name in samples.items() if (context / name).exists()}
        assert not unreachable, f"these patterns are declared but delete nothing: {unreachable}"

    def test_allowlist_beats_a_pattern_match(self, initialized_repo: Path) -> None:
        """Defence in depth: a durable name wins over a future greedy pattern."""
        import fnmatch

        from trw_mcp.bootstrap._version_migration import (
            _CONTEXT_ALLOWLIST,
            _TRANSIENT_PATTERNS,
        )

        collisions = [name for name in _CONTEXT_ALLOWLIST if any(fnmatch.fnmatch(name, p) for p in _TRANSIENT_PATTERNS)]
        context = initialized_repo / ".trw" / "context"
        for name in collisions:
            (context / name).write_text("keep", encoding="utf-8")

        update_project(initialized_repo)

        for name in collisions:
            assert (context / name).exists(), f"{name} is allowlisted and must survive"


class TestNoDriftBetweenWritersAndCleanup:
    """The allowlist rotted because nothing bound it to the writers."""

    def test_no_production_context_writer_is_swept(self) -> None:
        """Re-derive the writer set from source and prove the sweep spares it.

        This is the structural guard. The bug was not one missing name — it was
        that two sides of a contract were maintained independently, so a test
        listing the names I happened to find would rot identically.
        """
        import fnmatch
        import re

        import trw_mcp
        from trw_mcp.bootstrap._version_migration import _TRANSIENT_PATTERNS

        root = Path(trw_mcp.__file__).parent
        # Matches both `/ "context" / "name.ext"` and `context_dir / "name.ext"`.
        # No dot-extension requirement: extension-less context files are real
        # (`stop_block_count`, `last_ups_phase`) and a pattern like `stop_*`
        # would sweep them while a stricter regex kept this guard green.
        writer = re.compile(r'(?:"context"|context_dir)\s*/\s*"([A-Za-z0-9_.\-]+)"')
        # Two shell forms, both in use: the literal `.../.trw/context/<name>`
        # and the `$_context_dir/<name>` idiom (pre-compact.sh, helper-idle.sh,
        # completion-gate.sh, stop-ceremony.sh). Matching only the first left
        # four hooks invisible to this guard.
        shell_writer = re.compile(r"(?:/\.trw/context/|\$\{?_context_dir\}?/)([A-Za-z0-9_.\-]+)")
        found: set[str] = set()
        for path in root.rglob("*.py"):
            if "/tests/" in path.as_posix():
                continue
            found.update(writer.findall(path.read_text(encoding="utf-8")))

        # The bundled shell hooks write here too, and scanning only *.py left the
        # single most important file uncovered: `deliver-override-audit.jsonl`,
        # the record of every truthfulness-gate override, is written by
        # `data/hooks/pre-tool-deliver-gate.sh` — so the guard protecting the
        # audit trail could not see the audit trail.
        for path in root.rglob("*.sh"):
            found.update(shell_writer.findall(path.read_text(encoding="utf-8")))

        assert found, "the writer scan found nothing — the regex has rotted"
        assert "deliver-override-audit.jsonl" in found, (
            "the shell-hook scan is not reaching data/hooks/pre-tool-deliver-gate.sh; "
            "without it this guard silently stops covering the override audit trail"
        )

        # Collisions that are correct by design. Named individually rather than
        # pattern-suppressed: an intentional overlap costs one line, and
        # anything else reaching this list is a bug.
        #
        # - hook-executions.log: PRD-FIX-031 Non-Goals states the purge is
        #   deliberate ("rotation already exists in lib-trw.sh:85; cleanup here
        #   is a one-time purge on update only").
        # - tc_block_ / idle_block_: the ceremony block files themselves
        #   (completion-gate.sh:117, helper-idle.sh:118), which ARE the
        #   transients this cleanup exists to reap — lib-trw.sh:561 also rm's
        #   them directly. A writer of a transient is expected to collide with
        #   the pattern that deletes it.
        _INTENTIONAL_PURGE = {"hook-executions.log", "tc_block_", "idle_block_"}

        swept = sorted(
            name for name in found - _INTENTIONAL_PURGE if any(fnmatch.fnmatch(name, p) for p in _TRANSIENT_PATTERNS)
        )
        assert not swept, (
            "these filenames are written by production code AND match a "
            f"transient-deletion pattern, so an update-project destroys them: {swept}"
        )

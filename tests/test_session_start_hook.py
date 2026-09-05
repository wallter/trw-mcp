"""Regression tests for SessionStart hook state clearing."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
#: Every live copy of the hook (a formerly-vendored third mirror was deleted
#: wholesale in `a77650f238`; only these two remain).
#:
#: Deliberately NOT filtered with `if path.exists()`: every copy listed here
#: MUST be present, and skipping absent ones would turn a deleted hook into a
#: silent pass. If a third distribution copy is reintroduced, add it here.
_HOOK_PATHS = (
    _ROOT.parent / ".claude" / "hooks" / "session-start.sh",
    _ROOT / "src" / "trw_mcp" / "data" / "hooks" / "session-start.sh",
)


def _copy_hook_to_temp(tmp_path: Path, source_hook: Path) -> tuple[Path, Path]:
    source_path = source_hook.as_posix()
    hook_label = "bundled" if "/src/trw_mcp/data/hooks/" in source_path else "dev"

    project_root = tmp_path / hook_label
    hooks_dir = project_root / ".claude" / "hooks"
    context_dir = project_root / ".trw" / "context"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    context_dir.mkdir(parents=True, exist_ok=True)

    hook_path = hooks_dir / "session-start.sh"
    hook_path.write_text(source_hook.read_text(encoding="utf-8"), encoding="utf-8")
    hook_path.chmod(0o755)

    lib_hook = hooks_dir / "lib-trw.sh"
    lib_hook.write_text(
        """#!/bin/sh
init_hook_timer() { :; }
get_repo_root() { printf '%s' "$TRW_PROJECT_ROOT"; }
""",
        encoding="utf-8",
    )
    lib_hook.chmod(0o755)
    return project_root, hook_path


def test_session_start_hook_copies_stay_in_sync() -> None:
    # Arity-independent: adding or retiring a distribution copy must not require
    # editing this assertion, but the guard must never degrade into a
    # single-copy no-op, hence the explicit >= 2 floor.
    assert len(_HOOK_PATHS) >= 2, "parity guard needs at least two copies to compare"
    contents = {hook_path.read_text(encoding="utf-8") for hook_path in _HOOK_PATHS}
    assert len(contents) == 1, f"session-start.sh copies diverged: {[p.as_posix() for p in _HOOK_PATHS]}"


def test_session_start_hook_clears_phase_and_injected_state_for_all_sources(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        for source in ("startup", "resume", "compact", "clear"):
            project_root, local_hook = _copy_hook_to_temp(tmp_path / hook_path.parent.name / source, hook_path)
            context_dir = project_root / ".trw" / "context"
            (context_dir / "last_ups_phase").write_text("implement", encoding="utf-8")
            (context_dir / "injected_learning_ids.txt").write_text("L-1\nL-2\n", encoding="utf-8")

            result = subprocess.run(
                ["sh", str(local_hook)],
                input=json.dumps({"source": source}),
                text=True,
                capture_output=True,
                cwd=project_root,
                env={
                    **os.environ,
                    "TRW_PROJECT_ROOT": str(project_root),
                },
                check=False,
            )

            assert result.returncode == 0
            assert not (context_dir / "last_ups_phase").exists()
            assert (context_dir / "injected_learning_ids.txt").read_text(encoding="utf-8") == ""


def test_session_start_hook_compact_guides_to_session_start(tmp_path: Path) -> None:
    for hook_path in _HOOK_PATHS:
        project_root, local_hook = _copy_hook_to_temp(tmp_path / hook_path.parent.name / "compact-guidance", hook_path)

        result = subprocess.run(
            ["sh", str(local_hook)],
            input=json.dumps({"source": "compact"}),
            text=True,
            capture_output=True,
            cwd=project_root,
            env={
                **os.environ,
                "TRW_PROJECT_ROOT": str(project_root),
            },
            check=False,
        )

        assert result.returncode == 0
        assert "trw_session_start(query='your task domain')" in result.stdout
        assert "After session_start, call trw_status()" in result.stdout


def _run_hook(local_hook: Path, project_root: Path, source: str) -> str:
    result = subprocess.run(
        ["sh", str(local_hook)],
        input=json.dumps({"source": source}),
        text=True,
        capture_output=True,
        cwd=project_root,
        env={**os.environ, "TRW_PROJECT_ROOT": str(project_root)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


#: Emission ceilings in BYTES for the events that fire mid-session. Measured
#: 2026-07-27 after the instruction-file dedup: resume 593, compact 1034,
#: clear 842. Before it, each of the three re-emitted the full behavioral
#: protocol that the client instruction file already carries and already keeps
#: in context — 5791/6214/6040 bytes, ~1.3k wasted tokens per event, paid again
#: on every compaction of a long session.
#:
#: These are SOFT ceilings with slack. If your change trips one, ask whether
#: the client already has the text in context; if it genuinely does not, raise
#: the ceiling here with the new measurement and why.
_MID_SESSION_EMISSION_CEILING_BYTES = {"resume": 1200, "compact": 1800, "clear": 1600}


def test_protocol_not_re_emitted_when_instruction_file_carries_it(tmp_path: Path) -> None:
    """resume/compact/clear must not restate a protocol already in the system prompt."""
    for hook_path in _HOOK_PATHS:
        for source in ("resume", "compact", "clear"):
            project_root, local_hook = _copy_hook_to_temp(
                tmp_path / hook_path.parent.name / f"dedup-{source}", hook_path
            )
            # A client instruction file carrying the protocol's anchor tool.
            (project_root / "CLAUDE.md").write_text(
                "# Project\n\n<!-- trw:start -->\n## TRW Behavioral Protocol\n"
                "- call trw_session_start() first\n<!-- trw:end -->\n",
                encoding="utf-8",
            )
            # The generated protocol file exists too — the hook must still skip it.
            (project_root / ".trw" / "context" / "behavioral_protocol.md").write_text(
                "# TRW Behavioral Protocol\n\n### Tool Lifecycle\n\nUNIQUE-PROTOCOL-BODY-MARKER\n",
                encoding="utf-8",
            )

            stdout = _run_hook(local_hook, project_root, source)

            assert "UNIQUE-PROTOCOL-BODY-MARKER" not in stdout, (
                f"{source}: re-emitted the protocol body the instruction file already carries"
            )
            assert "still in context" in stdout, f"{source}: lost the protocol pointer entirely"


def test_protocol_still_emitted_when_no_instruction_file_carries_it(tmp_path: Path) -> None:
    """The nudge is deduplicated, not removed: bare harnesses still get the protocol."""
    for hook_path in _HOOK_PATHS:
        for source in ("resume", "compact", "clear"):
            project_root, local_hook = _copy_hook_to_temp(
                tmp_path / hook_path.parent.name / f"bare-{source}", hook_path
            )
            (project_root / ".trw" / "context" / "behavioral_protocol.md").write_text(
                "# TRW Behavioral Protocol\n\n### Tool Lifecycle\n\nUNIQUE-PROTOCOL-BODY-MARKER\n",
                encoding="utf-8",
            )

            stdout = _run_hook(local_hook, project_root, source)

            assert "UNIQUE-PROTOCOL-BODY-MARKER" in stdout, (
                f"{source}: a project with no instruction file lost the protocol"
            )


def test_mid_session_emissions_stay_within_byte_budget(tmp_path: Path) -> None:
    """Tripwire: the deduplicated emissions must not silently regrow."""
    for hook_path in _HOOK_PATHS:
        for source, ceiling in _MID_SESSION_EMISSION_CEILING_BYTES.items():
            project_root, local_hook = _copy_hook_to_temp(
                tmp_path / hook_path.parent.name / f"budget-{source}", hook_path
            )
            (project_root / "CLAUDE.md").write_text("# Project\n\ncall trw_session_start() first\n", encoding="utf-8")

            emitted = len(_run_hook(local_hook, project_root, source).encode("utf-8"))

            assert emitted <= ceiling, (
                f"{source} SessionStart emission = {emitted} bytes (~{emitted // 4} tok), "
                f"ceiling {ceiling}. Every byte here is paid on every occurrence of this "
                f"event, and compaction can fire many times in one session. Cut the "
                f"duplication or raise the ceiling in this same change with the new "
                f"measurement and justification."
            )


def test_compact_framework_directive_is_honest_about_cost(tmp_path: Path) -> None:
    """The framework directive must state a MEASURED cost, never a remembered one.

    History: the compact branch claimed a full FRAMEWORK-CORE.md re-read costs
    ~500 tokens when it was ~8k, and the startup branch then claimed "~385 lines
    / ~8k tokens" when the file measured 393 lines and 35,073 characters.
    PRD-CORE-247-FR07 replaced both estimates with the measured figures and made
    the directive phase-scoped, so the assertion moved with them — the invariant
    ("the stated cost equals the measured cost") is unchanged, the numbers are
    the current measurement.
    """
    for hook_path in _HOOK_PATHS:
        project_root, local_hook = _copy_hook_to_temp(tmp_path / hook_path.parent.name / "compact-honesty", hook_path)

        stdout = _run_hook(local_hook, project_root, "compact")

        assert "~500 tokens" not in stdout, "restated the 17x-understated re-read cost"
        assert "~8k tokens" not in stdout, "restated the superseded whole-document estimate"
        assert "~385 lines" not in stdout, "restated the superseded line count"
        assert "35,073" in stdout, "dropped the measured whole-document size"
        assert "EXECUTION MODEL SUMMARY" in stdout, "dropped the targeted-reload guidance"


def test_instruction_file_that_merely_mentions_trw_still_gets_the_protocol(tmp_path: Path) -> None:
    """A prose mention is not a protocol block.

    The predicate first shipped as a token scan for ``trw_session_start``. Any
    project whose instruction file merely MENTIONS the tool — a migration note,
    a changelog line, a README paragraph — would have had the protocol
    suppressed and been handed a pointer to a section that does not exist, on
    exactly the events where an agent has least context. The predicate now
    requires the managed-block marker that only trw_instructions_sync writes.
    """
    for hook_path in _HOOK_PATHS:
        for source in ("resume", "compact", "clear"):
            project_root, local_hook = _copy_hook_to_temp(
                tmp_path / hook_path.parent.name / f"mention-{source}", hook_path
            )
            (project_root / "CLAUDE.md").write_text(
                "# Project\n\nWe migrated to TRW; run trw_session_start() at the top of a session.\n",
                encoding="utf-8",
            )
            (project_root / ".trw" / "context" / "behavioral_protocol.md").write_text(
                "# TRW Behavioral Protocol\n\nUNIQUE-PROTOCOL-BODY-MARKER\n",
                encoding="utf-8",
            )

            stdout = _run_hook(local_hook, project_root, source)

            assert "UNIQUE-PROTOCOL-BODY-MARKER" in stdout, f"{source}: a prose mention suppressed the protocol"
            assert "still in context" not in stdout, f"{source}: pointed at an instruction section that does not exist"


# ---------------------------------------------------------------------------
# PRD-CORE-263-FR06 — the injected-ids file is written under pressure too
# ---------------------------------------------------------------------------


def test_injected_ids_are_written_even_under_writer_pressure(tmp_path: Path) -> None:
    """PRD-CORE-263-FR06 — end to end: pressured recall, then a real hook read.

    Under writer pressure ``perform_session_recalls`` returns a
    ``side_effects_deferred`` advisory. The injected-ids write used to be gated
    on that advisory, so the state file went stale and the auto-injection hook
    re-injected learnings the session had just surfaced — spending the context
    budget the deferral existed to protect. The write touches no SQLite
    connection, so pressure was never a reason to skip it.

    Attribution: restoring the ``if "side_effects_deferred" not in extra`` guard
    turns the first assertion red, and the hook then re-injects ``L-real-id``.
    """
    from unittest.mock import patch

    from tests._auto_recall_hook_harness import (
        _HOOK_PATHS as _UPS_HOOK_PATHS,
    )
    from tests._auto_recall_hook_harness import (
        _MATCHING_PROMPT,
        _MATCHING_SUMMARY,
        _copy_hook_to_temp,
        _write_learning,
    )
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools import _ceremony_session_start_steps as steps

    for index, ups_hook in enumerate(_UPS_HOOK_PATHS):
        project_root, hook_path, entries_dir = _copy_hook_to_temp(tmp_path / f"fr06-{index}", ups_hook)
        trw_dir = project_root / ".trw"
        _write_learning(
            entries_dir,
            "L-real-id",
            status="active",
            summary=_MATCHING_SUMMARY,
            file_stem="2026-04-10-structlog-gotcha",
        )

        # A recall that returned learnings AND reported deferred side effects —
        # i.e. writer pressure engaged.
        pressured = (
            [{"id": "L-real-id", "summary": _MATCHING_SUMMARY}],
            [],
            {"side_effects_deferred": {"reason": "writer_pressure"}, "response_compacted": True},
        )
        results: dict[str, object] = {}
        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools._ceremony_helpers.perform_session_recalls", return_value=pressured),
        ):
            steps.step_recall_learnings("", TRWConfig(), results, [])  # type: ignore[arg-type]

        state_file = trw_dir / "context" / "injected_learning_ids.txt"
        assert state_file.is_file(), "the injected-ids write must not be gated on writer pressure"
        assert state_file.read_text(encoding="utf-8").split() == ["L-real-id"]
        # The deferral advisory itself is untouched — this PRD does not change
        # the pressure decision, only what happens after it (Non-Goal 2).
        assert results["side_effects_deferred"] == {"reason": "writer_pressure"}

        # A subsequent REAL hook read injects none of them.
        env = os.environ.copy()
        env.update(
            {
                "TRW_PROJECT_ROOT": str(project_root),
                "TRW_TEST_PHASE": "implement",
                "TRW_HOOK_LOG": str(project_root / "hook.log"),
            }
        )
        completed = subprocess.run(
            ["sh", str(hook_path)],
            input=json.dumps({"prompt": _MATCHING_PROMPT}),
            text=True,
            capture_output=True,
            cwd=project_root,
            env=env,
            check=False,
        )
        assert "[L-real-id]" not in completed.stdout, (
            f"the hook re-injected a learning this session already surfaced: {completed.stdout!r}"
        )


def test_injected_ids_write_failure_records_a_degradation(tmp_path: Path) -> None:
    """PRD-CORE-263-FR06 — the narrow handler stops being merely quiet."""
    from unittest.mock import patch

    from trw_mcp.tools._ceremony_session_start_steps import _write_session_start_ids

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    results: dict[str, object] = {}
    with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
        _write_session_start_ids(trw_dir, [{"id": "L-x"}], results)

    degradations = results["degradations"]
    assert isinstance(degradations, list)
    assert degradations[0]["step"] == "injected_ids_write"
    assert degradations[0]["error_class"] == "OSError"

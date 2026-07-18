"""Protect sprint guidance from bypassing lifecycle and coordination policy."""

from __future__ import annotations

from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"
INIT_SKILLS = (
    "skills/trw-sprint-init/SKILL.md",
    "codex/skills/trw-sprint-init/SKILL.md",
    "copilot/skills/trw-sprint-init/SKILL.md",
    "copilot/plugin/skills/trw-sprint-init/SKILL.md",
)
FINISH_SKILLS = (
    "skills/trw-sprint-finish/SKILL.md",
    "codex/skills/trw-sprint-finish/SKILL.md",
)


def test_sprint_init_keeps_parallelism_optional_and_portable() -> None:
    for relative in INIT_SKILLS:
        content = (DATA / relative).read_text(encoding="utf-8")
        assert "single-session sequential plan is always valid" in content, relative
        assert "active harness and project policy allow delegation" in content, relative
        assert "Do not launch helpers automatically" in content, relative
        assert "run_task_name" in content, relative
        assert "^[a-zA-Z0-9][a-zA-Z0-9_-]*$" in content, relative
        assert "at most 128 characters" in content, relative
        assert "objective=<display sprint name>" in content, relative
        assert "trw_init(task_name=<sprint name>" not in content, relative
        assert "Launch parallel subagents" not in content, relative
        assert "FPI Gating" not in content, relative
        assert "DISTILLERY-DEFECT-LEDGER" not in content, relative


def test_sprint_init_does_not_infer_completion_from_identifier_counts() -> None:
    for relative in INIT_SKILLS:
        content = (DATA / relative).read_text(encoding="utf-8")
        assert "identifier existence is not proof of completion" in content, relative
        assert ">80% of identifiers" not in content, relative
        assert "coverage_threshold: null" in content, relative
        assert "- id: delivery" not in content, relative


def test_cursor_sprint_init_is_a_thin_contract_adapter() -> None:
    content = (DATA / "cursor_ide/commands/trw-sprint-init.md").read_text(encoding="utf-8")
    assert "do not launch implementation" in content
    assert "Delegation is optional" in content
    assert "sequential plan is always valid" in content
    assert "docs/requirements-aare-f" not in content
    assert "READY PRDs" not in content


def test_sprint_finish_never_bypasses_prd_lifecycle() -> None:
    for relative in FINISH_SKILLS:
        content = (DATA / relative).read_text(encoding="utf-8")
        description = content.split("---", 2)[1]
        assert "updates PRD statuses" not in description, relative
        assert "validates PRD lifecycle" in description, relative
        assert "never bypasses the PRD lifecycle state machine" in content, relative
        assert "Do **not** edit a non-terminal PRD directly to `done`" in content, relative
        assert "draft -> review -> approved -> implemented -> done" in content, relative
        assert "eligible `implemented -> done`" in content, relative
        assert "use the Edit tool to change" not in content, relative


def test_sprint_finish_uses_observed_gates_and_safe_archive() -> None:
    for relative in FINISH_SKILLS:
        content = (DATA / relative).read_text(encoding="utf-8")
        for field in ("tests_passed", "test_count", "failure_count", "static_checks_clean", "scope"):
            assert field in content, (relative, field)
        assert "do not delete ambiguous files" in content, relative
        assert "Atomically move that exact selected active document" in content, relative
        assert "trusted project-owned configuration" in content, relative
        assert "rather than deleting it blindly" in content, relative
        assert "invalidating any earlier build or review evidence" in content, relative
        assert "Never reuse the pre-archive build or review result" in content, relative
        assert "trw_status().deliver_gate_summary" in content, relative
        archive_index = content.index("Atomically move that exact selected active document")
        final_build_index = content.rindex("trw_build_check(")
        deliver_index = content.index("trw_deliver()", final_build_index)
        assert archive_index < final_build_index < deliver_index, relative
        assert "rm -f" not in content, relative

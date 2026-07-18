"""Tests for R-03: PRD done status must detect sprint doc deferrals.

Covers:
  - done PRD with sprint doc deferral language emits warning
  - done PRD without deferral language emits no warning
  - draft PRD skips deferral check entirely
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_tree(tmp_path: Path) -> Path:
    """Create a minimal project tree with PRD and sprint doc directories."""
    # Create the PRD directory structure
    prd_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
    prd_dir.mkdir(parents=True)

    # Create sprint doc directories (both active and archive)
    sprint_dir = tmp_path / "docs" / "requirements-aare-f" / "sprints"
    sprint_dir.mkdir(parents=True)
    archive_dir = tmp_path / "docs" / "requirements-aare-f" / "archive" / "sprints"
    archive_dir.mkdir(parents=True)

    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDonePrdWithSprintDeferral:
    """done PRD + sprint doc deferral language -> warning emitted."""

    def test_deferred_keyword(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-CORE-042"}

        # Write a sprint doc that mentions deferral
        sprint_dir = project_tree / "docs" / "requirements-aare-f" / "sprints"
        sprint_doc = sprint_dir / "sprint-55.md"
        sprint_doc.write_text(
            "# Sprint 55\n\n- PRD-CORE-042: FR03 deferred to Phase 2\n- PRD-CORE-043: complete\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert len(warnings) == 1
        assert "sprint-55.md" in warnings[0]
        assert "PRD-CORE-042" in warnings[0]
        assert "deferral" in warnings[0].lower() or "deferred" in warnings[0].lower()

    def test_not_in_scope_keyword(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-FIX-010"}

        sprint_dir = project_tree / "docs" / "requirements-aare-f" / "sprints"
        sprint_doc = sprint_dir / "sprint-60.md"
        sprint_doc.write_text(
            "# Sprint 60\n\nPRD-FIX-010 FR02 explicitly NOT in scope for this sprint\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert len(warnings) >= 1
        assert "sprint-60.md" in warnings[0]

    def test_out_of_scope_keyword(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-QUAL-015"}

        archive_dir = project_tree / "docs" / "requirements-aare-f" / "archive" / "sprints"
        sprint_doc = archive_dir / "sprint-42.md"
        sprint_doc.write_text(
            "# Sprint 42\n\n| PRD-QUAL-015 | out of scope | moved to Phase 3 |\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert len(warnings) >= 1
        assert "sprint-42.md" in warnings[0]


class TestDonePrdWithoutDeferral:
    """done PRD + no deferral language -> no warning."""

    def test_no_sprint_docs(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-CORE-099"}

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []

    def test_sprint_doc_mentions_prd_without_deferral(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-CORE-050"}

        sprint_dir = project_tree / "docs" / "requirements-aare-f" / "sprints"
        sprint_doc = sprint_dir / "sprint-70.md"
        sprint_doc.write_text(
            "# Sprint 70\n\n- PRD-CORE-050: all FRs implemented and verified\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []

    def test_sprint_doc_mentions_different_prd_deferred(self, project_tree: Path) -> None:
        """Deferral language for a DIFFERENT PRD should not trigger a warning."""
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-CORE-050"}

        sprint_dir = project_tree / "docs" / "requirements-aare-f" / "sprints"
        sprint_doc = sprint_dir / "sprint-70.md"
        sprint_doc.write_text(
            "# Sprint 70\n\n- PRD-CORE-051: deferred to Phase 2\n- PRD-CORE-050: complete\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []


class TestDraftPrdSkipsDeferralCheck:
    """Non-done PRDs skip the deferral check entirely."""

    def test_draft_status(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "draft", "id": "PRD-CORE-042"}

        # Even with deferral language present, draft PRDs should not trigger
        sprint_dir = project_tree / "docs" / "requirements-aare-f" / "sprints"
        sprint_doc = sprint_dir / "sprint-55.md"
        sprint_doc.write_text(
            "# Sprint 55\n\n- PRD-CORE-042: deferred to Phase 2\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []

    def test_active_status(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "active", "id": "PRD-CORE-042"}

        sprint_dir = project_tree / "docs" / "requirements-aare-f" / "sprints"
        sprint_doc = sprint_dir / "sprint-55.md"
        sprint_doc.write_text(
            "# Sprint 55\n\n- PRD-CORE-042: deferred to Phase 2\n",
            encoding="utf-8",
        )

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []

    def test_missing_status(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"id": "PRD-CORE-042"}

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []


class TestDeferralCheckFailOpen:
    """Deferral check must never raise — always fail-open."""

    def test_missing_project_root(self) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done", "id": "PRD-CORE-042"}
        nonexistent = Path("/nonexistent/path/that/does/not/exist")

        # Must not raise — returns empty list
        warnings = _check_sprint_deferral(frontmatter, project_root=nonexistent)
        assert warnings == []

    def test_missing_id_field(self, project_tree: Path) -> None:
        from trw_mcp.state.validation._prd_validation import _check_sprint_deferral

        frontmatter: dict[str, object] = {"status": "done"}

        warnings = _check_sprint_deferral(frontmatter, project_root=project_tree)
        assert warnings == []


# ---------------------------------------------------------------------------
# PRD-QUAL-121-FR04: bounded execution-state work-in-progress
# ---------------------------------------------------------------------------


def test_prd_qual_121_fr04(tmp_path: Path) -> None:
    """FR04 acceptance, driven through the PRODUCTION gate (the sole ledger
    writer refuses over-limit transitions before any write): fourth global P0,
    thirteenth global P0/P1, second owner P0, fourth owner P0/P1, and second
    owner blocked-external all fail with occupied slots; expired candidates
    leave the hot path without lifecycle change; ambient wall-clock alone
    changes no canonical bytes."""
    from datetime import date

    import pytest

    from trw_mcp.models.requirements import ExecutionState
    from trw_mcp.state.requirements_registry import (
        ActivationRefusedError,
        RegistryWriter,
        build_registry,
    )

    def write_prd(
        prds: Path, prd_id: str, priority: str, status: str = "approved", updated: str = "2026-07-01"
    ) -> None:
        prds.mkdir(parents=True, exist_ok=True)
        (prds / f"{prd_id}.md").write_text(
            f"---\nprd:\n  id: {prd_id}\n  title: {prd_id}\n  status: {status}\n"
            f"  priority: {priority}\n  category: CORE\n  dates:\n    updated: '{updated}'\n---\n",
            encoding="utf-8",
        )

    def fresh(name: str) -> tuple[Path, RegistryWriter, Path]:
        base = tmp_path / name
        ledger = base / "ledger.jsonl"
        return base / "prds", RegistryWriter(ledger, utc_today=lambda: date(2026, 7, 11)), ledger

    def activate(writer: RegistryWriter, prds: Path, prd_id: str, owner: str) -> None:
        writer.set_execution_state(
            prd_id, ExecutionState.ACTIVE, prds_dir=prds, authorization_receipt="r1", actor="op", owner=owner
        )

    # Case 1 — fourth global P0 fails with the three occupied slots named.
    prds, writer, ledger = fresh("c1")
    for index, owner in enumerate(("a", "b", "c"), start=1):
        write_prd(prds, f"PRD-CORE-00{index}", "P0")
        activate(writer, prds, f"PRD-CORE-00{index}", owner)
    write_prd(prds, "PRD-CORE-010", "P0")
    ledger_before = ledger.read_text(encoding="utf-8")
    with pytest.raises(ActivationRefusedError) as excinfo:
        activate(writer, prds, "PRD-CORE-010", "d")
    assert "global P0 active limit 3" in str(excinfo.value)
    assert excinfo.value.occupied_slots == ["PRD-CORE-001", "PRD-CORE-002", "PRD-CORE-003"]
    assert ledger.read_text(encoding="utf-8") == ledger_before  # refused BEFORE write

    # Case 2 — thirteenth global P0/P1 fails (2 P0 + 10 P1 occupy the 12 cap).
    prds, writer, ledger_c2 = fresh("c2")
    for index in range(1, 3):
        write_prd(prds, f"PRD-CORE-0{index:02d}", "P0")
        activate(writer, prds, f"PRD-CORE-0{index:02d}", f"o{index}")
    for index in range(3, 13):
        write_prd(prds, f"PRD-CORE-0{index:02d}", "P1")
        activate(writer, prds, f"PRD-CORE-0{index:02d}", f"o{index}")
    write_prd(prds, "PRD-CORE-099", "P1")
    before_c2 = ledger_c2.read_text(encoding="utf-8")
    with pytest.raises(ActivationRefusedError, match="global P0/P1 active limit 12"):
        activate(writer, prds, "PRD-CORE-099", "z")
    assert ledger_c2.read_text(encoding="utf-8") == before_c2

    # Case 3 — second owner P0 fails on the PER-OWNER branch (global cap NOT hit).
    prds, writer, _ = fresh("c3")
    write_prd(prds, "PRD-CORE-001", "P0")
    activate(writer, prds, "PRD-CORE-001", "team-a")
    write_prd(prds, "PRD-CORE-002", "P0")
    ledger_c3 = tmp_path / "c3" / "ledger.jsonl"
    before_c3 = ledger_c3.read_text(encoding="utf-8")
    with pytest.raises(ActivationRefusedError, match=r"per-owner P0 active \(team-a\) limit 1"):
        activate(writer, prds, "PRD-CORE-002", "team-a")
    assert ledger_c3.read_text(encoding="utf-8") == before_c3

    # Case 4 — fourth owner P0/P1 fails on the per-owner P0/P1 branch.
    prds, writer, _ = fresh("c4")
    for index in range(1, 4):
        write_prd(prds, f"PRD-CORE-00{index}", "P1")
        activate(writer, prds, f"PRD-CORE-00{index}", "team-a")
    write_prd(prds, "PRD-CORE-004", "P1")
    ledger_c4 = tmp_path / "c4" / "ledger.jsonl"
    before_c4 = ledger_c4.read_text(encoding="utf-8")
    with pytest.raises(ActivationRefusedError, match=r"per-owner P0/P1 active \(team-a\) limit 3"):
        activate(writer, prds, "PRD-CORE-004", "team-a")
    assert ledger_c4.read_text(encoding="utf-8") == before_c4

    # Case 5 — second owner blocked-external exception fails.
    prds, writer, ledger_c5 = fresh("c5")
    write_prd(prds, "PRD-CORE-001", "P0")
    writer.set_execution_state(
        "PRD-CORE-001",
        ExecutionState.BLOCKED_EXTERNAL,
        prds_dir=prds,
        authorization_receipt="r1",
        actor="op",
        owner="team-a",
    )
    write_prd(prds, "PRD-CORE-002", "P2")
    before_c5 = ledger_c5.read_text(encoding="utf-8")
    with pytest.raises(ActivationRefusedError) as blocked_info:
        writer.set_execution_state(
            "PRD-CORE-002",
            ExecutionState.BLOCKED_EXTERNAL,
            prds_dir=prds,
            authorization_receipt="r1",
            actor="op",
            owner="team-a",
        )
    assert blocked_info.value.occupied_slots == ["PRD-CORE-001"]
    assert ledger_c5.read_text(encoding="utf-8") == before_c5

    # Expired candidate leaves the hot path without lifecycle/evidence change.
    prds, writer, ledger = fresh("c6")
    write_prd(prds, "PRD-CORE-020", "P2", status="draft", updated="2026-05-01")
    writer.advance_evaluation_epoch(authorization_receipt="r1", actor="op")  # epoch 2026-07-11
    registry = build_registry(prds, ledger)
    assert "PRD-CORE-020" in registry.expired
    assert "PRD-CORE-020" not in registry.hot_path
    entry = next(e for e in registry.entries if e.prd_id == "PRD-CORE-020")
    assert entry.lifecycle_status == "draft"  # unchanged

    # Ambient wall-clock alone changes no canonical bytes.
    assert build_registry(prds, ledger).canonical_bytes() == build_registry(prds, ledger).canonical_bytes()


def _last_json(out: str) -> dict:
    """Parse the trailing pretty-printed JSON document from CLI stdout.

    Cached structlog loggers configured to stdout by earlier tests can prepend
    log lines to the captured stream; the handler's JSON is always last.
    """
    import json as _json

    idx = out.rindex("\n{") + 1 if "\n{" in out else out.index("{")
    return _json.loads(out[idx:])


def test_prd_qual_121_fr04_cli_production_caller(tmp_path: Path, capsys) -> None:
    """FR04 production caller: `trw-mcp prd-state` activates through the
    WIP-limited ledger writer, persists the registry, and refuses over-limit
    transitions with the occupied slots — closing the FR04 activation residual."""
    import argparse

    import pytest

    from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

    handler = SUBCOMMAND_HANDLERS["prd-state"]
    prds = tmp_path / "docs" / "requirements-aare-f" / "prds"
    prds.mkdir(parents=True)
    for index in range(5):
        (prds / f"PRD-CORE-90{index}.md").write_text(
            f"---\nprd:\n  id: PRD-CORE-90{index}\n  title: T\n  status: approved\n"
            "  priority: P0\n  category: CORE\n  dates:\n    updated: '2026-07-01'\n---\n",
            encoding="utf-8",
        )

    def namespace(prd_id: str, owner: str) -> argparse.Namespace:
        return argparse.Namespace(
            prd_id=prd_id,
            state="active",
            receipt="op-authorization",
            actor="op",
            owner=owner,
            project_root=str(tmp_path),
            prds_dir="docs/requirements-aare-f/prds",
        )

    # Three P0 activations fill the global P0 WIP limit.
    for index in range(3):
        handler(namespace(f"PRD-CORE-90{index}", f"owner-{index}"))
        result = _last_json(capsys.readouterr().out)
        assert result["state"] == "active" and result["registry_status"] == "ok"

    # The persisted registry projection exists (production consumer artifact).
    assert (tmp_path / ".trw" / "registry").is_dir()
    ledger = (tmp_path / ".trw" / "registry" / "scheduling-ledger.jsonl").read_text(encoding="utf-8")
    assert ledger.count("set_execution_state") == 3

    # Fourth global P0 activation refuses with occupied slots; nothing appends.
    with pytest.raises(SystemExit) as exit_info:
        handler(namespace("PRD-CORE-903", "owner-3"))
    assert exit_info.value.code == 1
    refusal = _last_json(capsys.readouterr().out)
    assert refusal["refused"] is True and len(refusal["occupied_slots"]) == 3
    after = (tmp_path / ".trw" / "registry" / "scheduling-ledger.jsonl").read_text(encoding="utf-8")
    assert after == ledger  # refusal appended nothing

    # Invalid state vocabulary is a typed usage error.
    bad = namespace("PRD-CORE-904", "owner-4")
    bad.state = "bogus"
    with pytest.raises(SystemExit) as exit_info:
        handler(bad)
    assert exit_info.value.code == 2

"""Tests for AgentWorkEvidence v1 state assembly and MCP export tool."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastmcp import FastMCP

from tests.conftest import get_tools_sync
from trw_mcp.models.agent_work_evidence import AgentWorkEvidence
from trw_mcp.state.agent_work_evidence import assemble_agent_work_evidence
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.tools.agent_work_evidence import register_agent_work_evidence_tools


@pytest.fixture
def evidence_server() -> FastMCP:
    """Create a FastMCP server with only the evidence export tool registered."""
    server = FastMCP("agent-work-evidence-test")
    register_agent_work_evidence_tools(server)
    return server


@pytest.fixture
def evidence_run(tmp_path: Path, writer: FileStateWriter) -> Path:
    """Create a TRW run with metadata, events, artifacts, and build status."""
    run_dir = tmp_path / ".trw" / "runs" / "demo-task" / "20260520T033353Z-abc123"
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    writer.write_yaml(
        meta / "run.yaml",
        {
            "run_id": "20260520T033353Z-abc123",
            "task": "Implement PRD-CORE-168 FR-1",
            "framework": "v24.0_TRW",
            "status": "active",
            "phase": "implement",
            "prd_scope": ["PRD-CORE-168"],
            "owner_session_id": "agent-007",
            "objective": "Export evidence without leaking full diffs.",
            "artifacts": ["meta/run.yaml", "scratch/private-transcript.md"],
        },
    )
    writer.append_jsonl(meta / "events.jsonl", {"ts": "2026-05-20T03:33:53Z", "event": "run_init"})
    writer.append_jsonl(meta / "events.jsonl", {"ts": "2026-05-20T03:35:00Z", "event": "tests_passed"})
    writer.append_jsonl(
        meta / "checkpoints.jsonl",
        {
            "ts": "2026-05-20T03:36:00Z",
            "message": "Plan: schema, state assembly, tool wrapper. No transcript body should leak.",
        },
    )
    writer.write_yaml(
        tmp_path / ".trw" / "context" / "build-status.yaml",
        {
            "tests_passed": True,
            "static_checks_clean": True,
            "mypy_clean": True,
            "coverage_pct": 92.0,
            "test_count": 17,
            "failure_count": 0,
            "scope": "focused",
        },
    )
    return run_dir


def test_assemble_evidence_normalizes_build_events_artifacts_and_privacy(evidence_run: Path) -> None:
    """FR-1/3/6: run evidence is schema-valid, normalized, and privacy-safe."""
    evidence = assemble_agent_work_evidence(evidence_run, include_events=False)

    assert isinstance(evidence, AgentWorkEvidence)
    assert evidence.identity.run_id == "20260520T033353Z-abc123"
    assert evidence.agent.agent_id == "agent-007"
    assert evidence.intent == "Export evidence without leaking full diffs."
    assert evidence.verification.status == "passed"
    assert evidence.verification.tests_passed is True
    assert evidence.verification.static_checks_clean is True
    assert evidence.verification.coverage_pct == 92.0
    assert evidence.verification.failure_count == 0
    assert evidence.verification.id == "verification:focused:build-status"
    assert evidence.event_summary.total_count == 2
    assert evidence.events == []
    artifact_ids = [artifact.id for artifact in evidence.artifacts]
    assert "artifact:run:meta-run-yaml" in artifact_ids
    serialized = str(evidence.model_dump())
    assert "full_diff" not in serialized
    assert "secret" not in serialized.lower()
    assert "No transcript body should leak" not in serialized


def test_assemble_evidence_includes_review_yaml_verdict_and_findings(
    evidence_run: Path,
    writer: FileStateWriter,
) -> None:
    """FR-6: real review artifacts become deterministic review evidence items."""
    writer.write_yaml(
        evidence_run / "meta" / "review.yaml",
        {
            "review_id": "review-abc123",
            "verdict": "warn",
            "findings": [
                {
                    "category": "integration",
                    "severity": "warning",
                    "description": "Missing optional integration proof.",
                }
            ],
        },
    )

    evidence = assemble_agent_work_evidence(evidence_run)

    by_id = {review.id: review for review in evidence.review}
    assert by_id["review:review-abc123:verdict"].status == "warn"
    assert by_id["review:review-abc123:finding-1"].category == "integration"
    assert by_id["review:review-abc123:finding-1"].summary == "Missing optional integration proof."


def test_assemble_evidence_skips_artifacts_outside_run_directory(
    evidence_run: Path,
    writer: FileStateWriter,
) -> None:
    """NFR-2: artifact hashing must not follow traversal outside the run directory."""
    outside = evidence_run.parent / "outside.txt"
    outside.write_text("outside artifact body must not be hashed\n", encoding="utf-8")
    run_yaml = evidence_run / "meta" / "run.yaml"
    data = {
        "run_id": "20260520T033353Z-abc123",
        "task": "Implement PRD-CORE-168 FR-1",
        "framework": "v24.0_TRW",
        "status": "active",
        "phase": "implement",
        "prd_scope": ["PRD-CORE-168"],
        "owner_session_id": "agent-007",
        "objective": "Export evidence without leaking outside files.",
        "artifacts": ["../outside.txt", "meta/run.yaml"],
    }
    writer.write_yaml(run_yaml, data)

    evidence = assemble_agent_work_evidence(evidence_run, include_events=False)

    assert [artifact.path for artifact in evidence.artifacts] == ["meta/run.yaml"]
    assert "artifact skipped because it is outside the run directory" in evidence.warnings
    assert "outside artifact body" not in str(evidence.model_dump())


def test_missing_build_status_emits_missing_verification_warning(evidence_run: Path) -> None:
    """FR-3/NFR-4: missing optional build status degrades to warning."""
    (evidence_run.parents[2] / "context" / "build-status.yaml").unlink()

    evidence = assemble_agent_work_evidence(evidence_run)

    assert evidence.verification.status == "missing"
    assert evidence.verification.id == "verification:missing:build-status"
    assert "build-status.yaml missing; verification status set to missing" in evidence.warnings


def test_changed_files_are_repo_relative_with_diff_hash_and_related_ids(
    tmp_path: Path,
    writer: FileStateWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-2: git metadata is exported as repo-relative changed-file records."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    tracked = tmp_path / "src" / "feature.py"
    tracked.parent.mkdir()
    tracked.write_text("print('PRD-CORE-168 FR-2')\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/feature.py"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.name=TRW Test", "-c", "user.email=trw@example.invalid", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    tracked.write_text("print('PRD-CORE-168 FR-2 changed')\n", encoding="utf-8")
    new_file = tmp_path / "docs" / "PRD-CORE-168-FR-2.md"
    new_file.parent.mkdir()
    new_file.write_text("PRD-CORE-168 FR-2\n", encoding="utf-8")

    run_dir = tmp_path / ".trw" / "runs" / "demo-task" / "20260520T033353Z-git"
    (run_dir / "meta").mkdir(parents=True)
    writer.write_yaml(
        run_dir / "meta" / "run.yaml",
        {
            "run_id": "20260520T033353Z-git",
            "task": "Git metadata PRD-CORE-168",
            "status": "active",
            "phase": "implement",
            "prd_scope": ["PRD-CORE-168"],
        },
    )
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)

    evidence = assemble_agent_work_evidence(run_dir)
    by_path = {changed.path: changed for changed in evidence.changed_files}

    assert by_path["src/feature.py"].change_type == "modified"
    assert by_path["src/feature.py"].diff_hash is not None
    assert by_path["docs/PRD-CORE-168-FR-2.md"].change_type == "added"
    assert by_path["docs/PRD-CORE-168-FR-2.md"].diff_hash is None
    assert by_path["docs/PRD-CORE-168-FR-2.md"].related_prds == ["PRD-CORE-168"]
    assert by_path["docs/PRD-CORE-168-FR-2.md"].related_frs == ["FR-2"]


def test_tool_defaults_to_active_run_and_optionally_includes_json_schema(
    evidence_server: FastMCP,
    evidence_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-4: MCP tool defaults to active run and includes JSON Schema only on request."""
    tool = get_tools_sync(evidence_server)["trw_agent_work_evidence"]
    monkeypatch.setattr("trw_mcp.tools.agent_work_evidence.resolve_run_path", lambda run_path=None, **_: evidence_run)

    without_schema = tool.fn(run_path=None, include_events=False, include_schema=False)
    with_schema = tool.fn(run_path=None, include_events=True, include_schema=True)

    assert without_schema["evidence"]["identity"]["run_id"] == "20260520T033353Z-abc123"
    assert "schema" not in without_schema
    assert with_schema["evidence"]["events"][0]["event_type"] == "run_init"
    assert with_schema["schema"]["properties"]["schema_version"]["const"] == "agent-work-evidence/v1"
    assert len(with_schema["evidence"]["integrity"]["digest"]) == 64


def test_tool_validation_helper_returns_structured_errors(evidence_server: FastMCP) -> None:
    """FR-5: tool exposes pure validator with machine-readable errors."""
    tool = get_tools_sync(evidence_server)["trw_validate_agent_work_evidence"]

    result = tool.fn(data={"schema_version": "agent-work-evidence/v1", "extra": True})

    assert result["valid"] is False
    assert {"loc": ["identity"], "type": "missing", "message": "Field required"} in result["errors"]
    assert any(error["loc"] == ["extra"] and error["type"] == "extra_forbidden" for error in result["errors"])


def test_tool_returns_structured_failure_for_expected_assembly_errors(
    evidence_server: FastMCP,
    evidence_run: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public evidence export boundary returns structured failures for expected state errors."""
    tool = get_tools_sync(evidence_server)["trw_agent_work_evidence"]
    monkeypatch.setattr("trw_mcp.tools.agent_work_evidence.resolve_run_path", lambda run_path=None, **_: evidence_run)
    monkeypatch.setattr(
        "trw_mcp.tools.agent_work_evidence.assemble_agent_work_evidence",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("invalid evidence fixture")),
    )

    result = tool.fn(run_path=None, include_events=False, include_schema=False)

    assert result == {"error": "invalid evidence fixture", "status": "failed"}

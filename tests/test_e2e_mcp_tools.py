"""E2E Test Suite: MCP Server Tools

Executes test plan from docs/testing/E2E-MCP-SERVER-TOOLS.md.
Tests MCP tools through the FastMCP server extraction pattern.

Assertion standard (2026-07-28 hardening): every test here MUST fail when the
tool it names is gutted. ``assert result is not None`` does not qualify — these
tools all return a ``dict``, so that assertion held with ``trw_checkpoint``
replaced by ``return {}`` (mutation-verified: the whole file stayed green).
Assert the value the tool is supposed to produce, and where the tool's contract
is persistence, assert the artifact on disk.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.conftest import extract_tool_fn, get_tools_sync, make_test_server

_LEARNING_ID_RE = re.compile(r"^L-[A-Za-z0-9]+$")

# ── 1. Session & Delivery Lifecycle ─────────────────────────────────────────


class TestSessionLifecycle:
    """E2E 1.1-1.6: session_start and deliver."""

    def test_session_start_cold_start(self, tmp_project: Path) -> None:
        """1.1: Cold start with no prior learnings returns an empty corpus."""
        server = make_test_server("ceremony")
        fn = extract_tool_fn(server, "trw_session_start")
        result = fn()
        assert result.get("success") is True, f"session_start failed: {result}"
        assert result["learnings"] == [], f"cold start must recall nothing: {result['learnings']}"
        assert result["learnings_count"] == 0
        assert result["total_available"] == 0
        # No trw_init has run, so there is no run to recover — and session_start
        # must say so rather than silently adopting someone else's run.
        assert result["run"]["status"] == "no_active_run"
        assert result["run"]["active_run"] is None
        assert "session_started" in result["ceremony_status"]

    def test_session_start_with_query(self, tmp_project: Path) -> None:
        """1.2: Focused recall — the query filters the corpus, it is not ignored."""
        server = make_test_server("ceremony", "learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        learn_fn(summary="Authentication uses JWT tokens", detail="JWT auth detail", impact=0.7)
        learn_fn(summary="Reticulating splines offline", detail="Unrelated detail", impact=0.7)

        result = extract_tool_fn(server, "trw_session_start")(query="authentication")
        assert result.get("success") is True, f"session_start failed: {result}"
        summaries = [entry["summary"] for entry in result["learnings"]]
        assert summaries, f"focused recall returned nothing: {result}"
        assert summaries[0] == "Authentication uses JWT tokens", (
            f"query did not rank the matching learning first: {summaries}"
        )

    def test_deliver_no_active_run(self, tmp_project: Path) -> None:
        """1.6: deliver without init still delivers, and says the checkpoint was skipped."""
        server = make_test_server("ceremony")
        deliver_fn = extract_tool_fn(server, "trw_deliver")
        result = deliver_fn()
        # Learning persistence is independent of run state, so delivery succeeds…
        assert result["success"] is True, f"deliver should not fail without a run: {result}"
        assert result["errors"] == []
        assert result["run_path"] is None
        # …but it must NOT claim a checkpoint it never wrote (PRD-CORE-233 FR02).
        checkpoint = result["checkpoint"]
        assert checkpoint["status"] == "skipped"
        assert checkpoint["reason"] == "no_active_run"
        assert "trw_init()" in str(checkpoint["hint"])


# ── 2. Orchestration ────────────────────────────────────────────────────────


class TestOrchestration:
    """E2E 2.1-2.7: init, status, checkpoint."""

    def test_init_creates_run(self, tmp_project: Path) -> None:
        """2.1: Standard initialization creates run directory."""
        server = make_test_server("orchestration")
        init_fn = extract_tool_fn(server, "trw_init")
        result = init_fn(task_name="e2e-init-test", objective="Test init")
        assert "run_id" in result, f"Missing run_id: {result}"
        assert "run_path" in result, f"Missing run_path: {result}"
        run_path = Path(result["run_path"])
        assert run_path.exists(), f"Run path doesn't exist: {run_path}"
        # A run is more than a directory: the meta/ state the rest of the
        # lifecycle reads must be on disk, and the run must start in RESEARCH.
        assert result["status"] == "initialized"
        assert result["phase"] == "research"
        assert (run_path / "meta" / "run.yaml").is_file()
        assert (run_path / "meta" / "events.jsonl").is_file()
        assert run_path.name == result["run_id"]
        assert run_path.parent.name == "e2e-init-test"

    def test_status_no_run(self, tmp_project: Path) -> None:
        """2.5: Status with no active run raises StateError (expected)."""
        from trw_mcp.exceptions import StateError

        server = make_test_server("orchestration")
        status_fn = extract_tool_fn(server, "trw_status")
        with pytest.raises(StateError):
            status_fn()

    def test_status_with_active_run(self, tmp_project: Path) -> None:
        """2.4: Status reports active run details."""
        server = make_test_server("orchestration")
        init_fn = extract_tool_fn(server, "trw_init")
        status_fn = extract_tool_fn(server, "trw_status")
        init_result = init_fn(task_name="status-test")
        result = status_fn()
        assert result["run_id"] == init_result["run_id"], f"status reported a different run: {result}"
        assert result["task"] == "status-test"
        assert result["phase"] == "research"
        assert result["status"] == "active"
        # No build check has run, so the deliver gate must not be open.
        assert result["build_gate_ready"] is False

    def test_checkpoint_persists(self, tmp_project: Path) -> None:
        """2.6: Checkpoint creates persistence snapshot."""
        server = make_test_server("orchestration", "checkpoint")
        init_fn = extract_tool_fn(server, "trw_init")
        ckpt_fn = extract_tool_fn(server, "trw_checkpoint")
        init_result = init_fn(task_name="ckpt-test")
        result = ckpt_fn(message="Research complete")
        assert result["recorded"] is True, f"checkpoint not recorded: {result}"
        assert result["status"] == "checkpoint_created"
        assert result["message"] == "Research complete"

        # The whole point of the tool is the artifact, not the response.
        meta = Path(init_result["run_path"]) / "meta"
        records = [json.loads(line) for line in (meta / "checkpoints.jsonl").read_text().splitlines() if line]
        assert [r["message"] for r in records] == ["Research complete"]
        events = [json.loads(line) for line in (meta / "events.jsonl").read_text().splitlines() if line]
        assert any(e.get("event") == "checkpoint" for e in events), f"no checkpoint event logged: {events}"

    def test_checkpoint_empty_message_records_nothing(self, tmp_project: Path) -> None:
        """2.7: A blank checkpoint preserves nothing and must not claim otherwise."""
        server = make_test_server("orchestration", "checkpoint")
        init_result = extract_tool_fn(server, "trw_init")(task_name="ckpt-blank-test")
        result = extract_tool_fn(server, "trw_checkpoint")(message="   ")
        assert result["recorded"] is False, f"blank checkpoint claimed a write: {result}"
        assert not (Path(init_result["run_path"]) / "meta" / "checkpoints.jsonl").exists()


# ── 3. Learning/Memory Tools ────────────────────────────────────────────────


class TestLearningTools:
    """E2E 3.1-3.13: learn, learn_update, recall."""

    def test_learn_stores_entry(self, tmp_project: Path) -> None:
        """3.1: Store a learning entry."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        result = learn_fn(
            summary="Pydantic v2 requires model_config",
            detail="Use ConfigDict instead of class Config",
            tags=["pydantic", "migration"],
            impact=0.7,
        )
        assert result["status"] == "recorded", f"learn did not record: {result}"
        assert _LEARNING_ID_RE.match(result["learning_id"]), f"bad learning id: {result}"
        entry_path = Path(result["path"])
        assert entry_path.is_file(), f"learning yaml not written: {entry_path}"
        text = entry_path.read_text(encoding="utf-8")
        assert "Pydantic v2 requires model_config" in text
        assert "Use ConfigDict instead of class Config" in text

    def test_learn_all_types(self, tmp_project: Path) -> None:
        """3.5: Create learnings of each type — and the type is persisted, not dropped."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        for t in ["pattern", "incident", "convention", "hypothesis", "workaround"]:
            result = learn_fn(
                summary=f"Test {t} learning",
                detail=f"Detail for {t}",
                tags=["e2e"],
                impact=0.5,
                type=t,
            )
            assert result["status"] == "recorded", f"Failed to create {t} learning: {result}"
            text = Path(result["path"]).read_text(encoding="utf-8")
            assert f"type: {t}" in text, f"{t} learning did not persist its type: {text}"

    def test_recall_keyword_search(self, tmp_project: Path) -> None:
        """3.9: Recall returns keyword-matched results."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        recall_fn = extract_tool_fn(server, "trw_recall")

        learn_fn(summary="Authentication uses JWT tokens", detail="JWT auth detail", tags=["auth"], impact=0.7)
        learn_fn(summary="Database uses PostgreSQL", detail="PG detail", tags=["db"], impact=0.6)

        result = recall_fn(query="authentication")
        summaries = [entry["summary"] for entry in result["learnings"]]
        assert summaries, f"recall returned nothing: {result}"
        assert summaries[0] == "Authentication uses JWT tokens", (
            f"keyword search did not rank the match first: {summaries}"
        )
        assert result["query"] == "authentication"
        assert result["total_matches"] == len(result["learnings"])

    def test_recall_with_tags(self, tmp_project: Path) -> None:
        """3.10: Recall with tag filtering excludes the non-matching tag."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        recall_fn = extract_tool_fn(server, "trw_recall")

        learn_fn(summary="Auth pattern A", detail="Security detail", tags=["security"], impact=0.7)
        learn_fn(summary="DB pattern B", detail="DB detail", tags=["database"], impact=0.6)

        result = recall_fn(query="*", tags=["security"])
        summaries = [entry["summary"] for entry in result["learnings"]]
        assert summaries == ["Auth pattern A"], f"tag filter leaked or dropped entries: {result}"

    def test_recall_empty_results(self, tmp_project: Path) -> None:
        """3.12: Recall with no matches returns an empty corpus, not an error."""
        server = make_test_server("learning")
        recall_fn = extract_tool_fn(server, "trw_recall")
        result = recall_fn(query="nonexistent_topic_xyz_12345")
        assert result["learnings"] == []
        assert result["total_matches"] == 0
        assert result["query"] == "nonexistent_topic_xyz_12345"

    def test_recall_wildcard_all(self, tmp_project: Path) -> None:
        """3.13: Wildcard recall lists all learnings in the compact projection."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        recall_fn = extract_tool_fn(server, "trw_recall")

        # Summaries must be semantically distinct: near-identical entries are
        # deduped (the original "Learning 0/1/2" fixture silently merged the
        # third into the second at similarity 0.886, and the old
        # `assert result is not None` never noticed the missing entry).
        created = set()
        for summary, detail in (
            ("Retry budget for the HTTP transport", "Bounded exponential backoff"),
            ("Alembic migration ordering constraint", "Down-revision must be linear"),
            ("Tailwind purge drops dynamic class names", "Safelist the computed variants"),
        ):
            stored = learn_fn(summary=summary, detail=detail, tags=["e2e"], impact=0.5)
            assert stored["status"] == "recorded", f"fixture entry was deduped, not stored: {stored}"
            created.add(stored["learning_id"])

        result = recall_fn(query="*")
        assert result["compact"] is True, f'"*" must auto-enable the compact projection: {result}'
        assert result["learnings"], f"wildcard recall returned nothing: {result}"
        assert result["total_matches"] == len(result["learnings"])
        for entry in result["learnings"]:
            assert entry["id"] in created, f"wildcard returned a foreign entry: {entry}"
            # Compact projection drops the heavy fields; a regression that stops
            # trimming would silently multiply every caller's token cost.
            assert "detail" not in entry, f"compact projection leaked detail: {entry}"
            assert set(entry) == {"id", "summary", "tags", "impact", "status"}, entry

    def test_learn_update_status(self, tmp_project: Path) -> None:
        """3.7: Update learning status."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        update_fn = extract_tool_fn(server, "trw_learn_update")
        recall_fn = extract_tool_fn(server, "trw_recall")

        created = learn_fn(summary="Update test", detail="Detail for update", tags=["e2e"], impact=0.5)
        learning_id = created["learning_id"]
        assert _LEARNING_ID_RE.match(learning_id), f"learn returned no usable id: {created}"

        result = update_fn(learning_id=learning_id, status="resolved")
        assert result["status"] == "updated", f"update failed: {result}"
        assert result["learning_id"] == learning_id
        assert "resolved" in str(result["changes"])

        # The status change must be visible to the reader, not just the writer.
        assert [e["id"] for e in recall_fn(query="*", status="resolved")["learnings"]] == [learning_id]
        assert learning_id not in [e["id"] for e in recall_fn(query="*", status="active")["learnings"]]

    def test_learn_unicode_content(self, tmp_project: Path) -> None:
        """13.3: Unicode content stored and retrieved correctly."""
        server = make_test_server("learning")
        learn_fn = extract_tool_fn(server, "trw_learn")
        recall_fn = extract_tool_fn(server, "trw_recall")

        summary = "日本語テスト Unicode test"
        detail = "Ümlauts and ñ characters"
        result = learn_fn(summary=summary, detail=detail, tags=["unicode"], impact=0.5)
        assert result["status"] == "recorded", f"unicode learn failed: {result}"

        # "stored AND retrieved correctly" — round-trip through disk and recall,
        # byte-for-byte, or mojibake passes silently.
        text = Path(result["path"]).read_text(encoding="utf-8")
        assert summary in text and detail in text, f"unicode mangled on disk: {text}"
        recalled = recall_fn(query="*", compact=False)["learnings"]
        match = [e for e in recalled if e["id"] == result["learning_id"]]
        assert match, f"unicode entry not recallable: {recalled}"
        assert match[0]["summary"] == summary
        assert match[0]["detail"] == detail


# ── 4. Build & Quality Tools ────────────────────────────────────────────────


class TestBuildQuality:
    """E2E 4.1-4.5: build_check, quality_dashboard."""

    def test_build_check_passing(self, tmp_project: Path) -> None:
        """4.1: Passing build recorded."""
        server = make_test_server("orchestration", "build")
        init_fn = extract_tool_fn(server, "trw_init")
        build_fn = extract_tool_fn(server, "trw_build_check")

        init_fn(task_name="build-pass-test")
        result = build_fn(
            tests_passed=True,
            test_count=150,
            failure_count=0,
            coverage_pct=85.0,
            mypy_clean=True,
        )
        assert result["tests_passed"] is True, f"pass not recorded: {result}"
        assert result["test_count"] == 150
        assert result["failure_count"] == 0
        assert result["coverage_pct"] == 85.0
        assert result["static_checks_clean"] is True
        assert "build=passed" in result["ceremony_status"]
        # The gate reads the cached status, so the cache must exist and agree.
        cached = Path(result["cache_path"]).read_text(encoding="utf-8")
        assert "tests_passed: true" in cached, cached

    def test_build_check_failing(self, tmp_project: Path) -> None:
        """4.2: Failing build recorded — and never laundered into a pass."""
        server = make_test_server("orchestration", "build")
        init_fn = extract_tool_fn(server, "trw_init")
        build_fn = extract_tool_fn(server, "trw_build_check")

        init_fn(task_name="build-fail-test")
        result = build_fn(
            tests_passed=False,
            test_count=150,
            failure_count=3,
            coverage_pct=75.0,
            mypy_clean=False,
        )
        assert result["tests_passed"] is False, f"failure was laundered into a pass: {result}"
        assert result["failure_count"] == 3
        assert result["static_checks_clean"] is False
        assert "build=failed" in result["ceremony_status"]
        cached = Path(result["cache_path"]).read_text(encoding="utf-8")
        assert "tests_passed: false" in cached, cached


# ── 5. Ceremony Tools ───────────────────────────────────────────────────────


class TestCeremonyFeedbackInternalLogic:
    """E2E 5.x (PRD-FIX-076): the trw_ceremony_* MCP tools were removed; the
    underlying state logic is exercised directly as an internal API."""

    def test_ceremony_feedback_tools_deregistered(self, tmp_project: Path) -> None:
        """The 3 ceremony-feedback tools are no longer on the registered surface."""
        server = make_test_server("ceremony_feedback")

        names = set(get_tools_sync(server))
        assert not ({"trw_ceremony_status", "trw_ceremony_approve", "trw_ceremony_revert"} & names)

    def test_ceremony_status_internal_logic(self, tmp_project: Path) -> None:
        """5.1: the internal get_ceremony_status state API still works."""
        from trw_mcp.state.ceremony_feedback import get_ceremony_status

        result = get_ceremony_status(tmp_project / ".trw")
        task_classes = result["task_classes"]
        assert task_classes, f"no task classes reported: {result}"
        by_class = {entry["task_class"]: entry for entry in task_classes}
        assert "feature" in by_class, f"missing the feature task class: {sorted(by_class)}"
        feature = by_class["feature"]
        # A fresh project has no history: the tier must be the default and the
        # sample warning must fire rather than silently reporting confidence.
        assert feature["session_count"] == 0
        assert feature["avg_ceremony_score"] is None
        assert "insufficient_samples" in feature["warnings"]

    def test_ceremony_approve_internal_invalid(self, tmp_project: Path) -> None:
        """5.4: approving a nonexistent proposal raises ValueError (internal API)."""
        from trw_mcp.state.ceremony_feedback import approve_proposal

        with pytest.raises(ValueError, match="No pending proposal"):
            approve_proposal(tmp_project / ".trw", "nonexistent-id")


# ── 6. Requirements/PRD ─────────────────────────────────────────────────────


class TestRequirementsTools:
    """E2E 6.1-6.4: prd_create, prd_validate."""

    def test_prd_create(self, tmp_project: Path) -> None:
        """6.1: Create a PRD."""
        server = make_test_server("requirements")
        fn = extract_tool_fn(server, "trw_prd_create")
        result = fn(
            input_text="Add rate limiting to API endpoints",
            category="CORE",
            priority="P1",
        )
        assert result["prd_id"].startswith("PRD-CORE-"), f"wrong id scheme: {result['prd_id']}"
        assert result["title"] == "Add rate limiting to API endpoints"
        assert result["category"] == "CORE"
        assert result["priority"] == "P1"
        output_path = Path(result["output_path"])
        assert output_path.is_file(), f"PRD not written: {output_path}"
        written = output_path.read_text(encoding="utf-8")
        assert f"# {result['prd_id']}: Add rate limiting to API endpoints" in written


# ── 7. Review Tool ──────────────────────────────────────────────────────────


class TestReviewTool:
    """E2E 7.1: review with manual findings."""

    def test_manual_review(self, tmp_project: Path) -> None:
        """7.1: Manual review with findings persists a review artifact."""
        server = make_test_server("orchestration", "review")
        init_fn = extract_tool_fn(server, "trw_init")
        review_fn = extract_tool_fn(server, "trw_review")

        init_result = init_fn(task_name="review-test")
        result = review_fn(
            mode="manual",
            findings=[
                {
                    "severity": "P2",
                    "category": "quality",
                    "description": "test finding",
                    "file": "test.py",
                    "line": 1,
                }
            ],
        )
        assert result["review_id"].startswith("review-"), f"no review id: {result}"
        assert Path(result["run_path"]) == Path(init_result["run_path"])
        assert result["total_findings"] == 1, f"the supplied finding was dropped: {result}"
        # The field is advisory: omitted entirely when nothing was rejected.
        assert result.get("rejected_findings_count", 0) == 0, f"finding rejected: {result}"
        # The review must land as an on-disk artifact the deliver gate can read.
        review_yaml = Path(result["review_yaml"])
        assert review_yaml.is_file(), f"review.yaml not written: {review_yaml}"
        assert "test finding" in review_yaml.read_text(encoding="utf-8")

    def test_manual_review_malformed_finding_is_reported_not_silently_dropped(self, tmp_project: Path) -> None:
        """7.2: A finding missing the required schema fields is REJECTED loudly.

        Regression guard for the shape this file used to ship: the original 7.1
        fixture passed ``message=`` with no ``category``/``description``, so the
        finding was rejected, the review recorded zero findings with
        ``verdict: pass``, and ``assert result is not None`` reported it as
        covered. The drop must stay caller-visible.
        """
        server = make_test_server("orchestration", "review")
        extract_tool_fn(server, "trw_init")(task_name="review-malformed-test")
        result = extract_tool_fn(server, "trw_review")(
            mode="manual",
            findings=[{"severity": "P2", "file": "test.py", "line": 1, "message": "test finding"}],
        )
        assert result["total_findings"] == 0
        assert result["rejected_findings_count"] == 1
        assert result["rejected_findings"][0]["reason"], f"rejection gave no reason: {result}"
        assert result["substantive"] is False, f"an empty review claimed substance: {result}"


# ── 10. Knowledge & Sync ────────────────────────────────────────────────────


class TestKnowledgeTools:
    """E2E 10.x (PRD-FIX-076): the trw_knowledge_sync MCP tool was removed; the
    underlying state logic is exercised directly as an internal API."""

    def test_knowledge_sync_tool_deregistered(self, tmp_project: Path) -> None:
        """The knowledge-sync tool is no longer on the registered surface."""
        server = make_test_server("knowledge")

        assert "trw_knowledge_sync" not in get_tools_sync(server)

    def test_knowledge_sync_internal_dry_run(self, tmp_project: Path) -> None:
        """10.1: the internal execute_knowledge_sync state API still works (dry run)."""
        from trw_mcp.models.config import get_config
        from trw_mcp.state.knowledge_topology import execute_knowledge_sync

        result = execute_knowledge_sync(tmp_project / ".trw", get_config(), dry_run=True)
        # An empty corpus is below the sync threshold — the API must report that
        # rather than claiming a sync it did not perform.
        assert result["threshold_met"] is False, f"empty corpus reported as syncable: {result}"
        assert result["entry_count"] == 0
        assert result["threshold"] > 0


# ── 12. Cross-Tool Integration ──────────────────────────────────────────────


class TestCrossToolIntegration:
    """E2E 12.1: Full session lifecycle golden path."""

    def test_golden_path_lifecycle(self, tmp_project: Path) -> None:
        """12.1: session_start → init → learn → checkpoint → build_check."""
        server = make_test_server("ceremony", "orchestration", "learning", "checkpoint", "build")
        session_fn = extract_tool_fn(server, "trw_session_start")
        init_fn = extract_tool_fn(server, "trw_init")
        learn_fn = extract_tool_fn(server, "trw_learn")
        ckpt_fn = extract_tool_fn(server, "trw_checkpoint")
        build_fn = extract_tool_fn(server, "trw_build_check")

        # 1. Session start
        r = session_fn()
        assert r.get("success") is True, f"session_start failed: {r}"

        # 2. Init run
        init_result = init_fn(task_name="golden-path", objective="E2E")
        assert "run_id" in init_result, f"init failed: {init_result}"
        meta = Path(init_result["run_path"]) / "meta"

        # 3. Learn
        r = learn_fn(summary="Golden path discovery", detail="E2E detail", tags=["e2e"], impact=0.6)
        assert r["status"] == "recorded", f"learn failed: {r}"
        assert Path(r["path"]).is_file()

        # 4. Checkpoint — recorded against the run init just created
        r = ckpt_fn(message="Research complete")
        assert r["recorded"] is True, f"checkpoint failed: {r}"
        assert (meta / "checkpoints.jsonl").is_file()

        # 5. Build check — and the run's ceremony state advances to a passing build
        r = build_fn(tests_passed=True, test_count=10, coverage_pct=90)
        assert r["tests_passed"] is True, f"build_check failed: {r}"
        assert "build=passed" in r["ceremony_status"]

        # The lifecycle's whole promise is a legible trail: every step logged.
        events = [json.loads(line) for line in (meta / "events.jsonl").read_text().splitlines() if line]
        names = {str(e.get("event", "")) for e in events}
        assert {"run_init", "checkpoint"} <= names, f"lifecycle events missing: {sorted(names)}"
        assert any("build" in name for name in names), f"no build event logged: {sorted(names)}"

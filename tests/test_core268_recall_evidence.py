"""CORE-268: stored observations qualify real recall without implicit refresh."""

from datetime import datetime, timedelta, timezone

import pytest

from tests._tools_learning_shared import _get_tools, set_project_root  # noqa: F401
from trw_mcp.tools._stored_claim_evidence import stored_claim_evidence

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("stamp", "ttl", "observation", "freshness"),
    [
        ("2026-09-07T13:59:59+02:00", 60, "failure", "fresh"),
        ("2026-09-07T11:59:00Z", 60, "failure", "expired"),
        ("2026-09-07T11:59:59Z", 0, "failure", "unknown"),
        ("2026-09-07T11:59:59Z", -1, "failure", "unknown"),
        ("2026-09-07T12:00:01Z", 60, "unknown", "unknown"),
        ("2026-09-07T11:59:59", 60, "unknown", "unknown"),
        ("bad", 60, "unknown", "unknown"),
        (None, 60, "unknown", "unknown"),
    ],
)
def test_date_contract(stamp, ttl, observation, freshness):
    entry = {
        "assertions": [{"last_result": False, "last_verified_at": stamp}],
        "verification_status": "verified",
        "verification_checked_at": NOW.isoformat(),
    }
    evidence, fraction = stored_claim_evidence(entry, ttl_seconds=ttl, now=NOW)
    assert evidence["assertions"][0]["observation"] == observation
    assert evidence["assertions"][0]["freshness"] == freshness
    assert fraction == (1 if observation == "failure" else 0)
    assert evidence["current_tree_verified"] is False
    assert evidence["observation"] == observation  # aggregate cannot repair assertion


def test_mixed_and_expired_pass():
    evidence, fraction = stored_claim_evidence(
        {
            "assertions": [
                {"last_result": True, "last_verified_at": (NOW - timedelta(days=1)).isoformat()},
                {"last_result": False, "last_verified_at": (NOW - timedelta(days=2)).isoformat()},
                {"last_result": False, "last_verified_at": "invalid"},
            ],
            "verification_status": "verified",
            "verification_checked_at": NOW.isoformat(),
        },
        ttl_seconds=60,
        now=NOW,
    )
    assert fraction == 1 / 3
    assert evidence["assertions"][0]["observation"] == "pass"
    assert evidence["assertions"][0]["freshness"] == "expired"
    assert evidence["aggregate"]["observation"] == "pass"
    assert evidence["observation"] == "failure"


@pytest.mark.parametrize("mode", [{"compact": False}, {"compact": True}, {"ultra_compact": True}])
def test_registered_recall_penalizes_before_cap_without_refresh(tmp_path, monkeypatch, mode):
    from trw_memory.models.memory import Assertion, AssertionType, MemoryEntry

    from trw_mcp.state.memory_adapter import get_backend

    tools = _get_tools()
    backend = get_backend(tmp_path / ".trw")
    now = datetime.now(timezone.utc)
    for entry_id, result in [("L-failed", False), ("L-clean", True)]:
        backend.store(
            MemoryEntry(
                id=entry_id,
                content=f"Sibling claim evidence {entry_id}",
                created_at=now,
                updated_at=now,
                importance=0.8,
                assertions=[
                    Assertion(
                        type=AssertionType.GREP_PRESENT,
                        pattern="claim",
                        target="*.py",
                        last_result=result,
                        last_verified_at=now - timedelta(days=30),
                    )
                ],
            )
        )
    before = {i: backend.get(i, namespace="default").model_dump(mode="json") for i in ["L-failed", "L-clean"]}

    def forbidden(*args, **kwargs):
        pytest.fail("recall attempted implicit verification")

    for path in [
        "trw_mcp.tools._verification_pass.run_verification_pass",
        "trw_mcp.tools._verification_pass.persist_verification_outcome",
        "trw_memory.lifecycle.verification.verify_assertions",
        "trw_memory.lifecycle.anchor_validation.compute_anchor_validity",
        "trw_mcp.tools._verification_cache.warm_verified_verdict",
    ]:
        monkeypatch.setattr(path, forbidden)
    result = tools["trw_recall"].fn(query="*", max_results=1, **mode)
    assert [row["id"] for row in result["learnings"]] == ["L-clean"]
    assert result["learnings"][0]["verification_evidence"]["current_tree_verified"] is False
    visible = tools["trw_recall"].fn(query="*", max_results=2, **mode)
    assert len(visible["learnings"]) == 2
    failed = next(row for row in visible["learnings"] if row["id"] == "L-failed")
    assert failed["verification_evidence"]["observation"] == "failure"
    assert failed["verification_evidence"]["assertions"][0]["freshness"] == "expired"
    for i, prior in before.items():
        after = backend.get(i, namespace="default").model_dump(mode="json")
        for field in [
            "assertions",
            "verification_checked_at",
            "verification_status",
            "q_value",
            "q_observations",
            "outcome_history",
        ]:
            assert after[field] == prior[field]


@pytest.mark.parametrize("path", ["main", "focused", "recent", "phase"])
def test_startup_acquired_siblings_before_cap(tmp_path, monkeypatch, path):
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._session_recall_helpers import perform_session_recalls
    from trw_mcp.tools._session_recall_phase import _phase_contextual_recall

    stamp = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rows = [
        {
            "id": i,
            "summary": "sibling claim",
            "impact": 0.8,
            "created": datetime.now(timezone.utc).date().isoformat(),
            "assertions": [{"last_result": result, "last_verified_at": stamp}],
        }
        for i, result in [("L-failed", False), ("L-clean", True)]
    ]
    config = TRWConfig(recall_max_results=1, auto_recall_max_results=1, session_start_recent_bypass_days=0)
    if path == "phase":
        monkeypatch.setattr("trw_mcp.state.memory_adapter.recall_learnings", lambda *a, **k: rows)
        result = _phase_contextual_recall(tmp_path / ".trw", "claim", config, None, None)
    else:
        monkeypatch.setattr("trw_mcp.state.recall_factories.recall_baseline_high_impact", lambda *a, **k: rows)
        query = "*"
        if path == "focused":
            query = "sibling"
            monkeypatch.setattr("trw_mcp.state.recall_factories.recall_focused", lambda *a, **k: rows[:1])
        if path == "recent":
            config.session_start_recent_bypass_days = 1
            monkeypatch.setattr("trw_mcp.state.recall_factories.recall_recent_bypass", lambda *a, **k: rows[:1])
            monkeypatch.setattr("trw_mcp.state.recall_factories.recall_baseline_high_impact", lambda *a, **k: rows[1:])
        result, _, _ = perform_session_recalls(tmp_path / ".trw", query, config, FileStateReader())
    assert [entry["id"] for entry in result] == ["L-clean"]
    assert result[0]["verification_evidence"]["assertions"][0]["freshness"] == "expired"
    assert result[0]["verification_evidence"]["current_tree_verified"] is False


@pytest.mark.parametrize("mode", ["full", "compact", "ultra", "startup_main", "startup_phase"])
def test_explicit_refresh_reverses_public_recall_and_advice(tmp_path, capsys, monkeypatch, mode):
    import argparse
    import json

    from trw_memory.models.memory import Assertion, AssertionType, MemoryEntry

    from trw_mcp.server._subcommands_maintain import _run_maintain_verify
    from trw_mcp.state.memory_adapter import get_backend
    from trw_mcp.tools._retraction_nudge import unretracted_contradiction_nudge

    tools = _get_tools()
    backend = get_backend(tmp_path / ".trw")
    now = datetime.now(timezone.utc)
    (tmp_path / "claim.py").write_text("healthy = True\n")
    for entry_id, pattern in [("L-failed", "repaired"), ("L-clean", "healthy")]:
        backend.store(
            MemoryEntry(
                id=entry_id,
                content=f"Sibling claim evidence {entry_id}",
                created_at=now,
                updated_at=now,
                importance=0.8,
                q_value=0.7,
                q_observations=7,
                assertions=[Assertion(type=AssertionType.GREP_PRESENT, pattern=pattern, target="claim.py")],
            )
        )
    history = {
        i: (
            backend.get(i, namespace="default").q_value,
            backend.get(i, namespace="default").q_observations,
            backend.get(i, namespace="default").outcome_history,
        )
        for i in ["L-failed", "L-clean"]
    }
    _run_maintain_verify(argparse.Namespace(as_json=True, namespace="default"))
    assert json.loads(capsys.readouterr().out)["entries_processed"] == 2

    def read_without_refresh():
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._session_recall_helpers import perform_session_recalls
        from trw_mcp.tools._session_recall_phase import _phase_contextual_recall

        def forbidden(*args, **kwargs):
            pytest.fail("startup/recall refreshed or scheduled verification on read")

        before = {i: backend.get(i, namespace="default").model_dump(mode="json") for i in ["L-failed", "L-clean"]}
        with monkeypatch.context() as guard:
            for path in [
                "trw_mcp.tools._verification_pass.run_verification_pass",
                "trw_mcp.tools._verification_pass.persist_verification_outcome",
                "trw_mcp.tools._verification_cache.warm_verified_verdict",
                "trw_mcp.tools._maintain_verify.run_maintain_verify",
                "trw_mcp.tools._maintain_verify.run_maintain_verify_for_project",
                "trw_memory.lifecycle.verification.verify_assertions",
            ]:
                guard.setattr(path, forbidden)
            config = TRWConfig(recall_max_results=2, auto_recall_max_results=2, session_start_recent_bypass_days=0)
            if mode == "startup_main":
                rows, _, _ = perform_session_recalls(tmp_path / ".trw", "*", config, FileStateReader())
            elif mode == "startup_phase":
                rows = _phase_contextual_recall(tmp_path / ".trw", "claim", config, None, None)
            else:
                options = {"ultra_compact": True} if mode == "ultra" else {"compact": mode == "compact"}
                rows = tools["trw_recall"].fn(query="*", max_results=2, **options)["learnings"]
        for i, original in before.items():
            after = backend.get(i, namespace="default").model_dump(mode="json")
            for field in [
                "assertions",
                "verification_status",
                "verification_checked_at",
                "q_value",
                "q_observations",
                "outcome_history",
            ]:
                assert after[field] == original[field]
        assert len(rows) == 2  # actual SQLite acquisition, no injected candidate rows
        return {"learnings": rows}

    failed = read_without_refresh()
    assert [row["id"] for row in failed["learnings"]] == ["L-clean", "L-failed"]
    assert failed["learnings"][1]["verification_evidence"]["observation"] == "failure"
    # Startup helper invocation is not a complete session-start ceremony; seed
    # advisory scope through a real registered recall, not fabricated receipts.
    if mode.startswith("startup_"):
        tools["trw_recall"].fn(query="*", max_results=2, compact=False)
    assert "L-failed" in unretracted_contradiction_nudge(tmp_path / ".trw")
    (tmp_path / "claim.py").write_text("healthy = True\nrepaired = True\n")
    _run_maintain_verify(argparse.Namespace(as_json=True, namespace="default"))
    assert json.loads(capsys.readouterr().out)["entries_processed"] == 2
    corrected = read_without_refresh()
    assert all(row["verification_evidence"]["observation"] == "pass" for row in corrected["learnings"])
    assert unretracted_contradiction_nudge(tmp_path / ".trw") == ""
    for i, old in history.items():
        entry = backend.get(i, namespace="default")
        assert (entry.q_value, entry.q_observations, entry.outcome_history) == old


def test_historical_labels_and_invalidated_evidence_do_not_create_advice():
    from trw_mcp.tools._retraction_nudge import _has_unsettled_contradiction

    legacy = {"outcome_history": [{"outcome": "assertion_contradicted"}]}
    assert not _has_unsettled_contradiction(legacy)
    failed = {
        "assertions": [
            {"last_result": False, "last_verified_at": (datetime.now(timezone.utc) - timedelta(days=500)).isoformat()}
        ]
    }
    assert _has_unsettled_contradiction(failed)
    assert not _has_unsettled_contradiction({**failed, "invalidated_by": "L-new"})
    assert not _has_unsettled_contradiction({**failed, "invalid_from": NOW.isoformat()})


def test_compact_acquisition_preserves_raw_observation_without_lookup():
    from trw_memory.models.memory import Assertion, AssertionType, MemoryEntry

    from trw_mcp.state._memory_transforms import _memory_to_learning_dict

    entry = MemoryEntry(
        id="L-local",
        content="claim",
        created_at=NOW,
        updated_at=NOW,
        assertions=[
            Assertion(
                type=AssertionType.GREP_PRESENT,
                pattern="x",
                target="x.py",
                last_result=False,
                last_verified_at=NOW,
                last_evidence="no match",
            )
        ],
        verification_status="verified",
        verification_checked_at=NOW.isoformat(),
    )
    compact = _memory_to_learning_dict(entry, compact=True)
    assert compact["assertions"][0]["last_result"] is False
    evidence, fraction = stored_claim_evidence(dict(compact), ttl_seconds=60, now=NOW)
    assert evidence["observation"] == "failure"
    assert fraction == 1
    assert evidence["aggregate"]["observation"] == "pass"


@pytest.mark.parametrize("mode", [{"compact": False}, {"compact": True}, {"ultra_compact": True}])
def test_registered_miss_is_unknown_without_implicit_work(tmp_path, monkeypatch, mode):
    from trw_memory.models.memory import Assertion, AssertionType, MemoryEntry

    from trw_mcp.state.memory_adapter import get_backend

    tools = _get_tools()
    backend = get_backend(tmp_path / ".trw")
    now = datetime.now(timezone.utc)
    backend.store(
        MemoryEntry(
            id="L-unverified",
            content="Unknown claim",
            created_at=now,
            updated_at=now,
            importance=0.8,
            assertions=[Assertion(type=AssertionType.GREP_PRESENT, pattern="missing", target="**/*.py")],
        )
    )

    def forbidden(*args, **kwargs):
        pytest.fail("unknown recall scheduled or performed verification")

    for path in [
        "trw_mcp.tools._verification_pass.run_verification_pass",
        "trw_mcp.tools._verification_pass.persist_verification_outcome",
        "trw_mcp.tools._verification_cache.warm_verified_verdict",
        "trw_memory.lifecycle.verification.verify_assertions",
    ]:
        monkeypatch.setattr(path, forbidden)
    result = tools["trw_recall"].fn(query="*", max_results=1, **mode)
    assert len(result["learnings"]) == 1
    evidence = result["learnings"][0]["verification_evidence"]
    assert evidence["observation"] == "unknown"
    assert evidence["assertions"][0]["freshness"] == "unknown"
    assert evidence["current_tree_verified"] is False
    assert backend.get("L-unverified", namespace="default").assertions[0].last_result is None


@pytest.mark.parametrize("namespaced", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_acquired_same_id_penalty_belongs_to_candidate(namespaced, reverse):
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.scoring._recall import rank_by_utility
    from trw_mcp.tools._recall_assertion_verification import _verify_assertions

    stamp = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rows = [
        {
            "id": "same",
            "summary": "equivalent claim",
            "impact": 0.8,
            "assertions": [{"last_result": result, "last_verified_at": stamp}],
            **({"namespace": namespace} if namespaced else {}),
        }
        for result, namespace in [(False, "bad"), (True, "good")]
    ]
    if reverse:
        rows.reverse()
    result = _verify_assertions(rows, [], TRWConfig(), rank_by_utility)
    assert result[0]["verification_evidence"]["observation"] == "pass"
    assert result[1]["verification_evidence"]["observation"] == "failure"
    assert result[0]["combined_score"] > result[1]["combined_score"]
    assert all(row["id"] == "same" for row in result)


@pytest.mark.parametrize("mode", [{"compact": False}, {"compact": True}, {"ultra_compact": True}])
def test_public_acquired_same_id_boundary_before_cap(tmp_path, monkeypatch, mode):
    """Injected acquisition boundary, not proof upstream federation retains IDs."""

    stamp = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    rows = [
        {
            "id": "same",
            "summary": summary,
            "impact": 0.8,
            "assertions": [{"last_result": result, "last_verified_at": stamp}],
        }
        for result, summary in [(False, "failed claim"), (True, "clean claim")]
    ]
    monkeypatch.setattr("trw_mcp.tools.learning.adapter_recall", lambda *args, **kwargs: rows)
    result = _get_tools()["trw_recall"].fn(query="*", max_results=1, **mode)
    assert len(result["learnings"]) == 1
    assert result["learnings"][0]["summary"] == "clean claim"
    assert result["learnings"][0]["verification_evidence"]["observation"] == "pass"


@pytest.mark.parametrize("path", ["main", "phase"])
def test_actual_startup_historical_only_invariance(tmp_path, path):
    """Actual SQLite acquisition through startup helpers, not a full ceremony."""
    from trw_memory.models.memory import MemoryEntry

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.memory_adapter import get_backend, reset_backend
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._session_recall_helpers import perform_session_recalls
    from trw_mcp.tools._session_recall_phase import _phase_contextual_recall

    config = TRWConfig(recall_max_results=3, auto_recall_max_results=3, session_start_recent_bypass_days=0)
    stamp = datetime.now(timezone.utc)
    results = []
    cohorts = []
    for variant in [0, 1]:
        # Separate stores prevent read/access accounting from confounding the pair.
        reset_backend()
        trw_dir = tmp_path / f"cohort-{variant}" / ".trw"
        backend = get_backend(trw_dir)
        for index in range(3):
            extreme = (index + variant) % 2 == 0
            backend.store(
                MemoryEntry(
                    id=f"L-{index}",
                    content=f"startup competing claim {index}",
                    importance=0.8,
                    created_at=stamp,
                    updated_at=stamp,
                    q_value=1.0 if extreme else 0.0,
                    q_observations=100 if extreme else 0,
                    outcome_history=["tests_passed"] if extreme else ["assertion_contradicted"],
                )
            )
        cohort = []
        for index in range(3):
            stored = backend.get(f"L-{index}", namespace="default").model_dump(mode="json")
            for field in ["q_value", "q_observations", "outcome_history"]:
                stored.pop(field)
            cohort.append(stored)
        cohorts.append(cohort)
        if path == "main":
            rows, _, _ = perform_session_recalls(trw_dir, "*", config, FileStateReader())
        else:
            rows = _phase_contextual_recall(trw_dir, "claim", config, None, None)
        assert len(rows) == 3
        results.append([(row["id"], row.get("combined_score")) for row in rows])
    assert cohorts[0] == cohorts[1]  # only historical fields differ before reads
    assert results[0] == results[1]
    reset_backend()

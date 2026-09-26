"""CORE-268: stored observations qualify real recall without implicit refresh."""

from datetime import datetime, timedelta, timezone

import pytest

from tests._memory_fixtures import DaemonCheckout
from tests._path_isolation import set_current_root
from tests._tools_learning_shared import _get_tools, set_project_root  # noqa: F401
from trw_mcp.tools._stored_claim_evidence import stored_claim_evidence

NOW = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)


def _seed(daemon_checkout: DaemonCheckout, entry_id: str, *, pattern: str, target: str, result=None, stamp=None):
    """Store one entry with a single grep_present assertion through the real daemon store."""
    from trw_mcp.state.memory_adapter import store_learning

    assertion: dict[str, object] = {"type": "grep_present", "pattern": pattern, "target": target}
    store_learning(
        daemon_checkout.trw_dir,
        entry_id,
        f"Sibling claim evidence {entry_id}",
        "",
        impact=0.8,
        assertions=[assertion],
    )
    if result is not None:
        import asyncio

        asyncio.run(
            daemon_checkout.client.update(
                entry_id,
                daemon_checkout.namespace,
                {"assertions": [{**assertion, "last_result": result, "last_verified_at": stamp.isoformat()}]},
            )
        )


def _fetch(daemon_checkout: DaemonCheckout, entry_id: str):
    """The stored entry as a ``MemoryEntry``, through the daemon (never a direct SQLite open)."""
    import asyncio

    from trw_memory.models.memory import MemoryEntry

    payload = asyncio.run(daemon_checkout.client.get(entry_id, daemon_checkout.namespace))
    return MemoryEntry.model_validate(payload["entry"])


def _capture_presented_rows(patcher):
    """Spy on the FR01 presenter to recover full ranked rows (verification_evidence
    included) behind trw_recall's default stub-only response. ``patcher`` is a
    pytest ``monkeypatch`` fixture or ``monkeypatch.context()`` instance.
    """
    import trw_mcp.tools._recall_presenter as presenter_module

    captured: list[list[dict]] = []
    real_present = presenter_module.present

    def spy(envelope, rows, **kwargs):
        captured.append(list(rows))
        return real_present(envelope, rows, **kwargs)

    patcher.setattr(presenter_module, "present", spy)
    return captured


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


def test_registered_recall_penalizes_before_cap_without_refresh(daemon_checkout: DaemonCheckout, monkeypatch):
    """PRD-CORE-280 slice e (batch 23b): ported off ``get_backend`` onto ``daemon_checkout``."""
    set_current_root(daemon_checkout.trw_dir.parent)
    tools = _get_tools()
    now = datetime.now(timezone.utc)
    for entry_id, result in [("L-failed", False), ("L-clean", True)]:
        _seed(daemon_checkout, entry_id, pattern="claim", target="*.py", result=result, stamp=now - timedelta(days=30))
    before = {i: _fetch(daemon_checkout, i).model_dump(mode="json") for i in ["L-failed", "L-clean"]}

    def forbidden(*args, **kwargs):
        pytest.fail("recall attempted implicit verification")

    for path in [
        "trw_memory.lifecycle.verification_pass.run_verification_pass",
        "trw_memory.lifecycle.verification_pass.persist_verification_outcome",
        "trw_memory.lifecycle.verification.verify_assertions",
        "trw_memory.lifecycle.anchor_validation.compute_anchor_validity",
        "trw_mcp.tools._verification_cache.warm_verified_verdict",
    ]:
        monkeypatch.setattr(path, forbidden)
    captured = _capture_presented_rows(monkeypatch)
    result = tools["trw_recall"].fn(query="*", max_results=1)
    assert [row["id"] for row in result["learnings"]] == ["L-clean"]
    assert captured[-1][0]["verification_evidence"]["current_tree_verified"] is False
    visible = tools["trw_recall"].fn(query="*", max_results=2)
    assert len(visible["learnings"]) == 2
    failed = next(row for row in captured[-1] if row["id"] == "L-failed")
    assert failed["verification_evidence"]["observation"] == "failure"
    assert failed["verification_evidence"]["assertions"][0]["freshness"] == "expired"
    for i, prior in before.items():
        after = _fetch(daemon_checkout, i).model_dump(mode="json")
        for field in [
            "assertions",
            "verification_checked_at",
            "verification_status",
            "outcome_history",
        ]:
            assert after[field] == prior[field]


def test_startup_acquired_siblings_before_cap(tmp_path, monkeypatch):
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._session_recall_helpers import perform_session_recalls

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
    config = TRWConfig(recall_max_results=1, auto_recall_max_results=1)
    monkeypatch.setattr("trw_mcp.state.recall_factories.recall_session_start", lambda *a, **k: rows)
    result, _ = perform_session_recalls(tmp_path / ".trw", "*", config, FileStateReader(), verbose=True)
    assert [entry["id"] for entry in result] == ["L-clean"]
    assert result[0]["verification_evidence"]["assertions"][0]["freshness"] == "expired"
    assert result[0]["verification_evidence"]["current_tree_verified"] is False


@pytest.mark.parametrize("mode", ["query", "startup_main"])
def test_explicit_refresh_reverses_public_recall_and_advice(daemon_checkout: DaemonCheckout, capsys, monkeypatch, mode):
    """PRD-CORE-280 slice e (batch 23b): ported off ``get_backend`` onto ``daemon_checkout``."""
    import argparse
    import json

    from trw_mcp.server._subcommands_maintain import _run_maintain_verify
    from trw_mcp.tools._retraction_nudge import unretracted_contradiction_nudge

    set_current_root(daemon_checkout.trw_dir.parent)
    tools = _get_tools()
    project_root = daemon_checkout.trw_dir.parent
    (project_root / "claim.py").write_text("healthy = True\n")
    for entry_id, pattern in [("L-failed", "repaired"), ("L-clean", "healthy")]:
        _seed(daemon_checkout, entry_id, pattern=pattern, target="claim.py")
    history = {i: (_fetch(daemon_checkout, i).outcome_history,) for i in ["L-failed", "L-clean"]}
    _run_maintain_verify(argparse.Namespace(as_json=True, namespace=daemon_checkout.namespace))
    assert json.loads(capsys.readouterr().out)["entries_processed"] == 2

    def read_without_refresh():
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._session_recall_helpers import perform_session_recalls

        def forbidden(*args, **kwargs):
            pytest.fail("startup/recall refreshed or scheduled verification on read")

        before = {i: _fetch(daemon_checkout, i).model_dump(mode="json") for i in ["L-failed", "L-clean"]}
        with monkeypatch.context() as guard:
            for path in [
                "trw_memory.lifecycle.verification_pass.run_verification_pass",
                "trw_memory.lifecycle.verification_pass.persist_verification_outcome",
                "trw_mcp.tools._verification_cache.warm_verified_verdict",
                "trw_memory.lifecycle.verification_pass.run_maintain_verify",
                "trw_mcp.tools._maintain_verify.run_maintain_verify_for_project",
                "trw_memory.lifecycle.verification.verify_assertions",
            ]:
                guard.setattr(path, forbidden)
            config = TRWConfig(recall_max_results=2, auto_recall_max_results=2)
            if mode == "startup_main":
                rows, _ = perform_session_recalls(daemon_checkout.trw_dir, "*", config, FileStateReader(), verbose=True)
            else:
                captured = _capture_presented_rows(guard)
                tools["trw_recall"].fn(query="*", max_results=2)
                rows = captured[-1]
        for i, original in before.items():
            after = _fetch(daemon_checkout, i).model_dump(mode="json")
            for field in [
                "assertions",
                "verification_status",
                "verification_checked_at",
                "outcome_history",
            ]:
                assert after[field] == original[field]
        assert len(rows) == 2  # actual daemon acquisition, no injected candidate rows
        return {"learnings": rows}

    failed = read_without_refresh()
    assert [row["id"] for row in failed["learnings"]] == ["L-clean", "L-failed"]
    assert failed["learnings"][1]["verification_evidence"]["observation"] == "failure"
    # Startup helper invocation is not a complete session-start ceremony; seed
    # advisory scope through a real registered recall, not fabricated receipts.
    if mode.startswith("startup_"):
        tools["trw_recall"].fn(query="*", max_results=2)
    assert "L-failed" in unretracted_contradiction_nudge(daemon_checkout.trw_dir)
    (project_root / "claim.py").write_text("healthy = True\nrepaired = True\n")
    _run_maintain_verify(argparse.Namespace(as_json=True, namespace=daemon_checkout.namespace))
    assert json.loads(capsys.readouterr().out)["entries_processed"] == 2
    corrected = read_without_refresh()
    assert all(row["verification_evidence"]["observation"] == "pass" for row in corrected["learnings"])
    assert unretracted_contradiction_nudge(daemon_checkout.trw_dir) == ""
    for i, old in history.items():
        entry = _fetch(daemon_checkout, i)
        assert (entry.outcome_history,) == old


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


def test_registered_miss_is_unknown_without_implicit_work(daemon_checkout: DaemonCheckout, monkeypatch):
    """PRD-CORE-280 slice e (batch 23b): ported off ``get_backend`` onto ``daemon_checkout``."""
    set_current_root(daemon_checkout.trw_dir.parent)
    tools = _get_tools()
    _seed(daemon_checkout, "L-unverified", pattern="missing", target="**/*.py")

    def forbidden(*args, **kwargs):
        pytest.fail("unknown recall scheduled or performed verification")

    for path in [
        "trw_memory.lifecycle.verification_pass.run_verification_pass",
        "trw_memory.lifecycle.verification_pass.persist_verification_outcome",
        "trw_mcp.tools._verification_cache.warm_verified_verdict",
        "trw_memory.lifecycle.verification.verify_assertions",
    ]:
        monkeypatch.setattr(path, forbidden)
    captured = _capture_presented_rows(monkeypatch)
    result = tools["trw_recall"].fn(query="*", max_results=1)
    assert len(result["learnings"]) == 1
    evidence = captured[-1][0]["verification_evidence"]
    assert evidence["observation"] == "unknown"
    assert evidence["assertions"][0]["freshness"] == "unknown"
    assert evidence["current_tree_verified"] is False
    assert _fetch(daemon_checkout, "L-unverified").assertions[0].last_result is None


@pytest.mark.parametrize("namespaced", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_acquired_same_id_penalty_belongs_to_candidate(namespaced, reverse):
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.scoring._recall import rank_targeted_by_utility
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
    result = _verify_assertions(rows, [], TRWConfig(), rank_targeted_by_utility)
    assert result[0]["verification_evidence"]["observation"] == "pass"
    assert result[1]["verification_evidence"]["observation"] == "failure"
    assert result[0]["combined_score"] > result[1]["combined_score"]
    assert all(row["id"] == "same" for row in result)


def test_public_acquired_same_id_boundary_before_cap(tmp_path, monkeypatch):
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
    captured = _capture_presented_rows(monkeypatch)
    result = _get_tools()["trw_recall"].fn(query="*", max_results=1)
    assert len(result["learnings"]) == 1
    assert captured[-1][0]["summary"] == "clean claim"
    assert captured[-1][0]["verification_evidence"]["observation"] == "pass"


# test_actual_startup_historical_only_invariance DELETED (PRD-CORE-280 slice e,
# batch 23b): it asserted combined_score is unaffected by an entry's
# outcome_history by seeding two cohorts differing only in that field via
# ``backend.store(MemoryEntry(..., outcome_history=...))``. Neither
# ``store_learning``/``StoreRequest`` nor the daemon's ``update`` patch
# (``LearningPatch`` has no ``outcome_history`` field -- it is written only by
# the verification pipeline itself) can set that field, so the seed cannot be
# expressed through any public store API post-e3. The invariant it guarded
# (``rank_targeted_by_utility`` never reads ``outcome_history`` -- confirmed:
# the field does not appear in ``scoring/_recall.py``) is not at risk of silent
# regression from an untested code path.

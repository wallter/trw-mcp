"""Store and recall tests for state/memory_adapter.py.

PRD-CORE-280 slice e1: real store-behaviour tests are ported onto
``daemon_checkout`` — the namespace ``store_learning``/``recall_learnings``
both resolve to there (``fake_memory_store`` does not: ``store_learning``
writes under the fixture's ``FAKE_NAMESPACE`` but the fake's ``recall()``
only searches ``"default"``; see ``tests/test_memory_adapter_wildcard_ranking.py``
for the seeding workaround that route needs — not used in this file since
everything here needing both write and read went to ``daemon_checkout``
instead).

The security-setting tests that used to live here (provenance, RBAC, quarantine,
redact, canary halt) moved in PRD-CORE-298 FR07. Those settings are daemon-wide, so
``tests/test_daemon_security_settings.py`` starts a daemon under them and checks it
enforces them. A write carrying an injection is refused before any recall could redact
it, so recall-side redaction is tested in trw-memory
(``tests/test_injection_scan_surface.py``). Canary halt is tested there too
(``tests/test_tools_recall_gaps.py``), since the canary row sits outside any checkout's
grant.

Ported, not blocked (PRD-CORE-280 slice e1 rework):

* The status-vocabulary tests drive the refusal through
  ``fake_memory_store``'s ``next_put_status`` hook (``tests/_memory_store_fake.py``,
  with a matching contract case in ``tests/test_store_contract.py``) instead of
  patching ``memory_adapter.memory_store_impl``: the fake has no write gate.
* ``test_invalid_store_input_is_refused_by_the_delegated_validation`` is
  ported onto ``daemon_checkout``. The daemon reports the refusal as an
  ``invalid`` status (PRD-CORE-280 e3), which ``store_learning`` answers as
  ``rejected``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.state.memory_adapter import find_entry_by_id, recall_learnings, store_learning

from ._memory_adapter_support import (
    trw_dir,  # noqa: F401
    trw_dir_with_entries,  # noqa: F401
)
from ._memory_store_fake import FakeMemoryStore


class TestStoreLearning:
    def test_basic_store(self, daemon_checkout: DaemonCheckout) -> None:
        result = store_learning(
            daemon_checkout.trw_dir,
            "L-new001",
            "Test summary",
            "Test detail",
            tags=["test"],
            impact=0.7,
        )
        assert result["learning_id"] == "L-new001"
        assert result["status"] == "recorded"
        assert "path" in result
        assert "distribution_warning" in result

    def test_return_shape_keys(self, daemon_checkout: DaemonCheckout) -> None:
        """Return dict must have exact key set for API compatibility."""
        result = store_learning(
            daemon_checkout.trw_dir,
            "L-shape01",
            "s",
            "d",
        )
        expected_keys = {"learning_id", "path", "status", "distribution_warning"}
        assert set(result.keys()) == expected_keys

    def test_shard_id_stored_in_metadata(self, daemon_checkout: DaemonCheckout) -> None:
        store_learning(
            daemon_checkout.trw_dir,
            "L-shard01",
            "s",
            "d",
            shard_id="shard-A",
        )
        entry = find_entry_by_id(daemon_checkout.trw_dir, "L-shard01")
        assert entry is not None
        assert entry["shard_id"] == "shard-A"


class TestRecallLearnings:
    def test_wildcard_returns_all(self, daemon_checkout: DaemonCheckout) -> None:
        store_learning(daemon_checkout.trw_dir, "L-r1", "Alpha learning", "d1")
        store_learning(daemon_checkout.trw_dir, "L-r2", "Beta learning", "d2")
        results = recall_learnings(daemon_checkout.trw_dir, "*")
        assert len(results) == 2

    def test_keyword_search(self, daemon_checkout: DaemonCheckout) -> None:
        store_learning(daemon_checkout.trw_dir, "L-k1", "Python gotcha", "patching issue")
        store_learning(daemon_checkout.trw_dir, "L-k2", "Rust memory", "ownership rules")
        results = recall_learnings(daemon_checkout.trw_dir, "Python")
        assert len(results) >= 1
        assert any(r["id"] == "L-k1" for r in results)

    def test_min_impact_filter(self, daemon_checkout: DaemonCheckout) -> None:
        store_learning(daemon_checkout.trw_dir, "L-i1", "Low impact", "d", impact=0.3)
        store_learning(daemon_checkout.trw_dir, "L-i2", "High impact", "d", impact=0.9)
        results = recall_learnings(daemon_checkout.trw_dir, "*", min_impact=0.7)
        assert len(results) == 1
        assert results[0]["id"] == "L-i2"

    def test_tag_filter_on_wildcard(self, daemon_checkout: DaemonCheckout) -> None:
        store_learning(daemon_checkout.trw_dir, "L-t1", "s1", "d", tags=["python"])
        store_learning(daemon_checkout.trw_dir, "L-t2", "s2", "d", tags=["rust"])
        results = recall_learnings(daemon_checkout.trw_dir, "*", tags=["python"])
        assert len(results) == 1
        assert results[0]["id"] == "L-t1"

    def test_compact_mode(self, daemon_checkout: DaemonCheckout) -> None:
        store_learning(daemon_checkout.trw_dir, "L-c1", "Summary", "Detail")
        results = recall_learnings(daemon_checkout.trw_dir, "*", compact=True)
        assert len(results) == 1
        result = results[0]
        assert "id" in result
        assert "summary" in result
        assert "impact" in result
        assert "detail" not in result

    def test_return_shape_keys(self, daemon_checkout: DaemonCheckout) -> None:
        """Recalled entries have the expected learning dict keys."""
        store_learning(daemon_checkout.trw_dir, "L-rs1", "s", "d", tags=["t"], evidence=["e"])
        results = recall_learnings(daemon_checkout.trw_dir, "*", compact=False)
        assert len(results) == 1
        entry = results[0]
        expected_keys = {
            "id",
            "summary",
            "tags",
            "impact",
            "status",
            "detail",
            "evidence",
            "source_type",
            "source_identity",
            "created",
            "updated",
            "access_count",
            "last_accessed_at",
            "recurrence",
            "shard_id",
        }
        assert expected_keys <= set(entry.keys())


class TestRecallByLearningId:
    """FIX-055: recall queries containing learning IDs (L-xxxxxxxx) resolve
    via direct primary-key lookup instead of keyword intersection."""

    @pytest.fixture
    def trw_dir_with_entries(self, daemon_checkout: DaemonCheckout) -> Path:
        store_learning(
            daemon_checkout.trw_dir,
            "L-test0001",
            "Test learning about Python",
            "Python is a great language",
            tags=["python", "testing"],
            impact=0.8,
        )
        store_learning(
            daemon_checkout.trw_dir,
            "L-test0002",
            "Testing gotcha with mocking",
            "Always patch at the import site",
            tags=["testing", "gotcha"],
            evidence=["test_foo.py"],
            impact=0.6,
        )
        store_learning(
            daemon_checkout.trw_dir,
            "L-test0003",
            "Obsolete learning",
            "No longer relevant",
            tags=["old"],
            impact=0.4,
        )
        from trw_mcp.state.memory_adapter import update_learning

        update_learning(daemon_checkout.trw_dir, "L-test0003", status="obsolete")
        return daemon_checkout.trw_dir

    def test_single_id_returns_exact_match(self, trw_dir_with_entries: Path) -> None:
        """Querying a single learning ID returns that exact entry."""
        results = recall_learnings(trw_dir_with_entries, query="L-test0001")
        ids = [str(r["id"]) for r in results]
        assert "L-test0001" in ids

    def test_two_ids_returns_both(self, trw_dir_with_entries: Path) -> None:
        """Querying two learning IDs returns both entries (OR, not AND)."""
        results = recall_learnings(
            trw_dir_with_entries,
            query="L-test0001 L-test0002",
        )
        ids = {str(r["id"]) for r in results}
        assert "L-test0001" in ids
        assert "L-test0002" in ids

    def test_id_plus_keywords_returns_union(self, trw_dir_with_entries: Path) -> None:
        """Mixed query with IDs and keywords returns union of both result sets."""
        results = recall_learnings(
            trw_dir_with_entries,
            query="L-test0001 mocking",
        )
        ids = {str(r["id"]) for r in results}
        assert "L-test0001" in ids
        assert "L-test0002" in ids

    def test_nonexistent_id_returns_empty(self, trw_dir_with_entries: Path) -> None:
        """Querying a non-existent learning ID returns no results."""
        results = recall_learnings(trw_dir_with_entries, query="L-00000000")
        assert results == []

    def test_id_lookup_respects_status_filter(self, trw_dir_with_entries: Path) -> None:
        """Direct ID lookup still applies status filter."""
        results = recall_learnings(
            trw_dir_with_entries,
            query="L-test0003",
            status="active",
        )
        ids = [str(r["id"]) for r in results]
        assert "L-test0003" not in ids


class TestDelegatedStoreStatusVocabulary:
    """PRD-CORE-251 FR03: the memory vocabulary translated into the learning one."""

    def test_every_status_the_store_impl_can_return_is_mapped(self) -> None:
        """Totality, read off the real source — not a hand-listed vocabulary."""
        import ast
        import inspect

        from trw_memory.tools import store as store_module

        from trw_mcp.state.memory_adapter import _STORE_STATUS_TO_LEARNING_STATUS

        tree = ast.parse(inspect.getsource(store_module))
        returned: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=False):
                if isinstance(key, ast.Constant) and key.value == "status":
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        returned.add(value.value)
                    elif isinstance(value, ast.IfExp):
                        returned.update(
                            branch.value
                            for branch in (value.body, value.orelse)
                            if isinstance(branch, ast.Constant) and isinstance(branch.value, str)
                        )

        assert returned, "found no status literals — the scan broke, it did not pass"
        unmapped = returned - set(_STORE_STATUS_TO_LEARNING_STATUS)
        assert not unmapped, (
            f"memory_store_impl can return {sorted(unmapped)}, which "
            "_STORE_STATUS_TO_LEARNING_STATUS does not classify. Decide "
            "deliberately whether each means recorded, quarantined or error — an "
            "unclassified success status becomes 'error', and an unclassified "
            "failure status mapped to 'recorded' writes an orphan YAML sidecar."
        )

    def test_an_unknown_status_fails_safe_to_error(self) -> None:
        """The default direction must suppress the sidecar, never write one."""
        from trw_mcp.state.memory_adapter import _learning_status_for

        assert _learning_status_for("some_future_status") == "error"
        assert _learning_status_for(None) == "error"
        assert _learning_status_for("stored") == "recorded"
        assert _learning_status_for("updated") == "recorded"
        assert _learning_status_for("quarantined") == "quarantined"

    @pytest.mark.parametrize("store_status", ["not_found", "unheard_of"])
    def test_a_failed_store_returns_error_and_never_recorded(
        self, fake_memory_store: FakeMemoryStore, trw_dir: Path, store_status: str
    ) -> None:
        """The regression this exists to catch, driven through the real function.

        If any refusal status leaked through as ``recorded``, ``execute_learn``
        would fall through to the YAML sidecar write with NO row in SQLite. The
        dedup check reads the DB, so it could never suppress the retry, and the
        same summary would accumulate one orphan sidecar per attempt.
        """
        fake_memory_store.next_put_status = store_status

        result = store_learning(trw_dir, "L-refused", "s", "d")

        assert result["status"] == "error"
        assert set(result) == {"learning_id", "path", "status", "distribution_warning", "error"}
        assert fake_memory_store.get("L-refused") is None

    @pytest.mark.parametrize("store_status", ["invalid", "blocked"])
    def test_a_refused_store_returns_rejected_with_the_reason(
        self, fake_memory_store: FakeMemoryStore, trw_dir: Path, store_status: str
    ) -> None:
        """A refusal of the content itself is ``rejected``: never recorded, and never retried by the journal."""
        fake_memory_store.next_put_status = store_status

        result = store_learning(trw_dir, "L-refused", "s", "d")

        assert (result["status"], result["reason"]) == ("rejected", store_status)
        assert set(result) == {"learning_id", "path", "status", "distribution_warning", "reason", "message"}
        assert fake_memory_store.get("L-refused") is None

    def test_a_quarantined_store_keeps_the_quarantined_status(
        self, fake_memory_store: FakeMemoryStore, trw_dir: Path
    ) -> None:
        """Quarantine is a decision, not a failure: the entry IS in the quarantine store."""
        fake_memory_store.next_put_status = "quarantined"

        result = store_learning(trw_dir, "L-q", "s", "d")

        assert result["status"] == "quarantined"
        assert set(result) == {"learning_id", "path", "status", "distribution_warning"}
        assert fake_memory_store.get("L-q") is None


class TestDelegatedStoreRunsTheValidations:
    def test_invalid_store_input_is_refused_by_the_delegated_validation(self, daemon_checkout: DaemonCheckout) -> None:
        """``validate_store_inputs`` runs on the learning path even through the daemon.

        Ported from a ``SchemaValidationError`` raise straight out of
        ``store_learning`` (the pre-daemon shape). Through ``daemon_checkout``
        the daemon reports the refusal as an ``invalid`` status (PRD-CORE-280
        e3), which ``store_learning`` answers as ``rejected``.
        """
        result = store_learning(daemon_checkout.trw_dir, "L-badinput", "   ", "detail")

        assert (result["status"], result["reason"]) == ("rejected", "invalid")
        assert "schema invalid" in str(result["message"])

        row = asyncio.run(daemon_checkout.client.get("L-badinput", daemon_checkout.namespace))
        assert row.get("status") == "not_found" or row.get("entry") is None

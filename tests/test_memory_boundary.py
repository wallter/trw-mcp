"""Integration tests for the trw-mcp -> trw-memory boundary.

These tests target the serialization fragility at the adapter layer:
- the store path maps summary->content, impact->importance
- ``_memory_to_learning_dict`` reverses that mapping on read-back
- Any regression in either direction breaks the entire learning store

PRD-CORE-280 slice e1: ``TestFieldRenameRoundTrip`` and
``TestMemoryStatusRoundTrip`` are ported onto ``daemon_checkout`` — they drive
both ``store_learning`` (write direction) and ``recall_learnings`` (read
direction) together, which needs a store where both resolve to the SAME
project namespace (``fake_memory_store`` does not: ``store_learning`` writes
under the fixture's ``FAKE_NAMESPACE`` but the fake's ``recall()`` only
searches ``"default"``).

Deleted, not ported:

* ``TestStorageErrorPropagation`` (3) and ``TestHybridSearchPath`` (3) — both
  patch methods (``.store``, ``.search``, ``.list_entries``,
  ``.get_vector_records``) directly on a backend obtained through the SQLite
  singleton accessor, or spy on ``trw_memory.retrieval.pipeline.hybrid_search``
  — backend-internal plumbing the contract reserves to trw-memory, and
  unreachable through the store contract (``FakeMemoryStore.recall`` never
  calls real hybrid search; the daemon runs its own backend in a separate
  process, so a patch here never reaches it).
* ``TestEmbeddingBackfill`` (3) — ``backfill_embeddings()`` is a
  singleton-accessor-only, backend-internal operation (embedding-vector
  backfill against ``backend.upsert_vector``/``existing_vector_ids``), the
  same "local-file backfill" category the fixture contract calls out for
  deletion.
* ``TestConcurrentSingletonAccess`` (2) — asserts the SQLite singleton
  accessor's identity/thread-safety directly: exactly the "singleton caching"
  category the fixture contract calls out for deletion.

The monorepo boundary-ratchet gate tests below (``check_memory_boundary.py``)
never touch the memory store at all and are unchanged.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.state.memory_adapter import recall_learnings, store_learning, update_learning

# ---------------------------------------------------------------------------
# P1-B: Field rename round-trip (THE critical test)
# ---------------------------------------------------------------------------


class TestFieldRenameRoundTrip:
    """P1-B: The bidirectional field mapping is the primary serialization fragility.

    trw-mcp uses:  summary / impact  (learning API)
    trw-memory uses: content / importance  (storage layer)

    store_learning():             summary -> content, impact -> importance
    _memory_to_learning_dict():   content -> summary, importance -> impact

    Any regression in either direction silently stores/returns data under the
    wrong key, causing tool callers to see None or KeyError.
    """

    def test_store_and_recall_expose_learning_field_names(self, daemon_checkout: DaemonCheckout) -> None:
        """After storing via store_learning(), recall_learnings() must return
        dicts with 'summary' and 'impact' keys — NOT 'content' or 'importance'."""
        result = store_learning(
            daemon_checkout.trw_dir,
            "L-rt001",
            "test summary text",
            "detailed explanation",
            impact=0.8,
        )
        assert result["status"] == "recorded"

        recalled = recall_learnings(daemon_checkout.trw_dir, "*")
        assert len(recalled) == 1

        entry = recalled[0]
        # These are the external learning-API field names
        assert "summary" in entry, "Field 'summary' missing — likely stored as 'content'"
        assert "impact" in entry, "Field 'impact' missing — likely stored as 'importance'"
        # These are the internal MemoryEntry field names — must NOT appear at boundary
        assert "content" not in entry, "'content' leaked through to external dict"
        assert "importance" not in entry, "'importance' leaked through to external dict"

    def test_stored_summary_value_round_trips_correctly(self, daemon_checkout: DaemonCheckout) -> None:
        """The summary string must survive the summary->content->summary journey intact."""
        original_summary = "unique boundary test string xyzzy"
        store_learning(daemon_checkout.trw_dir, "L-rt002", original_summary, "detail", impact=0.6)

        recalled = recall_learnings(daemon_checkout.trw_dir, "*")
        assert len(recalled) == 1
        assert recalled[0]["summary"] == original_summary

    def test_stored_impact_value_round_trips_correctly(self, daemon_checkout: DaemonCheckout) -> None:
        """The impact float must survive the impact->importance->impact journey intact."""
        original_impact = 0.85
        store_learning(daemon_checkout.trw_dir, "L-rt003", "summary", "detail", impact=original_impact)

        recalled = recall_learnings(daemon_checkout.trw_dir, "*")
        assert len(recalled) == 1
        assert recalled[0]["impact"] == pytest.approx(original_impact)

    def test_underlying_store_holds_memory_field_names(self, daemon_checkout: DaemonCheckout) -> None:
        """The store layer must use 'content' and 'importance' — the MemoryEntry
        field names. This verifies the inbound direction of the mapping."""
        import asyncio

        store_learning(daemon_checkout.trw_dir, "L-rt004", "summary for store test", "detail", impact=0.75)

        row = asyncio.run(daemon_checkout.client.get("L-rt004", daemon_checkout.namespace))
        raw_entry = row["entry"]
        assert raw_entry is not None, "Entry not found in the store after store_learning()"

        # MemoryEntry must have content and importance (storage field names)
        assert raw_entry["content"] == "summary for store test", "MemoryEntry.content should hold the summary string"
        assert raw_entry["importance"] == pytest.approx(0.75), "MemoryEntry.importance should hold the impact float"
        # MemoryEntry should NOT have summary or impact keys
        assert "summary" not in raw_entry, "field name is 'content', not 'summary'"
        assert "impact" not in raw_entry, "field name is 'importance', not 'impact'"

    def test_compact_mode_also_uses_learning_field_names(self, daemon_checkout: DaemonCheckout) -> None:
        """Compact recall output must also use 'summary' and 'impact', not the
        internal MemoryEntry field names. Compact path has its own dict construction."""
        store_learning(daemon_checkout.trw_dir, "L-rt005", "compact test summary", "detail", impact=0.5)

        recalled = recall_learnings(daemon_checkout.trw_dir, "*", compact=True)
        assert len(recalled) == 1
        entry = recalled[0]
        assert "summary" in entry
        assert "impact" in entry
        assert "content" not in entry
        assert "importance" not in entry
        # Compact mode omits detail
        assert "detail" not in entry

    def test_update_learning_maps_summary_to_content_field(self, daemon_checkout: DaemonCheckout) -> None:
        """update_learning() with summary= must write to MemoryEntry.content, not a
        'summary' column (which doesn't exist in the store). This is the update path
        of the field rename — also a regression vector."""
        store_learning(daemon_checkout.trw_dir, "L-rt006", "original summary", "detail")
        update_learning(daemon_checkout.trw_dir, "L-rt006", summary="updated summary text")

        recalled = recall_learnings(daemon_checkout.trw_dir, "*")
        assert len(recalled) == 1
        assert recalled[0]["summary"] == "updated summary text"

    def test_update_learning_maps_impact_to_importance_field(self, daemon_checkout: DaemonCheckout) -> None:
        """update_learning() with impact= must write to MemoryEntry.importance."""
        store_learning(daemon_checkout.trw_dir, "L-rt007", "summary", "detail", impact=0.3)
        update_learning(daemon_checkout.trw_dir, "L-rt007", impact=0.95)

        recalled = recall_learnings(daemon_checkout.trw_dir, "*")
        assert len(recalled) == 1
        assert recalled[0]["impact"] == pytest.approx(0.95)

    def test_multiple_entries_all_use_correct_field_names(self, daemon_checkout: DaemonCheckout) -> None:
        """Field rename correctness must hold for all entries, not just the first.
        Tests that _memory_to_learning_dict is applied consistently in the list path."""
        for i in range(5):
            store_learning(
                daemon_checkout.trw_dir,
                f"L-rt{100 + i:03d}",
                f"summary number {i}",
                "detail",
                impact=0.1 * (i + 1),
            )

        all_entries = recall_learnings(daemon_checkout.trw_dir, "*")
        assert len(all_entries) == 5

        for entry in all_entries:
            assert "summary" in entry, f"entry {entry.get('id')} missing 'summary'"
            assert "impact" in entry, f"entry {entry.get('id')} missing 'impact'"
            assert "content" not in entry
            assert "importance" not in entry


# ---------------------------------------------------------------------------
# MemoryStatus round-trip through adapter
# ---------------------------------------------------------------------------


class TestMemoryStatusRoundTrip:
    """Status values must survive store -> recall -> update -> recall without
    corruption or silent coercion. The adapter converts MemoryStatus enum
    values to/from the string representation expected by tool callers."""

    def test_default_status_is_active(self, daemon_checkout: DaemonCheckout) -> None:
        """Freshly stored entries must have status='active' when recalled."""
        store_learning(daemon_checkout.trw_dir, "L-st001", "active entry", "detail")

        recalled = recall_learnings(daemon_checkout.trw_dir, "*")
        assert len(recalled) == 1
        assert recalled[0]["status"] == "active"

    def test_status_active_stored_and_recalled(self, daemon_checkout: DaemonCheckout) -> None:
        """Explicitly stored status='active' survives the round-trip."""
        store_learning(daemon_checkout.trw_dir, "L-st002", "explicit active", "detail")
        recalled = recall_learnings(daemon_checkout.trw_dir, "*", status="active")
        assert any(r["id"] == "L-st002" for r in recalled)
        entry = next(r for r in recalled if r["id"] == "L-st002")
        assert entry["status"] == "active"

    def test_status_resolved_after_update(self, daemon_checkout: DaemonCheckout) -> None:
        """After update_learning(status='resolved'), recall must return status='resolved'."""
        store_learning(daemon_checkout.trw_dir, "L-st003", "will be resolved", "detail")
        update_result = update_learning(daemon_checkout.trw_dir, "L-st003", status="resolved")
        assert update_result["status"] == "updated"

        # Wildcard recall must return the entry with updated status
        recalled = recall_learnings(daemon_checkout.trw_dir, "*")
        entry = next((r for r in recalled if r["id"] == "L-st003"), None)
        assert entry is not None, "Entry not found after status update"
        assert entry["status"] == "resolved"

    def test_status_filter_excludes_resolved_from_active_query(self, daemon_checkout: DaemonCheckout) -> None:
        """Filtering by status='active' must exclude entries with status='resolved'."""
        store_learning(daemon_checkout.trw_dir, "L-st004", "active one", "detail")
        store_learning(daemon_checkout.trw_dir, "L-st005", "resolved one", "detail")
        update_learning(daemon_checkout.trw_dir, "L-st005", status="resolved")

        active_entries = recall_learnings(daemon_checkout.trw_dir, "*", status="active")
        ids = [str(r["id"]) for r in active_entries]
        assert "L-st004" in ids
        assert "L-st005" not in ids, "Resolved entry appeared in active-only query"

    def test_status_obsolete_round_trip(self, daemon_checkout: DaemonCheckout) -> None:
        """Status='obsolete' must also survive the round-trip via update path."""
        store_learning(daemon_checkout.trw_dir, "L-st006", "will be obsolete", "detail")
        update_learning(daemon_checkout.trw_dir, "L-st006", status="obsolete")

        recalled = recall_learnings(daemon_checkout.trw_dir, "*")
        entry = next((r for r in recalled if r["id"] == "L-st006"), None)
        assert entry is not None
        assert entry["status"] == "obsolete"

    def test_status_string_value_not_enum_object_in_result(self, daemon_checkout: DaemonCheckout) -> None:
        """_memory_to_learning_dict must return the string value of MemoryStatus,
        not the MemoryStatus enum object itself. Tool callers expect plain strings."""
        store_learning(daemon_checkout.trw_dir, "L-st007", "string status check", "detail")

        recalled = recall_learnings(daemon_checkout.trw_dir, "*")
        assert len(recalled) == 1
        status_value = recalled[0]["status"]
        assert isinstance(status_value, str), (
            f"Expected str status, got {type(status_value)}: {status_value!r}. "
            "Check _memory_to_learning_dict enum -> string conversion."
        )
        assert status_value in {"active", "resolved", "obsolete"}, f"Unexpected status string value: {status_value!r}"

    def test_status_filter_on_keyword_search_path(self, daemon_checkout: DaemonCheckout) -> None:
        """Status filter must apply on the keyword search path, not just wildcard.
        Ensures the filter is wired through the store's recall path, not just recall_learnings."""
        store_learning(daemon_checkout.trw_dir, "L-st008", "python active test", "detail")
        store_learning(daemon_checkout.trw_dir, "L-st009", "python resolved test", "detail")
        update_learning(daemon_checkout.trw_dir, "L-st009", status="resolved")

        results = recall_learnings(daemon_checkout.trw_dir, "python", status="active")
        ids = [str(r["id"]) for r in results]
        assert "L-st008" in ids
        assert "L-st009" not in ids, "Resolved entry appeared in active-filtered keyword search"


# ---------------------------------------------------------------------------
# PRD-CORE-251 FR09: the memory-concern boundary ratchet
# ---------------------------------------------------------------------------
#
# ``scripts/check_memory_boundary.py`` is the gate. Phase 1 lands it ARMED WITH
# NOTHING on purpose: no concern has been delegated yet, so the
# re-implementation scan has an empty registry and would pass a tree full of
# duplication. A gate in that state is the kind that rots — green through five
# phases while never once biting. So these tests do two different jobs:
#
# * the checks that bite TODAY (import direction stays guarded, the trw-mcp-only
#   allowlist is present and rationalised, the delegation floor rises with the
#   first ``trw_memory.tools`` import) are asserted against the REAL tree;
# * the check that is armed with nothing is asserted against a PLANTED tree with
#   an injected concern registry, so the mechanism is proven before Phase 2
#   depends on it.

REPO_ROOT = Path(__file__).resolve().parents[2]
_BOUNDARY_SCRIPT = REPO_ROOT / "scripts" / "check_memory_boundary.py"

# Monorepo-only invariant: the repo-root scripts/ layout is absent from the
# standalone trw-mcp mirror, where these gate tests do not apply. The adapter
# tests above are NOT monorepo-only, so the skip is per-test, not module-level.
monorepo_only = pytest.mark.skipif(
    not _BOUNDARY_SCRIPT.is_file(),
    reason="monorepo-only invariant (repo-root scripts/ absent in mirror)",
)


def _load_gate() -> object:
    """Load the gate script as a module (it is a script, not an installed package)."""
    spec = importlib.util.spec_from_file_location("check_memory_boundary", _BOUNDARY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the module's @dataclass declarations can resolve
    # annotations — dataclasses looks the defining module up in sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@monorepo_only
def test_the_gate_passes_on_the_current_tree() -> None:
    """The ratchet must be green at the Phase 1 boundary (PRD-CORE-251 NFR02)."""
    assert _load_gate().main() == 0


@monorepo_only
def test_import_direction_is_guarded_by_the_real_scanner() -> None:
    """trw-memory importing trw_mcp must remain a build failure.

    The scan lives in ``scripts/check_import_boundaries.py`` and runs in
    ``make check`` through ``seam-check``; re-implementing it here would be the
    same duplication PRD-CORE-251 exists to delete. What was unguarded is the
    registry entry that makes it apply to trw-memory — this asserts it.
    """
    gate = _load_gate()
    assert gate.check_import_direction_is_guarded() == []
    assert "trw_mcp" in gate._load_import_boundaries().BOUNDARIES["trw-memory"][1]


@monorepo_only
def test_import_direction_check_fails_when_the_boundary_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Planted violation: drop trw_mcp from trw-memory's forbidden tuple."""
    gate = _load_gate()
    boundaries_module = gate._load_import_boundaries()
    source_dir, forbidden = boundaries_module.BOUNDARIES["trw-memory"]
    weakened = dict(boundaries_module.BOUNDARIES)
    weakened["trw-memory"] = (source_dir, tuple(root for root in forbidden if root != "trw_mcp"))
    monkeypatch.setattr(boundaries_module, "BOUNDARIES", weakened)
    monkeypatch.setattr(gate, "_load_import_boundaries", lambda: boundaries_module)

    violations = gate.check_import_direction_is_guarded()
    assert len(violations) == 1
    assert "no longer forbids 'trw_mcp'" in violations[0].message


@monorepo_only
def test_every_allowlist_entry_carries_a_rationale() -> None:
    """FR09 AC4 — an unexplained allowlist entry is an exemption nobody can review."""
    gate = _load_gate()
    assert gate.TRW_MCP_ONLY_CONCERNS, "the trw-mcp-only allowlist is empty — it would exempt nothing"
    for concern in gate.TRW_MCP_ONLY_CONCERNS:
        assert concern.rationale.strip(), f"{concern.module} has no rationale"
        assert len(concern.rationale.split()) >= 5, f"{concern.module}'s rationale is not a reason: {concern.rationale}"


@monorepo_only
def test_kept_concerns_still_live_in_trw_mcp() -> None:
    """Section 6's keep list is asserted, not described — a swept module fails here."""
    assert _load_gate().check_kept_concerns_present() == []


@monorepo_only
def test_kept_concern_check_fails_when_a_module_is_swept_away(tmp_path: Path) -> None:
    """Planted violation: the audit-recurrence detector has left trw-mcp."""
    gate = _load_gate()
    kept = gate.KeptConcern("state/consolidation/_audit_patterns.py", "TRW audit vocabulary, not a memory concern")
    violations = gate.check_kept_concerns_present(tree=tmp_path, kept=(kept,))
    assert len(violations) == 1
    assert "absent from trw-mcp" in violations[0].message


@monorepo_only
def test_no_memory_concern_is_reimplemented() -> None:
    """FR09 — the headline gate over the real tree.

    No longer vacuous: Phase 2 armed the store concern, so this scan now has
    something to find. The companion tests below still prove the mechanism on a
    planted tree, because a pass here is a pass over the concerns registered so
    far, not over every memory concern.
    """
    assert _load_gate().scan_reimplementations() == []


@monorepo_only
def test_the_ratchet_is_armed_with_the_phases_that_shipped() -> None:
    """A phase that delegates a concern but forgets to register it is visible here.

    Phase 2 (FR03) is the store concern; phase 3 (PRD-CORE-291) is dedup. Each later phase adds its own entry in
    the change that lands it; the assertion is on the phases DELEGATED, so a
    registration that runs ahead of the delegation fails just as loudly as one
    that lags behind it.
    """
    concerns = _load_gate().DELEGATED_CONCERNS
    assert {concern.phase for concern in concerns} == {2, 3}, (
        "DELEGATED_CONCERNS no longer matches the phases that have shipped — register the "
        "concern in the change that delegates it, and confirm its trw-mcp implementation "
        "was actually deleted."
    )
    store = next(concern for concern in concerns if concern.name == "store")
    assert store.owner == "trw_memory.tools.store.memory_store_impl"
    assert "_learning_to_memory_entry" in store.symbols, (
        "the retired hand builder must stay in the ratchet — re-introducing it is exactly "
        "the second-write-path regression FR03 removed"
    )
    dedup = next(concern for concern in concerns if concern.name == "dedup")
    assert dedup.owner == "trw_memory.lifecycle.dedup"
    assert dedup.symbols == {"check_duplicate", "merge_entries", "batch_dedup"}


@monorepo_only
def test_a_planted_reimplementation_fails_the_gate(tmp_path: Path) -> None:
    """The mechanism Phases 2-5 depend on, proven before they depend on it."""
    gate = _load_gate()
    concern = gate.Concern(
        name="dedup",
        owner="trw_memory.lifecycle.dedup",
        symbols=frozenset({"check_duplicate"}),
        phase=3,
    )
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "dedup.py").write_text(
        "def check_duplicate(entry: dict[str, object]) -> None:\n    return None\n",
        encoding="utf-8",
    )

    violations = gate.scan_reimplementations(tree=tmp_path, concerns=(concern,), kept=())
    assert len(violations) == 1
    assert violations[0].where == "trw-mcp/src/trw_mcp/state/dedup.py"
    assert "dedup" in violations[0].message
    assert "trw_memory.lifecycle.dedup" in violations[0].message, "the message must name the trw-memory owner"


@monorepo_only
def test_an_allowlisted_module_may_define_a_delegated_symbol(tmp_path: Path) -> None:
    """FR09/US-005 AC2 — the allowlist is what keeps the gate from blocking kept concerns."""
    gate = _load_gate()
    concern = gate.Concern(
        name="consolidation cycle",
        owner="trw_memory.lifecycle.consolidation",
        symbols=frozenset({"consolidate_cycle"}),
        phase=3,
    )
    (tmp_path / "state" / "consolidation").mkdir(parents=True)
    (tmp_path / "state" / "consolidation" / "_audit_patterns.py").write_text(
        "def consolidate_cycle() -> None:\n    return None\n", encoding="utf-8"
    )
    kept = (gate.KeptConcern("state/consolidation/_audit_patterns.py", "TRW audit vocabulary, not a memory concern"),)

    assert gate.scan_reimplementations(tree=tmp_path, concerns=(concern,), kept=kept) == []


@monorepo_only
def test_the_store_path_is_the_first_tool_surface_importer() -> None:
    """PRD-CORE-251 Phase 2 landed the first delegation; Phase 1 measured zero.

    This is the counter FR09 hands to the floor check, and it is now non-zero,
    which is what ARMS that check. Each later phase adds importers; this asserts
    the store path is among them rather than pinning an exact list. Since
    CORE-280 e3 (W09) the store path reaches the tool surface through the daemon
    store module, not ``memory_adapter``.
    """
    importers = _load_gate().count_tool_surface_imports()
    assert "state/_daemon_store.py" in importers, importers


@monorepo_only
def test_the_declared_floor_covers_the_live_delegation() -> None:
    """With a live importer the floor check is armed, and the declared floor clears it.

    Phase 1 could only assert this check was inert. It is now evaluated against
    the real tree and the real pyproject: a floor below the release that first
    ships ``MemoryToolSurface`` would fail HERE rather than at a user's first
    tool call.
    """
    gate = _load_gate()
    assert gate.count_tool_surface_imports(), "no importer — this assertion would be vacuous"
    assert gate.check_delegation_floor() == []


@monorepo_only
def test_the_first_tool_surface_import_forces_the_floor_up(tmp_path: Path) -> None:
    """FR01 — a delegating trw-mcp with a stale floor fails the build.

    This is what makes a version skew fail at INSTALL time rather than at the
    first tool call, once there is a call to fail. Planted rather than live,
    because trw-mcp does not import the surface yet.
    """
    gate = _load_gate()
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "memory_adapter.py").write_text(
        "from trw_memory.tools import memory_store_impl\n", encoding="utf-8"
    )
    stale = tmp_path / "pyproject.toml"
    stale.write_text('[project]\nname = "trw-mcp"\ndependencies = ["trw-memory>=0.12.0,<1.0.0"]\n', encoding="utf-8")

    assert gate.count_tool_surface_imports(tmp_path) == ["state/memory_adapter.py"]
    violations = gate.check_delegation_floor(tree=tmp_path, pyproject=stale)
    assert len(violations) == 1
    assert gate.PROTOCOL_MIN_VERSION in violations[0].message

    current = tmp_path / "current.toml"
    current.write_text(
        f'[project]\nname = "trw-mcp"\ndependencies = ["trw-memory>={gate.PROTOCOL_MIN_VERSION},<1.0.0"]\n',
        encoding="utf-8",
    )
    assert gate.check_delegation_floor(tree=tmp_path, pyproject=current) == []

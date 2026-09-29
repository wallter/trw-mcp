"""PRD-CORE-305-FR06 (B80-37) — trw_status fields are source-aware.

``trw_status`` mixes CURRENT-RUN evidence (read from this run's own
run.yaml/events.jsonl) with PROJECT-scoped values that look past
the current run (``stale_count`` scans every run; ``version_warning`` compares
against the deployed framework version, not this run's; ``formation`` reads
sibling runs' own directories; ``build_gate_ready``/``review_gate_ready``/
``deliver_gate_summary`` come from project config plus session-wide
``ceremony_state.json``, not this run's own state;
``ceremony_status``/``nudge_content`` are project-session/learnings-derived,
added after the run-scoped assembly). Nothing in the pre-fix response said
which was which. This is the failing-first test: on the pre-fix tree
``field_scope`` is absent from the ``trw_status`` response entirely, so every
assertion below fails.

Sol round-1 P1 fixed two follow-on gaps in the first landing:

- ``build_gate_ready``/``review_gate_ready``/``deliver_gate_summary`` were
  classified "run" although they read project config and session-wide
  ceremony state, never this run's own ``run.yaml``.
- the classification table was a "project" set with an implicit "everything
  else is run" fallback, so an unrecognized key silently became "run" rather
  than surfacing as unclassified. ``_FIELD_SCOPE`` is now a POSITIVE,
  explicit table for every field, and this file's completeness test fails
  the moment a ``TrwStatusDict`` field lacks an entry.

The response itself is now compact (round-1 P2): it lists only the minority
``project`` keys by name plus a ``note`` that everything else is run-scoped,
rather than repeating the whole field list on every default call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests._tools_orchestration_support import orch_tools, set_project_root  # noqa: F401
from trw_mcp.models.typed_dicts import TrwStatusDict
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._orchestration_status_assembly import _FIELD_SCOPE, assemble_status_result, field_scope_label

pytestmark = pytest.mark.unit


def _minimal_result(run: Path) -> dict[str, object]:
    return dict(
        assemble_status_result(
            {"run_id": run.name, "task": run.name, "phase": "implement"},
            [],
            run,
            FileStateReader(),
            run / "meta",
        )
    )


# ---------------------------------------------------------------------------
# Completeness: every field TrwStatusDict can carry has an explicit entry
# ---------------------------------------------------------------------------


def test_every_trwstatusdict_field_has_an_explicit_scope_entry() -> None:
    """A field lacking an entry in ``_FIELD_SCOPE`` must fail THIS test, not silently read as 'run'."""
    declared_fields = set(TrwStatusDict.__annotations__)
    # ceremony_status/nudge_content are attached by shared middleware
    # (_apply_ceremony_status), not declared on TrwStatusDict itself, but
    # they DO appear in the real response field_scope_label must cover.
    known_fields = declared_fields | {"ceremony_status", "nudge_content"}

    missing = known_fields - set(_FIELD_SCOPE)
    assert not missing, f"fields with no explicit scope entry: {missing}"


def test_field_scope_table_has_no_stale_entries() -> None:
    """An entry naming a field TrwStatusDict no longer declares is dead weight that can drift silently."""
    declared_fields = set(TrwStatusDict.__annotations__) | {"ceremony_status", "nudge_content"}
    stale = set(_FIELD_SCOPE) - declared_fields
    assert not stale, f"_FIELD_SCOPE entries for fields TrwStatusDict no longer declares: {stale}"


def test_field_scope_values_are_only_run_or_project() -> None:
    assert set(_FIELD_SCOPE.values()) <= {"run", "project"}


# ---------------------------------------------------------------------------
# Shape: compact -- only the minority project keys, plus a note
# ---------------------------------------------------------------------------


def test_field_scope_label_lists_only_project_keys_and_a_note(tmp_path: Path) -> None:
    result = _minimal_result(tmp_path / "runs" / "r1")
    label = field_scope_label(result)

    assert set(label) <= {"project", "note", "unclassified"}
    assert "note" in label and isinstance(label["note"], str)
    assert "run_id" not in label["project"], "run-scoped fields must not be listed by name"


def test_run_scoped_fields_are_not_named_in_the_label(tmp_path: Path) -> None:
    result = _minimal_result(tmp_path / "runs" / "r2")
    label = field_scope_label(result)

    for run_field in ("run_id", "task", "phase", "status", "confidence", "event_count", "reflection"):
        assert run_field in result, f"sanity: {run_field!r} must be present to prove it's excluded, not just absent"
        assert run_field not in label["project"]


def test_stale_count_is_named_in_the_project_list(tmp_path: Path) -> None:
    """stale_count scans every run in the project, never just this one."""
    result = _minimal_result(tmp_path / "runs" / "r3")
    label = field_scope_label(result)

    assert "stale_count" in result, "sanity: stale_count is always present"
    assert "stale_count" in label["project"]


def test_formation_block_is_named_in_the_project_list_when_present(
    formation_env: FormationFixture,
) -> None:
    """The formation board is built from SIBLING runs' own directories -- not this run alone."""
    from trw_mcp.formation import create

    create(formation_env.orchestrator_run, formation_env.payload(), prds_dir=None)

    result = _minimal_result(formation_env.orchestrator_run)
    assert "formation" in result, "sanity: the formation block is present for this fixture"
    label = field_scope_label(result)
    assert "formation" in label["project"]


def test_deliver_gate_fields_are_named_in_the_project_list_when_present() -> None:
    """build_gate_ready/review_gate_ready/deliver_gate_summary read project config + session
    ceremony state, never this run's own run.yaml -- sol round-1 P1's specific finding."""
    result: dict[str, object] = {
        "run_id": "r",
        "build_gate_ready": True,
        "review_gate_ready": False,
        "deliver_gate_summary": "READY",
    }
    label = field_scope_label(result)

    for gate_field in ("build_gate_ready", "review_gate_ready", "deliver_gate_summary"):
        assert gate_field in label["project"], f"{gate_field!r} must be project-scoped"


def test_unclassified_key_is_surfaced_never_silently_folded_into_run() -> None:
    """sol round-1 P1's second finding: an unrecognized key must not default to 'run'."""
    result: dict[str, object] = {"run_id": "r", "a_future_field_nobody_classified_yet": 1}
    label = field_scope_label(result)

    assert "a_future_field_nobody_classified_yet" not in label["project"]
    assert label.get("unclassified") == ["a_future_field_nobody_classified_yet"]
    # sol round-2 P2: the note must not overclaim coverage of a field nobody
    # classified -- "every other field" would be false when unclassified is
    # non-empty; it must say "every other CLASSIFIED field".
    assert "classified" in label["note"].lower(), (
        f"the note overclaims when an unclassified field is present: {label['note']!r}"
    )


def test_unclassified_is_omitted_when_everything_present_is_classified(tmp_path: Path) -> None:
    result = _minimal_result(tmp_path / "runs" / "r4")
    label = field_scope_label(result)
    assert "unclassified" not in label


# ---------------------------------------------------------------------------
# End-to-end through the real tool
# ---------------------------------------------------------------------------


def test_the_real_trw_status_tool_labels_every_field_including_ceremony_status(
    orch_tools: dict[str, object],
    tmp_path: Path,
) -> None:
    """End-to-end through the registered trw_status tool.

    ``ceremony_status``/``nudge_content`` are attached by shared middleware
    AFTER the run-scoped assembly, so this is the acceptance-level proof that
    the label covers the response actually returned to a caller, not just the
    internal helper's output.
    """
    init_result = orch_tools["trw_init"].fn(task_name="fr06-e2e")  # type: ignore[attr-defined]
    run_path = str(init_result["run_path"])

    status_result = orch_tools["trw_status"].fn(run_path=run_path)  # type: ignore[attr-defined]

    assert "field_scope" in status_result
    label = status_result["field_scope"]
    assert label.get("unclassified") is None, f"unclassified fields in a real response: {label.get('unclassified')}"
    if "ceremony_status" in status_result:
        assert "ceremony_status" in label["project"]

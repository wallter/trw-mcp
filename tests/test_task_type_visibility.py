"""PRD-CORE-246 FR04/NFR03 — the classification is visible, and its knobs are bounded.

``DetectionResult.rationale`` existed and reached only a log line. ``RunStatusDict``
had no task-type field at all, so ``trw_session_start`` reported nothing. An agent
therefore could not tell a DETECTED type from a DEFAULTED one — the exact
"unset value indistinguishable from a checked positive result" shape this PRD
closes. 175 of 191 measured on-disk ``run.yaml`` files carry no ``task_type``
key, so the defaulted branch is a live read path, not dead code.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from trw_mcp.models.typed_dicts import RunStatusDict
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.tools._ceremony_runtime_helpers import _get_run_status

pytestmark = pytest.mark.integration


def _write_run(tmp_path: Path, payload: dict[str, object]) -> Path:
    run_dir = tmp_path / ".trw" / "runs" / "t" / "20260903T000000Z-cccc3333"
    (run_dir / "meta").mkdir(parents=True)
    FileStateWriter().write_yaml(run_dir / "meta" / "run.yaml", payload)
    return run_dir


_BASE: dict[str, object] = {
    "run_id": "20260903T000000Z-cccc3333",
    "task": "t",
    "status": "active",
    "phase": "deliver",
}


# ── FR04: the three sources are distinguishable ─────────────────────────


def test_session_start_reports_task_type_and_source(tmp_path: Path) -> None:
    """FR04 AC2/AC3: ``run_yaml`` and ``default_unknown`` are distinguishable.

    Both cases report ``task_type``; only ``task_type_source`` tells them apart.
    A test that asserted the value alone would pass on the defect.
    """
    keyless = _get_run_status(_write_run(tmp_path / "a", dict(_BASE)))
    assert keyless["task_type"] == "unknown"
    assert keyless["task_type_source"] == "default_unknown"

    typed = _get_run_status(_write_run(tmp_path / "b", {**_BASE, "task_type": "coding"}))
    assert typed["task_type"] == "coding"
    assert typed["task_type_source"] == "run_yaml"

    # The whole point: same key, different provenance.
    assert keyless["task_type_source"] != typed["task_type_source"]


def test_keyless_run_yaml_reports_default_unknown(tmp_path: Path) -> None:
    """Migration test: a run.yaml shaped like the 175 measured on-disk files.

    No write migration is performed — the read-time default is made VISIBLE, not
    changed. Reading twice must be idempotent and must not write the key back.
    """
    run_dir = _write_run(tmp_path, dict(_BASE))
    before = (run_dir / "meta" / "run.yaml").read_bytes()

    first = _get_run_status(run_dir)
    second = _get_run_status(run_dir)

    assert first["task_type_source"] == second["task_type_source"] == "default_unknown"
    assert (run_dir / "meta" / "run.yaml").read_bytes() == before, "_get_run_status must not write back"


def test_unreadable_run_reports_unresolved(tmp_path: Path) -> None:
    """FR04 boundary: an unreadable run is ``unresolved``, NOT a defaulted unknown.

    Seeding the two keys before the read is what makes this hold — a
    populate-on-success implementation would leave the keys absent and a caller
    could not distinguish "no run.yaml" from "no task_type key".
    """
    missing = tmp_path / ".trw" / "runs" / "t" / "20260903T000000Z-dddd4444"
    (missing / "meta").mkdir(parents=True)

    result = _get_run_status(missing)
    assert result["task_type"] == "unknown"
    assert result["task_type_source"] == "unresolved"

    # A malformed run.yaml takes the same branch, not the defaulted one.
    (missing / "meta" / "run.yaml").write_text("::: not yaml :::\n", encoding="utf-8")
    broken = _get_run_status(missing)
    assert broken["task_type_source"] == "unresolved"


def test_run_status_dict_declares_both_fields() -> None:
    """FR04 ``grep_present``: the typed shape, not just the runtime dict."""
    # ``from __future__ import annotations`` in the defining module makes these
    # ForwardRefs, so compare the resolved type rather than the raw annotation.
    resolved = get_type_hints(RunStatusDict)
    assert resolved["task_type"] is str
    assert resolved["task_type_source"] is str


def test_trw_init_returns_the_rationale_and_method() -> None:
    """FR04 AC1 + ``output_contains``: ``trw_init`` explains WHICH signal fired."""
    from tests.conftest import extract_tool_fn, make_test_server

    trw_init = extract_tool_fn(make_test_server("orchestration"), "trw_init")
    result = trw_init(task_name="ticket-77", objective="Investigate the crash", run_type="research")

    assert result["task_type"] == "rca"
    assert result["task_type_detection_method"] == "keyword"
    assert "task_type_rationale" in result
    assert "rca" in result["task_type_rationale"]

    # The defaulted case is explained differently — that is the requirement.
    defaulted = trw_init(task_name="ticket-78", objective="", run_type="")
    assert defaulted["task_type"] == "unknown"
    assert defaulted["task_type_detection_method"] == "fallback"
    assert "no task-type signals matched" in defaulted["task_type_rationale"]


def test_init_then_session_start_agree_on_the_task_type(tmp_path: Path) -> None:
    """FR04 integration: what ``trw_init`` decided is what ``trw_session_start``
    later reports, with ``task_type_source: run_yaml`` because the key was
    actually written."""
    from tests.conftest import extract_tool_fn, make_test_server

    trw_init = extract_tool_fn(make_test_server("orchestration"), "trw_init")
    created = trw_init(task_name="ticket-79", objective="Investigate the outage")

    status = _get_run_status(Path(str(created["run_path"])))
    assert status["task_type"] == created["task_type"] == "rca"
    assert status["task_type_source"] == "run_yaml"


# ── NFR03: bounded knobs ────────────────────────────────────────────────


def test_rationale_is_bounded_and_threshold_is_validated() -> None:
    """NFR03: the config field rejects out-of-range values and the only
    caller-supplied rationale fragment is truncated.

    A 4 KB ``run_type`` is echoed by the FALLBACK branch (it maps to nothing),
    which is precisely why that branch echoes it at all: without the echo the
    bound would be vacuously satisfied by never printing the value.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._task_type_detection import _MAX_RATIONALE_ECHO_CHARS, detect_task_type

    for bad in (0, 1001, -1):
        with pytest.raises(ValidationError):
            TRWConfig(deliver_gate_unclassified_change_threshold=bad)

    for good in (1, 5, 1000):
        assert (
            TRWConfig(deliver_gate_unclassified_change_threshold=good).deliver_gate_unclassified_change_threshold
            == good
        )

    assert TRWConfig().deliver_gate_unclassified_change_threshold == 1

    huge = "z" * 4096
    result = detect_task_type(run_type=huge)
    assert result.detection_method == "fallback"
    assert huge not in result.rationale, "the full 4 KB caller string must never reach a response"
    assert huge[:_MAX_RATIONALE_ECHO_CHARS] in result.rationale, "the bounded fragment IS surfaced"
    assert _MAX_RATIONALE_ECHO_CHARS == 64
    # Total rationale stays small: fixed template + one 64-char fragment.
    assert len(result.rationale) < 200

"""PRD-FIX-157-FR04: `_write_deferred_evidence`'s journal-type guard.

`_write_deferred_evidence` takes `journal: object` and, unlike the repo's
7 pure-narrowing `assert` sites, performs a real runtime `isinstance` check
before treating it as a `DeliverJournal`. Under `python -O` an `assert` is
stripped, so a wrong-typed journal used to silently reach `journal.step()`
inside a fail-open `try`/`except Exception` block -- a type bug masked as
if it were an I/O failure. This module proves the typed `TypeError` guard
fires under both normal and `-O` execution, and that a correctly-typed
journal's D23/D24 evidence-write behavior is unchanged.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from trw_mcp.tools._deferred_delivery import _write_deferred_evidence
from trw_mcp.tools._delivery_journal_wiring import DeliverJournal


def test_write_deferred_evidence_rejects_wrong_type() -> None:
    """A non-DeliverJournal `journal` raises TypeError, not AssertionError."""
    with pytest.raises(TypeError, match="DeliverJournal"):
        _write_deferred_evidence(object(), Path("/tmp"), None, {}, [])


def test_write_deferred_evidence_rejects_wrong_type_under_dash_o(tmp_path: Path) -> None:
    """The guard survives `python -O`, where a bare `assert` would be stripped.

    Failing-first evidence: against the unpatched (assert-based) source, this
    same invocation silently returns instead of raising -- the base-behavior
    proof captured separately in the implementation's WHY.
    """
    script = tmp_path / "probe.py"
    script.write_text(
        textwrap.dedent(
            """
            from pathlib import Path
            from trw_mcp.tools._deferred_delivery import _write_deferred_evidence

            try:
                _write_deferred_evidence(object(), Path("/tmp"), None, {}, [])
            except TypeError:
                print("TYPEERROR_RAISED")
            else:
                print("SILENTLY_PROCEEDED")
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-O", str(script)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        timeout=30,
        check=False,
    )
    assert "TYPEERROR_RAISED" in result.stdout, result.stdout + result.stderr


def test_write_deferred_evidence_persists_and_logs_for_a_correctly_typed_journal(tmp_path: Path) -> None:
    """A correctly-typed journal still runs both D23 (persist) and D24 (audit).

    `DeliverJournal()`'s default `mode="off"` makes `step()` a no-op context
    manager that always yields True, so both writes below still happen --
    this proves the D23/D24 write path is unchanged for the valid-input case.
    """
    journal = DeliverJournal()
    resolved_run = tmp_path / "run"
    (resolved_run / "meta").mkdir(parents=True)
    (resolved_run / "meta" / "run.yaml").write_text("{}\n", encoding="utf-8")
    trw_dir = tmp_path / ".trw"
    errors: list[str] = []

    _write_deferred_evidence(journal, trw_dir, resolved_run, {"outcome": "ok"}, errors)

    run_yaml_path = resolved_run / "meta" / "run.yaml"
    assert "deferred_results" in run_yaml_path.read_text(encoding="utf-8"), (
        "D23 persist must still write the results into run.yaml"
    )

    audit_log = trw_dir / "logs" / "deferred-deliver.jsonl"
    assert audit_log.is_file(), "D24 audit must still append an event"
    assert '"outcome": "ok"' in audit_log.read_text(encoding="utf-8")
    assert not errors

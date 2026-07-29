"""FR04 — writer/reader record comparison, as a (writer, shape, reader) TRIPLE.

The historical defect had a perfectly consistent field name. ``stop-ceremony.sh``
grepped ``session-events.jsonl`` for the event type ``trw_deliver_complete``,
which was a real, correctly-spelled event type — written into a *different
file*. Measured at the time: 149 rows, 0 matches, 21 actual deliveries, six
consecutive false reminders in one observed session. A field-name diff sees
nothing. Comparing which artifact the writer targets sees it immediately.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.wiring.checks.schema import check_schema
from trw_mcp.wiring.config import DEFAULT_CONFIG
from trw_mcp.wiring.detector import DetectorResult
from trw_mcp.wiring.model import ContractKind, EdgeClass
from trw_mcp.wiring.registry import ArtifactContract, build_registry

RECORD = "trw_deliver_complete"
ARTIFACT = "session-events.jsonl"


def _synthetic_contract(reader_relative: str) -> ArtifactContract:
    return ArtifactContract(
        contract_id="event:synthetic",
        kind=ContractKind.EVENT_STREAM,
        producer="synthetic writer",
        consumer="synthetic reader",
        artifact=RECORD,
        artifact_container=ARTIFACT,
        detail=(("reader_file", reader_relative),),
    )


def _write_repo(tmp_path: Path, writer_body: str) -> Path:
    reader = tmp_path / "hooks"
    reader.mkdir(parents=True)
    (reader / "reader.sh").write_text(f'grep "{RECORD}" "$_dir/{ARTIFACT}"\n', encoding="utf-8")
    package = tmp_path / "trw-mcp/src/trw_mcp/tools"
    package.mkdir(parents=True)
    (package / "writer.py").write_text(writer_body, encoding="utf-8")
    return tmp_path


def test_deliver_event_name_mismatch_detected(tmp_path: Path) -> None:
    """The record type is emitted — into the wrong artifact. That is the defect."""
    root = _write_repo(
        tmp_path,
        'def log_deliver(run_dir):\n    write(run_dir / "meta/events.jsonl", "trw_deliver_complete")\n',
    )
    findings = check_schema(root, _synthetic_contract("hooks/reader.sh"), max_bytes=DEFAULT_CONFIG.max_source_bytes)
    assert [f.edge_class for f in findings] == [EdgeClass.SCHEMA_DIVERGENCE]
    evidence = findings[0].evidence
    assert "writer/reader triple mismatch" in evidence
    assert "hooks/reader.sh" in evidence and ARTIFACT in evidence
    # The actionable half: name where the record IS produced.
    assert "writer.py::log_deliver" in evidence


def test_matching_writer_produces_no_finding(tmp_path: Path) -> None:
    """One function that knows both the record AND the artifact satisfies the contract."""
    root = _write_repo(
        tmp_path,
        'def write_marker(context_dir):\n    log(context_dir / "session-events.jsonl", "trw_deliver_complete")\n',
    )
    findings = check_schema(root, _synthetic_contract("hooks/reader.sh"), max_bytes=DEFAULT_CONFIG.max_source_bytes)
    assert not findings, [f.render() for f in findings]


def test_writer_scope_is_the_function_not_the_module(tmp_path: Path) -> None:
    """A module mentioning both in unrelated functions is not a writer of one into the other."""
    root = _write_repo(
        tmp_path,
        'def unrelated(path):\n    open(path / "session-events.jsonl")\n\n'
        'def log_deliver(run_dir):\n    write(run_dir, "trw_deliver_complete")\n',
    )
    findings = check_schema(root, _synthetic_contract("hooks/reader.sh"), max_bytes=DEFAULT_CONFIG.max_source_bytes)
    assert [f.edge_class for f in findings] == [EdgeClass.SCHEMA_DIVERGENCE]


def test_stale_registry_entry_is_a_finding(tmp_path: Path) -> None:
    """If the reader stopped looking for the record, the registry entry is stale — and fails."""
    root = _write_repo(tmp_path, 'def w(d):\n    log(d / "session-events.jsonl", "trw_deliver_complete")\n')
    (root / "hooks" / "reader.sh").write_text("grep something_else file\n", encoding="utf-8")
    findings = check_schema(root, _synthetic_contract("hooks/reader.sh"), max_bytes=DEFAULT_CONFIG.max_source_bytes)
    assert [f.edge_class for f in findings] == [EdgeClass.SCHEMA_DIVERGENCE]
    assert "the contract moved and the registry did not" in findings[0].evidence


def test_live_deliver_contract_is_currently_satisfied(live_result: DetectorResult, repo_root: Path) -> None:
    """UF-004 regression guard.

    The stop-ceremony defect was fixed on 2026-07-24 by
    ``write_session_deliver_marker``, which emits the record into
    ``session-events.jsonl``. The contract stays registered so that deleting
    that writer fires this signature again instead of silently reintroducing
    six false reminders per session.
    """
    contract = next(c for c in build_registry(repo_root) if c.kind is ContractKind.EVENT_STREAM)
    assert contract.contract_id == "event:session-deliver-marker"
    assert not [f for f in live_result.findings if f.contract_id == contract.contract_id]

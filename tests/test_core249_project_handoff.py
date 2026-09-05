"""PRD-CORE-249 FR01/FR02/FR05 + NFR03/NFR04/NFR05 — the project handoff store.

FR01: the typed ``project_handoff_path`` field, its default, and its containment.
FR02: the marker-bounded, idempotent, ``(run_id, gate_id)``-keyed write.
FR05: the managed ``## Remaining work handoff`` section in reports/final.md.
NFR03: path containment after symlink resolution, field bounds, marker injection.
NFR04: two concurrent writers produce the row union, with no torn read.
NFR05: forward-only adoption — creation, marker-less append, repeated writes.
"""

from __future__ import annotations

import multiprocessing
import os
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.plan_acceptance import AcceptanceStatus, parse_status_token
from trw_mcp.tools import _project_handoff as ph

# --------------------------------------------------------------------------- #
# FR01 — the typed field
# --------------------------------------------------------------------------- #


def _blocked(gate_id: str, owner: str = "ops@example", reason: str = "credential rotation") -> AcceptanceStatus:
    return parse_status_token(gate_id, f"blocked:human-only:{owner} — {reason}")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project root both the resolver and the config singleton agree on."""
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)
    return tmp_path


def _use_config(monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> TRWConfig:
    cfg = TRWConfig(**kwargs)  # type: ignore[arg-type]
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: cfg)
    return cfg


def test_default_and_override_resolution(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR01: unset -> .trw/HANDOFF.md; a set relative value resolves under root."""
    _use_config(monkeypatch)
    assert TRWConfig().project_handoff_path == ".trw/HANDOFF.md"
    assert ph.resolve_handoff_path() == (project / ".trw" / "HANDOFF.md").resolve()

    _use_config(monkeypatch, project_handoff_path="docs/documentation/improvement-backlog.md")
    assert ph.resolve_handoff_path() == (project / "docs/documentation/improvement-backlog.md").resolve()

    for bad in ("/etc/passwd", "../escape.md", "a/../../escape.md", "   "):
        with pytest.raises(ValidationError, match="project_handoff_path"):
            TRWConfig(project_handoff_path=bad)  # type: ignore[call-arg]


def test_path_containment_and_field_bounds(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR03: symlink escape refused at write time; caps truncate; markers neutralised."""
    outside = project.parent / "outside"
    outside.mkdir()
    (project / "linked").symlink_to(outside, target_is_directory=True)
    _use_config(monkeypatch, project_handoff_path="linked/HANDOFF.md")
    # The lexical validator cannot see a symlink; the write-time re-check can.
    with pytest.raises(ValueError, match="outside the project root"):
        ph.resolve_handoff_path()
    status = ph.write_handoff_rows(run_id="R1", accepted=[_blocked("X-1")], resolved_gate_ids=[])
    assert status["status"] == "failed"
    assert not (outside / "HANDOFF.md").exists()

    # Caps: parse truncates to the documented bound rather than aborting the run.
    long_owner = "o" * 400
    long_reason = "r" * 900
    parsed = parse_status_token("X-2", f"blocked:ops-only:{long_owner} — {long_reason}")
    assert len(parsed.owner) == 128
    assert len(parsed.reason) == 500

    # Marker injection: an end marker inside a field cannot terminate the block.
    _use_config(monkeypatch, project_handoff_path=".trw/HANDOFF.md")
    injected = parse_status_token("X-3", f"blocked:ops-only:me — see {ph.HANDOFF_END_MARKER} then evil")
    ph.write_handoff_rows(run_id="R1", accepted=[injected], resolved_gate_ids=[])
    content = ph.resolve_handoff_path().read_text(encoding="utf-8")
    span = ph.find_block_span(content)
    assert span is not None
    block = content[span[0] : span[1]]
    assert "X-3" in block
    # Exactly one whole-line end marker, and it is the block terminator.
    assert [ln for ln in content.splitlines() if ln.strip() == ph.HANDOFF_END_MARKER] == [ph.HANDOFF_END_MARKER]


# --------------------------------------------------------------------------- #
# FR02 — idempotence, key identity, user-content preservation
# --------------------------------------------------------------------------- #


def test_idempotent_marker_write_preserves_user_content(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR02: a second write is byte-identical and out-of-span bytes survive."""
    _use_config(monkeypatch)
    target = project / ".trw" / "HANDOFF.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    prologue = "# My notes\n\nA sentence mentioning `<!-- trw:handoff:end -->` inline in prose.\n"
    epilogue = "\n## Afterword\n\nUser-authored tail.\n"
    target.write_text(prologue + ph.HANDOFF_START_MARKER + "\n" + ph.HANDOFF_END_MARKER + epilogue, encoding="utf-8")

    rows = [_blocked("X-3"), _blocked("P-1", owner="sre@example", reason="staging quota")]
    ph.write_handoff_rows(run_id="RUN-A", accepted=rows, resolved_gate_ids=[], today=date(2026, 9, 3))
    first = target.read_text(encoding="utf-8")
    ph.write_handoff_rows(run_id="RUN-A", accepted=rows, resolved_gate_ids=[], today=date(2026, 9, 30))
    second = target.read_text(encoding="utf-8")

    assert first == second, "repeated deliver must not append or re-stamp"
    span = ph.find_block_span(second)
    assert span is not None
    assert second[: span[0]] == prologue, "user prose above the block was modified"
    assert second[span[1] :] == epilogue, "user prose below the block was modified"
    # first_seen is preserved across the second write despite the later date.
    assert ph.parse_rows(second[span[0] : span[1]])[0].first_seen == "2026-09-03"
    assert len(ph.parse_rows(second[span[0] : span[1]])) == 2

    # A different run naming the same gate is a DISTINCT row.
    ph.write_handoff_rows(run_id="RUN-B", accepted=[_blocked("X-3")], resolved_gate_ids=[])
    third = ph.resolve_handoff_path().read_text(encoding="utf-8")
    third_span = ph.find_block_span(third)
    assert third_span is not None
    assert len(ph.parse_rows(third[third_span[0] : third_span[1]])) == 3

    # Declaring it satisfied later REMOVES this run's row and only this run's.
    ph.write_handoff_rows(run_id="RUN-B", accepted=[], resolved_gate_ids=["X-3"])
    fourth = ph.resolve_handoff_path().read_text(encoding="utf-8")
    fourth_span = ph.find_block_span(fourth)
    assert fourth_span is not None
    keys = {r.key for r in ph.parse_rows(fourth[fourth_span[0] : fourth_span[1]])}
    assert ("RUN-B", "X-3") not in keys
    assert ("RUN-A", "X-3") in keys


def test_forward_only_idempotent_adoption(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR05: creation, marker-less adoption, and repetition destroy no bytes."""
    _use_config(monkeypatch)
    target = project / ".trw" / "HANDOFF.md"

    ph.write_handoff_rows(run_id="R", accepted=[_blocked("X-1")], resolved_gate_ids=[])
    created = target.read_text(encoding="utf-8")
    assert created.startswith(ph.HANDOFF_FILE_HEADER.rstrip("\n").split("\n")[0])
    assert ph.HANDOFF_OWNERSHIP_HEADER in created

    # A pre-existing marker-less file is adopted by APPENDING; prior bytes survive.
    legacy = "# Backlog\n\n- an item a human wrote\n"
    target.write_text(legacy, encoding="utf-8")
    ph.write_handoff_rows(run_id="R", accepted=[_blocked("X-1")], resolved_gate_ids=[])
    adopted = target.read_text(encoding="utf-8")
    assert adopted.startswith(legacy.rstrip("\n"))
    assert ph.find_block_span(adopted) is not None

    # A run with nothing to defer still converges: the block exists and is empty.
    target.unlink()
    ph.write_handoff_rows(run_id="R", accepted=[], resolved_gate_ids=[])
    empty_once = target.read_text(encoding="utf-8")
    ph.write_handoff_rows(run_id="R", accepted=[], resolved_gate_ids=[])
    assert target.read_text(encoding="utf-8") == empty_once


# --------------------------------------------------------------------------- #
# NFR04 — concurrency
# --------------------------------------------------------------------------- #


def _writer_process(root: str, run_id: str, gate_id: str) -> None:  # pragma: no cover - child process
    os.environ["TRW_PROJECT_ROOT"] = root
    from trw_mcp.models.plan_acceptance import parse_status_token as _parse
    from trw_mcp.tools import _project_handoff as _ph

    _ph.write_handoff_rows(
        run_id=run_id,
        accepted=[_parse(gate_id, "blocked:ops-only:owner — reason")],
        resolved_gate_ids=[],
    )


@pytest.mark.integration
def test_concurrent_writers_row_union(tmp_path: Path) -> None:
    """NFR04: two OS processes writing the same file yield the row union."""
    (tmp_path / ".trw").mkdir(parents=True)
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_writer_process, args=(str(tmp_path), f"RUN-{i}", f"X-{i}")) for i in range(1, 5)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=120)
        assert proc.exitcode == 0

    content = (tmp_path / ".trw" / "HANDOFF.md").read_text(encoding="utf-8")
    span = ph.find_block_span(content)
    assert span is not None
    keys = {row.key for row in ph.parse_rows(content[span[0] : span[1]])}
    assert keys == {(f"RUN-{i}", f"X-{i}") for i in range(1, 5)}, "a concurrent writer's rows were lost"


# --------------------------------------------------------------------------- #
# FR05 — the reports/final.md remaining-work section
# --------------------------------------------------------------------------- #


def _run_dir(project: Path, *, gates: str, run_id: str = "20260903T000000Z-fr05") -> Path:
    run_dir = project / ".trw" / "runs" / "task" / run_id
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "reports").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(
        f"run_id: {run_id}\nstatus: active\nphase: deliver\ntask_type: coding\nprd_scope: []\n",
        encoding="utf-8",
    )
    (run_dir / "reports" / "plan.md").write_text("# Plan\n\n- X-1: do the thing\n- X-2: do the other\n", "utf-8")
    (run_dir / "reports" / "acceptance.yaml").write_text(f"schema_version: 1\ngates:\n{gates}", encoding="utf-8")
    return run_dir


def test_final_md_remaining_work_section(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR05: the section is present, marker-replaced, and never silently absent."""
    from trw_mcp.tools import _ceremony_deliver_steps as steps

    _use_config(monkeypatch)
    run_dir = _run_dir(
        project,
        gates='  "X-1": "blocked:human-only:ops@example — rotate creds"\n  "X-2": "blocked:ops-only:sre — quota"\n',
    )
    final_md = run_dir / "reports" / "final.md"
    final_md.write_text("# Final report\n\nHuman-authored body.\n", encoding="utf-8")

    results: dict[str, object] = {}
    steps.step_project_handoff(run_dir, results)  # type: ignore[arg-type]
    body = final_md.read_text(encoding="utf-8")
    assert "## Remaining work handoff" in body
    assert "X-1" in body and "ops@example" in body and "rotate creds" in body
    assert "X-2" in body and "sre" in body
    assert body.startswith("# Final report\n\nHuman-authored body.")
    assert results["project_handoff"]["status"] == "written"  # type: ignore[index]

    # Re-running REPLACES the section in place rather than appending a second one.
    steps.step_project_handoff(run_dir, results)  # type: ignore[arg-type]
    again = final_md.read_text(encoding="utf-8")
    assert again.count("## Remaining work handoff") == 1
    assert again == body

    # No deferred work => the section is present and SAYS SO (never absent).
    clean = _run_dir(project, gates='  "X-1": "satisfied"\n  "X-2": "satisfied"\n', run_id="20260903T000001Z-clean")
    steps.step_project_handoff(clean, {})
    clean_body = (clean / "reports" / "final.md").read_text(encoding="utf-8")
    assert "## Remaining work handoff" in clean_body
    assert "No work was deferred by this run." in clean_body


# --------------------------------------------------------------------------- #
# Review round 1 — the cell codec must round-trip, or rows disappear
# --------------------------------------------------------------------------- #


def test_pipe_and_newline_in_fields_round_trip_through_write_parse_merge(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `|` in a field must not delete the row -- and must not delete OTHER rows.

    Regression for the round-1 critical: `neutralise` escaped `|` as `\\|` while
    `parse_rows` split on a bare `|`, so any owner or reason carrying a pipe
    produced a seven-cell row that the parser skipped. The NEXT write rebuilt the
    block from only what it could parse, so that row -- and, once a second run's
    row shared the block, that row too -- silently vanished, and an identifier
    declared again got a fresh `first_seen`.
    """
    _use_config(monkeypatch)
    hostile = parse_status_token(
        "X-1",
        "blocked:ops-only:team|ops@example — rotate creds | then verify < 5min & > 0 rows 100% done",
    )
    innocent = _blocked("X-2", owner="sre@example", reason="staging quota")

    ph.write_handoff_rows(run_id="RUN-A", accepted=[hostile, innocent], resolved_gate_ids=[], today=date(2026, 9, 3))
    target = ph.resolve_handoff_path()
    first = target.read_text(encoding="utf-8")
    span = ph.find_block_span(first)
    assert span is not None
    rows, unreadable = ph.parse_block(first[span[0] : span[1]])
    assert unreadable == [], "a row this code wrote must always be readable"
    assert len(rows) == 2, "the pipe-bearing row was dropped by the parser"

    # The values survive the round trip EXACTLY -- not merely "a row exists".
    by_gate = {row.gate_id: row for row in rows}
    assert by_gate["X-1"].owner == "team|ops@example"
    assert by_gate["X-1"].reason == "rotate creds | then verify < 5min & > 0 rows 100% done"

    # A second write of the same declaration is byte-identical, keeps BOTH rows,
    # and does not reset the age clock of the pipe-bearing identifier.
    ph.write_handoff_rows(run_id="RUN-A", accepted=[hostile, innocent], resolved_gate_ids=[], today=date(2026, 10, 20))
    second = target.read_text(encoding="utf-8")
    assert second == first
    second_span = ph.find_block_span(second)
    assert second_span is not None
    reparsed = {r.gate_id: r for r in ph.parse_rows(second[second_span[0] : second_span[1]])}
    assert set(reparsed) == {"X-1", "X-2"}
    assert reparsed["X-1"].first_seen == "2026-09-03", "first_seen was reset by the re-declaration"

    # A LATER run writing into the same block does not lose the earlier rows.
    ph.write_handoff_rows(run_id="RUN-B", accepted=[_blocked("X-9")], resolved_gate_ids=[])
    third = target.read_text(encoding="utf-8")
    third_span = ph.find_block_span(third)
    assert third_span is not None
    keys = {row.key for row in ph.parse_rows(third[third_span[0] : third_span[1]])}
    assert keys == {("RUN-A", "X-1"), ("RUN-A", "X-2"), ("RUN-B", "X-9")}

    # And the readback sees the decoded values, not the wire escapes.
    from trw_mcp.tools._project_handoff_readback import read_open_handoff

    observed = read_open_handoff()
    assert observed["total"] == 3
    owners = {item["gate_id"]: item["owner"] for item in observed["items"]}
    assert owners["X-1"] == "team|ops@example"
    assert "%7C" not in str(observed["items"])


def test_codec_is_reversible_for_every_escaped_character() -> None:
    """`decode_cell(neutralise(x))` is `x.strip()` -- the property the row store rests on."""
    for raw in (
        "plain",
        "a|b",
        "100% done",
        "%7C is not a pipe until decoded",
        "<!-- trw:handoff:end -->",
        "line one\nline two\r\nline three",
        "  padded  ",
        "%%%|||<<<>>>",
    ):
        assert ph.decode_cell(ph.neutralise(raw)) == raw.strip(), raw


def test_hand_broken_row_is_preserved_not_deleted(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A row a HUMAN broke survives the next write; the machine never writes one.

    Because the codec is reversible, an unreadable row can only be a human edit,
    and deleting a line a human typed inside the block is the same class of harm
    as the truncation the whole-line marker rule exists to prevent.
    """
    _use_config(monkeypatch)
    target = ph.resolve_handoff_path()
    ph.write_handoff_rows(run_id="RUN-A", accepted=[_blocked("X-1")], resolved_gate_ids=[])
    broken = "| 2026-01-01 | X-HAND | human-only |"
    content = target.read_text(encoding="utf-8")
    span = ph.find_block_span(content)
    assert span is not None
    target.write_text(
        content[: span[1] - len(ph.HANDOFF_END_MARKER)]
        + broken
        + "\n"
        + content[span[1] - len(ph.HANDOFF_END_MARKER) :],
        encoding="utf-8",
    )
    rows, unreadable = ph.parse_block(target.read_text(encoding="utf-8"))
    assert [r.gate_id for r in rows] == ["X-1"]
    assert unreadable == [broken]

    ph.write_handoff_rows(run_id="RUN-B", accepted=[_blocked("X-2")], resolved_gate_ids=[])
    after = target.read_text(encoding="utf-8")
    assert broken in after, "a hand-edited row was silently deleted by the next write"
    assert "X-1" in after and "X-2" in after


def test_impossible_date_is_not_measured_never_absent(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A well-shaped but impossible date must not take the whole readback down.

    `2026-02-30` matches the `first_seen` pattern; before the fix it raised inside
    `age_days`, the step failed, and `open_handoff` went ABSENT -- which FR03
    forbids, because absent is indistinguishable from a step that never ran.
    """
    from trw_mcp.tools._project_handoff_readback import read_open_handoff

    _use_config(monkeypatch)
    with pytest.raises(ValidationError):
        ph.HandoffRow(first_seen="2026-02-30", gate_id="X-1", blocking_class="human-only")

    target = ph.resolve_handoff_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        ph.HANDOFF_START_MARKER
        + "\n\n"
        + "\n".join(
            [
                "| First seen | Gate | Class | Owner | Run | Reason |",
                "| --- | --- | --- | --- | --- | --- |",
                "| 2026-02-30 | X-BAD | human-only | ops | RUN-A | impossible date |",
            ]
        )
        + "\n\n"
        + ph.HANDOFF_END_MARKER
        + "\n",
        encoding="utf-8",
    )
    observed = read_open_handoff()
    assert observed["status"] == "measured"
    assert observed["total"] == 0
    assert "status" in observed, "open_handoff must never be absent"

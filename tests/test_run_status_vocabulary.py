"""PRD-FIX-126 — the run-status vocabulary must equal what the runtime writes.

Contract suite for FR01, FR03, FR04, FR05 and all four NFRs. It mocks nothing:
no ``RunState`` double, no ``FileStateReader``/``FileStateWriter`` stand-in, no
patched enum. Every assertion goes through the real model against real files,
and the gate assertions run the real ``scripts/check-run-status-vocabulary.py``
as a subprocess against real trees.

Measured baseline that motivates all of this (2026-09-03, live tree, N=191
files matching ``.trw/runs/*/*/meta/run.yaml``): ``RunState.model_validate``
succeeded on 2 and failed on 189, and ``status`` was the sole failing field on
every one of the 189.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from trw_mcp.models.run import (
    _STATUS_ALIASES,
    _TERMINAL_RUN_STATUSES,
    RunState,
    RunStatus,
    coerce_run_status,
    is_terminal_status,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_TRW_MCP_SRC = Path(__file__).resolve().parents[1] / "src"
_GATE = _REPO_ROOT / "scripts" / "check-run-status-vocabulary.py"
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "run_status"

#: The vocabulary this PRD removed. Kept here (not in production code) purely so
#: the grep-absent assertions have a named subject.
_REMOVED_MEMBERS = ("RunStatus.PAUSED", "RunStatus.FAILED")

#: Every word the run-status vocabulary has ever used, canonical or not. A bare
#: string assignment to a ``status`` key drawn from THIS set is the FR03 defect;
#: an assignment of ``"archived"`` or ``"success"`` to some other object's
#: status key is not, which is why the scan is keyed on the value.
_RUN_STATUS_WORDS = frozenset({m.value for m in RunStatus} | set(_STATUS_ALIASES) | {"paused", "failed"})

#: NFR01 budget: p95 wall time of the gate over a corpus of >= 191 files.
_GATE_BUDGET_SECONDS = 5.0
#: Runs used to compute that p95.
_GATE_TIMING_RUNS = 5


# ── helpers ─────────────────────────────────────────────────────────────


def _run_yaml_text(status: str, *, run_id: str = "20260903T000000Z-aaaa1111") -> str:
    return (
        f"run_id: {run_id}\n"
        "task: vocabulary-probe\n"
        "framework: v26.2_TRW\n"
        f"status: {status}\n"
        "phase: review\n"
        "task_type: coding\n"
    )


def _seed_tree(root: Path, statuses: dict[str, str]) -> Path:
    """Write ``root/.trw/runs/<task>/<run_id>/meta/run.yaml`` per entry."""
    runs_root = root / ".trw" / "runs"
    for index, (name, status) in enumerate(sorted(statuses.items())):
        run_id = f"20260903T00000{index}Z-{name[:8]:_<8}"
        meta = runs_root / name / run_id / "meta"
        meta.mkdir(parents=True, exist_ok=True)
        (meta / "run.yaml").write_text(_run_yaml_text(status, run_id=run_id), encoding="utf-8")
    return runs_root


def _invoke_gate(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the real gate script against *project_root*."""
    if not (_REPO_ROOT / "release-packages.yaml").is_file():
        pytest.skip("run-status gate script is a monorepo-only subject")
    assert _GATE.is_file(), "run-status gate script is missing from the monorepo"
    env = dict(os.environ)
    env["TRW_PROJECT_ROOT"] = str(project_root)
    env.pop("TRW_RUNS_ROOT", None)
    return subprocess.run(
        [sys.executable, str(_GATE), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_REPO_ROOT),
    )


def _bare_status_literal_sites() -> tuple[list[str], list[str]]:
    """``(hits, unparseable)`` for ``x["status"] = "<run-status word>"`` in trw-mcp/src.

    The FR03 detector. AST, not grep, so a comment or a docstring mentioning
    the word cannot register as a writer and a multi-line assignment cannot
    hide from a line-oriented pattern.

    A file that will not parse is returned separately rather than skipped: a
    detector that reports "zero writers" over a file it could not read is
    reporting its own blindness as a pass.
    """
    hits: list[str] = []
    unparseable: list[str] = []
    for path in sorted(_TRW_MCP_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            unparseable.append(str(path.relative_to(_REPO_ROOT)))
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                continue
            if node.value.value not in _RUN_STATUS_WORDS:
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "status"
                ):
                    hits.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}")
    return hits, unparseable


# ── FR01 ────────────────────────────────────────────────────────────────


def test_run_status_members_equal_writer_union() -> None:
    """FR01: the enum is exactly the union of what production writers emit."""
    assert {m.value for m in RunStatus} == {"active", "complete", "delivered", "abandoned"}

    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted(_TRW_MCP_SRC.rglob("*.py")))
    for removed in _REMOVED_MEMBERS:
        assert removed not in source, f"{removed} still referenced under trw-mcp/src"

    model_src = (_TRW_MCP_SRC / "trw_mcp" / "models" / "run.py").read_text(encoding="utf-8")
    assert 'ABANDONED = "abandoned"' in model_src

    # Every member's writer + terminal disposition is recoverable from the
    # source, not only from the PRD: each member line is preceded by a comment
    # block naming a writer module and its terminal disposition.
    lines = model_src.splitlines()
    for member in RunStatus:
        index = next(i for i, line in enumerate(lines) if line.strip().startswith(f"{member.name} = "))
        doc = []
        cursor = index - 1
        while cursor >= 0 and lines[cursor].strip().startswith("#:"):
            doc.insert(0, lines[cursor].strip())
            cursor -= 1
        blob = " ".join(doc)
        assert doc, f"RunStatus.{member.name} carries no documentation line"
        assert "trw_mcp/" in blob, f"RunStatus.{member.name} does not name its writer: {blob}"
        expected = "Terminal." if member.is_terminal else "Non-terminal."
        assert expected in blob, f"RunStatus.{member.name} does not state its terminal disposition: {blob}"


# ── FR02 (predicate half; the adopt-gate half is in test_heartbeat_and_adopt) ──


def test_terminal_predicate_is_single_and_correct() -> None:
    """FR02: one predicate, and it now includes ``abandoned``."""
    assert RunStatus.ABANDONED.is_terminal is True
    assert RunStatus.COMPLETE.is_terminal is True
    assert RunStatus.DELIVERED.is_terminal is True
    assert RunStatus.ACTIVE.is_terminal is False
    assert _TERMINAL_RUN_STATUSES == {RunStatus.COMPLETE, RunStatus.DELIVERED, RunStatus.ABANDONED}

    # The string-facing entry point agrees, member for member.
    for member in RunStatus:
        assert is_terminal_status(member.value) is member.is_terminal
    # An unnameable status is NOT terminal: no gate seals a record on a typo.
    assert is_terminal_status("abandonded") is False
    assert is_terminal_status("") is False
    # The alias resolves before the predicate runs.
    assert is_terminal_status("completed") is True


def test_no_private_terminal_status_tuple_remains() -> None:
    """FR02: every site that filters terminal runs reads the shared predicate.

    The third row moved on 2026-09-04. It used to name ``state/_paths.py``,
    whose status-filtered mtime scan was the terminal-filtering site there;
    PRD-FIX-132 deleted that scan outright as dormant (zero production callers),
    which left ``_paths.py`` with no terminal-status decision at all -- so
    demanding the predicate appear in it would have asserted nothing. The
    run-listing site that still makes that decision is the offline CLI's
    advisory candidate list, so the row follows the behaviour rather than the
    file it used to live in.
    """
    for relative, forbidden in (
        ("trw_mcp/tools/_ceremony_adopt_run.py", '("delivered", "complete", "failed")'),
        ("trw_mcp/state/_run_gc.py", '{"complete", "failed", "delivered", "abandoned"}'),
        ("trw_mcp/services/_local_run_identity.py", '("complete", "failed", "abandoned", "delivered")'),
    ):
        text = (_TRW_MCP_SRC / relative).read_text(encoding="utf-8")
        assert forbidden not in text, f"{relative} still carries a private terminal set"
        assert "is_terminal_status" in text, f"{relative} does not read the shared predicate"


# ── FR03 ────────────────────────────────────────────────────────────────


def test_every_writer_emits_a_parseable_status(tmp_path: Path) -> None:
    """FR03: each of the five writer sites produces a status RunState accepts."""
    # The five writer values, read off the production modules rather than
    # retyped here — a writer that changed its value would change this list.
    writer_values = {
        "trw_init": RunStatus.ACTIVE.value,
        "_mark_run_complete": RunStatus.COMPLETE.value,
        "mark_local_delivered": RunStatus.DELIVERED.value,
        "sweep_stale_runs": RunStatus.ABANDONED.value,
        "analytics_stale_closer": RunStatus.ABANDONED.value,
    }
    for writer, value in writer_values.items():
        path = tmp_path / f"{writer}.yaml"
        path.write_text(_run_yaml_text(value), encoding="utf-8")
        state = RunState.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        assert state.status == value, writer

    hits, unparseable = _bare_status_literal_sites()
    assert unparseable == [], f"the FR03 scan could not read: {unparseable}"
    assert hits == []

    gc_src = (_TRW_MCP_SRC / "trw_mcp" / "state" / "_run_gc.py").read_text(encoding="utf-8")
    assert "RunStatus.ABANDONED.value" in gc_src


def test_bare_status_literal_scan_is_non_vacuous(tmp_path: Path) -> None:
    """The FR03 scan must be able to fail — it found four sites before the fix."""
    probe = tmp_path / "probe.py"
    probe.write_text('data = {}\ndata["status"] = "abandoned"\n', encoding="utf-8")
    tree = ast.parse(probe.read_text(encoding="utf-8"))
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and node.value.value in _RUN_STATUS_WORDS
        and any(
            isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value == "status"
            for t in node.targets
        )
    ]
    assert len(found) == 1


# ── FR04 ────────────────────────────────────────────────────────────────


def test_legacy_alias_normalises_on_read_only(tmp_path: Path) -> None:
    """FR04: ``completed`` normalises to ``complete``; nothing else widens."""
    source = tmp_path / "run.yaml"
    source.write_text(_run_yaml_text("completed"), encoding="utf-8")
    before = source.read_bytes()

    state = RunState.model_validate(yaml.safe_load(source.read_text(encoding="utf-8")))
    assert state.status == RunStatus.COMPLETE.value == "complete"
    # Read-only: validating did not touch the file.
    assert source.read_bytes() == before

    assert set(_STATUS_ALIASES) == {"completed"}
    canonical = {m.value for m in RunStatus}
    assert not (set(_STATUS_ALIASES) & canonical), "a canonical member may never be an alias key"

    for near_miss in ("Completed", "COMPLETED", "completed_", "paused", "failed", "comple"):
        with pytest.raises(ValidationError) as exc:
            RunState.model_validate({"run_id": "r", "task": "t", "status": near_miss})
        assert near_miss in str(exc.value)

    # Negative test 4 of the PRD test strategy: an absent status keeps the default.
    assert RunState.model_validate({"run_id": "r", "task": "t"}).status == RunStatus.ACTIVE


def test_coerce_run_status_is_the_single_normalisation_path() -> None:
    """FR04: the model validator and the predicate resolve values identically."""
    for value in ("active", "complete", "delivered", "abandoned", "completed"):
        member = coerce_run_status(value)
        assert member is not None
        assert RunState.model_validate({"run_id": "r", "task": "t", "status": value}).status == member.value
    for value in ("Completed", "completed_", "paused", "failed", ""):
        assert coerce_run_status(value) is None


# ── FR05 ────────────────────────────────────────────────────────────────


def test_fixture_tree_covers_every_member_and_alias() -> None:
    """FR05: one fixture per member plus one per alias, and all of them parse."""
    fixtures = sorted(_FIXTURES.glob("*.yaml"))
    assert len(fixtures) == len(RunStatus) + len(_STATUS_ALIASES)

    seen: set[str] = set()
    for fixture in fixtures:
        data = yaml.safe_load(fixture.read_text(encoding="utf-8"))
        state = RunState.model_validate(data)
        seen.add(str(data["status"]))
        assert str(state.status) in {m.value for m in RunStatus}
    assert seen == {m.value for m in RunStatus} | set(_STATUS_ALIASES)


def test_gate_passes_on_a_clean_tree(tmp_path: Path) -> None:
    """FR05: the gate reports equal scanned/parsed counts and exits zero."""
    _seed_tree(tmp_path, {name: name for name in {m.value for m in RunStatus}} | {"legacy": "completed"})
    result = _invoke_gate(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scanned=5 parsed=5" in result.stdout


def test_gate_fails_on_unparseable_status(tmp_path: Path) -> None:
    """FR05: a deliberately corrupted status fails the gate and is named."""
    _seed_tree(tmp_path, {"good": "abandoned", "corrupt": "abandonded"})
    result = _invoke_gate(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "scanned=2 parsed=1" in result.stdout
    assert "abandonded" in result.stdout
    assert "corrupt" in result.stdout


def test_gate_reports_the_live_tree(tmp_path: Path) -> None:
    """FR05: the live ``.trw/runs`` tree parses completely.

    Post-fix expectation from the PRD: ``scanned=N parsed=N``. The count itself
    is not pinned — it grows with every run — but scanned and parsed must be
    equal and the corpus must be non-empty, or the gate is inert.
    """
    result = _invoke_gate(_REPO_ROOT, "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["scanned"] >= 191, "live corpus shrank below the measured baseline"
    assert payload["parsed"] == payload["scanned"]
    assert payload["findings"] == []


# ── NFR01 ───────────────────────────────────────────────────────────────


@pytest.mark.slow
def test_gate_completes_within_budget() -> None:
    """NFR01: p95 wall time under 5 s over 5 runs on the >=191-file corpus."""
    payload = json.loads(_invoke_gate(_REPO_ROOT, "--json").stdout)
    assert payload["scanned"] >= 191

    timings: list[float] = []
    for _ in range(_GATE_TIMING_RUNS):
        start = time.perf_counter()
        result = _invoke_gate(_REPO_ROOT)
        timings.append(time.perf_counter() - start)
        assert result.returncode == 0
    timings.sort()
    # p95 of 5 samples is the largest sample (ceil(0.95 * 5) == 5).
    assert timings[-1] < _GATE_BUDGET_SECONDS, f"p95={timings[-1]:.3f}s over {timings}"


# ── NFR02 ───────────────────────────────────────────────────────────────


def test_gate_fails_closed_reader_stays_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR02: the gate rejects a truncated file; ``resolve_task_type`` still returns None."""
    runs_root = _seed_tree(tmp_path, {"good": "abandoned"})
    truncated = runs_root / "truncated" / "20260903T000009Z-trunc001" / "meta"
    truncated.mkdir(parents=True, exist_ok=True)
    # Truncated mid-document: the header never reaches a status key and the
    # remainder is an unterminated quoted scalar.
    (truncated / "run.yaml").write_text(
        'run_id: 20260903T000009Z-trunc001\ntask: "unterminated\nphase: rev',
        encoding="utf-8",
    )

    result = _invoke_gate(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "scanned=2 parsed=1" in result.stdout
    assert "truncated" in result.stdout

    # The same file through the fail-open reader: None, no exception.
    from trw_mcp.middleware.surface_authority import resolve_task_type
    from trw_mcp.models.config import _reset_config
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs
    from trw_mcp.state._pin_store import upsert_pin_entry

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    _reset_config()
    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    upsert_pin_entry("sess-truncated", truncated.parent)
    assert resolve_task_type(session_id="sess-truncated") is None

    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    _reset_config()


def test_gate_fails_on_an_undecodable_file(tmp_path: Path) -> None:
    """NFR02: an unreadable file is a finding, never a silent skip."""
    runs_root = _seed_tree(tmp_path, {"good": "active"})
    binary = runs_root / "binary" / "20260903T000008Z-binary01" / "meta"
    binary.mkdir(parents=True, exist_ok=True)
    (binary / "run.yaml").write_bytes(b"\xff\xfe\x00status: active\n")

    result = _invoke_gate(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "unreadable" in result.stdout
    assert "scanned=2 parsed=1" in result.stdout


# ── NFR03 ───────────────────────────────────────────────────────────────


def test_gate_containment_and_exact_alias_matching(tmp_path: Path) -> None:
    """NFR03: an out-of-root run is excluded and reported; aliasing stays exact."""
    project = tmp_path / "project"
    project.mkdir()
    _seed_tree(project, {"good": "abandoned"})

    # A run directory living outside the project root, reached by symlink.
    outside = tmp_path / "outside" / "20260903T000007Z-out00001" / "meta"
    outside.mkdir(parents=True)
    (outside / "run.yaml").write_text(_run_yaml_text("abandonded"), encoding="utf-8")
    (project / ".trw" / "runs" / "escaped").symlink_to(outside.parent.parent)

    result = _invoke_gate(project, "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
    assert payload["scanned"] == 1
    assert len(payload["excluded"]) == 1
    assert "escaped" in payload["excluded"][0]
    # Reported, not silently dropped — the human-readable mode says so too.
    assert "excluded (escapes project root)" in _invoke_gate(project).stdout

    # Exact matching: no prefix, case-folding, or pattern rule reaches the map.
    for near_miss in ("Completed", "completed_", " completed x"):
        assert coerce_run_status(near_miss) is None
    # Surrounding whitespace only — the one normalisation the map does apply.
    assert coerce_run_status("  completed  ") is RunStatus.COMPLETE


def test_gate_never_writes(tmp_path: Path) -> None:
    """NFR03: no write amplification — a CI run cannot mutate run state."""
    runs_root = _seed_tree(tmp_path, {"good": "abandoned", "legacy": "completed"})
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in runs_root.rglob("run.yaml")}
    assert _invoke_gate(tmp_path).returncode == 0
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in runs_root.rglob("run.yaml")}
    assert before == after

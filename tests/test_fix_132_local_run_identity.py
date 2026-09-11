"""PRD-FIX-132: the offline CLI refuses instead of guessing which run is yours.

The defect these tests lock down: ``trw-mcp local checkpoint|status|deliver``
with no ``--run-path`` used to select the run whose ``run.yaml`` had the newest
mtime. mtime carries no ownership information, so under concurrency the winner
is whichever run *another* agent touched last -- and the bundled degraded-mode
protocol block told agents to run exactly that command. The result was a
checkpoint, and an ungated ``delivered`` stamp, appended to a stranger's audit
trail in a form indistinguishable from an honest record.

Every test here is red if its fix is reverted.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT

REPO_ROOT = MONOREPO_ROOT or PACKAGE_ROOT.parent
BUNDLED_LIB = PACKAGE_ROOT / "src" / "trw_mcp" / "data" / "hooks" / "lib-trw.sh"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _seed_run(root: Path, task: str, run_id: str, status: str = "active") -> Path:
    run_dir = root / ".trw" / "runs" / task / run_id
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(
        f"run_id: {run_id}\ntask: {task}\nstatus: {status}\nphase: implement\n",
        encoding="utf-8",
    )
    return run_dir


def _runs_digest(root: Path) -> str:
    """Recursive content digest of the runs tree.

    Names *and* bytes, so a created directory, a created file, and an appended
    line are each visible. This is the NFR01 instrument: a refusal must leave
    this value unchanged.
    """
    runs_root = root / ".trw" / "runs"
    digest = hashlib.sha256()
    for path in sorted(runs_root.rglob("*")):
        digest.update(str(path.relative_to(runs_root)).encode())
        digest.update(b"\0")
        if path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _cli_env(root: Path, *, session_id: str | None = None) -> dict[str, str]:
    """Environment for an offline-CLI subprocess with identity stated explicitly.

    The host's own session variables are stripped: whether the developer's
    terminal exports one must not decide whether these assertions hold.
    """
    from trw_mcp.client_profiles.session_identity import known_session_id_env_vars

    env = dict(os.environ)
    for name in known_session_id_env_vars():
        env.pop(name, None)
    env.pop("TRW_SESSION_ID", None)
    env["TRW_PROJECT_ROOT"] = str(root)
    if session_id is not None:
        env["TRW_SESSION_ID"] = session_id
    return env


def _run_cli(root: Path, *args: str, session_id: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "trw_mcp.server", "local", *args],
        capture_output=True,
        text=True,
        cwd=str(root),
        env=_cli_env(root, session_id=session_id),
        check=False,
    )


@pytest.fixture
def three_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """A project with an older run and a newer one, plus pin-store isolation.

    ``newest`` is deliberately the lexicographically and chronologically later
    run: it is what the deleted mtime heuristic would have picked.
    """
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    older = _seed_run(tmp_path, "owned-task", "20260101T000000Z-aaaa1111")
    newest = _seed_run(tmp_path, "stranger-task", "20260904T000000Z-bbbb2222")
    # Make the ordering explicit rather than incidental to file-creation order.
    os.utime(newest / "meta" / "run.yaml", (9_000_000_000, 9_000_000_000))
    os.utime(older / "meta" / "run.yaml", (1_000_000_000, 1_000_000_000))
    return tmp_path, older, newest


# ---------------------------------------------------------------------------
# FR01 / NFR01 -- a pinless caller refuses and writes nothing
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_pinless_checkpoint_refuses_and_writes_nothing(three_runs: tuple[Path, Path, Path]) -> None:
    """FR01 + NFR01: the whole runs tree is byte-identical across the refusal."""
    root, _older, newest = three_runs
    before = _runs_digest(root)

    result = _run_cli(root, "checkpoint", "--message", "stolen?")

    assert result.returncode != 0, f"a pinless checkpoint must fail, got stdout={result.stdout!r}"
    assert "Refusing to select a run" in result.stdout
    assert not (newest / "meta" / "checkpoints.jsonl").exists()
    assert _runs_digest(root) == before, "a refused checkpoint must not touch any run"


@pytest.mark.integration
def test_pinless_deliver_refuses_and_leaves_status_untouched(three_runs: tuple[Path, Path, Path]) -> None:
    """FR01 on the highest-consequence command: no ungated delivered stamp."""
    root, _older, newest = three_runs
    before = _runs_digest(root)

    result = _run_cli(root, "deliver", "--message", "done")

    assert result.returncode != 0
    assert "status: active" in (newest / "meta" / "run.yaml").read_text(encoding="utf-8")
    assert _runs_digest(root) == before


@pytest.mark.integration
def test_pinless_status_refuses_rather_than_reporting_a_strangers_run(
    three_runs: tuple[Path, Path, Path],
) -> None:
    """A read is not harmless here: reporting a foreign run as 'yours' is a false answer."""
    root, _older, newest = three_runs

    result = _run_cli(root, "status")

    assert result.returncode != 0
    assert newest.name not in result.stdout.split("Candidate runs")[0]


# ---------------------------------------------------------------------------
# FR02 -- the session pin, not recency, decides
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_session_pin_selects_the_owned_run_not_the_newest(three_runs: tuple[Path, Path, Path]) -> None:
    """FR02: with TRW_SESSION_ID + a pin, the OLDER pinned run receives the write."""
    from trw_mcp.state._pin_store import upsert_pin_entry

    root, older, newest = three_runs
    upsert_pin_entry("fix132-session", older)

    result = _run_cli(root, "checkpoint", "--message", "mine", session_id="fix132-session")

    assert result.returncode == 0, result.stdout + result.stderr
    records = (older / "meta" / "checkpoints.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(records[0])["message"] == "mine"
    assert not (newest / "meta" / "checkpoints.jsonl").exists(), "the newer run must be untouched"


def test_dangling_pin_is_not_ownership(three_runs: tuple[Path, Path, Path]) -> None:
    """A pin pointing at a deleted run resolves to a refusal, never to a neighbour."""
    from trw_mcp.services._local_run_identity import pinned_run_for_session
    from trw_mcp.state._pin_store import invalidate_pin_store_cache, upsert_pin_entry

    _root, older, _newest = three_runs
    upsert_pin_entry("fix132-dangling", older)
    shutil.rmtree(older / "meta")
    invalidate_pin_store_cache()

    os.environ["TRW_SESSION_ID"] = "fix132-dangling"
    try:
        assert pinned_run_for_session() is None
    finally:
        del os.environ["TRW_SESSION_ID"]


def test_process_uuid_alone_is_not_an_identity(three_runs: tuple[Path, Path, Path]) -> None:
    """FR02: the per-process UUID layer is reported as *no* identity.

    A pin keyed on it could never be looked up by another process, so treating
    it as an answer is the same guess in a different costume.
    """
    from trw_mcp.services._local_run_identity import resolve_session_pin_key

    assert resolve_session_pin_key() is None

    os.environ["TRW_SESSION_ID"] = "fix132-anchored"
    try:
        assert resolve_session_pin_key() == "fix132-anchored"
    finally:
        del os.environ["TRW_SESSION_ID"]


# ---------------------------------------------------------------------------
# FR03 -- an explicit path wins
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_explicit_run_path_wins_over_pin_and_mtime(three_runs: tuple[Path, Path, Path]) -> None:
    """FR03: --run-path is the caller stating the answer; nothing overrides it."""
    from trw_mcp.state._pin_store import upsert_pin_entry

    root, older, newest = three_runs
    upsert_pin_entry("fix132-explicit", older)

    result = _run_cli(
        root,
        "checkpoint",
        "--message",
        "explicit",
        "--run-path",
        str(newest),
        session_id="fix132-explicit",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (newest / "meta" / "checkpoints.jsonl").exists()
    assert not (older / "meta" / "checkpoints.jsonl").exists()


@pytest.mark.integration
def test_missing_explicit_run_path_fails_without_creating_it(three_runs: tuple[Path, Path, Path]) -> None:
    """FR03: a bad explicit path is a caller mistake, reported as one."""
    root, _older, _newest = three_runs
    ghost = root / ".trw" / "runs" / "ghost-task" / "20260904T000000Z-ffff"

    result = _run_cli(root, "checkpoint", "--message", "nope", "--run-path", str(ghost))

    assert result.returncode != 0
    assert "does not exist" in result.stdout
    assert not ghost.exists()


# ---------------------------------------------------------------------------
# FR04 -- the refusal is actionable, and its advisory list is bounded
# ---------------------------------------------------------------------------


def test_refusal_names_every_remedy_and_bounds_the_advisory_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR04: three executable remedies, an advisory label, at most five candidates."""
    from trw_mcp.services._local_run_identity import (
        MAX_ADVISORY_CANDIDATES,
        LocalRunIdentityError,
        resolve_owned_run_path,
    )

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    for index in range(MAX_ADVISORY_CANDIDATES + 3):
        _seed_run(tmp_path, f"task-{index}", f"2026090{index % 10}T00000{index}Z-cccc{index}")

    with pytest.raises(LocalRunIdentityError) as exc_info:
        resolve_owned_run_path(None)

    message = str(exc_info.value)
    assert "--run-path" in message
    assert "TRW_SESSION_ID" in message
    assert "trw-mcp local init" in message
    assert "advisory only" in message
    assert len(exc_info.value.candidates) == MAX_ADVISORY_CANDIDATES
    listed = [line for line in message.splitlines() if line.startswith("  - /")]
    assert len(listed) == MAX_ADVISORY_CANDIDATES


def test_advisory_candidates_exclude_sealed_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A terminal run is not a candidate: naming it would invite writing into a sealed trail."""
    from trw_mcp.services._local_run_identity import candidate_runs

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    live = _seed_run(tmp_path, "live-task", "20260101T000000Z-live")
    sealed = _seed_run(tmp_path, "sealed-task", "20260904T000000Z-done", status="delivered")

    names = {path.name for path in candidate_runs()}
    assert live.name in names
    assert sealed.name not in names


# ---------------------------------------------------------------------------
# FR05 -- the degraded-mode protocol block names the run, or says it cannot
# ---------------------------------------------------------------------------


def _emit_offline_block(root: Path, *, session_id: str | None) -> str:
    env = dict(os.environ)
    env.pop("TRW_SESSION_ID", None)
    if session_id is not None:
        env["TRW_SESSION_ID"] = session_id
    probe = subprocess.run(
        ["sh", "-c", f'. "{BUNDLED_LIB}" >/dev/null 2>&1; trw_emit_offline_protocol_block ""'],
        capture_output=True,
        text=True,
        cwd=str(root),
        env=env,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    return probe.stdout


@pytest.fixture
def hook_project(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    return tmp_path


@pytest.mark.integration
def test_offline_block_prints_the_resolved_run_path(hook_project: Path) -> None:
    """FR05: when the pin resolves, the printed commands carry the real run path."""
    run_dir = _seed_run(hook_project, "owned-task", "20260101T000000Z-aaaa1111")
    pins = hook_project / ".trw" / "runtime" / "pins.json"
    pins.parent.mkdir(parents=True, exist_ok=True)
    pins.write_text(json.dumps({"hook-session": {"run_path": str(run_dir)}}), encoding="utf-8")

    out = _emit_offline_block(hook_project, session_id="hook-session")

    assert f"trw-mcp local checkpoint --message MSG --run-path {run_dir}" in out
    assert f"trw-mcp local status --run-path {run_dir}" in out
    assert f"trw-mcp local deliver --message MSG --run-path {run_dir}" in out
    assert "RUN IDENTITY UNKNOWN" not in out


@pytest.mark.integration
def test_offline_block_states_unknown_identity_instead_of_a_command_that_refuses(
    hook_project: Path,
) -> None:
    """FR05: with no pin, no run-scoped command is printed without its identity flag."""
    _seed_run(hook_project, "stranger-task", "20260904T000000Z-bbbb2222")

    out = _emit_offline_block(hook_project, session_id=None)

    assert "RUN IDENTITY UNKNOWN" in out
    assert "trw-mcp local init --task NAME" in out
    for command in (
        "trw-mcp local checkpoint --message MSG",
        "trw-mcp local status",
        "trw-mcp local deliver --message MSG",
    ):
        line = next(one for one in out.splitlines() if command in one)
        assert "--run-path" in line, f"run-scoped line printed without identity: {line!r}"
    # The stranger's run is never named: the block must not hand over a guess.
    assert "20260904T000000Z-bbbb2222" not in out


# ---------------------------------------------------------------------------
# FR06 -- the dormant mtime scan is gone
# ---------------------------------------------------------------------------


def test_dormant_mtime_scan_entry_point_is_gone() -> None:
    """FR06: the explicit legacy scan had no production caller and is deleted."""
    with pytest.raises(ImportError):
        from trw_mcp.state._paths import find_run_via_mtime_scan  # noqa: F401


def test_the_offline_resolver_contains_no_mtime_comparison() -> None:
    """FR06: the replacement must not reintroduce the heuristic it replaced.

    Asserted over the AST rather than the text: the module docstring names
    ``st_mtime`` when explaining what it replaced, and a grep that cannot tell
    an explanation from an implementation would force the explanation out.
    """
    import ast

    from trw_mcp.services import _local_run_identity

    source = Path(str(_local_run_identity.__file__)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "st_mtime" not in attributes
    assert "st_mtime_ns" not in attributes
    assert not {"getmtime", "getctime"} & (attributes | names)

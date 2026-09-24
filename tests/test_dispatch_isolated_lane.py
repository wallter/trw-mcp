"""PRD-CORE-297-FR04: the isolated-review lane runs in a snapshot, never the caller's tree.

A registry copy of agy is made admissible (``isolated_review`` set) and its binary
replaced by a Python sentinel that records its argv, cwd and HOME. ``env`` stands
in for the host write-denial prefix so the sentinel's own writes stay observable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.test_dispatch_snapshot import _git, _repo

_ANSWER = 'print(json.dumps({"event": "result", "result": {"status": "success", "response": "Verdict: PASS"}}))\n'


@pytest.fixture
def caller(tmp_path: Path) -> Path:
    return _repo(tmp_path / "caller")


def _lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str = "", *, mcp_out: str = "none", mcp_exit: int = 0
) -> Path:
    """Admit agy's isolated lane with a sentinel child; return where it records itself.

    The MCP lister answers only when it runs under the confinement prefix
    (``TRW_TEST_CONFINED``), so an unconfined preflight is refused.
    """
    from trw_mcp.dispatch import _isolated, _posture
    from trw_mcp.dispatch._client_spec_types import IsolatedReviewSpec
    from trw_mcp.dispatch._client_specs import _SPEC_BY_ID

    record = tmp_path / "record.json"
    script = tmp_path / "child.py"
    script.write_text(
        "import json, os, subprocess, sys\n"
        f"open({str(record)!r}, 'w').write(json.dumps("
        "{'argv': sys.argv[1:], 'cwd': os.getcwd(), 'home': os.environ.get('HOME')}))\n" + body + _ANSWER,
        encoding="utf-8",
    )
    lister = f"import os, sys; print({mcp_out!r} if os.environ.get('TRW_TEST_CONFINED') else 'unconfined'); sys.exit({mcp_exit})"
    lane = IsolatedReviewSpec(
        mcp_list_argv=(sys.executable, "-c", lister), mcp_empty_marker="none", strip_paths=(".grok",)
    )
    spec = _SPEC_BY_ID["agy"].model_copy(
        update={"binary": sys.executable, "base_argv": (sys.executable, str(script)), "isolated_review": lane}
    )
    monkeypatch.setitem(_SPEC_BY_ID, "agy", spec)
    monkeypatch.setattr(_posture, "confinement_prefix", lambda: ["env"])
    monkeypatch.setattr("trw_mcp.dispatch._host_confinement.confinement_prefix", lambda writable=None: ["env"])
    monkeypatch.setattr(_isolated, "confinement_prefix", lambda writable=None: ["env", "TRW_TEST_CONFINED=1"])
    return record


def _run(caller: Path):  # type: ignore[no-untyped-def]
    from trw_mcp.dispatch import dispatch
    from trw_mcp.dispatch._types import DispatchRequest

    return dispatch(
        DispatchRequest(client="agy", prompt="review", read_only=True, posture="isolated-review", cwd=caller)
    )


def test_a_clean_run_happens_in_the_snapshot_with_a_temp_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caller: Path
) -> None:
    record = _lane(monkeypatch, tmp_path)
    result = _run(caller)
    seen = json.loads(record.read_text(encoding="utf-8"))

    assert result.isolation == "snapshot-write-confined" and result.contamination == "clean", result.raw_stderr
    assert result.ok is True and result.posture_enforced is False and result.changed_paths == []
    snapshot = Path(seen["cwd"])
    assert snapshot != caller and str(caller) not in seen["argv"], "the caller never reaches argv (--add-dir)"
    assert seen["argv"][seen["argv"].index("--add-dir") + 1] == str(snapshot)
    assert seen["home"] != str(Path.home()) and Path(seen["home"]).parent == snapshot.parent
    assert not snapshot.exists(), "the snapshot is removed after the run"
    assert "standalone snapshot" in (result.sandbox_note or "")


def test_a_write_in_the_snapshot_is_contamination_and_keeps_the_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caller: Path
) -> None:
    _lane(monkeypatch, tmp_path, "open('planted.py', 'w').write('x')\n")
    result = _run(caller)
    assert result.contamination == "contaminated" and result.changed_paths == ["planted.py"]
    assert result.ok is False and result.text == "Verdict: PASS"
    assert not (caller / "planted.py").exists()


def test_a_caller_head_move_during_the_run_is_contamination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caller: Path
) -> None:
    move = (
        f"subprocess.run(['git', '-C', {str(caller)!r}, '-c', 'user.name=t', '-c', 'user.email=t@t',"
        " 'commit', '-q', '--allow-empty', '-m', 'moved'], check=True)\n"
    )
    _lane(monkeypatch, tmp_path, move)
    before = _git(caller, "rev-parse", "HEAD")
    result = _run(caller)
    assert _git(caller, "rev-parse", "HEAD") != before, "the sentinel really moved the caller"
    assert result.ok is False and "caller:HEAD" in result.changed_paths


def test_mcp_that_cannot_be_turned_off_refuses_before_spawn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caller: Path
) -> None:
    record = _lane(monkeypatch, tmp_path, mcp_out="trw: .venv/bin/trw-mcp (project)")
    result = _run(caller)
    assert not record.exists(), "the child ran although MCP stayed on"
    assert result.ok is False and "MCP could not be turned off" in result.raw_stderr
    assert result.isolation == "none"


@pytest.mark.parametrize(
    ("mcp_out", "mcp_exit"),
    [("none", 1), ("none\ntrw: .venv/bin/trw-mcp", 0), ("servers: none", 0)],
    ids=["nonzero-exit", "marker-plus-a-server", "marker-as-substring"],
)
def test_only_an_exact_empty_listing_with_exit_0_admits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caller: Path, mcp_out: str, mcp_exit: int
) -> None:
    record = _lane(monkeypatch, tmp_path, mcp_out=mcp_out, mcp_exit=mcp_exit)
    result = _run(caller)
    assert not record.exists() and result.ok is False and "MCP could not be turned off" in result.raw_stderr


def test_an_unconfined_preflight_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caller: Path) -> None:
    from trw_mcp.dispatch import _isolated

    record = _lane(monkeypatch, tmp_path)
    monkeypatch.setattr(_isolated, "confinement_prefix", lambda writable=None: ["env"])
    result = _run(caller)
    assert not record.exists() and "unconfined" in result.raw_stderr


def test_no_wrapper_on_the_host_refuses_before_any_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caller: Path
) -> None:
    from trw_mcp.dispatch import _posture

    record = _lane(monkeypatch, tmp_path)
    monkeypatch.setattr(_posture, "confinement_prefix", list)
    monkeypatch.setattr("trw_mcp.dispatch._host_confinement.confinement_prefix", lambda writable=None: [])
    result = _run(caller)
    assert not record.exists() and result.ok is False and "reviewer posture refused" in result.raw_stderr


def test_the_preflight_and_the_child_may_write_only_the_temp_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caller: Path
) -> None:
    """PRD-CORE-297-FR05 (lead-approved, PLAN 0b): the one write allowance is the per-run HOME."""
    from trw_mcp.dispatch import _isolated

    record = _lane(monkeypatch, tmp_path)
    writable: dict[str, object] = {}
    monkeypatch.setattr(
        "trw_mcp.dispatch._host_confinement.confinement_prefix", lambda writable=None: writable_seen("child", writable)
    )
    monkeypatch.setattr(_isolated, "confinement_prefix", lambda writable=None: writable_seen("preflight", writable))

    def writable_seen(who: str, path: object) -> list[str]:
        writable[who] = path
        return ["env", "TRW_TEST_CONFINED=1"]

    assert _run(caller).ok is True
    home = Path(json.loads(record.read_text(encoding="utf-8"))["home"])
    assert writable == {"child": home, "preflight": home}


def _real_wrapper() -> bool:
    from trw_mcp.dispatch._confine import confinement_prefix

    return bool(confinement_prefix())


@pytest.mark.skipif(not _real_wrapper(), reason="no host write-confinement wrapper (sandbox-exec) on this host")
def test_the_real_profile_allows_the_home_and_denies_everything_else(tmp_path: Path, caller: Path) -> None:
    """Direct, symlinked, ``..`` and inherited-TMPDIR writes out of the HOME are all denied (core297e P2)."""
    import os
    import shlex
    import subprocess

    from tests.test_dispatch_snapshot import _digest
    from trw_mcp.dispatch._confine import confinement_prefix

    home, tmpdir = tmp_path / "home", tmp_path / "tmpdir"
    home.mkdir()
    tmpdir.mkdir()
    (home / "link").symlink_to(caller / "tracked.py")
    before = _digest(caller)
    q = {name: shlex.quote(str(path)) for name, path in {"home": home, "caller": caller, "tmp": tmp_path}.items()}
    script = (
        f"mkdir -p {q['home']}/.gemini/a && echo x > {q['home']}/.gemini/a/f; echo y > {q['caller']}/escape.txt;"
        f' echo z > {q["tmp"]}/out; echo s > {q["home"]}/link; echo d > {q["home"]}/../dotdot; echo t > "$TMPDIR/t"'
    )
    env = os.environ | {"TMPDIR": str(tmpdir)}
    prefix = confinement_prefix(writable=home)
    assert prefix, "the skip condition found a wrapper; an empty prefix would run the writes unconfined"
    subprocess.run([*prefix, "/bin/sh", "-c", script], env=env, capture_output=True)
    assert (home / ".gemini" / "a" / "f").read_text(encoding="utf-8") == "x\n"
    assert not (caller / "escape.txt").exists() and not (tmp_path / "out").exists()
    assert not (tmp_path / "dotdot").exists() and not any(tmpdir.iterdir())
    assert _digest(caller) == before, "the write through the HOME symlink reached the caller"


def test_a_writable_path_that_could_break_the_profile_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Runs on every host: the platform and the wrapper binary are faked present."""
    from trw_mcp.dispatch import _confine
    from trw_mcp.dispatch._confine import confinement_prefix

    monkeypatch.setattr(_confine.sys, "platform", "darwin")
    monkeypatch.setattr(_confine.os.path, "isfile", lambda _p: True)
    monkeypatch.setattr(_confine.os, "access", lambda _p, _m: True)
    assert confinement_prefix(writable=tmp_path)[0] == "/usr/bin/sandbox-exec", "the fake reaches the profile"
    with pytest.raises(ValueError, match="profile"):
        confinement_prefix(writable=tmp_path / 'x") (allow file-write* (subpath "/')


def test_with_trw_is_refused_at_construction() -> None:
    """The preflight proves no MCP server; with_trw would add one after it (core297d P2)."""
    from trw_mcp.dispatch._types import DispatchRequest

    with pytest.raises(ValueError, match="isolated-review"):
        DispatchRequest(client="agy", prompt="r", read_only=True, posture="isolated-review", with_trw=True)


def test_a_rebuilt_with_trw_request_never_spawns(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caller: Path) -> None:
    from trw_mcp.dispatch import dispatch
    from trw_mcp.dispatch._types import DispatchRequest

    record = _lane(monkeypatch, tmp_path)
    fields = DispatchRequest(client="agy", prompt="r", read_only=True, posture="isolated-review", cwd=caller)
    result = dispatch(DispatchRequest.model_construct(**(dict(fields) | {"with_trw": True})))
    assert not record.exists() and result.ok is False and "with_trw" in result.raw_stderr

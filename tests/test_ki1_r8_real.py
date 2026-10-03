"""ki1-race-review r8, real volumes: the TRW user dir (now the snapshot home) on the project's own full volume,
and a fresh TRW user dir on a volume whose modes cannot be hardened (exFAT/FAT)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401

pytestmark = pytest.mark.integration


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _shas(root: Path) -> set[str]:
    out = set()
    for dirpath, _d, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            if p.is_file() and not p.is_symlink():
                out.add(_sha(p.read_bytes()))
    return out


def _fill(volume: Path) -> Path:
    filler = volume / f"filler-{os.getpid()}"
    fd = os.open(filler, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        for size in (1 << 20, 1 << 16, 1 << 12, 512, 1):
            chunk = b"\0" * size
            while True:
                try:
                    if os.write(fd, chunk) < size:
                        break
                except OSError:  # trw-fail-silent-allow: the volume is full, so filling stops
                    break
    finally:
        os.close(fd)
    return filler


@pytest.mark.skipif(
    not os.environ.get("KI1_FULLVOL"), reason="needs a scratch volume in KI1_FULLVOL"
)  # skip-category: opt-in
def test_r8_REAL_userdir_on_project_volume(initialized_repo, monkeypatch) -> None:
    from trw_mcp.bootstrap import _update_project as up

    vol = Path(os.environ["KI1_FULLVOL"])
    ud = vol / f"ud-{os.getpid()}"
    monkeypatch.setenv("TRW_USER_DIR", str(ud))
    repo = vol / f"cn-{os.getpid()}"
    shutil.copytree(initialized_repo, repo, symlinks=True)
    shutil.rmtree(repo / ".git")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "x",
        "GIT_AUTHOR_EMAIL": "x@x",
        "GIT_COMMITTER_NAME": "x",
        "GIT_COMMITTER_EMAIL": "x@x",
    }
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True)
    s = repo / ".claude" / "settings.json"
    data = json.loads(s.read_text())
    data["userKey"] = "my uncommitted edit"
    data.get("hooks", {}).pop("SessionStart", None)
    s.write_text(json.dumps(data, indent=2) + "\n")
    user_bytes = s.read_bytes()
    real = up._restore_dirty_files
    filler = vol / f"filler-{os.getpid()}"

    def fill_then_restore(*a, **k):
        _fill(vol)
        return real(*a, **k)

    monkeypatch.setattr(up, "_restore_dirty_files", fill_then_restore)
    exc = None
    try:
        try:
            result = up.update_project(repo, ide="claude-code")
        except BaseException as e:
            exc = e
            result = {"errors": [], "warnings": []}
        monkeypatch.setattr(up, "_restore_dirty_files", real)
        in_proj = _sha(user_bytes) in _shas(repo)
        snaps = (
            sorted(p.name for p in (ud / "update-snapshots").iterdir()) if (ud / "update-snapshots").exists() else []
        )
        in_snap = any(_sha(user_bytes) in _shas(ud / "update-snapshots" / n) for n in snaps)
        cps = [m.group(1) for e in result["errors"] for m in [re.search(r"then run: (cp -n \S+ \S+)", e)] if m]
        recs = list((ud / "refused-restores").glob("*.json")) if (ud / "refused-restores").exists() else []
        print(
            f"\nUDREAL {vol.name}: exc={exc!r} user_in_project={in_proj} user_in_snapshot={in_snap} snaps={snaps} "
            f"records={len(recs)} cps={len(cps)} errors={[e[:160] for e in result['errors']]}"
        )
        assert exc is None
        if in_proj:
            return
        assert in_snap and cps, "user bytes neither in the project nor named in a kept snapshot"
        filler.unlink(missing_ok=True)
        for c in cps:
            r = subprocess.run(c, shell=True, capture_output=True, text=True)
            print(f"UDREAL run-after-free rc={r.returncode} err={r.stderr.strip()[-90:]}")
        pre = Path(str(s) + ".pre-update")
        print(f"UDREAL pre-update holds user bytes={pre.exists() and pre.read_bytes() == user_bytes}")
        pre.unlink(missing_ok=True)
        r2 = up.update_project(repo, ide="claude-code")
        moved = [w for w in r2["warnings"] + r2["errors"] if "pre-update" in w]
        print(f"UDREAL retry warnings={[m[:160] for m in moved]} errors={r2['errors'][:2]}")
        assert _sha(user_bytes) in _shas(repo / ".trw" / "trash") or (s.exists() and s.read_bytes() == user_bytes)
    finally:
        filler.unlink(missing_ok=True)
        shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(ud, ignore_errors=True)


@pytest.mark.skipif(
    not os.environ.get("KI1_FULLVOL"), reason="needs a scratch volume in KI1_FULLVOL"
)  # skip-category: opt-in
def test_r8_REAL_fresh_userdir_on_volume(initialized_repo, monkeypatch) -> None:
    """Project on the normal disk; a fresh TRW user dir on the scratch volume (exFAT/FAT: modes cannot be hardened)."""
    import traceback

    from trw_mcp.bootstrap import _update_project as up

    vol = Path(os.environ["KI1_FULLVOL"])
    ud = vol / f"udfresh-{os.getpid()}"
    monkeypatch.setenv("TRW_USER_DIR", str(ud))
    exc = None
    result = None
    try:
        try:
            result = up.update_project(initialized_repo, ide="claude-code")
        except BaseException as e:
            exc = e
            tb = traceback.extract_tb(e.__traceback__)
            where = [f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in tb if "trw_mcp" in f.filename][-3:]
        print(
            f"\nUDFRESH {vol.name}: exc={type(exc).__name__ if exc else None}: {str(exc)[:150] if exc else ''} "
            f"where={where if exc else None} errors={None if result is None else result['errors'][:2]}"
        )
        assert exc is None, f"update-project crashed with a fresh TRW user dir on {vol.name}"
    finally:
        shutil.rmtree(ud, ignore_errors=True)

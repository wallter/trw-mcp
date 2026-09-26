"""A dispatch never builds the reviewed checkout's code index (rc8, pre-C12 sol review P1).

The dispatcher used to build ``.trw/code-index`` in the parent before a reviewer lane: a
read-only dispatch wrote chunks.sqlite and manifest.json into the checkout it reviewed, and
a symlinked ``.trw/code-index`` redirected those writes. The prebuild is gone; a reviewer
reads an index that is already there, and a missing one answers ``index_missing``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trw_mcp.dispatch._client_specs import _SPEC_BY_ID, client_spec_for
from trw_mcp.dispatch._runner import dispatch
from trw_mcp.dispatch._types import DispatchRequest
from trw_mcp.tools.code_search import code_search


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A project with one source file, and a fake codex that records that it ran."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("def reviewer_target() -> int:\n    return 1\n", encoding="utf-8")
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(tmp_path / 'ran.txt')!r}).write_text('ran', encoding='utf-8')\n"
        'print(\'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\')\n',
        encoding="utf-8",
    )
    spec = client_spec_for("codex")
    patched = spec.model_copy(update={"binary": sys.executable, "base_argv": (sys.executable, str(script))})
    monkeypatch.setitem(_SPEC_BY_ID, "codex", patched)
    return project


def _dispatch(project: Path, posture: str) -> object:
    return dispatch(DispatchRequest(client="codex", prompt="review", posture=posture, cwd=project, timeout_s=60))  # type: ignore[arg-type]


@pytest.mark.parametrize("posture", ["reviewer", "default"])
def test_a_dispatch_writes_no_code_index_into_the_checkout(repo: Path, posture: str) -> None:
    result = _dispatch(repo, posture)

    assert (repo.parent / "ran.txt").exists()
    assert not (repo / ".trw" / "code-index").exists()
    assert not hasattr(result, "code_index")
    assert code_search(str(repo), "reviewer_target")["error_code"] == "index_missing"


def test_a_symlinked_index_directory_receives_nothing(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / ".trw").mkdir()
    (repo / ".trw" / "code-index").symlink_to(outside, target_is_directory=True)

    _dispatch(repo, "reviewer")

    assert list(outside.iterdir()) == []


def test_a_reviewer_reads_an_index_that_is_already_there_unchanged(repo: Path) -> None:
    from trw_mcp.code_index.store import default_store_path
    from trw_mcp.code_index.update import update_code_index

    update_code_index(repo)
    store = default_store_path(repo)
    before = (store.read_bytes(), store.stat().st_mtime_ns)

    _dispatch(repo, "reviewer")

    assert (store.read_bytes(), store.stat().st_mtime_ns) == before
    assert code_search(str(repo), "reviewer_target")["results"][0]["path"] == "app.py"  # type: ignore[index]

"""PRD-CORE-320 FR04 — a hook entry point verifies only with a real-path marker.

``instructions-loaded.sh`` is the hook the marker convention was added to
(FR04's traceability row): its dependency-present branch (jq or python3 on
PATH) writes ``hook_real_path``; the degraded branch (neither) exits 0 with no
marker. This module runs the REAL hook both ways, then exercises
:func:`verify_chain` with and without that evidence.

Soundness scope (stated once, matching ``hook_entry.py``'s module docstring):
a hook entry verifies only when the hook script's own text invokes the next
hop's module or CLI verb (the matching line is named as the call site) AND
this run's evidence contains an event that only the hook's real,
dependency-present branch writes. It does not prove the hook runs on every
session, nor that the degraded branch is absent.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests._layout import PACKAGE_ROOT, path_without
from trw_mcp.state.validation.call_chain import verify_chain

pytestmark = pytest.mark.integration

_DATA = PACKAGE_ROOT / "src" / "trw_mcp" / "data"
_HOOK = _DATA / "hooks" / "instructions-loaded.sh"
_STEM = "instructions-loaded"
_SESSION_EVENTS = ".trw/context/session-events.jsonl"


def _write_hook_env(root: Path) -> None:
    """The REAL production hook-env writer (matches ``tests/hooks/_ownership_harness.py::write_hook_env``)."""
    from trw_mcp.bootstrap._file_ops import _write_hook_env_file
    from trw_mcp.models.config._profiles import resolve_client_profile

    _write_hook_env_file(root / ".trw", resolve_client_profile("claude-code"))


def _run_hook(root: Path, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    _write_hook_env(root)
    return subprocess.run(
        ["sh", str(_HOOK)],
        input=json.dumps({"file_path": "CLAUDE.md", "load_reason": "path-scoped", "session_id": "sess-1"}),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _marker_lines(root: Path) -> list[dict[str, object]]:
    path = root / _SESSION_EVENTS
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_dependency_present_writes_the_marker(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root), "CLAUDE_PROJECT_DIR": str(root)}
    result = _run_hook(root, env=env)
    assert result.returncode == 0, result.stderr

    rows = _marker_lines(root)
    assert any(row.get("hook_real_path") == _STEM for row in rows), rows


def test_dependency_absent_writes_no_marker(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    env = {"PATH": path_without(tmp_path, {"jq", "python3"}), "HOME": str(root), "CLAUDE_PROJECT_DIR": str(root)}
    result = _run_hook(root, env=env)
    assert result.returncode == 0, result.stderr

    rows = _marker_lines(root)
    assert not any(row.get("hook_real_path") == _STEM for row in rows), rows


# --------------------------------------------------------------------------- #
# verify_chain: with and without the marker in the run's evidence.
#
# A FIXTURE hook (never the bundled one, which invokes no python module/CLI
# verb of its own) is pointed to by monkeypatching hook_entry.hooks_dir, the
# same pattern test_call_chain.py uses for registered_tool_sites.
# --------------------------------------------------------------------------- #

_FIXTURE_STEM = "fixture-hook"
_NEXT_HOP_MODULE = "fixturehookpkg.leaf"
_NEXT_HOP = f"{_NEXT_HOP_MODULE}.capability"


def _fixture_hooks_dir(src_root: Path, invoking_line: str) -> Path:
    """Under *src_root* (the ``repo_root`` verify_chain is called with), so its call site is relative."""
    hooks = src_root / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / f"{_FIXTURE_STEM}.sh").write_text(f"#!/bin/sh\n{invoking_line}\nexit 0\n", encoding="utf-8")
    return hooks


def _write_fixture_pkg(src_root: Path) -> None:
    (src_root / "fixturehookpkg").mkdir(parents=True, exist_ok=True)
    (src_root / "fixturehookpkg" / "__init__.py").write_text("", encoding="utf-8")
    (src_root / "fixturehookpkg" / "leaf.py").write_text(
        "def capability() -> None:\n    return None\n", encoding="utf-8"
    )


def test_hook_chain_is_wired_with_marker_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import trw_mcp.state.validation.hook_entry as hook_entry_module

    src_root = tmp_path / "src"
    _write_fixture_pkg(src_root)
    hooks_dir = _fixture_hooks_dir(src_root, f"python3 -m {_NEXT_HOP_MODULE}")
    monkeypatch.setattr(hook_entry_module, "hooks_dir", lambda: hooks_dir)

    verdict = verify_chain(src_root, (f"hook:{_FIXTURE_STEM}", _NEXT_HOP), hook_evidence=frozenset({_FIXTURE_STEM}))
    assert verdict.status == "wired", verdict.reason
    assert verdict.call_sites and verdict.call_sites[0].startswith(f"hooks/{_FIXTURE_STEM}.sh:")


def test_hook_chain_is_isolated_without_marker_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import trw_mcp.state.validation.hook_entry as hook_entry_module

    src_root = tmp_path / "src"
    _write_fixture_pkg(src_root)
    hooks_dir = _fixture_hooks_dir(src_root, f"python3 -m {_NEXT_HOP_MODULE}")
    monkeypatch.setattr(hook_entry_module, "hooks_dir", lambda: hooks_dir)

    verdict = verify_chain(src_root, (f"hook:{_FIXTURE_STEM}", _NEXT_HOP), hook_evidence=frozenset())
    assert verdict.status == "isolated"
    assert verdict.reason == "hook real path not observed"


def test_hook_chain_that_never_invokes_the_next_hop_is_isolated_even_with_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different, uninvoked module: the marker alone cannot fake a chain the script never names."""
    import trw_mcp.state.validation.hook_entry as hook_entry_module

    src_root = tmp_path / "src"
    _write_fixture_pkg(src_root)
    hooks_dir = _fixture_hooks_dir(src_root, "echo hello")  # never mentions fixturehookpkg
    monkeypatch.setattr(hook_entry_module, "hooks_dir", lambda: hooks_dir)

    verdict = verify_chain(src_root, (f"hook:{_FIXTURE_STEM}", _NEXT_HOP), hook_evidence=frozenset({_FIXTURE_STEM}))
    assert verdict.status == "isolated"
    assert "does not invoke" in verdict.reason


# ── sol core-320-fr04 r1 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "python3 -m fixturepkg.leaf.extra",
        "trw-mcp leaf-extra",
        "true # python3 -m fixturepkg.leaf",
        "echo 'x' #trw-mcp leaf",
    ],
    ids=["longer-module", "longer-verb", "inline-comment", "inline-comment-verb"],
)
def test_a_line_that_names_a_different_target_or_a_comment_does_not_invoke(line: str) -> None:
    from trw_mcp.state.validation.hook_entry import _invoking_line

    assert _invoking_line(f"#!/bin/sh\n{line}\n", "fixturepkg.leaf") is None


@pytest.mark.parametrize(
    "line", ["python3 -m fixturepkg.leaf", "  trw-mcp leaf --flag", "x && python -m fixturepkg.leaf; y"]
)
def test_an_exact_invocation_still_matches(line: str) -> None:
    from trw_mcp.state.validation.hook_entry import _invoking_line

    assert _invoking_line(f"#!/bin/sh\n{line}\n", "fixturepkg.leaf") == 2


@pytest.mark.parametrize("stem", ["../outside", "/tmp/fake-hook", "a/b", "", ".hidden"])
def test_a_stem_outside_the_bundled_hooks_directory_is_refused(stem: str) -> None:
    from trw_mcp.state.validation.hook_entry import verify_hook_hop

    result = verify_hook_hop(Path("/"), stem, "fixturepkg.leaf.capability", {stem})
    assert result.call_site == ""
    assert "hook stem" in result.reason

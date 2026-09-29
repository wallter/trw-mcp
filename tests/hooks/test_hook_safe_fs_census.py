"""PRD-FIX-156 FR03: hooks remove checkout state only through the safe-fs helpers.

A plain ``rm -f "$dir/x"`` resolves every component of ``$dir``, so a checkout
that ships ``.trw/context`` as a symlink turns a routine reset into "delete x
wherever that link points" (B71-103). ``_trw_safe_rm`` (lib-trw.sh) vets the
parent chain first, the way ``_trw_safe_write`` does for writes.

The census walks every bundled shell script and fails on an ``rm``, ``rmdir``
or ``unlink`` outside those helpers, unless the allowlist below names it with
a reason. The behaviour tests run the real hooks against a symlinked
``.trw/context`` and check the file behind the link survives.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.hooks._sh_census import DATA, census

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")

#: A removal command at a command position (start of line, after a separator or `(`/`{`).
_REMOVAL = re.compile(r"(?:^|[;&|({]|\bthen|\bdo|\belse)\s*(?:rm|rmdir|unlink)\s")

#: The helpers that ARE the guarded writer/remover; their own rm calls are the point.
_SAFE_FS_HELPERS = frozenset({"_trw_safe_write", "_trw_safe_rm"})

#: (path relative to data/, enclosing function, command text) -> why it stays a plain rm.
_ALLOWLIST: dict[tuple[str, str, str], str] = {
    (lib, "_trw_bounded_python", 'rm -f "$_bp_out"'): "the hook's own mktemp file in $TMPDIR, not checkout state"
    for lib in (
        "hooks/cursor/trw-before-edit-hint.sh",
        "copilot/hooks/trw-copilot-distill-hint.sh",
        "claude_code/hooks/pre-tool-distill-hint.sh",
    )
}
#: CORE-336-S3-KI: the batch-processed-files journal is a top-level `mktemp`
#: file in $TMPDIR (never a `${_hints_dir}/...` checkout path -- codex review
#: core336-s3ki r1 of 6fb90b972), so its cleanup is the same "own mktemp file"
#: exemption as `_bp_out`, just outside any function.
_ALLOWLIST[("claude_code/hooks/pre-tool-distill-hint.sh", "", 'rm -f "$_processed_file"')] = (
    "the hook's own mktemp journal in $TMPDIR, not checkout state"
)

_LIB_TRW = DATA / "hooks" / "lib-trw.sh"
_SENTINEL = "OUTSIDE_THE_CHECKOUT"


def _bundled_scripts() -> list[Path]:
    return sorted(p for p in DATA.rglob("*.sh") if p.is_file())


def _unlisted(path: Path) -> list[str]:
    return [
        f"{site.relpath}:{site.lineno} ({site.function or 'top level'}): {site.source}"
        for site in census(path, _REMOVAL)
        if site.function not in _SAFE_FS_HELPERS
        and not any(
            (rel, fn) == (site.relpath, site.function) and needle in site.source for rel, fn, needle in _ALLOWLIST
        )
    ]


def test_census_every_removal_goes_through_a_safe_fs_helper() -> None:
    offenders = [line for path in _bundled_scripts() for line in _unlisted(path)]
    assert offenders == [], f"plain rm of checkout state; use _trw_safe_rm (lib-trw.sh): {offenders}"


def test_census_catches_a_planted_plain_rm(tmp_path: Path) -> None:
    """Non-vacuity: the pre-fix user-prompt-submit.sh reset line is an offender."""
    planted = tmp_path / "planted-hook.sh"
    planted.write_text(
        '#!/bin/sh\nif true; then\n  rm -f "$_context_dir/none_phase_prompt_count" 2>/dev/null || true\nfi\n',
        encoding="utf-8",
    )
    assert _unlisted(planted) == [
        'planted-hook.sh:3 (top level): rm -f "$_context_dir/none_phase_prompt_count" 2>/dev/null || true'
    ]


def test_every_allowlist_row_still_names_a_real_site() -> None:
    """A row whose site was deleted or rewritten would silently allow its next occupant."""
    sites = {(s.relpath, s.function, s.source) for p in _bundled_scripts() for s in census(p, _REMOVAL)}
    stale = [key for key in _ALLOWLIST if not any(key[:2] == s[:2] and key[2] in s[2] for s in sites)]
    assert stale == []


def _safe_rm(project_root: Path, *paths: Path) -> int:
    script = f'. "{_LIB_TRW}"\n_trw_safe_rm "$@"\n'
    args = ["sh", "-c", script, "safe-rm", *(str(p) for p in paths)]
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_root)}
    return subprocess.run(args, env=env, cwd=project_root, timeout=10, check=False).returncode


def _symlinked_context(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout whose `.trw/context` links to a directory outside it."""
    project_root = tmp_path / "project"
    (project_root / ".trw").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project_root / ".trw" / "context").symlink_to(outside, target_is_directory=True)
    return project_root, outside


def test_safe_rm_refuses_a_symlinked_ancestor(tmp_path: Path) -> None:
    project_root, outside = _symlinked_context(tmp_path)
    (outside / "state").write_text(_SENTINEL, encoding="utf-8")

    assert _safe_rm(project_root, project_root / ".trw" / "context" / "state") == 1
    assert (outside / "state").read_text(encoding="utf-8") == _SENTINEL


def test_safe_rm_removes_a_symlinked_leaf_not_its_target(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    (project_root / ".trw" / "context").mkdir(parents=True)
    target = tmp_path / "target"
    target.write_text(_SENTINEL, encoding="utf-8")
    leaf = project_root / ".trw" / "context" / "state"
    leaf.symlink_to(target)
    plain = project_root / ".trw" / "context" / "plain"
    plain.write_text("x", encoding="utf-8")

    assert _safe_rm(project_root, leaf, plain, project_root / ".trw" / "context" / "absent") == 0
    assert not leaf.is_symlink() and not plain.exists()
    assert target.read_text(encoding="utf-8") == _SENTINEL


def test_user_prompt_submit_cadence_reset_keeps_a_file_behind_a_symlinked_context(tmp_path: Path) -> None:
    """B71-103: a prompt in a real phase resets the "none" cadence counter; pre-fix the
    plain rm deleted `none_phase_prompt_count` wherever `.trw/context` pointed."""
    from tests._auto_recall_hook_harness import _run_hook

    project_root = tmp_path / "bundled"  # the harness's project root for the bundled hook
    (project_root / ".trw").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "none_phase_prompt_count").write_text(_SENTINEL, encoding="utf-8")
    (project_root / ".trw" / "context").symlink_to(outside, target_is_directory=True)

    run = _run_hook(tmp_path, DATA / "hooks" / "user-prompt-submit.sh", prompt="a question", phase="implement")

    assert run.returncode == 0
    assert (outside / "none_phase_prompt_count").read_text(encoding="utf-8") == _SENTINEL
    assert {p.name for p in outside.iterdir()} == {"none_phase_prompt_count"}


def test_session_start_keeps_a_file_behind_a_symlinked_context(tmp_path: Path) -> None:
    """session-start.sh clears the phase cache on every start; pre-fix its plain rm
    deleted `last_ups_phase` in whatever directory `.trw/context` pointed at."""
    project_root, outside = _symlinked_context(tmp_path)
    (outside / "last_ups_phase").write_text(_SENTINEL, encoding="utf-8")
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_root)}
    env.pop("TRW_SESSION_ID", None)

    subprocess.run(
        ["sh", str(DATA / "hooks" / "session-start.sh")],
        input=json.dumps({"source": "startup", "session_id": "s1"}),
        text=True,
        capture_output=True,
        cwd=project_root,
        env=env,
        timeout=30,
        check=False,
    )

    assert (outside / "last_ups_phase").read_text(encoding="utf-8") == _SENTINEL

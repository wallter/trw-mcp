"""PRD-CORE-305-FR05 sol round-1 P0 — CLI access is deny-by-default under a bounded lane.

Round-1 review: ``learn-drain``, ``memory migrate --apply``, ``sync pull
--full``, and ``gc --no-dry-run`` were all reachable AND unclassified under a
reviewer or dispatched-child lane. The prior design
(``enforce_state_changing_guard`` checking only ``CLI_REPLACEMENTS.state_changing``
and a small ``LOCAL_STATE_CHANGING_COMMANDS`` table) was an allowlist of BAD
verbs -- allow by default, deny the ones someone remembered to name. Neither
table had ever heard of these four, so they ran unchecked.

This file has two halves:

1. A COMPLETENESS test that walks the REAL argparse tree (never a hand-copied
   list) and proves every leaf command path NOT on
   ``_cli_reviewer_policy``'s read-only allowlist is refused by the guard
   under a bounded lane -- coverage for the WHOLE surface, not one verb at a
   time. It also cross-checks the allowlist against the real tree in the
   other direction, so a renamed/removed verb can't keep a stale entry alive.
2. END-TO-END subprocess tests for the four verbs round-1 named, through the
   real ``trw-mcp`` CLI entry point, proving the refusal and the "nothing
   written" guarantee at the process boundary, not just at the guard
   function.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._stdio_harness import pinned_server_env
from trw_mcp.server._cli_argparse import _build_arg_parser
from trw_mcp.server._cli_replacements import enforce_state_changing_guard
from trw_mcp.server._cli_reviewer_policy import classified_command_paths
from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

pytestmark = pytest.mark.integration

#: Absolute, so a subprocess whose cwd is a scratch directory still resolves
#: THIS checkout's trw_mcp rather than the editable install pinned in .venv
#: (see test_local_cli_reviewer_enrolment.py's module docstring for the
#: measured failure mode a relative PYTHONPATH produces here).
_TRW_MCP_SRC = str(Path(__file__).resolve().parents[1] / "src")
_TRW_MEMORY_SRC = str(Path(__file__).resolve().parents[2] / "trw-memory" / "src")


# ---------------------------------------------------------------------------
# Real-tree introspection (never a hand-maintained list)
# ---------------------------------------------------------------------------


def _enumerate_leaf_paths(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> list[str]:
    """Every leaf command path under *parser*, found by walking its REAL subparsers tree."""
    sub_action = next(
        (action for action in parser._actions if isinstance(action, argparse._SubParsersAction)),
        None,
    )
    if sub_action is None:
        return [" ".join(prefix)] if prefix else []
    leaves: list[str] = []
    for name, subparser in sub_action.choices.items():
        leaves.extend(_enumerate_leaf_paths(subparser, (*prefix, name)))
    return leaves


def _all_guarded_leaf_paths() -> list[str]:
    """Every leaf path that actually reaches ``enforce_state_changing_guard``.

    ``serve`` (and bare ``trw-mcp`` with no subcommand) is excluded on
    purpose: neither is a key in ``SUBCOMMAND_HANDLERS``, so
    ``_cli.py::main`` never calls the guard for it -- it boots the MCP
    server, bounded by an entirely different mechanism
    (``SurfaceAuthorityMiddleware`` on the MCP tool surface, not this CLI
    guard).
    """
    leaves = _enumerate_leaf_paths(_build_arg_parser())
    return [path for path in leaves if path.split(" ", 1)[0] in SUBCOMMAND_HANDLERS]


def _namespace_for_path(path: str) -> SimpleNamespace:
    """A synthetic namespace carrying just the nested ``*_command`` chain ``invoked_command_path`` walks.

    No other flag is needed to prove a refusal: the guard only reads
    ``f"{current}_command"`` attributes to reconstruct the path, then hands
    the (mostly empty) namespace to the policy's predicate. A predicate that
    reads a real flag (``dry_run``, ``write``, ``apply``) does so with
    ``getattr(..., default)``, so its absence here still resolves to that
    flag's own real default.
    """
    ns = SimpleNamespace()
    parts = path.split(" ")
    current = parts[0]
    for nxt in parts[1:]:
        setattr(ns, f"{current.replace('-', '_')}_command", nxt)
        current = nxt
    return ns


# ---------------------------------------------------------------------------
# 1. Completeness: the whole surface, not one verb
# ---------------------------------------------------------------------------


def test_every_unclassified_leaf_is_refused_under_reviewer_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """A leaf absent from the read-only allowlist must be refused -- proven across the WHOLE tree.

    This is the regression net for the round-1 defect class: if
    ``enforce_state_changing_guard``/``is_reviewer_safe`` ever regressed to
    allow-by-default, this test would fail on nearly every one of the ~60
    unclassified leaves below, not just the four round-1 happened to name.
    """
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    allowed = classified_command_paths()
    leaves = _all_guarded_leaf_paths()
    assert leaves, "sanity: the real CLI tree must produce at least one guarded leaf"

    unclassified = [path for path in leaves if path not in allowed]
    assert unclassified, "sanity: this coverage test is vacuous if every real leaf is already allowlisted"
    # The verbs round-1/round-2 named that have NO safe mode at all must be
    # part of what this sweep actually covers, never classified out from
    # under it (round-2: doctor, hook-flags, and memory migrate all turned
    # out to write despite round-1's help-text-only classification).
    # PRD-CORE-311: `backup create`/`backup restore` write the local store
    # (and, for restore, replace it outright) -- neither must ever gain a
    # read-only allowlist entry.
    for named in (
        "learn-drain",
        "sync pull",
        "doctor",
        "hook-flags",
        "memory migrate",
        "backup create",
        "backup restore",
    ):
        assert named in unclassified, f"{named!r} unexpectedly missing from the unclassified (denied) set"

    for path in unclassified:
        cmd = path.split(" ", 1)[0]
        with pytest.raises(SystemExit) as exc_info:
            enforce_state_changing_guard(cmd, _namespace_for_path(path))
        assert exc_info.value.code == 1, f"{path!r} did not refuse cleanly under a bounded lane"


def test_run_evidence_pack_is_registered_and_not_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD-CORE-323 NFR04 (pack half): ``run evidence-pack`` writes a file, so it is never allowlisted.

    It must exist in the real CLI tree (so the sweep above actually covers it) and the
    guard must refuse it under a bounded lane. Slice S3 adds ``run evidence-verify``
    as read-only.
    """
    assert "run evidence-pack" in _all_guarded_leaf_paths()
    assert "run evidence-pack" not in classified_command_paths()
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    with pytest.raises(SystemExit) as exc_info:
        enforce_state_changing_guard("run", _namespace_for_path("run evidence-pack"))
    assert exc_info.value.code == 1


def test_every_unclassified_leaf_is_refused_under_dispatched_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_DISPATCH_CHILD", "1")
    allowed = classified_command_paths()
    unclassified = [path for path in _all_guarded_leaf_paths() if path not in allowed]
    assert unclassified

    for path in unclassified:
        cmd = path.split(" ", 1)[0]
        with pytest.raises(SystemExit) as exc_info:
            enforce_state_changing_guard(cmd, _namespace_for_path(path))
        assert exc_info.value.code == 1, f"{path!r} did not refuse cleanly under a dispatched child"


def test_every_allowlisted_leaf_still_exists_in_the_real_cli() -> None:
    """A stale allowlist entry (a renamed or removed verb) must not sit there unnoticed."""
    real_leaves = set(_all_guarded_leaf_paths())
    for command_path in classified_command_paths():
        assert command_path in real_leaves, f"{command_path!r} is allowlisted but no longer exists in the CLI"


#: The safe kwargs for each CONDITIONAL allowlist entry -- the args a real
#: invocation would carry in its read-only mode. Absent from
#: ``_namespace_for_path`` by construction (that helper only sets the
#: ``*_command`` chain), so this test sets them explicitly per entry rather
#: than asserting on a namespace no real conditional invocation would produce.
_CONDITIONAL_SAFE_KWARGS: dict[str, dict[str, object]] = {
    "gc": {"dry_run": True},
    "session-changelog": {"write": False},
    "channel-doctor clean": {"dry_run": True},
}


def test_allowlisted_leaves_actually_run_without_refusal_under_reviewer_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every allowlist entry, invoked in its read-only mode, must not be refused (a false positive is as bad as a miss)."""
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    allowlisted = classified_command_paths()
    assert allowlisted
    passed: list[str] = []
    for command_path in allowlisted:
        cmd = command_path.split(" ", 1)[0]
        ns = _namespace_for_path(command_path)
        for key, value in _CONDITIONAL_SAFE_KWARGS.get(command_path, {}).items():
            setattr(ns, key, value)
        try:
            enforce_state_changing_guard(cmd, ns)
        except SystemExit:
            pytest.fail(f"{command_path!r} is on the read-only allowlist but the guard refused it")
        passed.append(command_path)
    assert sorted(passed) == sorted(allowlisted)

    # Contrast: a leaf outside the allowlist IS refused under the same role.
    unlisted = next(p for p in _all_guarded_leaf_paths() if p not in allowlisted)
    with pytest.raises(SystemExit) as exc_info:
        enforce_state_changing_guard(unlisted.split(" ", 1)[0], _namespace_for_path(unlisted))
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# 2. End-to-end: the four verbs round-1 named, through the real CLI process
# ---------------------------------------------------------------------------


def _run_cli(argv: list[str], cwd: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([_TRW_MCP_SRC, _TRW_MEMORY_SRC])
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "trw_mcp.server", *argv],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=pinned_server_env(env),
    )


def test_learn_drain_refused_under_reviewer_role(tmp_path: Path) -> None:
    result = _run_cli(["learn-drain"], tmp_path, {"TRW_SURFACE_ROLE": "reviewer"})
    assert result.returncode != 0, result.stdout + result.stderr
    assert "learn-drain" in result.stderr


def test_memory_migrate_refused_under_reviewer_role_in_both_apply_and_preview_mode(tmp_path: Path) -> None:
    """sol round-2 P1: the --apply-less "preview" still creates a temp dir and copies
    the SQLite store (state/_store_migration.py's preview_migration) -- it is NOT
    write-free, so BOTH modes are refused, not just --apply."""
    applied = _run_cli(["memory", "migrate", "--to", "user", "--apply"], tmp_path, {"TRW_SURFACE_ROLE": "reviewer"})
    assert applied.returncode != 0, applied.stdout + applied.stderr
    assert "memory migrate" in applied.stderr

    preview = _run_cli(["memory", "migrate", "--to", "user"], tmp_path, {"TRW_SURFACE_ROLE": "reviewer"})
    assert preview.returncode != 0, preview.stdout + preview.stderr
    assert "memory migrate" in preview.stderr


def test_doctor_refused_under_reviewer_role(tmp_path: Path) -> None:
    """sol round-2 P0: doctor's memory-backend check opens the store (checkpointing
    its SQLite WAL) and its backend-connectivity check makes a live network request."""
    result = _run_cli(["doctor"], tmp_path, {"TRW_SURFACE_ROLE": "reviewer"})
    assert result.returncode != 0, result.stdout + result.stderr
    assert "doctor" in result.stderr


def test_hook_flags_refused_under_reviewer_role_and_writes_nothing(tmp_path: Path) -> None:
    """sol round-2 P0: hook-flags calls write_hook_flags, which creates .trw/runtime/
    and writes hook-flags every time -- it is not a pure printer."""
    runtime_dir = tmp_path / ".trw" / "runtime"
    result = _run_cli(["hook-flags"], tmp_path, {"TRW_SURFACE_ROLE": "reviewer"})
    assert result.returncode != 0, result.stdout + result.stderr
    assert "hook-flags" in result.stderr
    assert not runtime_dir.exists(), "a refused 'hook-flags' must not create .trw/runtime/"


def test_sync_pull_full_refused_under_reviewer_role(tmp_path: Path) -> None:
    result = _run_cli(["sync", "pull", "--full"], tmp_path, {"TRW_SURFACE_ROLE": "reviewer"})
    assert result.returncode != 0, result.stdout + result.stderr
    assert "sync pull" in result.stderr


def test_gc_no_dry_run_refused_but_default_dry_run_is_not_denied_by_the_guard(tmp_path: Path) -> None:
    denied = _run_cli(["gc", "--no-dry-run"], tmp_path, {"TRW_SURFACE_ROLE": "reviewer"})
    assert denied.returncode != 0, denied.stdout + denied.stderr
    assert "gc" in denied.stderr

    allowed = _run_cli(["gc"], tmp_path, {"TRW_SURFACE_ROLE": "reviewer"})
    assert "not on the reviewer/dispatched-child read-only allowlist" not in allowed.stderr


@pytest.mark.parametrize(
    "argv",
    [["learn-drain"], ["sync", "pull", "--full"], ["memory", "migrate", "--to", "user", "--apply"]],
    ids=["learn-drain", "sync-pull-full", "memory-migrate-apply"],
)
def test_flagged_write_verbs_also_refused_under_dispatched_child(argv: list[str], tmp_path: Path) -> None:
    result = _run_cli(argv, tmp_path, {"TRW_DISPATCH_CHILD": "1"})
    assert result.returncode != 0, result.stdout + result.stderr
    assert argv[0] in result.stderr or " ".join(argv[:2]) in result.stderr


# ---------------------------------------------------------------------------
# 3. sol round-2 P1: the guard runs BEFORE logging touches disk
# ---------------------------------------------------------------------------


def test_refused_command_with_verbose_flags_creates_no_trw_logs(tmp_path: Path) -> None:
    """``-vv``/``--debug`` make ``configure_logging`` create ``.trw/logs/`` and open a
    file (``_logging.py``). A refused command must leave literally no trace, so the
    guard must run before that setup, not just before the handler."""
    logs_dir = tmp_path / ".trw" / "logs"
    result = _run_cli(["-vv", "learn-drain"], tmp_path, {"TRW_SURFACE_ROLE": "reviewer"})
    assert result.returncode != 0, result.stdout + result.stderr
    assert not logs_dir.exists(), ".trw/logs/ must not exist after a refused command, even under -vv"


def test_refused_command_with_debug_flag_creates_no_trw_logs(tmp_path: Path) -> None:
    logs_dir = tmp_path / ".trw" / "logs"
    result = _run_cli(["--debug", "learn-drain"], tmp_path, {"TRW_SURFACE_ROLE": "reviewer"})
    assert result.returncode != 0, result.stdout + result.stderr
    assert not logs_dir.exists(), ".trw/logs/ must not exist after a refused command, even under --debug"


# ---------------------------------------------------------------------------
# sol round-4 P2: an ALLOWED command under --debug/-vv must not create
# .trw/logs/ either. Round-2's fix only covered a REFUSED command (the guard
# runs before logging setup) -- but an allowlisted verb reaches
# configure_logging() regardless, and --debug/-vv there is not itself a
# command the guard can refuse: it is configure_logging silently opening a
# file as a side effect of turning on verbose output. Parametrized over
# {no flag, --debug, -vv} x {reviewer role, dispatched child} for ONE
# representative allowlisted verb (config-reference, the exact case round-4
# named) rather than the full CASES product in test_cli_reviewer_write_freedom.py
# -- each case here is a real subprocess, and the host load rule (uptime,
# -n 2, narrow runs) makes an 18-24-subprocess product too slow to justify
# for a fix that lives entirely in configure_logging's own dispatch, not in
# per-verb behaviour.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("flags", [[], ["--debug"], ["-vv"]], ids=["no_flag", "debug", "vv"])
@pytest.mark.parametrize(
    ("env_key", "env_value"),
    [("TRW_SURFACE_ROLE", "reviewer"), ("TRW_DISPATCH_CHILD", "1")],
    ids=["reviewer_role", "dispatched_child"],
)
def test_allowed_command_with_debug_flags_creates_no_trw_logs_in_a_bounded_lane(
    flags: list[str], env_key: str, env_value: str, tmp_path: Path
) -> None:
    logs_dir = tmp_path / ".trw" / "logs"
    result = _run_cli([*flags, "config-reference"], tmp_path, {env_key: env_value})
    assert result.returncode == 0, result.stdout + result.stderr
    assert not logs_dir.exists(), f"config-reference with flags={flags} under {env_key}={env_value} created .trw/logs/"

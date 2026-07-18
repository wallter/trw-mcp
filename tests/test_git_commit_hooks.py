"""Regression tests for native blocking-hook timeout + stdin isolation (P1).

``run_blocking_hooks`` executes native git hooks (pre-commit, prepare-commit-msg,
commit-msg) inside an isolated candidate context during a verified two-step
commit. A hung or interactive hook -- one that sleeps forever or blocks reading an
interactive stdin -- must NOT be able to stall publication indefinitely. These
tests prove each hook runs with a bounded wall-clock ``timeout`` and a closed
stdin, and that exceeding the timeout FAILS CLOSED via the transaction error type.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

import pytest

import trw_mcp.state._git_commit_hooks as hooks_mod
from trw_mcp.models.git_commit_transaction import OwnershipManifest
from trw_mcp.state._git_commit_hooks import (
    DEFAULT_HOOK_TIMEOUT_SECONDS,
    _resolve_hook_timeout_seconds,
    run_blocking_hooks,
)


class _Boom(RuntimeError):
    """Stand-in for GitTransactionError (the module takes error_type by injection)."""


def _git(repo_root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    merged = {**os.environ, **(env or {})}
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        env=merged,
    )
    if result.returncode != 0:
        raise _Boom(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def _init_repo(tmp_path: Path) -> tuple[Path, str]:
    _git(tmp_path, "init", "--quiet")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "owned.txt").write_text("v1\n", encoding="utf-8")
    _git(tmp_path, "add", "owned.txt")
    _git(tmp_path, "commit", "--quiet", "-m", "init")
    parent = _git(tmp_path, "rev-parse", "HEAD").strip()
    return tmp_path, parent


def _manifest(parent: str) -> OwnershipManifest:
    digest = "sha256:" + hashlib.sha256(b"v1\n").hexdigest()
    return OwnershipManifest(
        transaction_id="tx-test",
        run_id="run-test",
        parent_oid=parent,
        owned_paths=("owned.txt",),
        path_digests={"owned.txt": digest},
    )


def _write_pre_commit(repo: Path, body: str) -> None:
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    hook.chmod(0o755)


def _run(repo: Path, parent: str, *, timeout: float | None) -> None:
    run_blocking_hooks(
        repo,
        _manifest(parent),
        "commit message",
        git=_git,
        mark_mutation=lambda *_: None,
        error_type=_Boom,
        hook_timeout_seconds=timeout,
    )


@pytest.mark.integration
class TestBlockingHookTimeout:
    def test_hanging_hook_times_out_and_fails_closed(self, tmp_path: Path) -> None:
        """A pre-commit hook that sleeps past the timeout raises the transaction error fast."""
        repo, parent = _init_repo(tmp_path)
        _write_pre_commit(repo, "sleep 30")

        started = time.monotonic()
        with pytest.raises(_Boom) as exc:
            _run(repo, parent, timeout=0.5)
        elapsed = time.monotonic() - started

        assert "timeout" in str(exc.value)
        # The guard interrupts the hang instead of waiting out the full 30s sleep.
        assert elapsed < 10, f"timeout did not interrupt the hanging hook (took {elapsed:.1f}s)"

    def test_hook_uses_devnull_stdin_and_configured_timeout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each hook subprocess is spawned with stdin=DEVNULL (never inherited) and a timeout."""
        repo, parent = _init_repo(tmp_path)
        _write_pre_commit(repo, "exit 0")

        captured: dict[str, object] = {}
        real_run = subprocess.run

        def _spy(args: list[str], **kwargs: object):  # type: ignore[no-untyped-def]
            if args and str(args[0]).endswith("pre-commit"):
                captured["stdin"] = kwargs.get("stdin")
                captured["timeout"] = kwargs.get("timeout")
            return real_run(args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(hooks_mod.subprocess, "run", _spy)
        _run(repo, parent, timeout=7.0)

        assert captured["stdin"] is subprocess.DEVNULL, "hook stdin must be DEVNULL, not inherited"
        assert captured["timeout"] == 7.0

    def test_stdin_reading_hook_does_not_hang(self, tmp_path: Path) -> None:
        """A hook that drains stdin returns immediately (EOF from DEVNULL), never blocking."""
        repo, parent = _init_repo(tmp_path)
        # `cat` reads stdin to EOF; with an inherited interactive stdin it would
        # block until the timeout. With DEVNULL it sees EOF and exits at once.
        _write_pre_commit(repo, "cat >/dev/null; exit 0")

        started = time.monotonic()
        _run(repo, parent, timeout=10.0)  # returns cleanly (no error) => did not hang
        assert time.monotonic() - started < 8


@pytest.mark.unit
class TestHookTimeoutConfig:
    def test_resolves_documented_default_when_unset(self) -> None:
        """With no TRWConfig override the resolver returns the named default."""
        assert _resolve_hook_timeout_seconds() == DEFAULT_HOOK_TIMEOUT_SECONDS

    def test_config_field_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A configured git_hook_timeout_seconds takes precedence over the default."""

        class _Cfg:
            git_hook_timeout_seconds = 42.0

        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: _Cfg())
        assert _resolve_hook_timeout_seconds() == 42.0

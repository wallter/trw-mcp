"""PRD-CORE-231-FR01: post-commit T2 sidecar refresh.

The sidecar cache is keyed by HEAD sha, so every commit invalidates the T2 tier
and the PreToolUse hook falls back to T1/T0 forever. These tests cover the
planner that decides what the post-commit hook regenerates, its env hygiene,
and the bundled hook's fail-open contract.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._hint_sidecar_refresh import (
    RefreshPlan,
    build_subprocess_spec,
    changed_files,
    count_refreshed_targets,
    resolve_refresh_plan,
    sanitized_env,
    targets_file,
)

_HOOK = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "data" / "git_hooks" / "trw-post-commit.sh"


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """A git repo with one commit touching three files."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    return tmp_path


def _plan(repo: Path, **overrides: object) -> RefreshPlan:
    config = TRWConfig()
    kwargs: dict[str, object] = {
        "enabled": config.hint_sidecar_refresh_enabled,
        "file_cap": config.hint_sidecar_refresh_file_cap,
        "distill_available": True,
    }
    kwargs.update(overrides)
    return resolve_refresh_plan(repo, **kwargs)  # type: ignore[arg-type]


def _spec(plan: RefreshPlan, repo: Path, env: dict[str, str]) -> tuple[object, Path]:
    """Build the spec plus a live targets file the caller can still read."""
    with targets_file(plan.files) as path:
        spec = build_subprocess_spec(plan, repo, env, targets_path=path)
        content = path.read_text(encoding="utf-8")
    return spec, content  # type: ignore[return-value]


def test_regenerates_sidecar_for_changed_files(git_repo: Path) -> None:
    """The plan covers exactly the files HEAD touched, in ONE invocation.

    One per file was the defect: ``--file`` mode persists to
    ``before-edit-hint-<sha>.json``, a name with no per-file discriminator, so
    three invocations left one artifact describing whichever path git emitted
    last. ``--files-from`` persists ``before-edit-batch-<sha>.json``, which
    carries a hint per target.
    """
    plan = _plan(git_repo)

    assert plan.should_run
    assert set(plan.files) == {"a.py", "b.py", "c.py"}
    assert not plan.truncated

    spec, targets_content = _spec(plan, git_repo, {"PATH": "/usr/bin"})
    assert spec is not None
    argv = spec.argv  # type: ignore[attr-defined]
    assert argv[:3] == ("trw-distill", "self-improve", "before-edit")
    assert "--persist-sidecar" in argv
    assert str(git_repo) in argv
    # The single-file flag is what made N invocations collide.
    assert "--file" not in argv
    assert "--files-from" in argv
    assert set(targets_content.split()) == {"a.py", "b.py", "c.py"}


def test_a_comma_in_a_path_is_not_two_targets(git_repo: Path) -> None:
    """``--files A,B,C`` would split this path in half.

    The CLI's csv mode splits on commas, and a repo-relative path may legally
    contain one — which would substitute two nonexistent targets for a real
    file and silently deny it a hint. That is the same defect one level down,
    so the targets travel newline-separated.
    """
    plan = RefreshPlan(files=("weird,name.py", "plain.py"))

    spec, targets_content = _spec(plan, git_repo, {})

    assert targets_content.splitlines() == ["weird,name.py", "plain.py"]
    # And the path must not ALSO be on the command line, where the csv split
    # would still get at it.
    assert spec is not None
    assert "weird,name.py" not in spec.argv  # type: ignore[attr-defined]


def test_file_cap_bounds_the_plan(git_repo: Path) -> None:
    """NFR01: a large commit cannot fan out unbounded subprocesses."""
    plan = _plan(git_repo, file_cap=2)

    assert len(plan.files) == 2
    assert plan.truncated


def test_disabled_flag_is_a_no_op(git_repo: Path) -> None:
    """FR01 rollback: the config switch turns the whole mechanism off."""
    plan = _plan(git_repo, enabled=False)

    assert not plan.should_run
    assert plan.skipped_reason == "disabled_by_config"
    with targets_file(plan.files) as path:
        assert build_subprocess_spec(plan, git_repo, {}, targets_path=path) is None


def test_missing_distill_is_a_silent_no_op(git_repo: Path) -> None:
    """Free-tier fail-open contract, unchanged: no entitlement => no work."""
    plan = _plan(git_repo, distill_available=False)

    assert not plan.should_run
    assert plan.skipped_reason == "distill_unavailable"


def test_non_git_directory_degrades(tmp_path: Path) -> None:
    """No git => empty plan, never an exception."""
    assert changed_files(tmp_path) == ()

    plan = _plan(tmp_path)
    assert not plan.should_run
    assert plan.skipped_reason == "no_changed_files"


def test_subprocess_env_is_sanitized() -> None:
    """NFR03: an explicit allowlist, never os.environ passthrough."""
    source = {
        "PATH": "/usr/bin",
        "HOME": "/home/dev",
        "AWS_SECRET_ACCESS_KEY": "super-secret",
        "GITHUB_TOKEN": "ghp_secret",
        "TRW_PLATFORM_API_KEY": "secret",
    }

    env = sanitized_env(source)

    assert env == {"PATH": "/usr/bin", "HOME": "/home/dev"}
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "TRW_PLATFORM_API_KEY" not in env


def test_specs_carry_the_sanitized_env(git_repo: Path) -> None:
    """The allowlist reaches the actual invocation, not just the helper."""
    plan = _plan(git_repo)

    spec, _ = _spec(plan, git_repo, {"PATH": "/usr/bin", "GITHUB_TOKEN": "ghp_secret"})

    assert spec is not None
    assert "GITHUB_TOKEN" not in spec.env  # type: ignore[attr-defined]
    assert spec.env["PATH"] == "/usr/bin"  # type: ignore[attr-defined]


def test_shell_metacharacter_paths_never_reach_a_command_line(git_repo: Path) -> None:
    """A hostile filename must not reach argv OR a shell.

    Stronger than the argv-element guarantee it replaces: target paths are no
    longer on the command line at all, so the only surface is a file this
    process writes and the CLI reads.
    """
    hostile = "evil$(touch /tmp/pwned).py"
    plan = RefreshPlan(files=(hostile,))

    spec, targets_content = _spec(plan, git_repo, {})

    assert spec is not None
    assert hostile not in " ".join(spec.argv)  # type: ignore[attr-defined]
    assert targets_content.splitlines() == [hostile]


def test_targets_file_is_removed_after_use() -> None:
    """The temp file must not outlive the invocation it was written for."""
    with targets_file(("a.py",)) as path:
        assert path.is_file()
        leaked = path
    assert not leaked.exists()


#: A ``trw-distill`` shim that exits with a chosen code and, optionally, writes
#: a batch sidecar covering a chosen subset of the targets it was handed. Both
#: halves are independent ON PURPOSE: the defect this file guards is precisely
#: an exit code of 0 alongside artifacts that do not cover the target set.
_STUB_CLI = (
    f"#!{sys.executable}\n"
    + """import json, os, subprocess, sys
covers = os.environ.get("STUB_COVERS", "")
if covers:
    argv = sys.argv[1:]
    repo = argv[argv.index("--repo") + 1]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    cache = os.path.join(repo, ".trw", "distill", "map-cache")
    os.makedirs(cache, exist_ok=True)
    hints = [
        {
            "target_path": t, "target_exists_in_map": True, "importers": [],
            "inferred_tests": [], "doc_references": [], "co_change_neighbors": [],
            "hotspot_warnings": [], "risk_score": 0.1,
        }
        for t in covers.split(",")
    ]
    envelope = {
        "schema_version": "risk-report-sidecar/v0",
        "sha": os.environ.get("STUB_SHA") or sha,
        "generated_at_unix": 1.0,
        "payload": {
            "total_files": len(hints), "files_in_map": len(hints),
            "total_hotspot_warnings": 0, "hints": hints,
        },
    }
    with open(os.path.join(cache, "before-edit-batch-%s.json" % sha), "w") as fh:
        json.dump(envelope, fh)
sys.stderr.write("stub\\n")
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
"""
)


def _run_refresh_with_stub_cli(
    repo: Path,
    exit_code: int,
    *,
    covers: str = "",
    stamp_sha: str = "",
) -> list[dict[str, object]]:
    """Drive ``run_post_commit_refresh`` against a REAL subprocess with a stub CLI.

    The shim runs as an actual process, so the returncode and the artifacts both
    come from the real code path rather than from a mock.
    """
    from structlog.testing import capture_logs

    import trw_mcp.tools._hint_sidecar_refresh as refresh_mod

    bindir = repo / "stub-bin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "trw-distill"
    shim.write_text(_STUB_CLI, encoding="utf-8")
    shim.chmod(0o755)

    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "STUB_EXIT": str(exit_code),
        "STUB_COVERS": covers,
        "STUB_SHA": stamp_sha,
    }
    with (
        pytest.MonkeyPatch.context() as mp,
        capture_logs() as logs,
    ):
        mp.setattr(refresh_mod, "distill_available", lambda: True)
        # STUB_* are not on the sanitized allowlist, so hand them to the shim by
        # putting them in the process environment the sanitizer projects FROM.
        mp.setattr(refresh_mod, "_ENV_ALLOWLIST", ("PATH", "STUB_EXIT", "STUB_COVERS", "STUB_SHA"))
        refresh_mod.run_post_commit_refresh(repo, env)
    return list(logs)


def _completion(logs: list[dict[str, object]]) -> dict[str, object]:
    return next(e for e in logs if e.get("event") == "sidecar_refresh_complete")


def test_exit_zero_with_no_artifact_refreshes_nothing(git_repo: Path) -> None:
    """The defect, stated as a test: a clean exit is not a refreshed file.

    Every single-file invocation writes to the SAME ``before-edit-hint-<sha>``
    path, so a 13-file commit logged ``files=13 succeeded=13 failed=0`` while
    the cache held one artifact describing one file — and 12 of 13 edits got no
    T2 hint. Exit-code accounting cannot see that, because all 13 exits were
    genuinely 0. Count artifacts written FOR THE TARGET SET instead.
    """
    logs = _run_refresh_with_stub_cli(git_repo, exit_code=0, covers="")

    completion = _completion(logs)
    assert completion["planned"] == 3
    assert completion["refreshed"] == 0


def test_landed_artifact_is_counted(git_repo: Path) -> None:
    """Non-vacuity control.

    An implementation hardcoding ``refreshed=0``, or one that could not read the
    artifact at all, would pass the test above. The same code path with a CLI
    that actually writes must report the opposite.
    """
    logs = _run_refresh_with_stub_cli(git_repo, exit_code=0, covers="a.py,b.py,c.py")

    completion = _completion(logs)
    assert completion["planned"] == 3
    assert completion["refreshed"] == 3


def test_partial_coverage_is_reported_as_partial(git_repo: Path) -> None:
    """The production shape: some targets covered, the rest silently not.

    This is the case a boolean success flag or an exit code can never express,
    and the one that shipped: N planned, one delivered.
    """
    logs = _run_refresh_with_stub_cli(git_repo, exit_code=0, covers="a.py")

    completion = _completion(logs)
    assert completion["planned"] == 3
    assert completion["refreshed"] == 1


def test_artifact_stamped_with_another_sha_does_not_count(git_repo: Path) -> None:
    """An artifact from a different commit is not a refresh of this one.

    Non-vacuity for the sha check inside ``count_refreshed_targets``: without
    it, a leftover file from any previous commit would be counted as delivered.
    """
    logs = _run_refresh_with_stub_cli(git_repo, exit_code=0, covers="a.py,b.py,c.py", stamp_sha="0" * 40)

    assert _completion(logs)["refreshed"] == 0


def test_failed_refresh_is_warned_and_refreshes_nothing(git_repo: Path) -> None:
    """A non-zero exit must not read like success.

    ``trw-distill`` missing from the hook's sanitized PATH fails the invocation,
    and the completion line used to read like a full success anyway.
    """
    logs = _run_refresh_with_stub_cli(git_repo, exit_code=3)

    completion = _completion(logs)
    assert completion["planned"] == 3
    assert completion["refreshed"] == 0

    failures = [e for e in logs if e.get("event") == "sidecar_refresh_subprocess_nonzero_exit"]
    assert len(failures) == 1
    assert failures[0]["returncode"] == 3
    assert failures[0]["files"] == 3
    # A maintainer-needed failure reason must not go to `debug` — under a
    # default install that level reaches nowhere, which deletes the signal
    # rather than moving it (.claude/rules/trw-mcp-python.md).
    assert failures[0]["log_level"] == "warning"


def test_unspawnable_cli_is_warned_and_refreshes_nothing(git_repo: Path) -> None:
    """An OSError (binary absent) is a failure too, not an unrecorded no-op."""
    from structlog.testing import capture_logs

    import trw_mcp.tools._hint_sidecar_refresh as refresh_mod

    with (
        pytest.MonkeyPatch.context() as mp,
        capture_logs() as logs,
    ):
        mp.setattr(refresh_mod, "distill_available", lambda: True)
        # An empty PATH makes exec of "trw-distill" raise FileNotFoundError.
        refresh_mod.run_post_commit_refresh(git_repo, {"PATH": str(git_repo / "no-such-bin")})

    completion = _completion(list(logs))
    assert completion["planned"] == 3
    assert completion["refreshed"] == 0

    spawn_failures = [e for e in logs if e.get("event") == "sidecar_refresh_subprocess_failed"]
    assert len(spawn_failures) == 1
    assert spawn_failures[0]["log_level"] == "warning"


def test_count_refreshed_targets_reads_the_single_artifact_too(git_repo: Path) -> None:
    """A one-file commit legitimately produces the SINGLE artifact.

    The CLI switches to single-file mode when handed exactly one target, so the
    counter must recognise both shapes or every one-file commit would report a
    refresh of zero.
    """
    import json as _json
    import subprocess as _sp

    sha = _sp.check_output(["git", "rev-parse", "HEAD"], cwd=git_repo).decode().strip()
    cache = git_repo / ".trw" / "distill" / "map-cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"before-edit-hint-{sha}.json").write_text(
        _json.dumps(
            {
                "schema_version": "risk-report-sidecar/v0",
                "sha": sha,
                "generated_at_unix": 1.0,
                "payload": {"target_path": "a.py", "target_exists_in_map": True},
            }
        ),
        encoding="utf-8",
    )

    assert count_refreshed_targets(git_repo, ("a.py",)) == 1
    assert count_refreshed_targets(git_repo, ("a.py", "b.py")) == 1
    assert count_refreshed_targets(git_repo, ("b.py",)) == 0


def test_receipt_records_delivered_not_planned(git_repo: Path) -> None:
    """PostCommitReceipt.sidecar_files must be the achieved count.

    It was ``len(plan.files)`` — the number the hook INTENDED — so the receipt
    an operator reads to confirm the hook worked asserted 13 refreshed files on
    a run that delivered one.
    """
    import trw_mcp.tools._hint_sidecar_refresh as refresh_mod
    from trw_mcp.tools._post_commit import read_receipt, run_post_commit

    bindir = git_repo / "stub-bin"
    bindir.mkdir(exist_ok=True)
    shim = bindir / "trw-distill"
    shim.write_text(_STUB_CLI, encoding="utf-8")
    shim.chmod(0o755)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(refresh_mod, "distill_available", lambda: True)
        mp.setattr(refresh_mod, "_ENV_ALLOWLIST", ("PATH", "STUB_EXIT", "STUB_COVERS", "STUB_SHA"))
        run_post_commit(
            git_repo,
            {"PATH": f"{bindir}:/usr/bin:/bin", "STUB_EXIT": "0", "STUB_COVERS": "a.py", "STUB_SHA": ""},
        )

    receipt = read_receipt(git_repo)
    assert receipt is not None
    assert receipt["sidecar_files_planned"] == 3
    assert receipt["sidecar_files"] == 1


def test_refresh_failures_stay_out_of_the_return_value(git_repo: Path) -> None:
    """Diagnostics go to structlog, never to the caller-facing result.

    ``RefreshPlan`` is what the hook was asked to run; per-invocation outcomes
    are maintainer signal and must not widen it.
    """
    from dataclasses import fields

    field_names = {f.name for f in fields(RefreshPlan)}
    assert field_names == {"files", "skipped_reason", "truncated"}


def test_bundled_hook_exists_and_is_posix_sh() -> None:
    """The hook ships in the bundled data dir and parses under POSIX sh."""
    assert _HOOK.is_file()
    completed = subprocess.run(["sh", "-n", str(_HOOK)], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_bundled_hook_never_blocks_the_commit(tmp_path: Path) -> None:
    """NFR02: the hook exits 0 even with no Python, no distill, no repo state."""
    completed = subprocess.run(
        ["sh", str(_HOOK)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "TRW_PROJECT_DIR": str(tmp_path)},
        timeout=60,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""


def test_bundled_hook_does_not_interpolate_repo_path_into_python() -> None:
    """The repo path reaches Python via env, so a quoted path cannot inject code."""
    body = _HOOK.read_text(encoding="utf-8")

    assert "TRW_POST_COMMIT_REPO" in body
    assert 'os.environ["TRW_POST_COMMIT_REPO"]' in body
    # The classic injection shape — "$_repo" pasted inside the -c program.
    assert "Path('$_repo')" not in body


class TestAPreexistingArtifactIsNotCreditedToThisRun:
    """Reading an artifact back proves a sidecar exists, not that THIS run wrote it.

    ``count_refreshed_targets`` reads whatever current-SHA artifact is on disk. An
    artifact left by an earlier refresh at the same commit — or written by the
    pre-edit hook — is indistinguishable from one this run produced, so a run whose
    producer failed outright still reported those targets as refreshed.

    That is the third time this module's accounting has believed the wrong thing:
    first counting invocations, then counting exit codes, now counting artifacts it
    did not write. Found by an independent adversarial review of the commit that
    claimed the count came from "artifacts actually written".
    """

    def _seed(self, tmp_path, targets):
        """Write a valid current-SHA batch artifact, as a previous run would have."""
        import json
        import subprocess

        from trw_mcp.tools._sidecar_substrate import DEFAULT_CACHE_DIR_REL

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@e.st"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
        (tmp_path / "seed.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "seed"], check=True)

        from trw_mcp.tools._sidecar_substrate import resolve_git_sha

        sha = resolve_git_sha(tmp_path)
        cache = tmp_path / DEFAULT_CACHE_DIR_REL
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / f"before-edit-batch-{sha}.json"
        path.write_text(
            json.dumps({"sha": sha, "payload": {"hints": [{"target_path": t} for t in targets]}}),
            encoding="utf-8",
        )
        return path

    def test_without_a_snapshot_the_stale_artifact_is_counted(self, tmp_path) -> None:
        """The pre-fix behaviour, pinned. This is what `since=None` still does, and
        it is a legitimate question ("is a sidecar available?") — just not the one
        the receipt asks."""
        from trw_mcp.tools._hint_sidecar_refresh import count_refreshed_targets

        self._seed(tmp_path, ("a.py", "b.py"))

        assert count_refreshed_targets(tmp_path, ("a.py", "b.py")) == 2

    def test_with_a_snapshot_an_untouched_artifact_counts_zero(self, tmp_path) -> None:
        """The fix. RED before `since` existed: this returned 2 for a run that wrote nothing."""
        from trw_mcp.tools._hint_sidecar_refresh import (
            count_refreshed_targets,
            snapshot_artifact_mtimes,
        )

        self._seed(tmp_path, ("a.py", "b.py"))
        before = snapshot_artifact_mtimes(tmp_path)

        # No producer runs between the snapshot and the count — exactly the
        # "subprocess failed, artifact already there" case.
        assert count_refreshed_targets(tmp_path, ("a.py", "b.py"), since=before) == 0

    def test_a_rewritten_artifact_is_counted_again(self, tmp_path) -> None:
        """Precision control. The fix must not make a REAL refresh count zero —
        including a byte-identical rewrite, which is legitimate at the same commit."""
        import os

        from trw_mcp.tools._hint_sidecar_refresh import (
            count_refreshed_targets,
            snapshot_artifact_mtimes,
        )

        path = self._seed(tmp_path, ("a.py", "b.py"))
        before = snapshot_artifact_mtimes(tmp_path)

        raw = path.read_text(encoding="utf-8")
        path.write_text(raw, encoding="utf-8")  # same bytes, new mtime
        bumped = before[str(path)] + 1_000_000
        os.utime(path, ns=(bumped, bumped))

        assert count_refreshed_targets(tmp_path, ("a.py", "b.py"), since=before) == 2

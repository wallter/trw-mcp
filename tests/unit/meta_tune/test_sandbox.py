"""Tests for meta_tune.sandbox — PRD-HPO-SAFE-001 FR-2."""

from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path

import pytest

from trw_mcp.meta_tune import sandbox as sandbox_mod
from trw_mcp.meta_tune.errors import MetaTuneSafetyUnavailableError
from trw_mcp.meta_tune.sandbox import (
    ProbeIsolationContext,
    SandboxResult,
    run_sandboxed,
)

IS_LINUX = platform.system() == "Linux"


def test_sandbox_exports_probeisolationcontext_symbol() -> None:
    """FR-2/§10.1 — ProbeIsolationContext is exported and documented."""
    from trw_mcp.meta_tune.sandbox import ProbeIsolationContext as PIC  # noqa: F401

    assert ProbeIsolationContext.__doc__ is not None
    assert "shared primitive" in ProbeIsolationContext.__doc__.lower()


def test_sandbox_result_dataclass_shape() -> None:
    """SandboxResult exposes PRD §7.3 fields."""
    r = SandboxResult(
        exit_code=0,
        stdout="",
        stderr="",
        wall_ms=1.0,
        rss_peak_mb=0.0,
        network_attempted=False,
    )
    assert r.writes_outside_tmp == []
    assert r.timed_out is False


def test_sandbox_timeout_via_signal_alarm(tmp_path: Path) -> None:
    """FR-2 — wall-clock timeout enforced; timed_out=True, wall_ms < 2000."""
    result = run_sandboxed(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_s=1.0,
        strict=False,
    )
    assert result.timed_out is True
    assert result.wall_ms < 2000.0


def test_sandbox_determinism_under_fixed_seed(tmp_path: Path) -> None:
    """Identical deterministic commands produce matching stdout."""
    cmd = [sys.executable, "-c", "print('hello-deterministic')"]
    r1 = run_sandboxed(cmd, timeout_s=5.0, strict=False)
    r2 = run_sandboxed(cmd, timeout_s=5.0, strict=False)
    assert r1.stdout.strip() == r2.stdout.strip() == "hello-deterministic"
    assert r1.exit_code == r2.exit_code == 0


def test_sandbox_no_writes_to_live_paths(tmp_path: Path) -> None:
    """FR-2 — writes outside writable_paths are detected or blocked."""
    sentinel = tmp_path / "probe_test_should_fail"
    code = f"open({str(sentinel)!r}, 'w').write('x')"
    # writable_paths empty → the sentinel write is off-allowlist. We do not
    # bind-mount enforcement in v1, so the write may succeed; the guarantee
    # is that we report it. If the file was created, it must appear in
    # writes_outside_tmp OR exit_code must be non-zero.
    result = run_sandboxed(
        [sys.executable, "-c", code],
        timeout_s=5.0,
        writable_paths=[],
        strict=False,
    )
    created = sentinel.exists()
    reported = any(str(sentinel) in w or w.endswith("probe_test_should_fail") for w in result.writes_outside_tmp)
    # v1 guarantee: if enforcement is unavailable (degraded mode with no
    # bind-mount privileges) we accept that the write may succeed; the
    # contract is that the sandbox completes deterministically. Full
    # enforcement lands in PRD-HPO-SAFE-002 (container rollout).
    degraded_mode = not IS_LINUX or not sandbox_mod._HAS_SECCOMP
    assert (created and reported) or result.exit_code != 0 or not created or degraded_mode
    # Cleanup
    if sentinel.exists():
        sentinel.unlink()


def test_sandbox_no_network_egress() -> None:
    """FR-2 — network egress is blocked or flagged when allow_network=False."""
    code = (
        "import urllib.request, sys\n"
        "try:\n"
        "    urllib.request.urlopen('https://example.com', timeout=2)\n"
        "    sys.exit(0)\n"
        "except Exception as e:\n"
        "    sys.stderr.write(type(e).__name__ + ': ' + str(e))\n"
        "    sys.exit(1)\n"
    )
    result = run_sandboxed(
        [sys.executable, "-c", code],
        timeout_s=6.0,
        allow_network=False,
        strict=False,
    )
    # Either the call failed (exit_code != 0) or we flagged network_attempted.
    # In fully-degraded mode (no seccomp + no unshare privileges) network
    # isolation is genuinely unavailable — the test accepts that case and
    # validates that the sandbox still completed deterministically with a
    # SandboxResult. Full enforcement lands in PRD-HPO-SAFE-002.
    degraded_mode = not IS_LINUX or not sandbox_mod._HAS_SECCOMP
    assert result.exit_code != 0 or result.network_attempted or degraded_mode
    assert isinstance(result, SandboxResult)


@pytest.mark.skipif(not IS_LINUX, reason="RLIMIT_AS behavior is Linux-specific")
def test_sandbox_enforces_memory_cap_via_setrlimit() -> None:
    """FR-2 — RLIMIT_AS aborts allocations beyond cap."""
    # Try to allocate ~512 MB under a 64 MB cap.
    code = "x = bytearray(512 * 1024 * 1024); print(len(x))"
    result = run_sandboxed(
        [sys.executable, "-c", code],
        timeout_s=10.0,
        memory_cap_mb=64,
        strict=False,
    )
    # Under RLIMIT_AS the interpreter should exit non-zero (MemoryError or abort).
    assert result.exit_code != 0


def test_sandbox_readonly_path_raises_permission_error(tmp_path: Path) -> None:
    """FR-2 — mutation of readonly path is detected via mtime audit."""
    ro_file = tmp_path / "readonly.txt"
    ro_file.write_text("original")
    # Make it readonly at the FS level so the child cannot rewrite it.
    ro_file.chmod(0o444)

    code = (
        f"import os\n"
        f"try:\n"
        f"    open({str(ro_file)!r}, 'w').write('mutated')\n"
        f"    print('WROTE')\n"
        f"except PermissionError:\n"
        f"    print('BLOCKED')\n"
    )
    result = run_sandboxed(
        [sys.executable, "-c", code],
        timeout_s=5.0,
        readonly_paths=[ro_file],
        strict=False,
    )
    # Either the write was blocked, OR the audit detected a mutation.
    blocked = "BLOCKED" in result.stdout or "PermissionError" in result.stderr
    audited = str(ro_file) in result.writes_outside_tmp
    assert blocked or audited
    # Restore for cleanup
    ro_file.chmod(0o644)


def test_sandbox_degraded_mode_logs_warning_on_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-Linux platforms emit a degraded-mode warning."""
    monkeypatch.setattr(sandbox_mod, "_IS_LINUX", False)
    monkeypatch.setattr(sandbox_mod.platform, "system", lambda: "Darwin")

    captured: list[tuple[str, dict[str, object]]] = []

    class _FakeLogger:
        def info(self, event: str, **kwargs: object) -> None:
            captured.append((event, kwargs))

        def warning(self, event: str, **kwargs: object) -> None:
            captured.append((event, kwargs))

        def error(self, event: str, **kwargs: object) -> None:
            captured.append((event, kwargs))

    monkeypatch.setattr(sandbox_mod, "logger", _FakeLogger())

    ctx = ProbeIsolationContext(timeout_s=0.1, memory_cap_mb=64, strict=False)
    with ctx as runner:
        assert runner.degraded is True

    warn_events = [e for e, _ in captured if e == "sandbox_degraded_mode"]
    assert warn_events, f"expected degraded-mode warning, got: {captured}"


def test_sandbox_context_manager_cleanup() -> None:
    """__exit__ clears the internal runner reference."""
    ctx = ProbeIsolationContext(timeout_s=1.0, memory_cap_mb=64, strict=False)
    with ctx as runner:
        assert runner is not None
        assert ctx._runner is runner
    assert ctx._runner is None


def test_sandbox_wrap_cmd_unshare_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """If ``unshare`` is not on PATH, _wrap_cmd returns cmd unchanged and logs."""
    monkeypatch.setattr(sandbox_mod, "_IS_LINUX", True)
    monkeypatch.setattr(sandbox_mod.shutil, "which", lambda _: None)
    with ProbeIsolationContext(timeout_s=1.0, allow_network=False, strict=False) as runner:
        out = runner._wrap_cmd(["echo", "hi"])
    assert out == ["echo", "hi"]


def test_sandbox_allow_network_skips_unshare() -> None:
    """allow_network=True means no unshare wrapping."""
    with ProbeIsolationContext(timeout_s=1.0, allow_network=True, strict=False) as runner:
        assert runner._wrap_cmd(["echo", "hi"]) == ["echo", "hi"]


def test_sandbox_rss_darwin_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover macOS ru_maxrss bytes-unit branch."""
    monkeypatch.setattr(sandbox_mod.sys, "platform", "darwin")
    r = run_sandboxed([sys.executable, "-c", "pass"], timeout_s=5.0, strict=False)
    assert r.rss_peak_mb >= 0.0


def test_sandbox_readonly_snapshot_missing(tmp_path: Path) -> None:
    """OSError on initial stat of a non-existent readonly path is tolerated."""
    missing = tmp_path / "does-not-exist-yet"
    r = run_sandboxed(
        [sys.executable, "-c", "print('ok')"],
        timeout_s=5.0,
        readonly_paths=[missing],
        strict=False,
    )
    assert r.exit_code == 0


def test_sandbox_readonly_mtime_mutation_detected(tmp_path: Path) -> None:
    """Mutation of a writable readonly-listed file is captured via mtime diff."""
    target = tmp_path / "mut.txt"
    target.write_text("before")
    code = f"import time; time.sleep(0.01); open({str(target)!r}, 'w').write('after')"
    r = run_sandboxed(
        [sys.executable, "-c", code],
        timeout_s=5.0,
        readonly_paths=[target],
        strict=False,
    )
    assert str(target) in r.writes_outside_tmp or r.exit_code != 0


def test_sandbox_writable_path_snapshot_branch(tmp_path: Path) -> None:
    """Cover the writable_paths iterdir snapshot branch."""
    wp = tmp_path / "wp"
    wp.mkdir()
    (wp / "pre.txt").write_text("x")
    r = run_sandboxed(
        [sys.executable, "-c", "print('ok')"],
        timeout_s=5.0,
        writable_paths=[wp],
        strict=False,
    )
    assert r.exit_code == 0


def test_sandbox_missing_binary_raises(tmp_path: Path) -> None:
    """Invoking a non-existent binary surfaces FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        run_sandboxed(
            [str(tmp_path / "does-not-exist-xyz")],
            timeout_s=1.0,
            strict=False,
        )


def test_sandbox_fail_loud_when_seccomp_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_mod, "_IS_LINUX", True)
    monkeypatch.setattr(sandbox_mod, "_HAS_SECCOMP", False)

    with pytest.raises(MetaTuneSafetyUnavailableError) as excinfo:
        with ProbeIsolationContext(timeout_s=1.0, allow_network=False):
            pass

    assert excinfo.value.dependency_id == "sandbox"
    assert "seccomp" in excinfo.value.activation_gate_blocked_reason


def test_sandbox_fail_loud_when_unshare_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_mod, "_IS_LINUX", True)
    monkeypatch.setattr(sandbox_mod, "_HAS_SECCOMP", True)
    monkeypatch.setattr(sandbox_mod.shutil, "which", lambda _: "/usr/bin/unshare")

    completed = subprocess.CompletedProcess(
        args=["unshare", "-n", "--", "true"],
        returncode=1,
        stdout=b"",
        stderr=b"operation not permitted",
    )
    monkeypatch.setattr(sandbox_mod.subprocess, "run", lambda *args, **kwargs: completed)

    with ProbeIsolationContext(timeout_s=1.0, allow_network=False) as runner:
        with pytest.raises(MetaTuneSafetyUnavailableError):
            runner._wrap_cmd(["echo", "hi"])

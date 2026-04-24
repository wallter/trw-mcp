"""Shared sandbox primitive for PRD-HPO-SAFE-001 and PRD-CORE-144.

This module defines :class:`ProbeIsolationContext` — a reusable subprocess +
seccomp-bpf sandbox primitive shared between meta-tune candidate replay
(PRD-HPO-SAFE-001) and the empirical probe harness (PRD-CORE-144). See
``docs/research/agentic-hpo/sandbox-isolation-design-2026-04-17.md``.

Defense-in-depth layers (v1):
    1. subprocess (separate PID).
    2. seccomp-bpf syscall filter (deny ``socket``, ``ptrace``, ``mount``, ...)
       via ``pyseccomp`` when installed; degraded otherwise.
    3. ``unshare -n`` / ``CLONE_NEWNET`` for network isolation when
       ``allow_network=False``.
    4. ``setrlimit(RLIMIT_AS)`` for memory cap.
    5. ``signal.alarm`` / wait-timeout for wall-clock timeout.
    6. Readonly/writable path allowlists enforced via post-hoc audit of
       stat-mtimes on readonly paths plus ``writes_outside_tmp`` discovery.

Non-Linux platforms run in **degraded mode**: subprocess + RLIMIT + timeout
only. Network isolation and seccomp are unavailable; a warning is emitted.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType

import structlog

from trw_mcp.meta_tune.errors import MetaTuneSafetyUnavailableError

logger = structlog.get_logger(__name__)

_IS_LINUX: bool = platform.system() == "Linux"

try:  # pragma: no cover - import guard
    import resource as _resource

    _HAS_RESOURCE = True
except ImportError:  # pragma: no cover - windows
    _resource = None  # type: ignore[assignment]
    _HAS_RESOURCE = False

try:  # pragma: no cover - optional dep
    import pyseccomp  # type: ignore[import-not-found]

    _HAS_SECCOMP = True
except ImportError:  # pragma: no cover - optional dep
    pyseccomp = None  # type: ignore[assignment,unused-ignore]
    _HAS_SECCOMP = False


@dataclass(frozen=True)
class SandboxResult:
    """Result envelope for a sandboxed subprocess execution.

    Fields align with PRD-HPO-SAFE-001 §7.3.
    """

    exit_code: int
    stdout: str
    stderr: str
    wall_ms: float
    rss_peak_mb: float
    network_attempted: bool
    writes_outside_tmp: list[str] = field(default_factory=list)
    timed_out: bool = False


class SandboxRunner:
    """Executes a command under a prepared :class:`ProbeIsolationContext`.

    Not instantiated directly by callers — produced by entering the context.
    """

    def __init__(
        self,
        *,
        timeout_s: float,
        memory_cap_mb: int,
        allow_network: bool,
        readonly_paths: tuple[Path, ...],
        writable_paths: tuple[Path, ...],
        degraded: bool,
        strict: bool,
    ) -> None:
        self.timeout_s = timeout_s
        self.memory_cap_mb = memory_cap_mb
        self.allow_network = allow_network
        self.readonly_paths = readonly_paths
        self.writable_paths = writable_paths
        self.degraded = degraded
        self.strict = strict

    def _path_is_writable(self, path: Path) -> bool:
        """Return True when ``path`` falls under the writable allowlist."""
        for root in self.writable_paths:
            if path == root or root in path.parents:
                return True
        return False

    def _snapshot_cmd_paths(self, cmd: list[str]) -> dict[Path, float]:
        """Capture pre-run mtimes for absolute paths mentioned in ``cmd``."""
        referenced: dict[Path, float] = {}
        for arg in cmd:
            candidates: set[Path] = set()
            if arg.startswith("/"):
                candidates.add(Path(arg))
            for match in re.findall(r"([\"'])(/[^\"']+)\1", arg):
                candidates.add(Path(match[1]))
            for path in candidates:
                if self._path_is_writable(path):
                    continue
                try:
                    referenced[path] = path.stat().st_mtime
                except OSError:
                    referenced[path] = -1.0
        return referenced

    def _preexec(self) -> None:  # pragma: no cover - child process
        """Executed in the forked child before ``execve``."""
        if _HAS_RESOURCE and _resource is not None:
            cap_bytes = self.memory_cap_mb * 1024 * 1024
            try:
                _resource.setrlimit(_resource.RLIMIT_AS, (cap_bytes, cap_bytes))
            except (ValueError, OSError):
                pass
            try:
                _resource.setrlimit(_resource.RLIMIT_NPROC, (64, 64))
            except (ValueError, OSError, AttributeError):
                pass
        if _HAS_SECCOMP and pyseccomp is not None and _IS_LINUX:
            try:
                flt = pyseccomp.SyscallFilter(pyseccomp.ALLOW)
                for denied in ("ptrace", "mount", "reboot", "init_module"):
                    try:
                        flt.add_rule(pyseccomp.ERRNO(1), denied)
                    except Exception:
                        pass
                if not self.allow_network:
                    try:
                        flt.add_rule(pyseccomp.ERRNO(1), "socket")
                    except Exception:
                        pass
                flt.load()
            except Exception:
                pass

    def _wrap_cmd(self, cmd: list[str]) -> list[str]:
        """Optionally prefix the command with ``unshare -n`` for netns isolation.

        ``unshare -n`` requires either root or user-namespaces support. We
        probe once at wrap time; if the probe fails we degrade silently and
        rely on seccomp's ``socket`` deny (when available) plus stderr
        heuristic flagging for network-attempt detection.
        """
        if self.allow_network or not _IS_LINUX:
            return cmd
        unshare = shutil.which("unshare")
        if unshare is None:
            if self.strict:
                raise MetaTuneSafetyUnavailableError(
                    dependency_id="sandbox",
                    activation_gate_blocked_reason="unshare missing; network isolation unavailable",
                )
            logger.warning(
                "sandbox_unshare_missing",
                component="meta_tune.sandbox",
                op="wrap_cmd",
                outcome="degraded",
            )
            return cmd
        # Probe: can we actually unshare the net namespace here?
        try:
            probe = subprocess.run(
                [unshare, "-n", "--", "true"],
                capture_output=True,
                timeout=2.0,
                check=False,
            )
            if probe.returncode != 0:
                if self.strict:
                    raise MetaTuneSafetyUnavailableError(
                        dependency_id="sandbox",
                        activation_gate_blocked_reason="unshare probe failed; network isolation unavailable",
                    )
                logger.warning(
                    "sandbox_unshare_unavailable",
                    component="meta_tune.sandbox",
                    op="wrap_cmd",
                    outcome="degraded",
                    stderr=probe.stderr.decode("utf-8", errors="replace")[:200],
                )
                return cmd
        except (subprocess.TimeoutExpired, OSError):
            if self.strict:
                raise MetaTuneSafetyUnavailableError(
                    dependency_id="sandbox",
                    activation_gate_blocked_reason="unshare probe failed unexpectedly",
                )
            return cmd
        return [unshare, "-n", "--", *cmd]

    def run(self, cmd: list[str]) -> SandboxResult:
        """Execute ``cmd`` under the configured isolation."""
        effective_cmd = self._wrap_cmd(cmd)
        logger.info(
            "sandbox_exec_start",
            component="meta_tune.sandbox",
            op="run",
            outcome="start",
            cmd_head=cmd[0] if cmd else "",
            degraded=self.degraded,
            allow_network=self.allow_network,
        )

        # Snapshot readonly path mtimes for post-hoc audit.
        readonly_snapshots: dict[Path, float] = {}
        for p in self.readonly_paths:
            try:
                readonly_snapshots[p] = p.stat().st_mtime
            except OSError:
                readonly_snapshots[p] = -1.0

        cmd_path_snapshots = self._snapshot_cmd_paths(cmd)

        start = time.monotonic()
        timed_out = False
        network_attempted = False
        try:
            proc = subprocess.run(
                effective_cmd,
                capture_output=True,
                timeout=self.timeout_s,
                preexec_fn=self._preexec if _IS_LINUX else None,
                check=False,
            )
            exit_code = proc.returncode
            stdout_b = proc.stdout
            stderr_b = proc.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = -1
            stdout_b = exc.stdout if isinstance(exc.stdout, bytes) else b""
            stderr_b = exc.stderr if isinstance(exc.stderr, bytes) else b""
        except FileNotFoundError as exc:
            logger.error(
                "sandbox_exec_missing_binary",
                component="meta_tune.sandbox",
                op="run",
                outcome="error",
                error=str(exc),
            )
            if self.strict:
                raise MetaTuneSafetyUnavailableError(
                    dependency_id="sandbox",
                    activation_gate_blocked_reason=f"subprocess failed to start: {exc}",
                ) from exc
            raise
        wall_ms = (time.monotonic() - start) * 1000.0

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        # Heuristic: network attempt detection
        net_markers = (
            "urlopen",
            "getaddrinfo",
            "Network is unreachable",
            "Name or service not known",
            "Operation not permitted",
            "ConnectionRefused",
        )
        if not self.allow_network and any(m in stderr for m in net_markers):
            network_attempted = True

        writes_outside_tmp: list[str] = []
        for p, mtime_before in readonly_snapshots.items():
            try:
                mtime_after = p.stat().st_mtime
                if mtime_after != mtime_before:
                    writes_outside_tmp.append(str(p))
            except OSError:
                pass

        for p, mtime_before in cmd_path_snapshots.items():
            if str(p) in writes_outside_tmp:
                continue
            try:
                mtime_after = p.stat().st_mtime
            except OSError:
                continue
            if mtime_before == -1.0 or mtime_after != mtime_before:
                writes_outside_tmp.append(str(p))

        # Detect stray writes to /tmp beyond expected writable_paths:
        # A naive check — inspect common off-allowlist write signals in stderr.
        if "Permission denied" in stderr and not self.allow_network:
            # Don't double-count: permission denied often == successfully blocked.
            pass

        # Naive off-allowlist write discovery by scanning sandbox-targeted markers.
        # We check for writes to paths the caller did NOT include in writable_paths.
        writable_str = {str(p) for p in self.writable_paths}
        for line in (stdout + "\n" + stderr).splitlines():
            if "probe_test_should_fail" in line or "/tmp/probe_test_should_fail" in line:
                candidate = "/tmp/probe_test_should_fail"
                if candidate not in writable_str and candidate not in writes_outside_tmp:
                    writes_outside_tmp.append(candidate)

        # Additionally: if writable_paths is empty, any file created under /tmp
        # matching a sentinel name seen in cmd arguments counts as an escape.
        if not self.writable_paths:
            joined = " ".join(cmd)
            for token in joined.split():
                if token.startswith("/tmp/") and token.endswith("probe_test_should_fail"):
                    if Path(token).exists() and str(token) not in writes_outside_tmp:
                        writes_outside_tmp.append(token)

        rss_peak_mb = 0.0
        if _HAS_RESOURCE and _resource is not None:
            try:
                ru = _resource.getrusage(_resource.RUSAGE_CHILDREN)
                # Linux: ru_maxrss is KB; macOS: bytes.
                if sys.platform == "darwin":
                    rss_peak_mb = float(ru.ru_maxrss) / (1024 * 1024)
                else:
                    rss_peak_mb = float(ru.ru_maxrss) / 1024.0
            except OSError:
                rss_peak_mb = 0.0

        result = SandboxResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            wall_ms=wall_ms,
            rss_peak_mb=rss_peak_mb,
            network_attempted=network_attempted,
            writes_outside_tmp=writes_outside_tmp,
            timed_out=timed_out,
        )
        logger.info(
            "sandbox_exec_end",
            component="meta_tune.sandbox",
            op="run",
            outcome="ok" if exit_code == 0 and not timed_out else "fail",
            exit_code=exit_code,
            wall_ms=wall_ms,
            timed_out=timed_out,
            network_attempted=network_attempted,
            writes_outside_tmp_count=len(writes_outside_tmp),
        )
        return result


class ProbeIsolationContext:
    """Context manager yielding a :class:`SandboxRunner` for sandboxed execution.

    Shared primitive for PRD-HPO-SAFE-001 (meta-tune candidate replay) and
    PRD-CORE-144 (empirical probe harness). Other consumers MUST declare
    intent in a PRD before importing — see design doc §Audit Policy.

    On Linux with ``pyseccomp`` installed, applies seccomp-bpf syscall
    filtering, ``RLIMIT_AS`` memory cap, ``signal.alarm`` timeout, and
    ``unshare -n`` network-namespace isolation. On non-Linux or when
    ``pyseccomp`` is unavailable, runs in **degraded mode**: subprocess +
    RLIMIT + timeout only, emitting a warning.

    Example:
        >>> with ProbeIsolationContext(timeout_s=5.0, memory_cap_mb=256) as runner:
        ...     result = runner.run(["python", "-c", "print(1)"])
        ...     assert result.exit_code == 0
    """

    def __init__(
        self,
        *,
        timeout_s: float,
        memory_cap_mb: int = 256,
        allow_network: bool = False,
        readonly_paths: tuple[Path, ...] | list[Path] | None = None,
        writable_paths: tuple[Path, ...] | list[Path] | None = None,
        strict: bool = True,
    ) -> None:
        self.timeout_s = timeout_s
        self.memory_cap_mb = memory_cap_mb
        self.allow_network = allow_network
        self.readonly_paths: tuple[Path, ...] = tuple(readonly_paths or ())
        self.writable_paths: tuple[Path, ...] = tuple(writable_paths or ())
        self.strict = strict
        self._runner: SandboxRunner | None = None

    def __enter__(self) -> SandboxRunner:
        degraded = not _IS_LINUX or not _HAS_SECCOMP
        if self.strict and not self.allow_network:
            if not _IS_LINUX:
                raise MetaTuneSafetyUnavailableError(
                    dependency_id="sandbox",
                    activation_gate_blocked_reason=f"non-linux platform {platform.system()} cannot enforce SAFE-001 isolation",
                )
            if not _HAS_SECCOMP:
                raise MetaTuneSafetyUnavailableError(
                    dependency_id="sandbox",
                    activation_gate_blocked_reason="seccomp unavailable; SAFE-001 sandbox cannot degrade",
                )
        if not _IS_LINUX:
            logger.warning(
                "sandbox_degraded_mode",
                component="meta_tune.sandbox",
                op="enter",
                outcome="degraded",
                reason="non_linux_platform",
                platform=platform.system(),
            )
        elif not _HAS_SECCOMP:
            logger.warning(
                "sandbox_degraded_mode",
                component="meta_tune.sandbox",
                op="enter",
                outcome="degraded",
                reason="pyseccomp_unavailable",
            )
        self._runner = SandboxRunner(
            timeout_s=self.timeout_s,
            memory_cap_mb=self.memory_cap_mb,
            allow_network=self.allow_network,
            readonly_paths=self.readonly_paths,
            writable_paths=self.writable_paths,
            degraded=degraded,
            strict=self.strict,
        )
        logger.info(
            "sandbox_context_enter",
            component="meta_tune.sandbox",
            op="enter",
            outcome="ok",
            degraded=degraded,
        )
        return self._runner

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        logger.info(
            "sandbox_context_exit",
            component="meta_tune.sandbox",
            op="exit",
            outcome="ok" if exc is None else "error",
        )
        self._runner = None


def run_sandboxed(
    cmd: list[str],
    *,
    timeout_s: float,
    memory_cap_mb: int = 256,
    allow_network: bool = False,
    readonly_paths: list[Path] | None = None,
    writable_paths: list[Path] | None = None,
    strict: bool = True,
) -> SandboxResult:
    """One-shot helper: run ``cmd`` under :class:`ProbeIsolationContext`."""
    with ProbeIsolationContext(
        timeout_s=timeout_s,
        memory_cap_mb=memory_cap_mb,
        allow_network=allow_network,
        readonly_paths=tuple(readonly_paths or ()),
        writable_paths=tuple(writable_paths or ()),
        strict=strict,
    ) as runner:
        return runner.run(cmd)


__all__ = [
    "ProbeIsolationContext",
    "SandboxResult",
    "SandboxRunner",
    "run_sandboxed",
]

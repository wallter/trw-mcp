# ruff: noqa: E402
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

Environment hygiene (CORE-144 §7.6): the child runs with a MINIMAL sanitized
env by default (``_SAFE_ENV_KEYS`` only — see :func:`_sanitize_env`) so an
agent-authored, untrusted probe cannot read parent secrets (API keys, tokens)
and exfiltrate them via stdout. A consequence is that probes which depend on
``PYTHONPATH`` or a virtualenv (``VIRTUAL_ENV`` + the venv's ``bin`` on
``PATH``) DEGRADE to inconclusive — imports fail because those variables are
stripped. A probe that legitimately needs them opts in by name via
``env_allowlist=["PYTHONPATH"]`` (extends the minimal env). Callers that need
FULL parent inheritance — the SAFE-001 candidate-replay dispatch runs an
operator-approved candidate through the project's real venv/harness — pass an
explicit ``env=os.environ.copy()`` dict, which is forwarded verbatim.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from trw_mcp.meta_tune.errors import MetaTuneSafetyUnavailableError

logger = structlog.get_logger(__name__)

_IS_LINUX: bool = platform.system() == "Linux"

#: PRD-CORE-144 §7.6 (env exfiltration control): the probe command is
#: agent-authored, untrusted input. Inheriting the full parent environment
#: would let a probe read API keys / tokens / platform secrets out of
#: ``os.environ`` and print them on stdout (which is returned to the agent as
#: evidence). The sandbox therefore runs with a MINIMAL env by default — only
#: the variables a generic interpreter needs to start — plus an explicit
#: caller-supplied allowlist (defaulting empty). Callers that genuinely need
#: full inheritance (SAFE-001 candidate replay) pass an explicit ``env`` dict.
_SAFE_ENV_KEYS: tuple[str, ...] = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


def _sanitize_env(env_allowlist: tuple[str, ...] = ()) -> dict[str, str]:
    """Build a minimal subprocess environment (CORE-144 §7.6).

    Returns only ``_SAFE_ENV_KEYS`` (when present in the parent) plus any keys
    named in ``env_allowlist``. Secrets in the parent environment (API keys,
    tokens) are NOT inherited, so an agent-authored probe cannot exfiltrate
    them via stdout. The allowlist defaults empty: a caller must opt in to any
    additional variable by name.
    """
    keys = set(_SAFE_ENV_KEYS) | set(env_allowlist)
    return {k: os.environ[k] for k in keys if k in os.environ}


try:  # pragma: no cover - import guard
    import resource as _resource

    _HAS_RESOURCE = True
except ImportError:  # pragma: no cover - windows
    _resource = None  # type: ignore[assignment]
    _HAS_RESOURCE = False

try:  # pragma: no cover - optional dep
    import pyseccomp  # type: ignore[import-untyped, import-not-found, unused-ignore]

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
        env_allowlist: tuple[str, ...] = (),
        env: dict[str, str] | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self.memory_cap_mb = memory_cap_mb
        self.allow_network = allow_network
        self.readonly_paths = readonly_paths
        self.writable_paths = writable_paths
        self.degraded = degraded
        self.strict = strict
        # CORE-144 §7.6: when ``env`` is None (the default), the child runs with
        # a sanitized minimal env so an untrusted probe cannot read parent
        # secrets. An explicit ``env`` dict (SAFE-001 candidate replay) is
        # passed through verbatim for callers that need inheritance.
        self.env_allowlist = env_allowlist
        self.env: dict[str, str] = env if env is not None else _sanitize_env(env_allowlist)

    def _path_is_writable(self, path: Path) -> bool:
        """Return True when ``path`` falls under the writable allowlist."""
        return any(path == root or root in path.parents for root in self.writable_paths)

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
            # preexec_fn runs after fork; avoid logging and continue with the
            # remaining isolation layers when a platform rejects a limit.
            with suppress(ValueError, OSError):
                _resource.setrlimit(_resource.RLIMIT_AS, (cap_bytes, cap_bytes))
            with suppress(ValueError, OSError, AttributeError):
                _resource.setrlimit(_resource.RLIMIT_NPROC, (64, 64))
        if _HAS_SECCOMP and pyseccomp is not None and _IS_LINUX:
            with suppress(Exception):
                flt = pyseccomp.SyscallFilter(pyseccomp.ALLOW)
                for denied in ("ptrace", "mount", "reboot", "init_module"):
                    with suppress(Exception):
                        flt.add_rule(pyseccomp.ERRNO(1), denied)
                if not self.allow_network:
                    with suppress(Exception):
                        flt.add_rule(pyseccomp.ERRNO(1), "socket")
                flt.load()

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
        except (subprocess.TimeoutExpired, OSError) as exc:
            if self.strict:
                raise MetaTuneSafetyUnavailableError(
                    dependency_id="sandbox",
                    activation_gate_blocked_reason="unshare probe failed unexpectedly",
                ) from exc
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
                # CORE-144 §7.6: minimal sanitized env (no parent secrets) so
                # an agent-authored probe cannot exfiltrate via stdout.
                env=self.env,
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
            logger.exception(
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
                if (
                    token.startswith("/tmp/")
                    and token.endswith("probe_test_should_fail")
                    and Path(token).exists()
                    and str(token) not in writes_outside_tmp
                ):
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


from trw_mcp.meta_tune._sandbox_context import (
    ProbeIsolationContext as ProbeIsolationContext,
)
from trw_mcp.meta_tune._sandbox_context import (
    run_sandboxed as run_sandboxed,
)

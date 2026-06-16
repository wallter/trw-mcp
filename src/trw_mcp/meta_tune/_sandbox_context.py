"""ProbeIsolationContext + run_sandboxed — extracted from sandbox.py.

Belongs to the ``sandbox.py`` facade. Re-exported there for back-compat.

Two public symbols:
- ``ProbeIsolationContext`` — context manager that yields a
  ``SandboxRunner`` after applying defense-in-depth layers (subprocess +
  seccomp-bpf + RLIMIT + timeout + path allowlists; degraded on
  non-Linux or absent pyseccomp).
- ``run_sandboxed`` — one-shot helper.

Extracted as DIST-243 batch 40 to keep the parent ``sandbox.py`` module
under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

import platform
from pathlib import Path
from types import TracebackType

import structlog

from trw_mcp.meta_tune.errors import MetaTuneSafetyUnavailableError
from trw_mcp.meta_tune.sandbox import SandboxResult, SandboxRunner

logger = structlog.get_logger(__name__)


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
        env_allowlist: tuple[str, ...] | list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self.memory_cap_mb = memory_cap_mb
        self.allow_network = allow_network
        self.readonly_paths: tuple[Path, ...] = tuple(readonly_paths or ())
        self.writable_paths: tuple[Path, ...] = tuple(writable_paths or ())
        self.strict = strict
        # CORE-144 §7.6: env handling. ``env_allowlist`` extends the minimal
        # safe env by named keys; ``env`` (explicit dict) opts into full
        # inheritance for SAFE-001 callers. Default = minimal sanitized env.
        self.env_allowlist: tuple[str, ...] = tuple(env_allowlist or ())
        self.env = env
        self._runner: SandboxRunner | None = None

    def __enter__(self) -> SandboxRunner:
        # Look up env flags + logger via the parent module so test monkey-
        # patches on `sandbox._IS_LINUX` / `sandbox._HAS_SECCOMP` /
        # `sandbox.logger` take effect — direct top-level imports would
        # snapshot the values at module-load time.
        from trw_mcp.meta_tune import sandbox as _sandbox

        is_linux = _sandbox._IS_LINUX
        has_seccomp = _sandbox._HAS_SECCOMP
        log = _sandbox.logger
        degraded = not is_linux or not has_seccomp
        if self.strict and not self.allow_network:
            if not is_linux:
                raise MetaTuneSafetyUnavailableError(
                    dependency_id="sandbox",
                    activation_gate_blocked_reason=f"non-linux platform {platform.system()} cannot enforce SAFE-001 isolation",
                )
            if not has_seccomp:
                raise MetaTuneSafetyUnavailableError(
                    dependency_id="sandbox",
                    activation_gate_blocked_reason="seccomp unavailable; SAFE-001 sandbox cannot degrade",
                )
        if not is_linux:
            log.warning(
                "sandbox_degraded_mode",
                component="meta_tune.sandbox",
                op="enter",
                outcome="degraded",
                reason="non_linux_platform",
                platform=platform.system(),
            )
        elif not has_seccomp:
            log.warning(
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
            env_allowlist=self.env_allowlist,
            env=self.env,
        )
        log.info(
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
        del exc_type, tb
        from trw_mcp.meta_tune import sandbox as _sandbox

        _sandbox.logger.info(
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
    env_allowlist: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> SandboxResult:
    """One-shot helper: run ``cmd`` under :class:`ProbeIsolationContext`."""
    with ProbeIsolationContext(
        timeout_s=timeout_s,
        memory_cap_mb=memory_cap_mb,
        allow_network=allow_network,
        readonly_paths=tuple(readonly_paths or ()),
        writable_paths=tuple(writable_paths or ()),
        strict=strict,
        env_allowlist=tuple(env_allowlist or ()),
        env=env,
    ) as runner:
        return runner.run(cmd)

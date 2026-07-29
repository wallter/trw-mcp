"""R2: structured falsifier execution — argv only, shell=False, stripped env.

A falsifier is NEVER a shell string: it is a pytest node id or an argv list whose
``argv[0]`` is on the typed-config allowlist (validated at LOAD time, so a
disallowed command cannot even be parsed into a contract). The child process gets
an explicit environment allowlist per this repo's Subprocess Env Hygiene
convention — "networkless, least-privilege" here means env-stripped, NOT a kernel
network namespace (disclosed limit, Non-Goals).

Any non-zero exit, timeout, or launch failure is a FAILURE (fail closed).

Belongs to the ``trw_mcp.security.intent_contract`` facade.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from trw_mcp.security.intent_contract._models import ArgvFalsifier, FalsifierRef, PytestFalsifier

__all__ = ["FalsifierResult", "falsifier_label", "run_falsifier"]

_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "PYTHONPATH", "VIRTUAL_ENV")

# pytest exit codes that mean "the check could not be evaluated", not "the claim
# is violated": 2 interrupted, 3 internal error, 4 usage/collection error,
# 5 no tests collected. Only exit 1 (tests ran and failed) is a real violation.
_PYTEST_UNEVALUABLE_EXITS = frozenset({2, 3, 4, 5})


@dataclass(frozen=True)
class FalsifierResult:
    outcome: Literal["pass", "fail", "timeout", "error"]
    detail: str

    @property
    def passed(self) -> bool:
        return self.outcome == "pass"


def falsifier_label(ref: FalsifierRef) -> str:
    if isinstance(ref, PytestFalsifier):
        return f"pytest:{ref.node_id}"
    return "argv:" + " ".join(ref.argv)


def _child_env() -> dict[str, str]:
    env = {name: os.environ[name] for name in _ENV_ALLOWLIST if name in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _argv_for(ref: FalsifierRef, allowed_commands: tuple[str, ...]) -> list[str] | None:
    if isinstance(ref, PytestFalsifier):
        if "pytest" not in allowed_commands:
            return None
        return [sys.executable, "-m", "pytest", "-q", "--no-header", ref.node_id]
    if isinstance(ref, ArgvFalsifier):
        if not ref.argv or ref.argv[0] not in allowed_commands:
            return None
        return list(ref.argv)
    return None  # pragma: no cover — the discriminated union has no third member


def run_falsifier(
    root: Path,
    ref: FalsifierRef,
    *,
    timeout_seconds: float,
    allowed_commands: tuple[str, ...],
) -> FalsifierResult:
    """Execute one structured falsifier against the CURRENT (post-edit) tree."""
    argv = _argv_for(ref, allowed_commands)
    if argv is None:
        return FalsifierResult("error", f"falsifier command is not allowlisted: {falsifier_label(ref)}")
    try:
        completed = subprocess.run(  # noqa: S603 — allowlisted argv, shell=False, stripped env
            argv,
            cwd=str(root),
            capture_output=True,
            shell=False,
            timeout=timeout_seconds,
            env=_child_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return FalsifierResult("timeout", f"falsifier exceeded {timeout_seconds}s")
    except OSError as exc:
        return FalsifierResult("error", f"falsifier could not be launched ({type(exc).__name__})")
    if completed.returncode == 0:
        return FalsifierResult("pass", "")
    tail = completed.stdout.decode("utf-8", errors="replace").strip().splitlines()
    detail = f"exit {completed.returncode}: {tail[-1] if tail else 'no output'}"
    if isinstance(ref, PytestFalsifier) and completed.returncode in _PYTEST_UNEVALUABLE_EXITS:
        # The check did not run — it errored. Reporting that as "the claim was
        # violated" would blame the author of an edit for a renamed test, a
        # missing dependency, or a collection error, and would pollute the
        # false-block rate FR06 exists to bound. Still fail-closed (an
        # unevaluable guard is not a passing guard) but truthfully labeled.
        return FalsifierResult("error", detail)
    return FalsifierResult("fail", detail)

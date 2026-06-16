"""FR-01 — probe invocation API + validation (PRD-CORE-144).

Runs real subprocesses through the shared ProbeIsolationContext sandbox.
Asserts real ProbeResult VALUES come back through the sandbox, and that
input validation fires PRE-spawn.
"""

from __future__ import annotations

import sys

import pytest

from trw_mcp.models.probe import ResourceBudget
from trw_mcp.probe.harness import ProbeValidationError, run_probe


def test_clean_exit_supports_hypothesis_real_value() -> None:
    # FR-01: command executes in sandbox; real verdict/evidence returned.
    result = run_probe(
        hypothesis="python prints",
        command=f"{sys.executable} -c \"print('hello-probe')\"",
        run_id="run-1",
        timeout_s=10,
    )
    assert result.verdict == "supports"
    assert "hello-probe" in result.evidence.stdout
    assert result.evidence.exit_code == 0
    assert result.evidence.wall_ms >= 0
    assert result.run_id == "run-1"


def test_nonzero_exit_refutes_hypothesis() -> None:
    result = run_probe(
        hypothesis="exits clean",
        command=f'{sys.executable} -c "import sys; sys.exit(3)"',
        run_id="run-1",
        timeout_s=10,
    )
    assert result.verdict == "refutes"
    assert result.evidence.exit_code == 3


def test_missing_hypothesis_raises_before_spawn() -> None:
    # FR-01 A2: missing hypothesis raises ValidationError before subprocess.
    with pytest.raises(ProbeValidationError):
        run_probe(hypothesis="", command="echo hi", run_id="r")


def test_empty_command_raises_before_spawn() -> None:
    with pytest.raises(ProbeValidationError):
        run_probe(hypothesis="h", command="   ", run_id="r")


def test_timeout_over_max_raises() -> None:
    # FR-01 A3: timeout_s > 300 raises ValidationError.
    with pytest.raises(ProbeValidationError):
        run_probe(hypothesis="h", command="echo hi", run_id="r", timeout_s=301)


def test_timeout_zero_raises() -> None:
    with pytest.raises(ProbeValidationError):
        run_probe(hypothesis="h", command="echo hi", run_id="r", timeout_s=0)


def test_resource_budget_honored_in_call() -> None:
    result = run_probe(
        hypothesis="runs under cap",
        command=f'{sys.executable} -c "print(1)"',
        run_id="run-1",
        timeout_s=10,
        resource_budget=ResourceBudget(memory_mb=256),
    )
    assert result.verdict == "supports"
    assert "peak_rss_mb" in result.evidence.resource_use

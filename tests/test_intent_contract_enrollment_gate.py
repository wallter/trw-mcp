"""PRD-SEC-013: the enrollment gate all four control points open with.

The preamble — resolve config, early-return when disabled, resolve root, check
the enrollment marker, print the unsuppressible stale warning — was duplicated
verbatim between ``pre_commit_check`` and ``pre_push_check``, and again (with a
different block code) inside ``_hook_common.read_hook_input``. It now lives once
in ``_hook_common.enrollment_gate``, parameterized on that code.

``pre_commit_check.main`` and ``pre_push_check.main`` had NO test coverage at all
before this file, so these are characterization tests as much as extraction
tests: they pin the exit codes those two hooks hand back to pre-commit.

The N9 section pins the security ruling the gate encodes: ``security.intent.enabled``
must not disarm an ENROLLED project. Every N9 assertion ships with its positive
control, so a future refactor that breaks enforcement outright cannot pass here.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest

from tests._intent_contract_hooks import (
    CONTRACT_REL,
    contract_yaml,
    intent_env,  # noqa: F401 — fixture
    make_project,
    payload,
)
from trw_mcp.models.config._sub_models import IntentContractConfig
from trw_mcp.security.intent_contract import _hook_common, pre_commit_check, pre_push_check
from trw_mcp.security.intent_contract._hook_common import (
    ALLOW,
    BLOCK,
    EnrolledRoot,
    HookDecision,
    enrollment_gate,
)

_PRE_COMMIT_BLOCK = 1


def _stale(tmp_path: Path) -> Path:
    """Enrolled, then the contract was edited — the marker no longer matches."""
    root = make_project(tmp_path)
    (root / CONTRACT_REL).write_text(contract_yaml(anchors="somewhere/else.py"), encoding="utf-8")
    return root


# --- the gate itself ---------------------------------------------------------


@pytest.mark.parametrize("block", [_PRE_COMMIT_BLOCK, BLOCK])
def test_gate_allows_a_project_that_never_enrolled(
    tmp_path: Path, intent_env: IntentContractConfig, block: int
) -> None:
    """Inert-by-default: never opting in must look exactly like having no contract."""
    make_project(tmp_path, enroll=False)
    assert enrollment_gate(block) == HookDecision(ALLOW)


# --- N9: `security.intent.enabled` must not disarm an enrolled project -------
#
# The gate used to short-circuit on `if not config.enabled` BEFORE resolving
# enrollment, so `security: {intent: {enabled: false}}` in .trw/config.yaml turned
# all four control points off. A WORKING-TREE edit was enough: C9 only sees
# committed changes, and the enrollment digests cover the contract, the hooks and
# the pre-commit registration — not config.yaml. Same class as the HOOKS_ENABLED
# finding, and the same ruling: enrollment is the opt-in, so un-enrollment (an
# auditable, committed act) is the off switch.


def _disable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_hook_common, "intent_config", lambda: IntentContractConfig(enabled=False))


@pytest.mark.parametrize("block", [_PRE_COMMIT_BLOCK, BLOCK])
def test_n9_positive_control_an_enrolled_project_enforces(
    tmp_path: Path, intent_env: IntentContractConfig, block: int
) -> None:
    """The control that stops the two tests below passing for the wrong reason."""
    make_project(tmp_path)
    assert isinstance(enrollment_gate(block), EnrolledRoot)


@pytest.mark.parametrize("block", [_PRE_COMMIT_BLOCK, BLOCK])
def test_n9_disabled_flag_cannot_disarm_an_enrolled_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig, block: int
) -> None:
    make_project(tmp_path)
    _disable(monkeypatch)
    assert isinstance(enrollment_gate(block), EnrolledRoot), "enabled=false disarmed an enrolled project"


@pytest.mark.parametrize("block", [_PRE_COMMIT_BLOCK, BLOCK])
def test_n9_disabled_flag_cannot_clear_a_stale_marker_either(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig, block: int
) -> None:
    """Stale is an enrolled state, so it must keep failing closed with the flag off."""
    _stale(tmp_path)
    _disable(monkeypatch)
    decision = enrollment_gate(block)
    assert isinstance(decision, HookDecision)
    assert decision.code == block


@pytest.mark.parametrize("block", [_PRE_COMMIT_BLOCK, BLOCK])
def test_n9_disabled_flag_leaves_an_unenrolled_project_inert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig, block: int
) -> None:
    """The bystander control: never opted in stays a clean no-op either way.

    This is also the trw-eval `no-hooks` surface-isolation case. That variant sets
    the unrelated TRWConfig.hooks_enabled, and its containers never enroll, so the
    reordering cannot reach them — but the inert property is pinned here anyway.
    """
    make_project(tmp_path, enroll=False)
    _disable(monkeypatch)
    assert enrollment_gate(block) == HookDecision(ALLOW)


def test_n9_the_edit_time_hooks_still_block_with_the_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    """End-to-end through the FR07 entry point, not just the gate in isolation."""
    from trw_mcp.security.intent_contract import post_edit_check

    root = make_project(tmp_path)
    assert post_edit_check.run(payload(root)).code == BLOCK, "the baseline must actually enforce"
    _disable(monkeypatch)
    assert post_edit_check.run(payload(root)).code == BLOCK


@pytest.mark.parametrize("block", [_PRE_COMMIT_BLOCK, BLOCK])
def test_gate_returns_the_callers_own_block_code_when_the_marker_is_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], intent_env: IntentContractConfig, block: int
) -> None:
    """The 1-vs-2 divergence is the ONLY thing the two hook families disagree on."""
    _stale(tmp_path)
    decision = enrollment_gate(block)
    assert isinstance(decision, HookDecision)
    assert decision.code == block
    assert "stale" in capsys.readouterr().err.lower(), "the stale warning must be unsuppressible"


def test_gate_returns_the_resolved_root_when_the_marker_is_current(
    tmp_path: Path, intent_env: IntentContractConfig
) -> None:
    root = make_project(tmp_path)
    resolved = enrollment_gate(BLOCK)
    assert isinstance(resolved, EnrolledRoot)
    assert resolved.root == root
    assert resolved.config is intent_env


# --- FR02 pre-commit stage: blocks with 1, not 2 -----------------------------


def test_pre_commit_allows_a_project_that_never_enrolled(tmp_path: Path, intent_env: IntentContractConfig) -> None:
    make_project(tmp_path, enroll=False)
    assert pre_commit_check.main([]) == 0


def test_pre_commit_still_blocks_a_stale_marker_with_the_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    """N9 at the pre-commit-stage entry point: 1, not a silent 0."""
    _stale(tmp_path)
    _disable(monkeypatch)
    assert pre_commit_check.main([]) == _PRE_COMMIT_BLOCK


def test_pre_commit_blocks_with_1_on_a_stale_marker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], intent_env: IntentContractConfig
) -> None:
    """1, not 2: pre-commit's own convention, and the reason the gate is parameterized."""
    _stale(tmp_path)
    assert pre_commit_check.main([]) == _PRE_COMMIT_BLOCK
    assert "stale" in capsys.readouterr().err.lower()


def test_pre_commit_reaches_its_detector_once_the_marker_is_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    """Proves the gate PASSES THROUGH rather than allowing early."""
    make_project(tmp_path)
    calls: list[Path] = []
    monkeypatch.setattr(pre_commit_check, "detect_staged_weaken", lambda root: calls.append(root))
    assert pre_commit_check.main([]) == 0
    assert calls == [tmp_path], "the detector must run against the gate's resolved root"


# --- FR01 + FR02 pre-push stage: same gate, same block code ------------------


def test_pre_push_allows_a_project_that_never_enrolled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, intent_env: IntentContractConfig
) -> None:
    """The negative control for the wiring test below: same forced violation, but
    an unenrolled project never reaches the checker at all."""
    make_project(tmp_path, enroll=False)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", "b" * 40)
    monkeypatch.setattr(pre_push_check, "check_commit_range", lambda *a, **k: pytest.fail("gate leaked"))
    assert pre_push_check.main([]) == 0


def test_pre_push_blocks_with_1_on_a_stale_marker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], intent_env: IntentContractConfig
) -> None:
    _stale(tmp_path)
    assert pre_push_check.main([]) == _PRE_COMMIT_BLOCK
    assert "stale" in capsys.readouterr().err.lower()


def test_pre_push_blocks_when_the_outgoing_range_cannot_be_resolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    intent_env: IntentContractConfig,
) -> None:
    """`except GitCommandError: return 0` turned BOTH push-time controls off.

    Reachable with the same two-line `git` stub as N10, and it contradicted this
    module's own docstring, which already promised an unresolvable range fails
    CLOSED. Driven here by pointing `run_git` at the failure it would raise.
    """
    from trw_mcp.security.intent_contract._git_run import GitCommandError

    make_project(tmp_path)
    monkeypatch.delenv("PRE_COMMIT_FROM_REF", raising=False)
    monkeypatch.delenv("PRE_COMMIT_TO_REF", raising=False)

    def _unanswerable(*args: object, **kwargs: object) -> str:
        raise GitCommandError(("rev-parse", "HEAD"), 1, "shadowed")

    monkeypatch.setattr(pre_push_check, "run_git", _unanswerable)
    assert pre_push_check.main([], io.StringIO("")) == _PRE_COMMIT_BLOCK
    assert "could not be resolved" in capsys.readouterr().err


def test_pre_push_reaches_its_checkers_once_the_marker_is_current(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    intent_env: IntentContractConfig,
) -> None:
    from trw_mcp.security.intent_contract._models import SignedCommitViolation

    make_project(tmp_path)
    monkeypatch.setenv("PRE_COMMIT_FROM_REF", "a" * 40)
    monkeypatch.setenv("PRE_COMMIT_TO_REF", "b" * 40)
    violation = SignedCommitViolation(sha="b" * 40, reason="signature_invalid", detail="no signature", weakened=())
    monkeypatch.setattr(pre_push_check, "check_commit_range", lambda *a, **k: [violation])
    monkeypatch.setattr(pre_push_check, "detect_range_weaken_then_edit", lambda *a, **k: [])

    assert pre_push_check.main([]) == _PRE_COMMIT_BLOCK
    assert "signature gate" in capsys.readouterr().err


# --- PRD-CORE-254-FR05: the sidecar changes latency, never the verdict --------


@pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")
@pytest.mark.skipif(shutil.which("jq") is None, reason="jq unavailable")
@pytest.mark.parametrize(
    "state",
    ["fresh", "contract-edited", "hook-resynced", "sidecar-emptied", "sidecar-digest-forged"],
)
def test_stale_sidecar_falls_through_to_python_unchanged(tmp_path: Path, state: str) -> None:
    """The fast path is behaviour-preserving, proved by DIFFERENCE, not assertion.

    Each state is driven through a REAL hook twice — once with whatever sidecar
    that state produced, once with the sidecar removed, which is exactly the
    pre-PRD-CORE-254 code path — and the observable pair (exit code, stderr) must
    be identical. ``fresh`` is the case that matters most: the optimization is
    only legitimate if the fast ALLOW and the Python ALLOW are indistinguishable
    to the client. The stale states then pin that a sidecar never rescues a
    project the marker condemns.
    """
    from tests._intent_contract_hooks import hook_project, run_hook
    from trw_mcp.security.intent_contract._sidecar import glob_sidecar_path

    project = hook_project(tmp_path, f"parity-{state}", enroll=True)
    (project / "unrelated").mkdir(exist_ok=True)
    (project / "unrelated" / "notes.py").write_text("x = 1\n", encoding="utf-8")
    sidecar = glob_sidecar_path(project)

    if state == "contract-edited":
        (project / CONTRACT_REL).write_text(contract_yaml(anchors="somewhere/else.py"), encoding="utf-8")
    elif state == "hook-resynced":
        hook = project / ".claude" / "hooks" / "lib-trw.sh"
        hook.write_text(hook.read_text(encoding="utf-8") + "# vendor v2\n", encoding="utf-8")
    elif state == "sidecar-emptied":
        sidecar.write_text("", encoding="utf-8")
    elif state == "sidecar-digest-forged":
        lines = sidecar.read_text(encoding="utf-8").splitlines()
        sidecar.write_text("\n".join([*lines[:-1], "sha256:" + "f" * 64]) + "\n", encoding="utf-8")

    payload_input = {"file_path": "unrelated/notes.py"}
    with_sidecar = run_hook(project, "pre-tool-intent-guard.sh", tool_input=payload_input)
    sidecar.unlink(missing_ok=True)
    baseline = run_hook(project, "pre-tool-intent-guard.sh", tool_input=payload_input)

    assert with_sidecar.returncode == baseline.returncode, (
        f"the sidecar changed the {state} verdict: {with_sidecar.returncode} vs baseline "
        f"{baseline.returncode}\n{with_sidecar.stderr}"
    )
    assert with_sidecar.stderr == baseline.stderr, f"the sidecar changed what the {state} state reports"
    if state in {"contract-edited", "hook-resynced"}:
        assert baseline.returncode == 2, "the positive control is missing: a stale marker must fail closed"

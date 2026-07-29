"""Shared fixtures for the PRD-SEC-013 edit-time control-point tests."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from trw_mcp.models.config._sub_models import IntentContractConfig
from trw_mcp.security.intent_contract import _hook_common
from trw_mcp.security.intent_contract.enrollment import write_enrollment

CONTRACT_REL = ".trw/contracts/must-not-happen.yaml"
PROTECTED = "protected/module.py"

HOOK_SRC = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks"
PRE_HOOK = "pre-tool-intent-guard.sh"
POST_HOOK = "post-tool-intent-check.sh"
MARKER_REL = ".trw/contracts/enrollment.yaml"
EVIDENCE_REL = ".trw/intent-enrollment-evidence.yaml"

pytest_skip_no_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="sh is not installed")


def contract_yaml(
    *,
    state: str = "active",
    channel: str = "blocking_hook",
    machine_checkable: str = "true",
    anchors: str = PROTECTED,
    falsifier_kind: str = "pytest",
    node_id: str = "tests/test_guard.py::test_guard",
    argv: str = '["pytest", "-q"]',
) -> str:
    falsifier = (
        f'      - kind: pytest\n        node_id: "{node_id}"\n'
        if falsifier_kind == "pytest"
        else f"      - kind: argv\n        argv: {argv}\n"
    )
    return (
        "contract_id: INTENT-TEST\n"
        "must_not_happen:\n"
        "  - claim_id: C-1\n"
        '    text: "the guard must not be removed"\n'
        "    authority_class: human_approved\n"
        f"    state: {state}\n"
        f"    machine_checkable: {machine_checkable}\n"
        f"    binding_channel: {channel}\n"
        f'    anchors: ["{anchors}"]\n'
        "    falsifiers:\n" + falsifier
    )


def make_project(tmp_path: Path, contract: str | None = contract_yaml(), *, enroll: bool = True) -> Path:
    """A project root with an optional contract and enrollment marker."""
    (tmp_path / "protected").mkdir(parents=True, exist_ok=True)
    (tmp_path / PROTECTED).write_text("def guard():\n    return True\n", encoding="utf-8")
    if contract is not None:
        (tmp_path / ".trw/contracts").mkdir(parents=True, exist_ok=True)
        (tmp_path / CONTRACT_REL).write_text(contract, encoding="utf-8")
    if enroll:
        # These fixtures exercise the Python entry points directly, so they never
        # install the shell hooks. write_enrollment refuses that combination by
        # default (a marker over absent hooks reports `current` while nothing can
        # fire); the opt-out is exactly what it is for. Fixtures that DO install
        # hooks — `_hook_project` in the re-probe suite, the end-to-end builder —
        # deliberately enroll without it, so the refusal stays exercised.
        write_enrollment(tmp_path, CONTRACT_REL, allow_missing_hooks=True)
    return tmp_path


def payload(root: Path, rel_path: str = PROTECTED, tool: str = "Edit") -> io.StringIO:
    return io.StringIO(json.dumps({"tool_name": tool, "tool_input": {"file_path": str(root / rel_path)}}))


def hook_project(tmp_path: Path, name: str, *, enroll: bool = True) -> Path:
    """A project with the REAL bundled hooks installed, optionally enrolled."""
    root = tmp_path / name
    (root / ".claude" / "hooks").mkdir(parents=True)
    for hook in (PRE_HOOK, POST_HOOK, "lib-trw.sh"):
        shutil.copy2(HOOK_SRC / hook, root / ".claude" / "hooks" / hook)
    make_project(root, enroll=False)
    if enroll:
        write_enrollment(root, CONTRACT_REL)
    return root


def run_hook(
    project: Path,
    hook: str,
    *,
    extra_env: dict[str, str] | None = None,
    timeout: float = 60,
) -> subprocess.CompletedProcess[str]:
    """Drive a REAL shipped hook over sh + stdin JSON, as a client would."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("HOOKS_ENABLED", None)
    env.pop("TRW_HOOKS_ENABLED", None)
    # These tests assert the ENFORCEMENT decision, not latency. The shipped
    # budgets (1s pre-write, 5s post-edit) fail CLOSED on timeout by design, and
    # importing trw_mcp costs ~0.45s idle — so under a parallel full-suite run the
    # 1s budget is routinely blown and an ALLOW case comes back as a spurious 2.
    # Pin the budgets so a loaded machine cannot masquerade as a policy decision.
    # The budget itself is covered separately by test_hook_latency_budget.
    env.setdefault("TRW_INTENT_PRE_WRITE_BUDGET_SECONDS", "60")
    env.setdefault("TRW_INTENT_POST_EDIT_BUDGET_SECONDS", "120")
    env.update(extra_env or {})
    return subprocess.run(
        ["sh", str(project / ".claude" / "hooks" / hook)],
        input=json.dumps({"tool_name": "Edit", "tool_input": {"file_path": PROTECTED}}),
        text=True,
        capture_output=True,
        cwd=project,
        env=env,
        timeout=timeout,
        check=False,
    )


def poison_lib(project: Path, body: str) -> None:
    """Append one line to the shared lib the hooks probe."""
    lib = project / ".claude" / "hooks" / "lib-trw.sh"
    lib.write_text(lib.read_text(encoding="utf-8") + body, encoding="utf-8")


def write_hook_env(project: Path, body: str) -> None:
    """Write `.trw/runtime/hook-env.sh` — the file lib-trw.sh sources.

    `.trw/.gitignore` ignores the whole `runtime/` directory, so this file is
    attacker-writable AND invisible to `git status`.
    """
    runtime = project / ".trw" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "hook-env.sh").write_text(body, encoding="utf-8")


@pytest.fixture
def intent_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> IntentContractConfig:
    """Point the entry points at *tmp_path* with fast, deterministic knobs."""
    config = IntentContractConfig(falsifier_timeout_seconds=2.0)
    monkeypatch.setattr(_hook_common, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(_hook_common, "intent_config", lambda: config)
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    return config

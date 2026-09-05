"""PRD-SEC-013 end-to-end: one continuous scenario through the SHIPPED surfaces.

Every other test in this suite exercises a component. This one walks the whole
enforcement story the way a real project experiences it, driving the *installed
hook scripts* (via `sh`, with realistic PreToolUse/PostToolUse stdin) and the
real deliver-gate entry point — no mocks of the units under test:

    enroll -> guarded edit is BLOCKED
           -> unguarded edit is allowed
           -> post-edit falsifier failure records an open violation
           -> `trw_deliver`'s gate REFUSES while the violation is open
           -> an operator-minted break-glass token clears it
           -> the override is in the hash-chained ledger, and the ledger verifies

The point is the seams: a component suite can pass while the pieces do not
compose (this repo shipped exactly that defect twice — a hook nothing installed,
and a gate key projected away before dispatch). If this file goes red, the
feature does not work end to end regardless of what the unit suites say.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from trw_mcp.security.intent_contract.break_glass import mint_token
from trw_mcp.security.intent_contract.enrollment import write_enrollment
from trw_mcp.security.intent_contract.ledger import verify_override_ledger
from trw_mcp.security.intent_contract.violations import (
    intent_violation_gate_block,
    open_violations,
)
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._delivery_helpers import check_delivery_gates

_HOOK_SRC = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks"
_PRE_HOOK = "pre-tool-intent-guard.sh"
_POST_HOOK = "post-tool-intent-check.sh"

_PROTECTED = "src/payments/charge.py"
_CLAIM_ID = "INTENT-E2E-001"

# A falsifier that passes on the defended file and fails once the guard is gone.
_FALSIFIER = "tests/test_charge_guard.py::test_charge_is_idempotent"

_DEFENDED_SOURCE = '''"""Defended payment path."""


def charge(amount: int, *, idempotency_key: str) -> str:
    if not idempotency_key:
        raise ValueError("idempotency_key is required")
    return f"charged:{amount}:{idempotency_key}"
'''

_WEAKENED_SOURCE = '''"""Simplified payment path — the guard looked redundant."""


def charge(amount: int, *, idempotency_key: str = "") -> str:
    return f"charged:{amount}:{idempotency_key}"
'''

_FALSIFIER_TEST = """import pytest

from payments.charge import charge


def test_charge_is_idempotent():
    with pytest.raises(ValueError):
        charge(10, idempotency_key="")
"""


def _payload(file_path: str, tool: str = "Edit") -> str:
    return json.dumps({"tool_name": tool, "tool_input": {"file_path": file_path}})


def _run_hook(project: Path, hook: str, file_path: str) -> subprocess.CompletedProcess[str]:
    """Drive a shipped hook exactly as the client would: sh + stdin JSON.

    A real client has an interpreter with trw_mcp installed and this scratch
    project has none: no ``.venv``, no ``.mcp.json``, and PATH ``python3`` is
    whatever the developer's or CI's shell resolves. The guard's resolution
    order is ``$TRW_PYTHON`` -> ``$CLAUDE_PROJECT_DIR/.venv/bin/python`` ->
    the ``.mcp.json`` launcher shebang -> PATH ``python3``
    (PRD-CORE-250-FR05), and once enrolled it fails CLOSED when all of them
    fail to import — so without the override every step below returned the
    resolver's exit 2 instead of the enforcement decision this file exists to
    walk. Pointing TRW_PYTHON at the suite's own interpreter is what makes the
    returncodes here mean ALLOW and BLOCK.
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env.setdefault("TRW_PYTHON", sys.executable)
    # Assert the enforcement decision, not latency. The shipped budgets fail
    # CLOSED on timeout by design, and importing trw_mcp costs ~0.44s idle — so
    # under a parallel full-suite run the 1s pre-write budget is routinely blown
    # and an ALLOW case returns a spurious 2. Latency has its own test.
    env.setdefault("TRW_INTENT_PRE_WRITE_BUDGET_SECONDS", "60")
    env.setdefault("TRW_INTENT_POST_EDIT_BUDGET_SECONDS", "120")
    return subprocess.run(
        ["sh", str(project / ".claude" / "hooks" / hook)],
        input=_payload(file_path),
        text=True,
        capture_output=True,
        cwd=project,
        env=env,
        timeout=60,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A scratch project: git repo, contract, enrollment, installed hooks, real code."""
    root = tmp_path / "proj"
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / "src" / "payments").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / ".trw" / "contracts").mkdir(parents=True)

    # A realistic src-layout project: conftest puts `src` on the path, exactly as
    # a real repo does, so the falsifier can import the code it guards.
    (root / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent / 'src'))\n",
        encoding="utf-8",
    )
    (root / "src" / "payments" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "payments" / "charge.py").write_text(_DEFENDED_SOURCE, encoding="utf-8")
    (root / "tests" / "test_charge_guard.py").write_text(_FALSIFIER_TEST, encoding="utf-8")

    contract = {
        "contract_id": "E2E",
        "must_not_happen": [
            {
                "claim_id": _CLAIM_ID,
                "text": "Charging without an idempotency key must never be possible.",
                "authority_class": "policy_derived",
                "state": "active",
                "machine_checkable": True,
                "binding_channel": "blocking_hook",
                "anchors": [_PROTECTED],
                "falsifiers": [{"kind": "pytest", "node_id": _FALSIFIER}],
            }
        ],
    }
    # YAML is a superset of JSON, and the loader is strict about structure, not style.
    (root / ".trw" / "contracts" / "must-not-happen.yaml").write_text(json.dumps(contract, indent=2), encoding="utf-8")

    for hook in (_PRE_HOOK, _POST_HOOK, "lib-intent-guard.sh"):
        shutil.copy2(_HOOK_SRC / hook, root / ".claude" / "hooks" / hook)
    lib = _HOOK_SRC / "lib-trw.sh"
    if lib.exists():
        shutil.copy2(lib, root / ".claude" / "hooks" / "lib-trw.sh")

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=e2e@test", "-c", "user.name=E2E", "commit", "-qm", "seed"],
        cwd=root,
        check=True,
    )
    write_enrollment(root)
    return root


def test_unenrolled_project_is_untouched(tmp_path: Path) -> None:
    """The release-safety property: no enrollment marker means no enforcement."""
    bare = tmp_path / "bare"
    (bare / ".claude" / "hooks").mkdir(parents=True)
    for hook in (_PRE_HOOK, _POST_HOOK, "lib-intent-guard.sh"):
        shutil.copy2(_HOOK_SRC / hook, bare / ".claude" / "hooks" / hook)
    for hook in (_PRE_HOOK, _POST_HOOK):
        assert _run_hook(bare, hook, "anything.py").returncode == 0


def test_end_to_end_block_violate_refuse_breakglass_ledger(project: Path) -> None:
    # 1. An edit to a file no claim anchors is allowed through.
    unguarded = _run_hook(project, _PRE_HOOK, "src/payments/__init__.py")
    assert unguarded.returncode == 0, unguarded.stderr

    # 2. An edit that touches a protected anchor is ALSO allowed through — by
    #    design (FR05 step 8). The pre-write hook is metadata-only: it cannot see
    #    the post-edit state, so blocking on mere anchor contact would forbid every
    #    edit to a guarded file, including correct ones. FR07 is the control point.
    guarded = _run_hook(project, _PRE_HOOK, _PROTECTED)
    assert guarded.returncode == 0, (
        f"pre-write hook must ALLOW an anchored path (FR05 step 8); got {guarded.returncode}\n"
        f"stdout={guarded.stdout}\nstderr={guarded.stderr}"
    )

    # 3. The guard is removed anyway (break-glass, --no-verify, a Bash write —
    #    the post-edit channel exists precisely because the pre-write one is not
    #    the only way a file changes). The falsifier now fails against the REAL
    #    post-edit file, so an open violation is recorded.
    (project / "src" / "payments" / "charge.py").write_text(_WEAKENED_SOURCE, encoding="utf-8")
    post = _run_hook(project, _POST_HOOK, _PROTECTED)
    assert post.returncode == 2, (
        f"post-edit check must fail closed on a failing falsifier; got {post.returncode}\n"
        f"stdout={post.stdout}\nstderr={post.stderr}"
    )
    opened = open_violations(project)
    assert [v["claim_id"] for v in opened] == [_CLAIM_ID]

    # 4. Delivery is refused while the violation is open — through the real gate.
    gates = dict(check_delivery_gates(None, FileStateReader(), trw_dir=project / ".trw"))
    assert "intent_violation_block" in gates
    assert _CLAIM_ID in str(gates["intent_violation_block"])

    # 5. Only an operator-minted token clears it (the agent cannot mint one).
    token = mint_token(
        project,
        claim_id=_CLAIM_ID,
        file_path=_PROTECTED,
        ttl_seconds=300,
        max_ttl_seconds=3600,
    )
    assert token.exists()
    assert oct(token.stat().st_mode & 0o777) == "0o600"

    cleared = _run_hook(project, _POST_HOOK, _PROTECTED)
    assert cleared.returncode == 0, (
        f"a valid break-glass token must let the post-edit check pass; got {cleared.returncode}\n"
        f"stdout={cleared.stdout}\nstderr={cleared.stderr}"
    )

    # 6. The override is ledgered and the chain verifies — an override that is
    #    not recorded is the one thing this design must never allow.
    verification = verify_override_ledger(project)
    assert verification.valid, f"ledger must verify after an override: {verification}"
    ledger_text = (project / ".trw" / "contracts" / "intent-override-ledger.jsonl").read_text(encoding="utf-8")
    assert _CLAIM_ID in ledger_text

    # 7. Delivery is no longer blocked.
    assert intent_violation_gate_block(project) is None

    # 8. The token is single-use: a second violation must not ride the same token.
    (project / "src" / "payments" / "charge.py").write_text(_WEAKENED_SOURCE, encoding="utf-8")
    replay = _run_hook(project, _POST_HOOK, _PROTECTED)
    assert replay.returncode == 2, (
        f"a consumed break-glass token must not authorize a second violation; got {replay.returncode}"
    )


def test_restoring_the_defended_code_clears_the_violation_without_an_override(
    project: Path,
) -> None:
    """The intended resolution path: fix the code, the falsifier passes, the block lifts."""
    (project / "src" / "payments" / "charge.py").write_text(_WEAKENED_SOURCE, encoding="utf-8")
    assert _run_hook(project, _POST_HOOK, _PROTECTED).returncode == 2
    assert intent_violation_gate_block(project) is not None

    (project / "src" / "payments" / "charge.py").write_text(_DEFENDED_SOURCE, encoding="utf-8")
    fixed = _run_hook(project, _POST_HOOK, _PROTECTED)
    assert fixed.returncode == 0, f"restored code must pass: {fixed.stdout}{fixed.stderr}"
    assert intent_violation_gate_block(project) is None
    assert open_violations(project) == []


def test_expired_break_glass_token_does_not_authorize(project: Path) -> None:
    """A stale token is not a key: expiry is enforced, not decorative."""
    (project / "src" / "payments" / "charge.py").write_text(_WEAKENED_SOURCE, encoding="utf-8")
    assert _run_hook(project, _POST_HOOK, _PROTECTED).returncode == 2

    token = mint_token(
        project,
        claim_id=_CLAIM_ID,
        file_path=_PROTECTED,
        ttl_seconds=1,
        max_ttl_seconds=3600,
    )
    assert token.exists()
    time.sleep(2)
    still_blocked = _run_hook(project, _POST_HOOK, _PROTECTED)
    assert still_blocked.returncode == 2, "an expired token must not clear the violation"


def test_pre_write_hook_blocks_only_on_its_two_specified_conditions(project: Path) -> None:
    """FR05 steps 2 and 4: stale enrollment and a malformed contract fail closed.

    These are the pre-write hook's ONLY blocking conditions. Asserting them here
    keeps the "metadata-only" design honest: if someone later makes this hook
    block on anchor contact, this test and the E2E flow above disagree loudly.
    """
    contract_path = project / ".trw" / "contracts" / "must-not-happen.yaml"

    # Malformed contract -> fail closed.
    good = contract_path.read_text(encoding="utf-8")
    contract_path.write_text("{ this is not: valid: yaml: at all", encoding="utf-8")
    malformed = _run_hook(project, _PRE_HOOK, _PROTECTED)
    assert malformed.returncode == 2, "a malformed contract must fail closed"
    contract_path.write_text(good, encoding="utf-8")
    assert _run_hook(project, _PRE_HOOK, _PROTECTED).returncode == 0

    # Enrolled, then the contract digest drifts -> stale enrollment -> fail closed.
    contract_path.write_text(good + "\n# drift\n", encoding="utf-8")
    stale = _run_hook(project, _PRE_HOOK, _PROTECTED)
    assert stale.returncode == 2, "a stale enrollment marker must fail closed"

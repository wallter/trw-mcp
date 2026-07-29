"""PRD-CORE-233 FR01 — the delegated-run precondition is stated where it binds.

Seven bundled agents grant ``mcp__trw__trw_checkpoint`` without
``mcp__trw__trw_init``: they hold a tool whose precondition (an active run)
they cannot create for themselves. The mechanism to satisfy it already exists
(an inherited pin, or the tool's own ``run_path`` parameter) — what was missing
is the contract saying so, on both sides of the dispatch.

This test binds three things: every grant-holder states the caller-supplied run
precondition, the delegating agent states the reciprocal pre-dispatch
obligation, and no agent gained a ``trw_init`` grant in the process.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_agent_contracts.py"

# Monorepo-only: the repo-root scripts/ layout (and .claude/agents/ mirror) is
# absent from the standalone trw-mcp mirror. Skip cleanly there.
if not _SCRIPT.is_file():
    pytest.skip("monorepo-only invariant (repo-root scripts/ absent in mirror)", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("check_agent_contracts_fr01", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_lint = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _lint
_spec.loader.exec_module(_lint)

AGENTS_DIR: Path = _lint.AGENTS_DIR
MIRROR_DIR: Path = _lint.MIRROR_AGENTS_DIR
FRAGMENT = AGENTS_DIR / "_shared" / "delegated-run-precondition.md"

CHECKPOINT_GRANT = "mcp__trw__trw_checkpoint"
INIT_GRANT = "mcp__trw__trw_init"
_TOOL_MARKER_RE = re.compile(r"\{tool:(trw_\w+)\}")


def _expand(text: str) -> str:
    """Mirror ``scripts/sync-agents.py``: ``{tool:trw_X}`` renders as ``trw_X``."""
    return _TOOL_MARKER_RE.sub(lambda m: m.group(1), text)


def _grants(path: Path) -> set[str]:
    frontmatter, _, _ = _lint.split_frontmatter(path.read_text(encoding="utf-8"))
    return _lint.granted_tools(frontmatter)


def _checkpoint_holders_without_init() -> list[Path]:
    holders = []
    for path in sorted(AGENTS_DIR.glob("*.md")):
        grants = _grants(path)
        if CHECKPOINT_GRANT in grants and INIT_GRANT not in grants:
            holders.append(path)
    return holders


HOLDERS = _checkpoint_holders_without_init()


def test_grant_holder_set_is_not_empty() -> None:
    """Non-vacuity: a moved directory or renamed grant must not pass silently."""
    assert len(HOLDERS) >= 7, f"expected >=7 checkpoint-without-init agents, found {[p.name for p in HOLDERS]}"


def test_precondition_fragment_exists_and_names_the_executable_remedy() -> None:
    """The shared statement must name the parameter the caller can actually use."""
    assert FRAGMENT.is_file(), f"missing shared precondition fragment: {FRAGMENT}"
    text = _expand(FRAGMENT.read_text(encoding="utf-8"))
    assert "run_path" in text
    assert "recorded" in text, "the fragment must tell the agent how to read a not-recorded result"


@pytest.mark.parametrize("agent_path", HOLDERS, ids=lambda p: p.stem)
def test_grant_holders_state_caller_supplied_run(agent_path: Path) -> None:
    """FR01 sub-agent side: the precondition is stated in the body that holds the grant."""
    body = agent_path.read_text(encoding="utf-8")
    fragment = FRAGMENT.read_text(encoding="utf-8").strip("\n")
    assert fragment in body, f"{agent_path.name} does not carry the delegated-run precondition statement"


@pytest.mark.parametrize("agent_path", HOLDERS, ids=lambda p: p.stem)
def test_mirror_projection_carries_the_precondition(agent_path: Path) -> None:
    """The .claude/agents/ projection must carry the same statement, marker-expanded."""
    mirror = MIRROR_DIR / agent_path.name
    assert mirror.is_file(), f"missing mirror projection: {mirror}"
    fragment = _expand(FRAGMENT.read_text(encoding="utf-8").strip("\n"))
    assert fragment in mirror.read_text(encoding="utf-8"), (
        f"{mirror.name} is stale — run python3 scripts/sync-agents.py"
    )


def test_lead_states_the_pre_dispatch_run_obligation() -> None:
    """FR01 delegating side: the only trw_init holder owes the run before dispatch."""
    lead = AGENTS_DIR / "trw-lead.md"
    grants = _grants(lead)
    assert INIT_GRANT in grants, "trw-lead is expected to be the run-owning agent"

    body = _expand(lead.read_text(encoding="utf-8")).lower()
    assert "run_path" in body, "trw-lead must name run_path in its dispatch contract"
    assert "before dispatch" in body or "before dispatching" in body, (
        "trw-lead must state the pre-dispatch run obligation"
    )


def test_no_agent_other_than_lead_gained_trw_init() -> None:
    """FR01 adds no tool grant — run ownership stays with the orchestrator."""
    holders = [p.name for p in sorted(AGENTS_DIR.glob("*.md")) if INIT_GRANT in _grants(p)]
    assert holders == ["trw-lead.md"], f"unexpected trw_init grant holders: {holders}"

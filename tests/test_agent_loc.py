"""LOC discipline test for TRW agent files (PRD-QUAL-073 FR12).

Every ``.claude/agents/*.md`` file must be <= 350 lines. Growing a file past
that threshold indicates the agent body has accumulated mix-in concerns that
belong in a shared reference doc (see ``docs/documentation/audit-framework.md``
for precedent).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Public-mirror guard: this test asserts a MONOREPO invariant (repo-root
# scripts/ + .claude/ layout) absent from the standalone trw-mcp PyPI/GitHub
# mirror. Skip cleanly there; the monorepo CI still enforces it.
if not (REPO_ROOT / "scripts").is_dir():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )

AGENTS_DIR = REPO_ROOT / ".claude" / "agents"

#: Budget for the content an agent AUTHORS. Sentinel-delimited shared fragments
#: (scripts/agent_fragments.py) are identical across agents and injected, so
#: counting them charges an agent for policy it does not own — and adding a
#: shared fragment would push whichever agent is closest to the cap over it,
#: which is exactly what happened when the delegated-run precondition landed
#: (89ff62ff01) on trw-auditor at 312 authored lines. Same rule the
#: adversarial-auditor word budget uses in test_bundled_agents.py.
LOC_LIMIT = 350

#: Backstop on the WHOLE file, because the model reads every line including the
#: injected ones. Deliberate ceiling, not a census: it must sit far enough above
#: LOC_LIMIT to absorb the shared fragments (54 lines across three today) while
#: still catching unbounded growth in shared policy.
TOTAL_LOC_LIMIT = 420

#: Matches a whole injected block, sentinel lines included.
_FRAGMENT_BLOCK = re.compile(r"(?ms)^<!-- trw:[a-z-]+:start -->.*?^<!-- trw:[a-z-]+:end -->\n?")

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from post_extraction_static_audit import available_static_audit_commands


def _agent_files() -> list[Path]:
    return sorted(AGENTS_DIR.glob("*.md"))


_AGENT_PARAMS = [pytest.param(p, id=p.name) for p in _agent_files()]


@pytest.mark.parametrize("agent_path", _AGENT_PARAMS)
def test_loc_under_limit(agent_path: Path) -> None:
    """Authored content stays within budget, and the whole file within its backstop."""
    text = agent_path.read_text(encoding="utf-8")
    total = len(text.splitlines())
    authored = len(_FRAGMENT_BLOCK.sub("", text).splitlines())

    assert authored <= LOC_LIMIT, (
        f"{agent_path.name}: {authored} authored LOC exceeds limit of {LOC_LIMIT}; "
        "extract shared content into a referenced doc"
    )
    assert total <= TOTAL_LOC_LIMIT, (
        f"{agent_path.name}: {total} total LOC (authored {authored} + injected fragments) "
        f"exceeds the backstop of {TOTAL_LOC_LIMIT}; shared policy has grown too large to inject verbatim"
    )


def test_post_extraction_static_audit_has_default_compile_gate() -> None:
    """Optional post-extraction static audit always has a deterministic baseline command."""
    commands = available_static_audit_commands()
    assert any("compileall" in command for command in commands)


def test_makefile_exposes_pytest_timeout_override() -> None:
    """Pytest timeout can be overridden without editing the Makefile."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    assert "PYTEST_TIMEOUT ?= 30" in makefile
    assert "--timeout=$(PYTEST_TIMEOUT)" in makefile


def test_prd_grooming_guidance_warns_against_validator_gaming() -> None:
    """PRD style guidance treats tables as clarity tools, not scoring hacks."""
    skill = REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "skills" / "trw-prd-groom" / "SKILL.md"
    content = skill.read_text(encoding="utf-8")
    assert "do not convert everything into tables" in content

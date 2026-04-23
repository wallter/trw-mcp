"""LOC discipline test for TRW agent files (PRD-QUAL-073 FR12).

Every ``.claude/agents/*.md`` file must be <= 350 lines. Growing a file past
that threshold indicates the agent body has accumulated mix-in concerns that
belong in a shared reference doc (see ``docs/documentation/audit-framework.md``
for precedent).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"
LOC_LIMIT = 350


def _agent_files() -> list[Path]:
    return sorted(AGENTS_DIR.glob("*.md"))


_AGENT_PARAMS = [pytest.param(p, id=p.name) for p in _agent_files()]


@pytest.mark.parametrize("agent_path", _AGENT_PARAMS)
def test_loc_under_limit(agent_path: Path) -> None:
    """Every agent file must be <= 350 lines."""
    loc = len(agent_path.read_text(encoding="utf-8").splitlines())
    assert loc <= LOC_LIMIT, (
        f"{agent_path.name}: {loc} LOC exceeds limit of {LOC_LIMIT}; "
        "extract shared content into a referenced doc"
    )

"""The dev-repo `.claude/agents/*.md` must stay the resolved image of the bundled tiers.

WHY THIS EXISTS
    Three copies of every specialist agent live in this monorepo:

      * ``trw-mcp/src/trw_mcp/data/agents/*.md`` — the PORTABLE definitions that
        ship to users. Their frontmatter carries a capability TIER
        (``frontier`` / ``balanced`` / ``local-small``), per the standing rule
        that portable surfaces name tiers, never provider model ids.
      * ``.agents/agents/*.md`` — the antigravity-cli adapter copies, whose tokens
        resolve through ``_ANTIGRAVITY_MAP`` to ``pro``/``flash``. NOT checked here;
        see the note at the end of this docstring.
      * ``.claude/agents/*.md`` — this repo's own Claude Code adapter copies.
        Their frontmatter carries a raw Claude Code ALIAS (``opus`` / ``sonnet``
        / ``haiku``), which is exactly what ``tier_resolver`` produces for
        ``client="claude-code"``.

    A 2026-09-10 audit flagged that split as drift. It is not — it is the
    adapter boundary working. But nothing checked that the two stayed in
    correspondence, so an edit to either side could silently diverge and the
    dev repo would quietly run a different model than the shipped agent
    declares. This test makes the invariant falsifiable.

    Fix a failure by changing the TIER in the bundled file (the source of
    truth) and re-resolving, not by hand-editing the alias to match.

    SCOPE, stated so the coverage is not overread: this checks the claude-code surface
    only, and only in one direction — a bundled agent whose ``.claude/agents/`` copy is
    absent is skipped, not failed. Extending it to ``.agents/agents/`` and asserting
    presence would be strictly better; it is not done here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trw_mcp.agents.tier_resolver import resolve_tier

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLED_AGENTS = REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "agents"
DEV_AGENTS = REPO_ROOT / ".claude" / "agents"

requires_monorepo = pytest.mark.skipif(
    not DEV_AGENTS.is_dir() or not BUNDLED_AGENTS.is_dir(),
    reason="dev-repo agent tree not present (installed package, not the monorepo)",
)

_MODEL_LINE = re.compile(r"^model:\s*(\S+)\s*$", re.MULTILINE)


def _frontmatter_model(path: Path) -> str | None:
    """Return the `model:` value from a markdown file's YAML frontmatter."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    match = _MODEL_LINE.search(parts[1])
    return match.group(1) if match else None


def _bundled_with_tiers() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in sorted(BUNDLED_AGENTS.glob("*.md")):
        tier = _frontmatter_model(path)
        if tier:
            out.append((path.name, tier))
    return out


@requires_monorepo
def test_bundled_agents_declare_capability_tiers_not_model_ids() -> None:
    """Portable agent definitions must name tiers — never a provider alias or id."""
    bundled = _bundled_with_tiers()
    # Non-vacuity guard: an empty scan makes `offenders` empty and the test green, so a
    # renamed directory or a frontmatter-format change would read as an all-clear repo.
    # Same pattern as test_workflow_agent_schema_contract.py's scanner sanity check.
    assert len(bundled) >= 10, (
        f"bundled agent tier scan found {len(bundled)} agents — that is a scanner regression, "
        "not an all-clear repo. Check BUNDLED_AGENTS and _MODEL_LINE."
    )
    aliases = {"opus", "sonnet", "haiku", "fable", "inherit"}
    offenders = [name for name, tier in bundled if tier in aliases]
    assert not offenders, (
        f"bundled agents naming a provider alias instead of a capability tier: {offenders}. "
        "Portable surfaces carry tiers; model names belong in scoped adapters."
    )


@requires_monorepo
def test_dev_agent_aliases_match_resolved_bundled_tiers() -> None:
    """Each dev-repo copy's alias equals resolve_tier(bundled tier, claude-code)."""
    mismatches: list[str] = []
    checked = 0
    for name, tier in _bundled_with_tiers():
        dev_path = DEV_AGENTS / name
        if not dev_path.exists():
            continue
        checked += 1
        try:
            expected = resolve_tier(tier, client="claude-code")
        except ValueError:
            # resolve_tier raises for a tier absent from the client's map. Accumulating
            # keeps the actionable multi-agent message; letting it propagate would abort
            # the loop with a raw traceback, which is the opposite of the point.
            mismatches.append(f"{name}: bundled tier {tier!r} is not a known capability tier")
            continue
        actual = _frontmatter_model(dev_path)
        if actual != expected:
            mismatches.append(f"{name}: bundled tier {tier!r} resolves to {expected!r}, dev copy says {actual!r}")

    assert checked, "no dev/bundled agent pairs found — the test would pass vacuously"
    assert not mismatches, "dev-repo agent copies out of sync with the bundled tiers:\n  " + "\n  ".join(mismatches)

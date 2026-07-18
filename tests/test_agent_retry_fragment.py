"""Shared agent retry-protocol fragment tests (PRD-CORE-215-FR04).

Asserts every bundled agent template carries the generated retry-protocol block
and that the block matches the single source fragment (freshness), so the N
templates can never silently drift from the one source of truth.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

# Monorepo-only invariant: the repo-root scripts/ + data/agents layout is absent
# from the standalone trw-mcp PyPI/GitHub mirror. Skip cleanly there.
if not (REPO_ROOT / "scripts" / "agent_fragments.py").is_file():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/agent_fragments.py absent in mirror)",
        allow_module_level=True,
    )

_SPEC = importlib.util.spec_from_file_location(
    "agent_fragments",
    REPO_ROOT / "scripts" / "agent_fragments.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_FRAGMENTS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_FRAGMENTS)

BUNDLED_AGENTS_DIR = REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "agents"
FRAMEWORK_SOURCE = REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "framework.source.md"
FRAMEWORK_REFERENCE = REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "framework-reference.md"


def _has_allowed_trw_tool(frontmatter: dict[str, Any]) -> bool:
    for field in ("tools", "allowedTools"):
        values = frontmatter.get(field)
        if isinstance(values, list) and any(
            isinstance(value, str) and value.startswith("mcp__trw__") for value in values
        ):
            return True
    return False


def test_fragment_source_exists_and_nonempty() -> None:
    """The single-source fragment file exists and carries the protocol text."""
    fragment = _FRAGMENTS.load_fragment()
    assert fragment.strip(), "fragment source is empty"
    assert "retry" in fragment.lower()
    assert "record" in fragment.lower()


def test_framework_distinguishes_generic_and_trw_retry_budgets() -> None:
    """The canon must resolve attempt/retry ambiguity without weakening helpers."""
    source = FRAMEWORK_SOURCE.read_text(encoding="utf-8")
    reference = FRAMEWORK_REFERENCE.read_text(encoding="utf-8")
    required = (
        "non-TRW operations",
        "three total attempts",
        "`trw_*` call: retry once",
        "role-local persistence-critical policy may be stricter and wins",
    )
    for phrase in required:
        assert phrase in source
        assert phrase in reference


def test_every_bundled_agent_carries_the_block() -> None:
    """Each bundled agent template contains the sentinel-delimited block."""
    agents = _FRAGMENTS.agent_files()
    assert agents, "no bundled agent templates discovered"
    for path in agents:
        text = path.read_text(encoding="utf-8")
        assert _FRAGMENTS.START_MARKER in text, f"{path.name}: missing start sentinel"
        assert _FRAGMENTS.END_MARKER in text, f"{path.name}: missing end sentinel"


def test_every_agent_with_retry_protocol_can_call_a_trw_tool() -> None:
    """The shared TRW retry protocol must be reachable under each tool allowlist."""
    for path in _FRAGMENTS.agent_files():
        text = path.read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(text.split("---", 2)[1])
        assert isinstance(frontmatter, dict), f"{path.name}: frontmatter is not a mapping"
        assert _has_allowed_trw_tool(frontmatter), f"{path.name}: TRW retry protocol is unreachable"


@pytest.mark.parametrize(
    ("frontmatter", "expected"),
    [
        ({"tools": ["mcp__trw__trw_recall"]}, True),
        ({"allowedTools": ["mcp__trw__trw_code_search"]}, True),
        ({"description": "mcp__trw__", "disallowedTools": ["mcp__trw__trw_recall"]}, False),
    ],
)
def test_allowed_trw_tool_detection(frontmatter: dict[str, Any], expected: bool) -> None:
    assert _has_allowed_trw_tool(frontmatter) is expected


def test_every_block_matches_source_fragment() -> None:
    """Freshness: every injected block equals the current source fragment."""
    fragment = _FRAGMENTS.load_fragment()
    for path in _FRAGMENTS.agent_files():
        extracted = _FRAGMENTS.extract_block(path.read_text(encoding="utf-8"))
        assert extracted == fragment, (
            f"{path.name}: injected retry-protocol block diverges from the source "
            "fragment. Run scripts/agent_fragments.py to refresh."
        )


def test_check_all_reports_no_drift() -> None:
    """The freshness checker (wired into make bundle-sync) reports clean."""
    assert _FRAGMENTS.check_all() == []


def test_check_all_detects_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A tampered block is reported as drift by the checker."""
    fake_agents = tmp_path / "agents"
    fake_agents.mkdir()
    fragment = _FRAGMENTS.load_fragment()
    good = fake_agents / "good.md"
    good.write_text(_FRAGMENTS.inject("# good\n", fragment), encoding="utf-8")
    bad = fake_agents / "bad.md"
    bad.write_text(
        f"# bad\n\n{_FRAGMENTS.START_MARKER}\ntampered\n{_FRAGMENTS.END_MARKER}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(_FRAGMENTS, "agent_files", lambda: sorted(fake_agents.glob("*.md")))
    assert _FRAGMENTS.check_all() == ["bad.md"]


def test_inject_is_idempotent() -> None:
    """Re-injecting the block does not change an already-synced template."""
    fragment = _FRAGMENTS.load_fragment()
    for path in _FRAGMENTS.agent_files():
        original = path.read_text(encoding="utf-8")
        assert _FRAGMENTS.inject(original, fragment) == original, f"{path.name}: inject not idempotent"


def test_trw_lead_keeps_stricter_persistence_rule() -> None:
    """The generic fragment must not weaken trw-lead's stricter persistence rule."""
    lead = (BUNDLED_AGENTS_DIR / "trw-lead.md").read_text(encoding="utf-8")
    # trw-lead's stricter existing rules remain present alongside the fragment.
    assert "treat persistence failures as P0" in lead
    assert "Max 3 retries per tool failure" in lead
    # And the fragment block is also present (coexistence).
    assert _FRAGMENTS.START_MARKER in lead

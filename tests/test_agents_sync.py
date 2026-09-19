"""Agent-directory parity test (PRD-QUAL-073 FR13; PRD-INFRA-190-FR01).

``trw-mcp/src/trw_mcp/data/agents/`` is the source of truth and
``.claude/agents/`` is its claude-code projection. The expectation is computed
with ``materialize_agent(client="claude-code")`` -- the installer's own renderer
-- and never with a local copy of it: four regex copies once rendered bare
``trw_x`` names where the installer renders ``mcp__trw__trw_x``, so every
update-project run and every sync undid the other.

Regenerate drift with ``make client-mirror-sync``.
"""

from __future__ import annotations

import hashlib
import importlib.util
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

BUNDLED_DIR = REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "agents"
CLAUDE_DIR = REPO_ROOT / ".claude" / "agents"

_DEV_ONLY_AGENTS = {"trw-distill-sonnet-judge.md", "trw-distill-explorer.md"}

_MANIFEST_SPEC = importlib.util.spec_from_file_location(
    "bundle_hash_manifest",
    REPO_ROOT / "scripts" / "bundle_hash_manifest.py",
)
assert _MANIFEST_SPEC is not None and _MANIFEST_SPEC.loader is not None
_MANIFEST_MODULE = importlib.util.module_from_spec(_MANIFEST_SPEC)
_MANIFEST_SPEC.loader.exec_module(_MANIFEST_MODULE)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _agent_names() -> list[str]:
    return sorted(p.name for p in BUNDLED_DIR.glob("*.md"))


_AGENT_PARAMS = [pytest.param(name, id=name) for name in _agent_names()]


@pytest.mark.parametrize("agent_name", _AGENT_PARAMS)
def test_parity_with_the_installer_rendering(agent_name: str) -> None:
    """``.claude/agents/X`` equals what ``trw-mcp init`` writes for a Claude Code user."""
    from trw_mcp.agents.tier_resolver import materialize_agent

    src = BUNDLED_DIR / agent_name
    dst = CLAUDE_DIR / agent_name
    assert src.is_file(), f"bundled source missing: {src}"
    assert dst.is_file(), f".claude/agents copy missing: {dst} (run make client-mirror-sync)"

    expected = materialize_agent(src.read_text(encoding="utf-8"), client="claude-code").encode("utf-8")
    assert _sha256(dst.read_bytes()) == _sha256(expected), (
        f"{agent_name}: .claude/agents/ drifts from materialize_agent(client='claude-code'). "
        "Run make client-mirror-sync to regenerate."
    )


def test_the_projection_uses_the_claude_code_tool_namespace() -> None:
    """Non-vacuity: the rendering under test is the prefixed one, not bare names."""
    rendered = "\n".join(p.read_text(encoding="utf-8") for p in sorted(CLAUDE_DIR.glob("trw-*.md")))
    assert "mcp__trw__trw_checkpoint" in rendered


def test_the_manifest_recorder_accepts_the_projection_and_not_bare_names() -> None:
    """PRD-INFRA-190 Open Question 1, reproduced: the recorder's framework baseline
    for an agent is the raw bundle plus ``materialize_agent``'s output, so a bare-name
    rendering (the deleted ``scripts/sync-agents.py`` output) matched neither and was
    classified as a user edit -- every ``.claude/agents`` key left ``content_hashes``.
    """
    import re

    from trw_mcp.agents.tier_resolver import rewrite_model_line
    from trw_mcp.bootstrap._version_manifest import _framework_agent_hashes

    src = BUNDLED_DIR / "trw-reviewer.md"
    baseline = _framework_agent_hashes(src, client="claude-code")
    assert _sha256((CLAUDE_DIR / src.name).read_bytes()) in baseline
    bare = re.sub(r"\{tool:(trw_\w+)\}", lambda m: m.group(1), src.read_text(encoding="utf-8"))
    assert _sha256(rewrite_model_line(bare, client="claude-code").encode("utf-8")) not in baseline


def test_counts_match() -> None:
    """The two dirs have the same set of agent filenames."""
    bundled = {p.name for p in BUNDLED_DIR.glob("*.md")}
    claude = {p.name for p in CLAUDE_DIR.glob("*.md")} - _DEV_ONLY_AGENTS
    assert bundled == claude, (
        f"filename set drift:\n  bundled-only: {sorted(bundled - claude)}\n  claude-only:  {sorted(claude - bundled)}"
    )


def test_bundle_hash_manifest_matches_current_bundled_files() -> None:
    """Committed bundle hash manifest matches bundled agent, hook, and skill files."""
    assert _MANIFEST_MODULE.check_manifest() == []


def test_bundle_hash_manifest_detects_drift(tmp_path: Path) -> None:
    """Manifest checker reports changed bundled content by relative path."""
    bundled = tmp_path / "data"
    (bundled / "agents").mkdir(parents=True)
    (bundled / "hooks").mkdir()
    (bundled / "skills").mkdir()
    agent = bundled / "agents" / "trw-test.md"
    agent.write_text("old", encoding="utf-8")
    manifest = tmp_path / "bundle-hashes.json"
    _MANIFEST_MODULE.write_manifest(manifest, bundled)
    agent.write_text("new", encoding="utf-8")

    assert _MANIFEST_MODULE.check_manifest(manifest, bundled) == ["agents/trw-test.md"]


def test_bundle_hash_manifest_ignores_python_runtime_cache(tmp_path: Path) -> None:
    """Generated runtime cache files under bundled mirrors are never manifest inputs."""
    bundled = tmp_path / "data"
    (bundled / "agents").mkdir(parents=True)
    (bundled / "hooks" / "__pycache__").mkdir(parents=True)
    (bundled / "skills").mkdir()
    (bundled / "agents" / "trw-test.md").write_text("agent", encoding="utf-8")
    (bundled / "hooks" / "__pycache__" / "hook.cpython-312.pyc").write_bytes(b"cache")

    manifest = _MANIFEST_MODULE.build_manifest(bundled)
    entries = manifest["entries"]

    assert isinstance(entries, dict)
    assert set(entries) == {"agents/trw-test.md"}

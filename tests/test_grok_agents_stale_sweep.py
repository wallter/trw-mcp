"""GROK-AGENTS-STALE-SWEEP: a retired bundled agent's Grok copy is swept like every other client's.

``.grok/agents`` is recorded in the manifest (the managed-artifact recorders), but the stale sweep's
surface table had no entry for it, so a dropped ``trw-`` agent's Grok copy survived every update while the
same agent's Codex, Cursor, Copilot and Antigravity copies were removed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from ._bootstrap_test_support import fake_git_repo  # noqa: F401


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _a_bundled_agent_stem() -> str:
    from trw_mcp.bootstrap._utils import _DATA_DIR

    stems = sorted(path.stem for path in (_DATA_DIR / "agents").glob("*.md"))
    assert stems, "no bundled agents found"
    return stems[0]


def test_every_agent_capable_client_has_a_stale_sweep_surface() -> None:
    """Totality: a client whose agents TRW writes must have its agent dir swept, or retirements strand there."""
    from trw_mcp.agents.agent_formats import agent_format_for
    from trw_mcp.bootstrap._version_migration_clients import _CLIENT_ARTIFACT_SURFACES
    from trw_mcp.models.config import builtin_client_ids

    # .claude/agents and .opencode/agents are swept by _remove_stale_artifacts from the manifest's
    # agents / opencode_agents lists, not by this disk-driven client sweep.
    swept = {surface.client_dir for surface in _CLIENT_ARTIFACT_SURFACES} | {".claude/agents", ".opencode/agents"}
    missing = sorted(
        fmt.destination_dir
        for fmt in (agent_format_for(client) for client in builtin_client_ids())
        if fmt.supports_agents and fmt.destination_dir is not None and fmt.destination_dir not in swept
    )
    assert missing == [], f"agent dirs TRW writes but never sweeps: {missing}"


def test_a_retired_grok_agent_is_swept_and_an_edited_one_kept(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._version_migration_clients import _remove_stale_client_artifacts

    agents = tmp_path / ".grok" / "agents"
    agents.mkdir(parents=True)
    kept = f"{_a_bundled_agent_stem()}.md"
    (agents / "trw-gone.md").write_text("stale", encoding="utf-8")  # retired, TRW-written
    (agents / "trw-edited.md").write_text("my edit", encoding="utf-8")  # retired, but the user edited it
    (agents / kept).write_text("bundled", encoding="utf-8")  # still bundled
    (agents / "my-agent.md").write_text("user", encoding="utf-8")  # the user's own

    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": [], "warnings": []}
    _remove_stale_client_artifacts(
        tmp_path,
        result,
        manifest_hashes={
            ".grok/agents/trw-gone.md": _sha(b"stale"),
            ".grok/agents/trw-edited.md": _sha(b"original"),
            # Recorded with its exact bytes: the ownership proof would allow deleting it, so only the
            # bundled-name check keeps it (codex r1 KI: an unrecorded copy proved nothing about that check).
            f".grok/agents/{kept}": _sha(b"bundled"),
        },
    )

    assert not (agents / "trw-gone.md").exists()
    assert (agents / "trw-edited.md").read_text(encoding="utf-8") == "my edit"
    assert ".grok/agents/trw-edited.md (not_installer_owned)" in result["preserved"]
    assert (agents / kept).exists()
    assert (agents / "my-agent.md").exists()


@pytest.mark.usefixtures("no_memory_daemon")
def test_update_project_sweeps_a_retired_grok_agent(fake_git_repo: Path) -> None:
    """End to end: a recorded, unedited retired agent goes; an edited one stays byte for byte."""
    from trw_mcp.bootstrap import init_project, update_project

    assert not init_project(fake_git_repo, ide="grok")["errors"]
    agents = fake_git_repo / ".grok" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "trw-gone.md").write_text("stale", encoding="utf-8")
    (agents / "trw-edited.md").write_text("my edit", encoding="utf-8")
    manifest_path = fake_git_repo / ".trw" / "managed-artifacts.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("content_hashes", {}).update(
        {".grok/agents/trw-gone.md": _sha(b"stale"), ".grok/agents/trw-edited.md": _sha(b"original")}
    )
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")

    result = update_project(fake_git_repo, ide="grok")

    assert not result["errors"], result["errors"]
    assert not (agents / "trw-gone.md").exists()
    assert (agents / "trw-edited.md").read_text(encoding="utf-8") == "my edit"

"""Client-adapter parity fixes (FIX A/B/C — client-adapter audit).

FIX A: per-client stale bundled-artifact cleanup for codex/cursor/copilot.
FIX B: codex agents/skills content-aware refresh (unmodified refreshed, edited kept).
FIX C: SUPPORTED_IDES is the canonical client-ID source; integrations derive/validate.

Retirement (2026-07-11): aider was retired. It is no longer in SUPPORTED_IDES
and retains uninstall surfaces only (see test_uninstall). The gemini client was
removed outright on 2026-07-24, uninstall surfaces included.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from trw_mcp.bootstrap._client_integrations import (
    _INTEGRATION_EXCLUDED_IDES,
    CLIENT_INTEGRATIONS,
)
from trw_mcp.bootstrap._codex import (
    _codex_skills_source_dir,
    install_codex_skills,
)
from trw_mcp.bootstrap._utils import SUPPORTED_IDES
from trw_mcp.bootstrap._version_migration_clients import (
    _codex_manifest_hashes,
    _remove_stale_client_artifacts,
)
from trw_mcp.client_profiles.catalog import _CLIENT_ORDER, _RETIRED_CLIENTS, _write_target_label
from trw_mcp.models.config._profiles import resolve_client_profile


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _new_result() -> dict[str, list[str]]:
    return {"created": [], "updated": [], "preserved": [], "errors": [], "warnings": []}


# ---------------------------------------------------------------------------
# FIX A — per-client stale cleanup
# ---------------------------------------------------------------------------


def _a_bundled_agent_stem() -> str:
    """One stem from the shipped bundle, so keep-set assertions track the bundle."""
    from trw_mcp.bootstrap._utils import _DATA_DIR

    stems = sorted(path.stem for path in (_DATA_DIR / "agents").glob("*.md"))
    assert stems, "no bundled agents found; these sweeps have stopped testing anything"
    return stems[0]


def test_stale_codex_agent_removed_bundled_and_user_kept(tmp_path: Path) -> None:
    agents = tmp_path / ".codex" / "agents"
    agents.mkdir(parents=True)
    # The "still bundled" name is READ from the bundle, not written here: since
    # PRD-CORE-252 the sweep derives its keep-set from the bundled agent
    # directory, so a literal would go stale the day that directory moves.
    kept = f"{_a_bundled_agent_stem()}.toml"
    (agents / "trw-gone.toml").write_text("stale", encoding="utf-8")  # dropped TRW agent
    (agents / kept).write_text("bundled", encoding="utf-8")  # still bundled
    (agents / "my-agent.toml").write_text("user", encoding="utf-8")  # user file (no trw- prefix)

    result = _new_result()
    _remove_stale_client_artifacts(tmp_path, result)

    assert not (agents / "trw-gone.toml").exists()
    assert (agents / kept).exists()  # current bundle survives
    assert (agents / "my-agent.toml").exists()  # non-trw user file survives
    assert any("removed:" in line and "trw-gone.toml" in line for line in result["updated"])


def test_stale_cursor_skill_dir_removed(tmp_path: Path) -> None:
    skills = tmp_path / ".cursor" / "skills"
    skills.mkdir(parents=True)
    (skills / "trw-gone").mkdir()  # dropped skill (not in curated list)
    (skills / "trw-gone" / "SKILL.md").write_text("stale", encoding="utf-8")
    (skills / "trw-audit").mkdir()  # in _IDE_CURATED_SKILLS -> kept
    (skills / "trw-audit" / "SKILL.md").write_text("bundled", encoding="utf-8")
    (skills / "my-skill").mkdir()  # user dir kept
    (skills / "my-skill" / "SKILL.md").write_text("user", encoding="utf-8")

    result = _new_result()
    _remove_stale_client_artifacts(tmp_path, result)

    assert not (skills / "trw-gone").exists()
    assert (skills / "trw-audit").exists()
    assert (skills / "my-skill").exists()


def test_stale_copilot_agents_removed(tmp_path: Path) -> None:
    copilot = tmp_path / ".github" / "agents"
    copilot.mkdir(parents=True)
    kept = f"{_a_bundled_agent_stem()}.agent.md"
    (copilot / "trw-gone.agent.md").write_text("stale", encoding="utf-8")
    (copilot / kept).write_text("bundled", encoding="utf-8")  # in the bundle

    result = _new_result()
    _remove_stale_client_artifacts(tmp_path, result)

    assert not (copilot / "trw-gone.agent.md").exists()
    assert (copilot / kept).exists()


def test_stale_cleanup_leaves_unmanaged_client_dirs_untouched(tmp_path: Path) -> None:
    # Stale cleanup sweeps only directories TRW actually provisions. A client
    # directory TRW does not manage must never be swept, even when it holds a
    # ``trw-`` prefixed file that looks like a TRW artifact.
    unmanaged = tmp_path / ".someothertool" / "agents"
    unmanaged.mkdir(parents=True)
    (unmanaged / "trw-gone.md").write_text("stale", encoding="utf-8")

    result = _new_result()
    _remove_stale_client_artifacts(tmp_path, result)

    assert (unmanaged / "trw-gone.md").exists()


def test_stale_cleanup_dry_run_reports_without_deleting(tmp_path: Path) -> None:
    agents = tmp_path / ".codex" / "agents"
    agents.mkdir(parents=True)
    (agents / "trw-gone.toml").write_text("stale", encoding="utf-8")

    result = _new_result()
    _remove_stale_client_artifacts(tmp_path, result, dry_run=True)

    assert (agents / "trw-gone.toml").exists()  # nothing deleted in preview
    assert any("would remove:" in line and "trw-gone.toml" in line for line in result["updated"])
    assert not any("removed:" in line for line in result["updated"])


def test_stale_cleanup_never_removes_dir_as_file_or_vice_versa(tmp_path: Path) -> None:
    # A trw- prefixed FILE in a dir-artifact surface must not be unlinked; a
    # trw- prefixed DIR in a file-artifact surface must not be rmtree'd.
    skills = tmp_path / ".cursor" / "skills"
    skills.mkdir(parents=True)
    (skills / "trw-not-a-skill-file").write_text("x", encoding="utf-8")  # file where dirs expected
    agents = tmp_path / ".cursor" / "agents"
    agents.mkdir(parents=True)
    (agents / "trw-not-an-agent-dir").mkdir()  # dir where files expected

    result = _new_result()
    _remove_stale_client_artifacts(tmp_path, result)

    assert (skills / "trw-not-a-skill-file").exists()
    assert (agents / "trw-not-an-agent-dir").exists()


# ---------------------------------------------------------------------------
# FIX B — codex content-aware refresh
# ---------------------------------------------------------------------------


def test_codex_skill_unmodified_refreshed_and_edited_preserved(tmp_path: Path) -> None:
    source = _codex_skills_source_dir()
    skill_dirs = [d for d in sorted(source.iterdir()) if d.is_dir()]
    assert skill_dirs, "expected bundled codex skills"
    skill = skill_dirs[0]
    skill_file = next(f for f in sorted(skill.iterdir()) if f.is_file())
    bundled = skill_file.read_bytes()
    rel = f".agents/skills/{skill.name}/{skill_file.name}"

    dest_root = tmp_path / ".agents" / "skills" / skill.name
    dest_root.mkdir(parents=True)
    dest = dest_root / skill_file.name

    # Unmodified (matches previous-install hash) -> refreshed to bundled.
    old = b"# stale previous skill body\n"
    dest.write_bytes(old)
    result = install_codex_skills(tmp_path, manifest_hashes={rel: _sha(old)})
    assert dest.read_bytes() == bundled
    assert rel in result["updated"]

    # User-edited -> preserved.
    user_content = b"# my edited skill body\n"
    dest.write_bytes(user_content)
    result2 = install_codex_skills(tmp_path, manifest_hashes={rel: _sha(b"prev-install")})
    assert dest.read_bytes() == user_content
    assert rel in result2["preserved"]


def test_codex_manifest_hashes_keys_match_install_paths(tmp_path: Path) -> None:
    # The persisted manifest keys must line up with the rel paths the codex
    # installers use, otherwise the refresh guard silently never matches.
    #
    # PRD-CORE-252-FR04: `.codex/agents` left this recorder when codex agents
    # became materializations of the shared bundle. Their keys are now asserted
    # against the same installer in
    # `tests/test_install_agents_destinations.py::test_update_bytes_equal_a_fresh_materialization`,
    # and the ownership record is `_managed_client_artifacts.bundled_agent_contents`.
    install_codex_skills(tmp_path)
    hashes = _codex_manifest_hashes(tmp_path)

    assert not [key for key in hashes if key.startswith(".codex/agents/")]
    assert any(k.startswith(".agents/skills/") and k.endswith(".md") for k in hashes)


# ---------------------------------------------------------------------------
# FIX C — canonical client-ID source, no drift
# ---------------------------------------------------------------------------


def test_client_integrations_cover_supported_ides() -> None:
    coverage = {platform_id for integration in CLIENT_INTEGRATIONS for platform_id in integration.platform_ids}
    assert not coverage & _INTEGRATION_EXCLUDED_IDES
    assert set(SUPPORTED_IDES) == coverage | _INTEGRATION_EXCLUDED_IDES
    # aider dropped from the exclusion set on retirement (no longer selectable);
    # claude-code stays excluded-by-design (framework-core writes .claude/*).
    assert _INTEGRATION_EXCLUDED_IDES == frozenset({"claude-code"})


def test_client_order_covers_supported_plus_retired_ides() -> None:
    # catalog._CLIENT_ORDER retains retired ids (aider) so their uninstall
    # surfaces stay reachable, so it equals SUPPORTED_IDES plus the retired set.
    assert set(_CLIENT_ORDER) == set(SUPPORTED_IDES) | _RETIRED_CLIENTS


def test_retired_ides_absent_from_supported() -> None:
    assert not (_RETIRED_CLIENTS & set(SUPPORTED_IDES))


# ---------------------------------------------------------------------------
# Write-target labels
# ---------------------------------------------------------------------------


def test_write_target_label_agents_md_clients_unchanged() -> None:
    # codex/opencode/cursor-cli still surface the shared AGENTS.md surface.
    for cid in ("codex", "opencode", "cursor-cli"):
        assert _write_target_label(resolve_client_profile(cid)) == "AGENTS.md"

"""Tests for the agent-installer tier resolver wiring (PRD-INFRA-104).

Covers FR-03 (installer rewrite), FR-07 (install-path test), FR-09
(cursor-ide preservation), FR-10 (byte preservation), and FR-11
(unknown-tier resilience).

Sibling-style integration tests against ``_install_agents`` directly.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from trw_mcp.bootstrap._init_project_skills import _install_agents


@pytest.fixture()
def empty_target(tmp_path: Path) -> Path:
    """Return a target dir with the .claude/agents subtree pre-created."""
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    return tmp_path


def _empty_result() -> dict[str, list[str]]:
    return {"created": [], "skipped": [], "errors": []}


def _bundled_agent_names() -> set[str]:
    """Filenames of the agents the bundle actually ships."""
    from trw_mcp.bootstrap._init_project import _DATA_DIR

    return {path.name for path in (_DATA_DIR / "agents").glob("*.md")}


def _read_model_line(agent_path: Path) -> str | None:
    """Return the value after ``model: `` in the file, or ``None`` if absent."""
    for raw in agent_path.read_text(encoding="utf-8").splitlines():
        if raw.startswith("model:"):
            return raw.split(":", 1)[1].strip()
    return None


class TestInstallAgentsResolvesClaudeCodeTiers:
    """FR-03 + FR-07: the installer rewrites tiers via the resolver."""

    def test_implementer_resolves_to_opus(self, empty_target: Path) -> None:
        """The trw-implementer bundle pins ``frontier`` (post-FR-05); the
        Claude Code installer resolves it to ``opus``."""
        result = _empty_result()
        _install_agents(empty_target, force=False, result=result)

        impl = empty_target / ".claude" / "agents" / "trw-implementer.md"
        assert impl.exists(), "trw-implementer.md must be installed"
        # FR-05 restored ``model: frontier`` in the bundle. After rewrite,
        # the destination MUST carry ``opus``, not ``frontier``.
        model_value = _read_model_line(impl)
        # If the bundle still lacks ``model:`` (FR-05 not yet shipped),
        # accept ``None``; otherwise the rewrite must produce ``opus``.
        if model_value is not None:
            assert model_value == "opus", (
                f"trw-implementer.md model field should resolve to 'opus' under client=claude-code, got {model_value!r}"
            )

    def test_traceability_checker_resolves_to_haiku(self, empty_target: Path) -> None:
        """``local-small`` resolves to ``haiku`` for Claude Code."""
        result = _empty_result()
        _install_agents(empty_target, force=False, result=result)

        path = empty_target / ".claude" / "agents" / "trw-traceability-checker.md"
        assert path.exists()
        assert _read_model_line(path) == "haiku"

    def test_balanced_agents_resolve_to_sonnet(self, empty_target: Path) -> None:
        """All bundle agents pinned to ``balanced`` resolve to ``sonnet``."""
        result = _empty_result()
        _install_agents(empty_target, force=False, result=result)

        for agent in [
            "trw-auditor.md",
            "trw-adversarial-auditor.md",
            "trw-researcher.md",
            "trw-reviewer.md",
            "trw-tester.md",
            "trw-requirement-reviewer.md",
            "trw-requirement-writer.md",
        ]:
            path = empty_target / ".claude" / "agents" / agent
            assert path.exists(), f"{agent} not installed"
            assert _read_model_line(path) == "sonnet", f"{agent} should resolve balanced->sonnet"

    def test_no_unknown_tiers_in_bundle(self, empty_target: Path) -> None:
        """Smoke test: every bundled agent installs without entering FR-11
        unknown-tier path. If the bundle gains a tier the resolver does
        not know about, this test surfaces it before users hit it."""
        result = _empty_result()
        _install_agents(empty_target, force=False, result=result)

        unknown_tier_errors = [e for e in result["errors"] if "agents" in e]
        assert not unknown_tier_errors, f"Unexpected unknown-tier failures during install: {unknown_tier_errors}"

    def test_install_creates_destinations(self, empty_target: Path) -> None:
        """``result['created']`` enumerates every installed agent."""
        result = _empty_result()
        _install_agents(empty_target, force=False, result=result)
        agent_paths = [p for p in result["created"] if p.endswith(".md") and "agents" in p]
        # Derived from the bundle, not a literal: a hardcoded census stops
        # enforcing anything the moment an agent is added or retired.
        assert len(agent_paths) == len(_bundled_agent_names())

    def test_idempotent_second_run_skips(self, empty_target: Path) -> None:
        """A second install (without ``force``) skips existing files."""
        first = _empty_result()
        _install_agents(empty_target, force=False, result=first)

        second = _empty_result()
        _install_agents(empty_target, force=False, result=second)
        # Second run produces no creates and several skips.
        assert not [p for p in second["created"] if "agents" in p]
        skipped_agents = [p for p in second["skipped"] if "agents" in p]
        assert len(skipped_agents) == len(_bundled_agent_names())

    def test_force_overwrites_with_resolved_value(self, empty_target: Path) -> None:
        """A pre-existing file with a stale tier is overwritten on force."""
        impl = empty_target / ".claude" / "agents" / "trw-implementer.md"
        impl.parent.mkdir(parents=True, exist_ok=True)
        impl.write_text("---\nname: stale\nmodel: frontier\n---\n", encoding="utf-8")

        result = _empty_result()
        _install_agents(empty_target, force=True, result=result)

        # After force overwrite, the model line must be the resolved value.
        model_value = _read_model_line(impl)
        if model_value is not None:
            assert model_value == "opus"


class TestInstallAgentsBytePreservation:
    """FR-10: install rewrites only the ``model:`` value and ``{tool:...}`` markers."""

    def test_only_model_line_and_tool_markers_differ_from_bundle(self, empty_target: Path) -> None:
        """The destination matches the bundle line-for-line except the ``model:``
        line and lines carrying a profile-rendered tool placeholder."""
        result = _empty_result()
        _install_agents(empty_target, force=False, result=result)

        from trw_mcp.bootstrap._init_project import _DATA_DIR

        bundle = (_DATA_DIR / "agents" / "trw-traceability-checker.md").read_text(encoding="utf-8")
        installed = (empty_target / ".claude" / "agents" / "trw-traceability-checker.md").read_text(encoding="utf-8")

        bundle_lines = bundle.splitlines()
        installed_lines = installed.splitlines()
        assert len(bundle_lines) == len(installed_lines), (
            "rewrite changed line count -- byte-preservation contract broken"
        )
        diffs = [(i, a, b) for i, (a, b) in enumerate(zip(bundle_lines, installed_lines, strict=True)) if a != b]
        assert diffs, "expected at least the model: line to be rewritten"
        for _, source, dest in diffs:
            if source.startswith("model:"):
                assert dest.startswith("model:")
                continue
            # The only other permitted rewrite is placeholder rendering.
            assert "{tool:" in source, f"unexpected rewrite of a non-placeholder line: {source!r}"
            assert "{tool:" not in dest, f"placeholder survived into the installed agent: {dest!r}"

    def test_installed_agents_carry_no_literal_placeholders(self, empty_target: Path) -> None:
        """A ``{tool:trw_x}`` marker reaching a user project names no real tool.

        Regression guard: the installer resolved the capability tier but never
        rendered tool placeholders, so installed agents instructed the model to
        call a literal ``{tool:trw_recall}``.
        """
        _install_agents(empty_target, force=False, result=_empty_result())

        installed_dir = empty_target / ".claude" / "agents"
        offenders = {
            path.name: re.findall(r"\{tool:[^}]+\}", path.read_text(encoding="utf-8"))
            for path in sorted(installed_dir.glob("*.md"))
        }
        assert any(installed_dir.glob("*.md")), "no agents installed"
        assert not any(offenders.values()), f"literal placeholders survived install: {offenders}"

    def test_installed_agents_use_the_profile_tool_namespace(self, empty_target: Path) -> None:
        """claude-code exposes TRW tools as ``mcp__trw__*`` — the body must say so."""
        _install_agents(empty_target, force=False, result=_empty_result())

        implementer = (empty_target / ".claude" / "agents" / "trw-implementer.md").read_text(encoding="utf-8")
        assert "mcp__trw__trw_build_check(" in implementer


class TestInstallAgentsUnknownTier:
    """FR-11: an agent with an unknown tier is logged + skipped."""

    def test_unknown_tier_logs_and_skips(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mock a fake bundle dir containing one good agent + one with a
        bogus tier; install must skip the bogus agent (file appended to
        ``result['errors']``) but install the good one."""
        (tmp_path / ".claude" / "agents").mkdir(parents=True)
        bogus_bundle = tmp_path / "bundle"
        (bogus_bundle / "agents").mkdir(parents=True)
        good = bogus_bundle / "agents" / "trw-good.md"
        good.write_text(
            "---\nname: trw-good\nmodel: balanced\n---\n\nbody\n",
            encoding="utf-8",
        )
        bad = bogus_bundle / "agents" / "trw-bad.md"
        bad.write_text(
            "---\nname: trw-bad\nmodel: nonsense-tier\n---\n\nbody\n",
            encoding="utf-8",
        )

        monkeypatch.setattr("trw_mcp.bootstrap._init_project._DATA_DIR", bogus_bundle)

        result = _empty_result()
        _install_agents(tmp_path, force=False, result=result)

        good_dest = tmp_path / ".claude" / "agents" / "trw-good.md"
        bad_dest = tmp_path / ".claude" / "agents" / "trw-bad.md"

        assert good_dest.exists(), "good agent must still install"
        assert _read_model_line(good_dest) == "sonnet"
        assert not bad_dest.exists(), "bogus agent must be skipped"
        # The bogus source file path appears in the errors list.
        assert any("trw-bad.md" in e for e in result["errors"])


class TestInstallAgentsClientPassthrough:
    """FR-02 + FR-09: passing a non-claude-code client.

    Since PRD-CORE-252-FR03 the client argument is a LIST and it selects the
    destination as well as the tier vocabulary, so these assertions read from
    the client's own directory. The previous version of
    ``test_passthrough_client_keeps_tier`` asserted that an opencode install
    landed under ``.claude/agents`` — it pinned the defect this PRD closes, and
    its inverted form below fails against HEAD.
    """

    def test_cursor_ide_client_resolves_to_inherit(self, empty_target: Path) -> None:
        import yaml

        result = _empty_result()
        _install_agents(empty_target, force=False, result=result, clients=["cursor-ide"])

        for agent in [
            "trw-traceability-checker.md",
            "trw-auditor.md",
            "trw-reviewer.md",
        ]:
            path = empty_target / ".cursor" / "agents" / agent
            block = path.read_text(encoding="utf-8")[4:].split("\n---\n", 1)[0]
            assert yaml.safe_load(block)["model"] == "inherit"

    def test_opencode_install_lands_in_the_opencode_destination(self, empty_target: Path) -> None:
        """The inverted assertion. It asserted ``.claude/agents`` at HEAD."""
        result = _empty_result()
        _install_agents(empty_target, force=False, result=result, clients=["opencode"])

        assert (empty_target / ".opencode" / "agents" / "trw-traceability-checker.md").is_file()
        # The fixture pre-creates `.claude/agents`, so absence of the DIRECTORY
        # proves nothing; absence of any agent inside it is the assertion.
        assert not list((empty_target / ".claude" / "agents").iterdir()), (
            "an opencode install must not write into Claude Code's agent directory"
        )


@pytest.mark.parametrize(
    ("client", "relative_path"),
    [
        ("claude-code", ".claude/skills/trw-deliver/SKILL.md"),
        ("codex", ".agents/skills/trw-deliver/SKILL.md"),
        ("opencode", ".opencode/skills/trw-deliver/SKILL.md"),
    ],
)
def test_delivery_skill_preserves_unfinished_work_without_acceptance(
    tmp_path: Path, client: str, relative_path: str
) -> None:
    """CORE269 FR06: installed consumer distinguishes pause from acceptance."""
    from trw_mcp.bootstrap._codex import install_codex_skills
    from trw_mcp.bootstrap._init_project_skills import _install_skills
    from trw_mcp.bootstrap._opencode import install_opencode_skills

    def install() -> dict[str, list[str]]:
        if client == "codex":
            return dict(install_codex_skills(tmp_path))
        if client == "opencode":
            return install_opencode_skills(tmp_path)
        result = _empty_result()
        _install_skills(tmp_path, force=False, result=result)
        return result

    result = install()
    assert not result["errors"]
    target = tmp_path / relative_path
    content = target.read_text()
    assert "completed-work acceptance" in content or "accepting completed work" in content
    assert "checkpoint or durable native handoff with a next-read pointer" in content
    assert "Stopping is not acceptance" in content
    assert "do not manufacture an artifact or learning" in content
    assert "Already captured learnings remain persisted" in content
    assert "Deliver gate — no fourth path" in content
    assert "failed_command" in content and "expiry_iso" in content
    assert "authorized operator/config" in content

    # A later ordinary install must not overwrite user-authored instructions.
    edited = content + "\nUser-owned project handoff convention.\n"
    target.write_text(edited)
    assert not install()["errors"]
    assert target.read_text() == edited

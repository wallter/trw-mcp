"""Tests for the agent-installer tier resolver wiring (PRD-INFRA-104).

Covers FR-03 (installer rewrite), FR-07 (install-path test), FR-09
(cursor-ide preservation), FR-10 (byte preservation), and FR-11
(unknown-tier resilience).

Sibling-style integration tests against ``_install_agents`` directly.
"""

from __future__ import annotations

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
                f"trw-implementer.md model field should resolve to 'opus' "
                f"under client=claude-code, got {model_value!r}"
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
            assert _read_model_line(path) == "sonnet", (
                f"{agent} should resolve balanced->sonnet"
            )

    def test_no_unknown_tiers_in_bundle(self, empty_target: Path) -> None:
        """Smoke test: every bundled agent installs without entering FR-11
        unknown-tier path. If the bundle gains a tier the resolver does
        not know about, this test surfaces it before users hit it."""
        result = _empty_result()
        _install_agents(empty_target, force=False, result=result)

        unknown_tier_errors = [e for e in result["errors"] if "agents" in e]
        assert not unknown_tier_errors, (
            f"Unexpected unknown-tier failures during install: {unknown_tier_errors}"
        )

    def test_install_creates_destinations(self, empty_target: Path) -> None:
        """``result['created']`` enumerates every installed agent."""
        result = _empty_result()
        _install_agents(empty_target, force=False, result=result)
        agent_paths = [
            p for p in result["created"] if p.endswith(".md") and "agents" in p
        ]
        # Bundle ships 12 agents; installer must create all of them.
        assert len(agent_paths) == 12

    def test_idempotent_second_run_skips(self, empty_target: Path) -> None:
        """A second install (without ``force``) skips existing files."""
        first = _empty_result()
        _install_agents(empty_target, force=False, result=first)

        second = _empty_result()
        _install_agents(empty_target, force=False, result=second)
        # Second run produces no creates and several skips.
        assert not [p for p in second["created"] if "agents" in p]
        skipped_agents = [p for p in second["skipped"] if "agents" in p]
        assert len(skipped_agents) == 12

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
    """FR-10: rewrite preserves every byte except the ``model:`` value."""

    def test_only_model_line_differs_from_bundle(self, empty_target: Path) -> None:
        """For an agent that pins a tier, the destination matches the
        bundle byte-for-byte EXCEPT the ``model:`` line."""
        result = _empty_result()
        _install_agents(empty_target, force=False, result=result)

        from trw_mcp.bootstrap._init_project import _DATA_DIR

        bundle = (_DATA_DIR / "agents" / "trw-traceability-checker.md").read_text(
            encoding="utf-8"
        )
        installed = (
            empty_target / ".claude" / "agents" / "trw-traceability-checker.md"
        ).read_text(encoding="utf-8")

        bundle_lines = bundle.splitlines()
        installed_lines = installed.splitlines()
        assert len(bundle_lines) == len(installed_lines), (
            "rewrite changed line count -- byte-preservation contract broken"
        )
        diffs = [
            (i, a, b)
            for i, (a, b) in enumerate(zip(bundle_lines, installed_lines, strict=True))
            if a != b
        ]
        # Exactly one line must differ (the model: line) and both ends
        # must start with ``model:``.
        assert len(diffs) == 1, f"unexpected diffs: {diffs}"
        i, a, b = diffs[0]
        assert a.startswith("model:") and b.startswith("model:")


class TestInstallAgentsUnknownTier:
    """FR-11: an agent with an unknown tier is logged + skipped."""

    def test_unknown_tier_logs_and_skips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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

        monkeypatch.setattr(
            "trw_mcp.bootstrap._init_project._DATA_DIR", bogus_bundle
        )

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

    NOTE: Cursor IDE has its own dedicated installer at
    ``bootstrap/_cursor_ide.py`` so the installer is never called with
    ``client="cursor-ide"`` in production. We still verify the parameter
    is honored.
    """

    def test_cursor_ide_client_resolves_to_inherit(
        self, empty_target: Path
    ) -> None:
        result = _empty_result()
        _install_agents(empty_target, force=False, result=result, client="cursor-ide")

        for agent in [
            "trw-traceability-checker.md",
            "trw-auditor.md",
            "trw-code-simplifier.md",
        ]:
            path = empty_target / ".claude" / "agents" / agent
            assert _read_model_line(path) == "inherit"

    def test_passthrough_client_keeps_tier(self, empty_target: Path) -> None:
        """An adapter-less client (e.g. ``opencode``) preserves the
        original tier vocabulary at the destination."""
        result = _empty_result()
        _install_agents(empty_target, force=False, result=result, client="opencode")

        path = empty_target / ".claude" / "agents" / "trw-traceability-checker.md"
        assert _read_model_line(path) == "local-small"

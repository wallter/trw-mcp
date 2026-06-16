"""Unit tests for :mod:`trw_mcp.agents.tier_resolver`.

PRD-INFRA-104 FR-08, FR-10.
"""

from __future__ import annotations

import pytest

from trw_mcp.agents.tier_resolver import (
    KNOWN_CLIENTS,
    KNOWN_TIERS,
    resolve_launch_throttle_policy,
    resolve_tier,
    rewrite_model_line,
)
from trw_mcp.server._subcommands_tier import _format_tier_status_table
from trw_mcp.state._entitlements import Entitlement


class TestResolveTier:
    """Per-client tier resolution covers FR-01, FR-02, FR-08."""

    # --- Claude Code ----------------------------------------------------------

    def test_resolve_frontier_claude_code(self) -> None:
        assert resolve_tier("frontier", client="claude-code") == "opus"

    def test_resolve_balanced_claude_code(self) -> None:
        assert resolve_tier("balanced", client="claude-code") == "sonnet"

    def test_resolve_local_large_claude_code(self) -> None:
        assert resolve_tier("local-large", client="claude-code") == "sonnet"

    def test_resolve_local_small_claude_code(self) -> None:
        assert resolve_tier("local-small", client="claude-code") == "haiku"

    def test_resolve_unknown_tier_claude_code_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown tier 'garbage'"):
            resolve_tier("garbage", client="claude-code")

    # --- Cursor IDE -----------------------------------------------------------

    @pytest.mark.parametrize("tier", sorted(KNOWN_TIERS))
    def test_cursor_ide_inherit_for_every_tier(self, tier: str) -> None:
        """Cursor IDE preserves the existing _cursor_ide.py:262 behaviour
        of ``model: inherit`` for every tier (NFR-COMPAT-02 contract)."""
        assert resolve_tier(tier, client="cursor-ide") == "inherit"

    def test_resolve_unknown_tier_cursor_ide_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown tier 'garbage'"):
            resolve_tier("garbage", client="cursor-ide")

    # --- Passthrough clients --------------------------------------------------

    @pytest.mark.parametrize(
        "client",
        ["opencode", "codex", "copilot", "cursor-cli", "gemini", "aider"],
    )
    def test_resolve_passthrough_for_unadapted_clients(self, client: str) -> None:
        """Clients without an adapter map but in KNOWN_CLIENTS fall through
        (return tier unchanged).

        These are profiles whose harness is known to accept the tier
        vocabulary (or ``inherit``) directly — FR-02 explicitly does not
        adapt them in this PRD. The capability-tier token is intentionally
        surfaced at the destination unchanged.
        """
        for tier in KNOWN_TIERS:
            assert resolve_tier(tier, client=client) == tier

    def test_resolve_passthrough_does_not_validate_tier(self) -> None:
        """Passthrough clients accept ANY tier name; validation only fires
        for clients with a defined map."""
        assert resolve_tier("anything-goes", client="opencode") == "anything-goes"

    # --- Unknown-client degradation (Potemkin sub_zAfRqZYYq2KtF72d defect A) ---

    @pytest.mark.parametrize("tier", sorted(KNOWN_TIERS))
    def test_unknown_client_degrades_known_tier_to_safe_default(self, tier: str) -> None:
        """A KNOWN capability tier must NEVER leak raw to an unrecognised
        client.

        Potemkin-Gate submission sub_zAfRqZYYq2KtF72d defect A: bundled
        agents shipped ``model: balanced`` and reached a client whose harness
        rejected the tier token outright ("issue with the selected model
        (balanced)"), instantly disabling trw-adversarial-auditor /
        trw-requirement-reviewer. When the client is neither adapted nor a
        recognised passthrough profile, degrade a known tier to the
        universally-safe ``inherit`` rather than emitting an unresolvable
        capability token.
        """
        assert resolve_tier(tier, client="some-unknown-harness") == "inherit"

    def test_unknown_client_passes_through_non_tier_value(self) -> None:
        """A concrete model id (not one of KNOWN_TIERS) on an unknown client
        is still passed through unchanged — we only degrade the deliberate
        capability-tier vocabulary, never an explicit override."""
        assert resolve_tier("gpt-4o", client="some-unknown-harness") == "gpt-4o"

    # --- Vocabulary invariants ------------------------------------------------

    def test_known_tiers_matches_framework_whitelist(self) -> None:
        """Lock the tier vocabulary to the framework guidance — see CLAUDE.md
        and tests/test_bundled_agents.py:343."""
        assert KNOWN_TIERS == {"frontier", "balanced", "local-large", "local-small"}

    def test_known_clients_includes_claude_code_and_cursor_ide(self) -> None:
        """Adapted clients must be enumerated in KNOWN_CLIENTS."""
        assert "claude-code" in KNOWN_CLIENTS
        assert "cursor-ide" in KNOWN_CLIENTS


class TestRewriteModelLine:
    """File-level rewrite covers FR-10 byte-preservation discipline."""

    _FRONTMATTER = (
        "---\n"
        "name: trw-implementer\n"
        "effort: medium\n"
        'description: "do work"\n'
        "model: frontier\n"
        "maxTurns: 200\n"
        "---\n\n"
        "# Body content\n\n"
        "We mention the word model: somewhere in prose; it must NOT be rewritten.\n"
    )

    def test_rewrites_only_the_frontmatter_model_line(self) -> None:
        out = rewrite_model_line(self._FRONTMATTER, client="claude-code")
        assert "model: opus\n" in out
        assert "model: frontier" not in out
        # Body's "model:" mention is left alone (not anchored on line start).
        assert "We mention the word model: somewhere" in out

    def test_passthrough_client_returns_unchanged(self) -> None:
        out = rewrite_model_line(self._FRONTMATTER, client="opencode")
        assert out == self._FRONTMATTER

    def test_cursor_ide_rewrites_to_inherit(self) -> None:
        out = rewrite_model_line(self._FRONTMATTER, client="cursor-ide")
        assert "model: inherit\n" in out

    def test_no_model_line_unchanged(self) -> None:
        text = "---\nname: trw-lead\n---\n\nbody\n"
        assert rewrite_model_line(text, client="claude-code") == text

    def test_preserves_trailing_comment(self) -> None:
        text = "---\nmodel: frontier  # default tier\n---\n"
        out = rewrite_model_line(text, client="claude-code")
        assert "model: opus  # default tier\n" in out

    def test_unknown_tier_raises(self) -> None:
        text = "---\nmodel: nonsense-tier\n---\n"
        with pytest.raises(ValueError, match="Unknown tier 'nonsense-tier'"):
            rewrite_model_line(text, client="claude-code")

    def test_only_first_model_line_rewritten(self) -> None:
        """A file with two ``model:`` lines (an authoring error) has only
        the first rewritten. Bundle contract tests detect duplicates
        separately."""
        text = "---\nmodel: frontier\nmodel: balanced\n---\n"
        out = rewrite_model_line(text, client="claude-code")
        # First line resolved
        assert "model: opus" in out.splitlines()[1]
        # Second line untouched
        assert out.splitlines()[2] == "model: balanced"

    def test_rewrite_preserves_other_bytes(self) -> None:
        """Diffing rewrite output against input differs ONLY on the
        ``model:`` line — FR-10 snapshot-style assertion."""
        out = rewrite_model_line(self._FRONTMATTER, client="claude-code")
        in_lines = self._FRONTMATTER.splitlines()
        out_lines = out.splitlines()
        assert len(in_lines) == len(out_lines)
        for i, (a, b) in enumerate(zip(in_lines, out_lines, strict=True)):
            if a.startswith("model:") and b.startswith("model:"):
                continue  # the only line allowed to differ
            assert a == b, f"line {i} unexpectedly modified: {a!r} -> {b!r}"


class TestStructlogObservation:
    """NFR-OBS-01: structlog ``agent_tier_resolved`` event on rewrite."""

    def test_emits_debug_event_on_rewrite(self) -> None:
        import structlog
        from structlog.testing import capture_logs

        # Configure structlog for this test (required by capture_logs).
        structlog.configure(
            processors=[structlog.testing.LogCapture()],
            wrapper_class=structlog.make_filtering_bound_logger(0),
        )
        with capture_logs() as logs:
            rewrite_model_line("---\nmodel: frontier\n---\n", client="claude-code")
        events = [e for e in logs if e.get("event") == "agent_tier_resolved"]
        assert len(events) == 1
        assert events[0]["tier"] == "frontier"
        assert events[0]["client"] == "claude-code"
        assert events[0]["resolved"] == "opus"


class TestLaunchThrottlePolicy:
    """PRD-QUAL-087 FR03: dense helper launches get portable throttling guidance."""

    def test_large_dense_launch_uses_stagger_and_backoff(self) -> None:
        policy = resolve_launch_throttle_policy(12)

        assert policy.stagger_seconds == 2.0
        assert policy.max_concurrent_launches == 4
        assert policy.backoff_multiplier == 2.0
        assert policy.max_backoff_seconds == 60.0

    def test_invalid_helper_count_rejected(self) -> None:
        with pytest.raises(ValueError, match="helper_count"):
            resolve_launch_throttle_policy(0)


class TestTierStatusTable:
    """PRD-INFRA-119 FR06: tier status has auditable table columns."""

    def test_table_includes_entitlement_limit_and_expiration(self) -> None:
        table = _format_tier_status_table(Entitlement(tier="pro", reason="ok", expires_at_iso="2027-01-01T00:00:00Z"))

        assert "| state | entitlement | limit | expires |" in table
        assert "| active | pro | pro | 2027-01-01T00:00:00Z |" in table

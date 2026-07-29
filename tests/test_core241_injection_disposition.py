"""PRD-CORE-241 — why `agents_md_learning_injection` is KEPT, pinned as tests.

PRD-CORE-093 removed learning promotion from CLAUDE.md on three grounds: ~1,800
tokens per message, prompt-cache invalidation from a post-delivery re-sync, and
redundancy with `trw_session_start`'s focused recall. The obvious next step is to
apply the same reasoning to the structurally identical AGENTS.md mechanism.

It does not transfer, and these tests are here so nobody re-derives that the hard
way. The decisive fact is FR01: `claude-code` — the profile PRD-CORE-093 governed
— does not write AGENTS.md at all, while the profiles that DO write it have the
channels that would push an agent toward `session_start` switched off. For those
clients the injected section is not redundant with recall; it is the only static
learning channel they have. Removing it is a regression wearing a cleanup's
clothes.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig

# Ceiling for the rendered section. PRD-CORE-093 attributed ~1,800 tokens to the
# CLAUDE.md promotion; the AGENTS.md section measured ~150 tokens over a live
# store. 4,000 chars (~1,000 tokens) sits well above the observed size and well
# below the figure that justified the CLAUDE.md removal, so a regression toward
# CLAUDE.md-scale cost trips this before it ships.
_SECTION_CHAR_CEILING = 4_000


def _profile_matrix() -> dict[str, dict[str, bool]]:
    """Write-target and surface-flag matrix for the profiles FR01 governs."""
    from trw_mcp.models.config import resolve_client_profile

    matrix: dict[str, dict[str, bool]] = {}
    for name in ("claude-code", "opencode", "codex"):
        profile = resolve_client_profile(name)
        # write_targets is a sequence of (flag, value) pairs, not paths — reading
        # it as text and searching for "AGENTS.md" finds nothing and looks like a
        # disproof of FR01. It is not.
        targets = dict(profile.write_targets or ())
        matrix[name] = {
            "agents_md": bool(targets.get("agents_md", False)),
            "hooks": bool(profile.hooks_enabled),
            "mcp_instructions": bool(profile.mcp_instructions_enabled),
        }
    return matrix


class TestAgentsMdInjectionIsNotRedundant:
    """FR01 — the profiles that read AGENTS.md are not the ones recall covers."""

    def test_agents_md_consumers_lack_session_start_enforcement(self) -> None:
        matrix = _profile_matrix()

        # The profile PRD-CORE-093 governed does not share this surface at all,
        # so its conclusion cannot be carried across by analogy.
        assert matrix["claude-code"]["agents_md"] is False, (
            "claude-code writing AGENTS.md would mean the two surfaces share a consumer "
            "and PRD-CORE-093's redundancy argument might transfer — re-open the disposition"
        )

        # PRD-CORE-240-FR04 withdrew opencode's shared AGENTS.md, so codex is now
        # the light client that shares the surface. The disposition argument is
        # unchanged — it turns on SOME light client sharing AGENTS.md with the
        # injection consumer, not on which one.
        assert matrix["codex"]["agents_md"] is True, "codex is expected to write AGENTS.md"
        assert matrix["opencode"]["agents_md"] is False, (
            "opencode was withdrawn from the shared surface (PRD-CORE-240-FR04); if this "
            "flips back, re-check whether the disposition argument still holds for it"
        )

        # The reach argument still applies to BOTH light clients, whether or not
        # they share AGENTS.md: it is about how the client reaches trw_session_start.
        for name in ("opencode", "codex"):
            assert not (matrix[name]["hooks"] and matrix[name]["mcp_instructions"]), (
                f"{name} gained both hooks and mcp_instructions; if it now reliably reaches "
                "trw_session_start, the injected section may genuinely be redundant and "
                "PRD-CORE-241's KEEP verdict should be re-measured rather than assumed"
            )


class TestInjectedSectionCost:
    """FR02 — the section stays far below the cost that justified the CLAUDE.md removal."""

    def test_injected_section_cost_is_recorded(self) -> None:
        from trw_mcp.state.claude_md._agents_md import _inject_learnings_to_agents

        learnings = [
            {
                "id": f"L-{i:04d}",
                "summary": f"Learning {i}: a representative one-line summary of a discovery.",
                "detail": "Detail body that the renderer is expected to omit or truncate.",
                "tags": ["testing"],
            }
            for i in range(5)
        ]
        rendered = _inject_learnings_to_agents("# AGENTS\n\nBody.\n", learnings)  # type: ignore[arg-type]
        section = rendered[len("# AGENTS\n\nBody.\n") :]

        assert len(section) < _SECTION_CHAR_CEILING, (
            f"injected section is {len(section)} chars, at or above the {_SECTION_CHAR_CEILING} "
            "ceiling — approaching the cost that justified removing the CLAUDE.md equivalent"
        )


class TestRetiredMessengerStaysRetired:
    """FR07 — the removed nudge messenger must not be reachable through config."""

    def test_config_rejects_retired_learning_injection_messenger(self) -> None:
        with pytest.raises(Exception) as excinfo:
            TRWConfig(nudge_messenger="learning_injection")  # type: ignore[arg-type]
        # Pydantic echoes the rejected input, so the term appears in the message
        # regardless. What matters is that it is absent from the permitted set.
        message = str(excinfo.value)
        allowed = message.split("Input should be")[-1].split("[type=")[0]
        assert "learning_injection" not in allowed, (
            f"learning_injection is still an accepted nudge_messenger value: {allowed}"
        )

    @pytest.mark.parametrize("messenger", ["standard", "contextual"])
    def test_live_messengers_still_validate(self, messenger: str) -> None:
        config = TRWConfig(nudge_messenger=messenger)  # type: ignore[arg-type]
        assert config.nudge_messenger == messenger

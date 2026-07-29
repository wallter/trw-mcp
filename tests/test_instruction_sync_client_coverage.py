"""Every client profile is either sync-driven or explicitly excluded.

``trw_instructions_sync`` is the call the TRW protocol mandates at delivery. It
dispatches through ``_INSTRUCTION_SYNC_GENERATORS``, a table that named three of
the seven client profiles with no companion exclusion set and no totality test.
A profile missing from the table takes the same code path as "nothing to do", so
``antigravity-cli`` shipped with a working ``ANTIGRAVITY.md`` generator that the
delivery-time sync could never reach — reachable only from install-time
bootstrap, and therefore never refreshed after install.

That is wiring-defect pattern P11 (subset registry with no derivation), where the
fix that matters is not the missing entry but the mechanism that makes the next
omission impossible. These tests are that mechanism.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.config._profiles import _PROFILES
from trw_mcp.state.claude_md._agents_md import (
    _INSTRUCTION_SYNC_CLIENT_IDS,
    _INSTRUCTION_SYNC_EXCLUSIONS,
    _INSTRUCTION_SYNC_GENERATORS,
)


class TestClientCoverageIsTotal:
    def test_every_profile_is_driven_or_excluded(self) -> None:
        driven = set(_INSTRUCTION_SYNC_CLIENT_IDS)
        excluded = set(_INSTRUCTION_SYNC_EXCLUSIONS)
        unaccounted = set(_PROFILES) - driven - excluded

        assert not unaccounted, (
            f"client profile(s) {sorted(unaccounted)} are neither driven by "
            "_INSTRUCTION_SYNC_GENERATORS nor listed in _INSTRUCTION_SYNC_EXCLUSIONS. "
            "A profile in neither set is skipped silently by trw_instructions_sync."
        )

    def test_driven_and_excluded_sets_are_disjoint(self) -> None:
        overlap = set(_INSTRUCTION_SYNC_CLIENT_IDS) & set(_INSTRUCTION_SYNC_EXCLUSIONS)
        assert not overlap, f"{sorted(overlap)} is both driven and excluded"

    def test_every_exclusion_names_a_real_profile_and_states_a_reason(self) -> None:
        for client_id, reason in _INSTRUCTION_SYNC_EXCLUSIONS.items():
            assert client_id in _PROFILES, f"{client_id} is excluded but is not a real profile"
            assert reason.strip(), f"{client_id} is excluded with no stated reason"

    def test_every_driven_client_has_a_generator(self) -> None:
        missing = set(_INSTRUCTION_SYNC_CLIENT_IDS) - set(_INSTRUCTION_SYNC_GENERATORS)
        assert not missing, f"{sorted(missing)} are declared sync-capable but have no generator"


class TestAntigravitySurfaceIsReachableFromSync:
    """Regression: the specific surface the subset registry made unreachable."""

    def test_antigravity_is_declared_sync_capable(self) -> None:
        assert "antigravity-cli" in _INSTRUCTION_SYNC_CLIENT_IDS
        assert "antigravity-cli" in _INSTRUCTION_SYNC_GENERATORS

    def test_generator_writes_the_instruction_file(self, tmp_path: Path) -> None:
        """Behavioural, not registration-only: the file must actually appear."""
        from trw_mcp.state.claude_md._agents_md import _INSTRUCTION_SYNC_GENERATORS as gens

        gens["antigravity-cli"](tmp_path, False)

        written = tmp_path / "ANTIGRAVITY.md"
        assert written.is_file(), "the sync generator must produce ANTIGRAVITY.md"
        assert written.read_text(encoding="utf-8").strip(), "ANTIGRAVITY.md must not be empty"

    def test_generator_is_idempotent(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md._agents_md import _INSTRUCTION_SYNC_GENERATORS as gens

        gens["antigravity-cli"](tmp_path, False)
        first = (tmp_path / "ANTIGRAVITY.md").read_text(encoding="utf-8")
        gens["antigravity-cli"](tmp_path, False)
        second = (tmp_path / "ANTIGRAVITY.md").read_text(encoding="utf-8")

        assert first == second, "a second sync must be a byte-identical no-op"

    def test_generator_preserves_user_content_outside_the_markers(self, tmp_path: Path) -> None:
        from trw_mcp.state.claude_md._agents_md import _INSTRUCTION_SYNC_GENERATORS as gens

        gens["antigravity-cli"](tmp_path, False)
        path = tmp_path / "ANTIGRAVITY.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n## My notes\n\nKeep me.\n", encoding="utf-8")

        gens["antigravity-cli"](tmp_path, False)

        after = path.read_text(encoding="utf-8")
        assert "## My notes" in after
        assert "Keep me." in after

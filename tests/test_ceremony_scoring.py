"""Tests for PRD-CORE-098 ceremony reforms — claude_md_synced removal and ceremony weights.

Verifies:
- FR04: No claude_md_synced references remain in source
- FR07: CeremonyWeights defaults updated (checkpoint=20, review=10)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SRC_DIR = Path(__file__).parent.parent / "src" / "trw_mcp"


class TestFR04ClaudeMdSyncedRemoval:
    """FR04: Verify claude_md_synced tracking is fully removed."""

    def test_no_claude_md_synced_references(self) -> None:
        """Verify grep for 'claude_md_synced' returns zero source matches."""
        result = subprocess.run(
            ["grep", "-rn", "--binary-files=without-match", "claude_md_synced", str(SRC_DIR)],
            capture_output=True,
            text=True,
        )
        matches = [line for line in result.stdout.strip().split("\n") if line.strip()]
        assert len(matches) == 0, (
            f"Expected zero 'claude_md_synced' references in {SRC_DIR}, "
            f"found {len(matches)}:\n" + "\n".join(matches[:20])
        )


class TestFR07CeremonyWeights:
    """FR07: Verify CeremonyWeights defaults updated."""

    def test_ceremony_weights_sum_100(self) -> None:
        """Construct CeremonyWeights(), assert sum == 100."""
        from trw_mcp.models.config._client_profile import CeremonyWeights

        w = CeremonyWeights()
        total = w.session_start + w.deliver + w.checkpoint + w.learn + w.build_check + w.review
        assert total == 100

    def test_ceremony_weights_checkpoint_20(self) -> None:
        """Verify checkpoint default is 20."""
        from trw_mcp.models.config._client_profile import CeremonyWeights

        w = CeremonyWeights()
        assert w.checkpoint == 20

    def test_ceremony_weights_review_10(self) -> None:
        """Verify review default is 10."""
        from trw_mcp.models.config._client_profile import CeremonyWeights

        w = CeremonyWeights()
        assert w.review == 10

"""PRD-CORE-149 FR07: byte-identical parity test for claude-code protocol render.

Baseline captured at Wave 0 by driving ``ProtocolRenderer`` against the
``claude-code`` profile and concatenating the canonical section renderers in a
stable order. The SHA-256 sidecar fixes the expected digest.

Decomposition follow-up implementer: once ``_static_sections.py`` /
``_renderer.py`` / ``_sync.py`` are re-shaped into sub-packages (FR01, FR10,
FR11), this test MUST still pass byte-for-byte.

Note: this test deliberately exercises the deterministic renderer subset (no
user CLAUDE.md content, no learnings injection) so it is a stable regression
gate against the decomposition change alone.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from trw_mcp.models.config._profiles import resolve_client_profile
from trw_mcp.state.claude_md._renderer import ProtocolRenderer

pytestmark = pytest.mark.unit

FIXTURE_DIR = Path(__file__).parent / "fixtures"
BASELINE_PATH = FIXTURE_DIR / "claude_md_baseline.txt"
SHA_PATH = FIXTURE_DIR / "claude_md_baseline.txt.sha256"


def _render_baseline() -> str:
    profile = resolve_client_profile("claude-code")
    r = ProtocolRenderer(client_profile=profile, ceremony_mode="FULL")
    parts = [
        ("behavioral_protocol", r.render_behavioral_protocol()),
        ("ceremony_quick_ref", r.render_ceremony_quick_ref()),
        ("phase_descriptions", r.render_phase_descriptions()),
        ("ceremony_table", r.render_ceremony_table()),
        ("ceremony_flows", r.render_ceremony_flows()),
        ("framework_reference", r.render_framework_reference()),
        ("closing_reminder", r.render_closing_reminder()),
    ]
    return "".join(f"=== {name} ===\n{body}\n" for name, body in parts)


def test_baseline_fixture_exists() -> None:
    """Wave 0 baseline was captured."""
    assert BASELINE_PATH.exists(), f"missing baseline fixture at {BASELINE_PATH}"
    assert SHA_PATH.exists(), f"missing SHA sidecar at {SHA_PATH}"


def test_byte_identical_output() -> None:
    """Regenerated render must match the captured baseline byte-for-byte."""
    expected = BASELINE_PATH.read_text(encoding="utf-8")
    actual = _render_baseline()
    assert actual == expected, (
        "Renderer output diverged from baseline. "
        "If this is intentional (e.g., decomposition finished and output is "
        "genuinely identical), regenerate the fixture + SHA sidecar."
    )


def test_sha256_matches_baseline() -> None:
    """SHA-256 sidecar must match the fixture it accompanies."""
    content = BASELINE_PATH.read_bytes()
    expected_sha = SHA_PATH.read_text(encoding="utf-8").strip()
    actual_sha = hashlib.sha256(content).hexdigest()
    assert actual_sha == expected_sha, (
        f"SHA sidecar {SHA_PATH.name} stale: baseline file has changed but "
        "sidecar was not regenerated."
    )

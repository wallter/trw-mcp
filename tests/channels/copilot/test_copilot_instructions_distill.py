"""Tests for C1: CopilotInstructionsDistillRenderer (PRD-DIST-2406 FR02-FR07, NFR01).

Covers:
- test_marker_replace_distinct_from_ceremony (FR02)
- test_t1_write_under_budget (FR03)
- test_tier_down_ladder (FR03)
- test_token_cap_property — 50 parametrized inputs all <= 250 tokens (NFR01)
- test_t0_beacon_not_pruned_on_ttl_expiry (FR04)
- test_idempotency_same_sha (FR05)
- test_total_file_size_warning / test_quota_proximity_warning_fires (FR06)
- test_provenance_multiline_format (FR07)
- test_sidecar_absent_produces_t0_beacon
- test_concurrent_renders_skipped_lock (NFR05)
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sidecar(
    *,
    conventions: list[str] | None = None,
    hotspots: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    if conventions is None:
        conventions = ["Use structlog for all logging", "Pydantic v2 models only", "Type hints required"]
    if hotspots is None:
        hotspots = [
            {"file": "trw-mcp/src/trw_mcp/state/ceremony.py", "risk_score": 0.91, "reason": "high churn"},
            {"file": "backend/routers/admin.py", "risk_score": 0.87, "reason": "complex logic"},
            {"file": "trw-mcp/src/trw_mcp/tools/ceremony.py", "risk_score": 0.82, "reason": "god class risk"},
        ]
    return {"conventions": conventions, "hotspots": hotspots}


def _make_renderer() -> "CopilotInstructionsDistillRenderer":
    from trw_mcp.channels.copilot._instructions_distill import CopilotInstructionsDistillRenderer
    return CopilotInstructionsDistillRenderer()


# ---------------------------------------------------------------------------
# FR02 — marker replace distinct from ceremony
# ---------------------------------------------------------------------------


def test_marker_replace_distinct_from_ceremony(tmp_path: Path) -> None:
    """Distill markers are distinct from ceremony markers; ceremony section unchanged."""
    from trw_mcp.channels.copilot._instructions_distill import DISTILL_BEGIN, DISTILL_END

    # Pre-populate file with ceremony section
    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    instructions = github_dir / "copilot-instructions.md"
    ceremony_content = (
        "<!-- trw:copilot:start -->\n"
        "# TRW Ceremony\n"
        "<!-- trw:copilot:end -->\n"
    )
    instructions.write_text(ceremony_content, encoding="utf-8")

    renderer = _make_renderer()
    result = renderer.render(
        tmp_path,
        _make_sidecar(),
        sidecar_sha="abc123",
        target_file=instructions,
    )

    assert result.status == "written"
    content = instructions.read_text(encoding="utf-8")

    # Ceremony section unchanged
    assert "<!-- trw:copilot:start -->" in content
    assert "<!-- trw:copilot:end -->" in content
    assert "# TRW Ceremony" in content

    # Distill markers present and distinct
    assert DISTILL_BEGIN in content
    assert DISTILL_END in content

    # Distill appears after ceremony end
    ceremony_end_idx = content.index("<!-- trw:copilot:end -->")
    distill_begin_idx = content.index(DISTILL_BEGIN)
    assert distill_begin_idx > ceremony_end_idx


# ---------------------------------------------------------------------------
# FR03 — T1 write under budget
# ---------------------------------------------------------------------------


def test_t1_write_under_budget(tmp_path: Path) -> None:
    """Valid sidecar produces T1 segment with <= 250 token estimate."""
    from trw_mcp.channels.copilot._instructions_distill import (
        DISTILL_BEGIN,
        DISTILL_END,
        BUDGET_TOKENS,
        _count_tokens_estimate,
    )

    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    instructions = github_dir / "copilot-instructions.md"

    renderer = _make_renderer()
    result = renderer.render(
        tmp_path,
        _make_sidecar(),
        sidecar_sha="abc123",
        target_file=instructions,
    )

    assert result.status == "written"
    content = instructions.read_text(encoding="utf-8")
    assert DISTILL_BEGIN in content
    assert DISTILL_END in content

    # Extract interior between markers
    begin_idx = content.index(DISTILL_BEGIN) + len(DISTILL_BEGIN)
    end_idx = content.index(DISTILL_END)
    interior = content[begin_idx:end_idx]

    assert _count_tokens_estimate(interior) <= BUDGET_TOKENS


# ---------------------------------------------------------------------------
# FR03 — tier-down ladder
# ---------------------------------------------------------------------------


def test_tier_down_ladder(tmp_path: Path) -> None:
    """Verbose sidecar triggers tier-down; T0 beacon written."""
    from trw_mcp.channels.copilot._instructions_distill import DISTILL_BEGIN, DISTILL_END

    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    instructions = github_dir / "copilot-instructions.md"

    # Make extremely verbose sidecar that will exceed 250 tokens
    verbose_reason = "x" * 400
    sidecar = {
        "conventions": [verbose_reason, verbose_reason, verbose_reason, verbose_reason],
        "hotspots": [
            {"file": "a/b.py", "risk_score": 0.9, "reason": verbose_reason}
            for _ in range(5)
        ],
    }

    renderer = _make_renderer()
    result = renderer.render(
        tmp_path,
        sidecar,
        sidecar_sha="verbose123",
        target_file=instructions,
    )

    assert result.status in ("written",)
    content = instructions.read_text(encoding="utf-8")
    assert DISTILL_BEGIN in content
    assert DISTILL_END in content
    # Should have been tiered down
    assert result.tier_used in ("T0", "T1")


# ---------------------------------------------------------------------------
# NFR01 — token cap property: 50 parametrized inputs all <= 250 tokens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("size", range(0, 50))
def test_token_cap_property(size: int, tmp_path: Path) -> None:
    """All inputs produce <= 250-token segments between distill markers."""
    from trw_mcp.channels.copilot._instructions_distill import (
        DISTILL_BEGIN,
        DISTILL_END,
        BUDGET_TOKENS,
        _count_tokens_estimate,
    )

    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    instructions = github_dir / "copilot-instructions.md"

    # Generate varying-sized sidecar
    word = "a" * (size * 5 + 1)
    sidecar: dict[str, object] = {
        "conventions": [word] * (size + 1),
        "hotspots": [
            {"file": f"path/file_{i}.py", "risk_score": 0.5 + i * 0.01, "reason": word}
            for i in range(size + 1)
        ],
    }

    renderer = _make_renderer()
    result = renderer.render(
        tmp_path,
        sidecar,
        sidecar_sha=f"sha_{size}",
        target_file=instructions,
    )

    assert result.status in ("written",)
    content = instructions.read_text(encoding="utf-8")

    begin_idx = content.index(DISTILL_BEGIN) + len(DISTILL_BEGIN)
    end_idx = content.index(DISTILL_END)
    interior = content[begin_idx:end_idx]

    assert _count_tokens_estimate(interior) <= BUDGET_TOKENS, (
        f"size={size}: token estimate {_count_tokens_estimate(interior)} > {BUDGET_TOKENS}"
    )


# ---------------------------------------------------------------------------
# FR04 — T0 beacon not pruned on TTL expiry
# ---------------------------------------------------------------------------


def test_t0_beacon_not_pruned_on_ttl_expiry(tmp_path: Path) -> None:
    """TTL exceeded renders T0 beacon (TIER_DOWN), not a delete (FULL_PRUNE)."""
    from trw_mcp.channels.copilot._instructions_distill import (
        DISTILL_BEGIN,
        DISTILL_END,
        build_copilot_instructions_distill_entry,
    )
    from trw_mcp.channels._state import ChannelState, state_path_for, write_state
    from trw_mcp.channels._provenance import now_utc_iso8601

    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    instructions = github_dir / "copilot-instructions.md"

    # Write a state with a very old last_render_ts to force TTL expiry
    channels_dir = tmp_path / ".trw" / "channels"
    channels_dir.mkdir(parents=True)
    state_file = state_path_for("copilot-instructions-distill", channels_dir)
    old_state = ChannelState(
        channel_id="copilot-instructions-distill",
        last_render_tier="T1",
        last_render_bytes=100,
        last_render_tokens_est=50,
        last_sidecar_sha="old_sha",
        segment_interior_sha256="deadbeef",
        last_render_ts="2020-01-01T00:00:00Z",
    )
    write_state(old_state, state_file)

    renderer = _make_renderer()
    # Use a different sidecar SHA to ensure TTL check fires
    result = renderer.render(
        tmp_path,
        _make_sidecar(),
        sidecar_sha="new_sha",  # different from old_sha
        target_file=instructions,
    )

    # stale_action is TIER_DOWN, so file should contain T0 beacon, not be deleted
    assert result.status == "written"
    assert instructions.exists(), "File should NOT be deleted (stale_action is TIER_DOWN)"

    content = instructions.read_text(encoding="utf-8")
    # T0 beacon or distill segment should be present
    assert DISTILL_BEGIN in content or "trw-distill" in content


# ---------------------------------------------------------------------------
# FR05 — idempotency on same sidecar SHA
# ---------------------------------------------------------------------------


def test_idempotency_same_sha(tmp_path: Path) -> None:
    """Second render with same SHA returns skipped_conflict; file byte-identical."""
    from trw_mcp.channels.copilot._instructions_distill import DISTILL_BEGIN

    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    instructions = github_dir / "copilot-instructions.md"

    renderer = _make_renderer()
    # First render
    result1 = renderer.render(
        tmp_path,
        _make_sidecar(),
        sidecar_sha="sha-idemp",
        target_file=instructions,
    )
    assert result1.status == "written"
    content1 = instructions.read_text(encoding="utf-8")

    # Second render — same SHA
    result2 = renderer.render(
        tmp_path,
        _make_sidecar(),
        sidecar_sha="sha-idemp",
        target_file=instructions,
    )
    # Should be skipped_conflict (SHA matches)
    assert result2.status == "skipped_conflict"
    content2 = instructions.read_text(encoding="utf-8")
    assert content1 == content2


# ---------------------------------------------------------------------------
# FR06 — total file size warning
# ---------------------------------------------------------------------------


def test_quota_proximity_warning_fires(tmp_path: Path) -> None:
    """Large existing content triggers quota_proximity warning log."""
    import structlog.testing
    from trw_mcp.channels.copilot._instructions_distill import QUOTA_PROXIMITY_BYTES

    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    instructions = github_dir / "copilot-instructions.md"

    # Write a large existing file (just over the threshold)
    large_content = "A" * (QUOTA_PROXIMITY_BYTES + 500)
    instructions.write_text(large_content, encoding="utf-8")

    renderer = _make_renderer()
    with structlog.testing.capture_logs() as cap:
        result = renderer.render(
            tmp_path,
            _make_sidecar(),
            sidecar_sha="large-sha",
            target_file=instructions,
        )

    # Write should proceed (warn-only gate)
    assert result.status == "written"
    # Warning logged via structlog
    warning_events = [e for e in cap if e.get("log_level") == "warning"]
    assert any(
        "quota_proximity" in str(e.get("event", "")).lower() or
        "quota" in str(e.get("event", "")).lower()
        for e in warning_events
    ), f"Expected quota_proximity warning in: {warning_events}"


def test_total_file_size_warning(tmp_path: Path) -> None:
    """Alias for quota_proximity test — verifies FR06."""
    test_quota_proximity_warning_fires(tmp_path)


# ---------------------------------------------------------------------------
# FR07 — provenance multiline format
# ---------------------------------------------------------------------------


def test_provenance_multiline_format(tmp_path: Path) -> None:
    """Distill segment contains multiline provenance comment (FR07)."""
    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    instructions = github_dir / "copilot-instructions.md"

    renderer = _make_renderer()
    result = renderer.render(
        tmp_path,
        _make_sidecar(),
        sidecar_sha="prov-sha",
        target_file=instructions,
    )

    assert result.status == "written"
    content = instructions.read_text(encoding="utf-8")

    # Multiline provenance must include required fields
    assert "TRW:PROVENANCE" in content
    assert "generated_by: trw-mcp" in content
    assert "channel_id: copilot-instructions-distill" in content
    assert "sha: prov-sha" in content


# ---------------------------------------------------------------------------
# NFR05 — concurrent renders return skipped_lock
# ---------------------------------------------------------------------------


def test_concurrent_renders_skipped_lock(tmp_path: Path) -> None:
    """Background thread holds lock; foreground returns skipped_lock."""
    from trw_mcp.channels._lock import ChannelLock

    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    instructions = github_dir / "copilot-instructions.md"
    lock_path = tmp_path / ".trw" / "channels" / "copilot-instructions-distill.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Acquire lock in background thread
    bg_lock = ChannelLock(lock_path)
    release_event = threading.Event()
    acquired_event = threading.Event()

    def _hold_lock() -> None:
        bg_lock.__enter__()
        acquired_event.set()
        release_event.wait(timeout=5.0)
        bg_lock.__exit__(None, None, None)

    bg_thread = threading.Thread(target=_hold_lock, daemon=True)
    bg_thread.start()
    acquired_event.wait(timeout=2.0)

    try:
        renderer = _make_renderer()
        result = renderer.render(
            tmp_path,
            _make_sidecar(),
            sidecar_sha="concurrent",
            target_file=instructions,
        )
        assert result.status == "skipped_lock"
    finally:
        release_event.set()
        bg_thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Sidecar absent -> T0 beacon
# ---------------------------------------------------------------------------


def test_sidecar_absent_produces_t0_beacon(tmp_path: Path) -> None:
    """No sidecar data produces T0 beacon with regenerate instruction."""
    from trw_mcp.channels.copilot._instructions_distill import DISTILL_BEGIN, DISTILL_END

    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    instructions = github_dir / "copilot-instructions.md"

    renderer = _make_renderer()
    result = renderer.render(
        tmp_path,
        None,  # No sidecar
        sidecar_sha=None,
        target_file=instructions,
    )

    assert result.status == "written"
    assert result.tier_used == "T0"
    content = instructions.read_text(encoding="utf-8")
    assert DISTILL_BEGIN in content
    assert DISTILL_END in content
    # Beacon contains regenerate command
    assert "trw-distill" in content
    assert "self-improve" in content

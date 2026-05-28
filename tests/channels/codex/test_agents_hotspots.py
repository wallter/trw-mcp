"""Tests for channels/codex/_agents_hotspots.py — Codex AGENTS.md hotspot renderer.

PRD-DIST-2402 FR01, FR02, FR03, FR04, FR11, FR12, FR14, FR15, FR16, NFR03.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_sidecar(
    hotspot_count: int = 3,
    convention_count: int = 3,
    edge_case_count: int = 0,
) -> dict[str, Any]:
    """Build a minimal sidecar payload for testing."""
    hotspots = [
        {
            "file": f"src/module_{i}.py",
            "risk_score": round(0.9 - i * 0.05, 2),
            "reason": f"High churn module {i} with complex dependencies",
        }
        for i in range(hotspot_count)
    ]
    conventions = [
        f"Always validate input with Pydantic v2 before processing (convention {i})"
        for i in range(convention_count)
    ]
    edge_cases = [
        f"Edge case {i}: concurrent write to shared state file"
        for i in range(edge_case_count)
    ]
    return {
        "schema_version": "risk-report-sidecar/v0",
        "hotspots": hotspots,
        "conventions": conventions,
        "edge_cases": edge_cases,
    }


# ---------------------------------------------------------------------------
# FR01 — Sequential marker placement (NOT nested inside trw:end)
# ---------------------------------------------------------------------------


def test_segment_placed_after_trw_end(tmp_path: Path) -> None:
    """FR01: distill markers appear AFTER <!-- trw:end -->, not nested inside."""
    from trw_mcp.channels.codex._agents_hotspots import (
        HOTSPOTS_BEGIN,
        HOTSPOTS_END,
        _ensure_sequential_placement,
    )

    agents_md = (
        "# Project\n\n"
        "<!-- trw:start -->\n"
        "## TRW Section\nSome ceremony content.\n"
        "<!-- trw:end -->\n"
        "\n## Other Section\n"
    )
    segment = "Hotspot content here"
    result = _ensure_sequential_placement(agents_md, segment)

    # Markers must appear AFTER trw:end
    trw_end_pos = result.find("<!-- trw:end -->")
    begin_pos = result.find(HOTSPOTS_BEGIN)
    assert trw_end_pos != -1
    assert begin_pos != -1
    assert begin_pos > trw_end_pos, "distill BEGIN must be after trw:end"

    # Must NOT be nested inside trw:start...trw:end
    trw_start_pos = result.find("<!-- trw:start -->")
    assert trw_start_pos != -1
    assert begin_pos > result.find("<!-- trw:end -->"), "distill segment nested in TRW block"


def test_segment_not_nested_inside_trw_section(tmp_path: Path) -> None:
    """FR01: marker pair is a sibling, not a child of the TRW ceremony block."""
    from trw_mcp.channels.codex._agents_hotspots import (
        HOTSPOTS_BEGIN,
        _ensure_sequential_placement,
    )

    agents_md = "<!-- trw:start -->\nTRW content\n<!-- trw:end -->\n"
    result = _ensure_sequential_placement(agents_md, "segment")

    trw_start = result.find("<!-- trw:start -->")
    trw_end = result.find("<!-- trw:end -->")
    hotspot_begin = result.find(HOTSPOTS_BEGIN)

    assert hotspot_begin > trw_end, "HOTSPOTS_BEGIN must be after trw:end"
    # Hotspot should NOT be between trw:start and trw:end
    assert not (trw_start < hotspot_begin < trw_end)


def test_segment_appended_at_eof_when_no_trw_section(tmp_path: Path) -> None:
    """FR01 fallback: when no trw:end exists, segment appended at EOF."""
    from trw_mcp.channels.codex._agents_hotspots import (
        HOTSPOTS_BEGIN,
        HOTSPOTS_END,
        _ensure_sequential_placement,
    )

    agents_md = "# Project\n\nSome content.\n"
    result = _ensure_sequential_placement(agents_md, "hotspot content")
    assert HOTSPOTS_BEGIN in result
    assert HOTSPOTS_END in result
    assert result.index(HOTSPOTS_BEGIN) > result.index("Some content.")


# ---------------------------------------------------------------------------
# FR02 — Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "initial_content",
    [
        "",
        "# AGENTS.md\n\nPlain content.",
        "<!-- trw:start -->\nTRW\n<!-- trw:end -->\n",
        "Already has some content\n\n",
    ],
)
def test_render_idempotency_with_placement(
    initial_content: str, tmp_path: Path
) -> None:
    """FR02: render(render(c, s)) == render(c, s) via placement function."""
    from trw_mcp.channels.codex._agents_hotspots import (
        _content_for_tier_factory,
        _ensure_sequential_placement,
    )

    sidecar = _make_sidecar()
    cb = _content_for_tier_factory(sidecar)
    segment = cb("T1")

    result1 = _ensure_sequential_placement(initial_content, segment)
    result2 = _ensure_sequential_placement(result1, segment)
    assert result1 == result2, "Sequential placement is not idempotent"


# ---------------------------------------------------------------------------
# FR03 — Quota gate with 200 hotspots
# ---------------------------------------------------------------------------


def test_quota_tier_down_with_200_hotspots(tmp_path: Path) -> None:
    """FR03: output within 8192 bytes even with 200 hotspot entries."""
    from trw_mcp.channels.codex._agents_hotspots import (
        DEFAULT_QUOTA_BYTES,
        _content_for_tier_factory,
    )

    sidecar = _make_sidecar(hotspot_count=200, convention_count=50)
    cb = _content_for_tier_factory(sidecar)

    # T2 uses top-5, T1 uses top-3 — both should be well within quota
    for tier in ("T1", "T2"):
        content = cb(tier)
        encoded_bytes = len(content.encode("utf-8"))
        assert encoded_bytes <= DEFAULT_QUOTA_BYTES, (
            f"Tier {tier} segment ({encoded_bytes} bytes) exceeds quota "
            f"({DEFAULT_QUOTA_BYTES} bytes)"
        )


# ---------------------------------------------------------------------------
# FR04 — TTL expired stub contains regen command
# ---------------------------------------------------------------------------


def test_ttl_expired_stub_contains_regen_command() -> None:
    """FR04: stale stub contains STALE marker and regeneration command."""
    from trw_mcp.channels.codex._agents_hotspots import _stale_stub_content

    stub = _stale_stub_content()
    assert "STALE" in stub
    assert "trw-distill self-improve risk-report" in stub


# ---------------------------------------------------------------------------
# FR11 — Concurrent renders produce exactly one valid marker pair
# ---------------------------------------------------------------------------


def test_concurrent_renders_idempotent(tmp_path: Path) -> None:
    """FR11: 10 concurrent render calls produce exactly one marker pair."""
    from trw_mcp.channels.codex._agents_hotspots import (
        HOTSPOTS_BEGIN,
        HOTSPOTS_END,
        render_and_inject,
    )

    agents_md_path = tmp_path / "AGENTS.md"
    agents_md_path.write_text("# Project\n\n", encoding="utf-8")

    sidecar = _make_sidecar()
    errors: list[Exception] = []

    def _do_render() -> None:
        try:
            render_and_inject(
                repo_root=tmp_path,
                sidecar_data=sidecar,
                sidecar_sha="abc123",
                target_file=agents_md_path,
                force=True,
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_do_render) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent render errors: {errors}"

    final_content = agents_md_path.read_text(encoding="utf-8")
    begin_count = final_content.count(HOTSPOTS_BEGIN)
    end_count = final_content.count(HOTSPOTS_END)
    assert begin_count == 1, f"Expected 1 BEGIN marker, got {begin_count}"
    assert end_count == 1, f"Expected 1 END marker, got {end_count}"


# ---------------------------------------------------------------------------
# FR12 — Prune preserves empty markers
# ---------------------------------------------------------------------------


def test_prune_preserves_empty_markers() -> None:
    """FR12: cleanup_on_prune: clear_segment preserves marker pair with empty interior."""
    from trw_mcp.channels.codex._agents_hotspots import (
        HOTSPOTS_BEGIN,
        HOTSPOTS_END,
    )
    from trw_mcp.channels._marker_replace import replace_distill_segment
    from trw_mcp.channels._manifest_models import MarkersConfig

    content = (
        f"# Project\n\n"
        f"{HOTSPOTS_BEGIN}\nSome hotspot content.\n{HOTSPOTS_END}\n"
    )
    markers = MarkersConfig(start=HOTSPOTS_BEGIN, end=HOTSPOTS_END)
    # Clear_segment: replace with empty interior
    result = replace_distill_segment(content, "", markers=markers)

    assert HOTSPOTS_BEGIN in result
    assert HOTSPOTS_END in result
    # Interior should be empty (just whitespace between markers)
    begin_idx = result.find(HOTSPOTS_BEGIN) + len(HOTSPOTS_BEGIN)
    end_idx = result.find(HOTSPOTS_END)
    interior = result[begin_idx:end_idx]
    assert interior.strip() == "", f"Expected empty interior, got: {interior!r}"


# ---------------------------------------------------------------------------
# FR14 — Missing sidecar produces stub
# ---------------------------------------------------------------------------


def test_missing_sidecar_produces_stub(tmp_path: Path) -> None:
    """FR14: absent sidecar renders TTL-expired stub with regen command."""
    from trw_mcp.channels.codex._agents_hotspots import (
        _content_for_tier_factory,
        _stale_stub_content,
    )

    cb = _content_for_tier_factory(None)
    result = cb("T1")
    expected_stub = _stale_stub_content()
    assert result == expected_stub


# ---------------------------------------------------------------------------
# FR15 — T1 contains top-3 hotspots, T2 contains top-5 + edge cases
# ---------------------------------------------------------------------------


def test_t1_contains_top3_hotspots() -> None:
    """FR15: T1 segment contains top-3 hotspot entries."""
    from trw_mcp.channels.codex._agents_hotspots import _content_for_tier_factory

    sidecar = _make_sidecar(hotspot_count=10)
    cb = _content_for_tier_factory(sidecar)
    content = cb("T1")

    # Top-3 files must appear
    for i in range(3):
        assert f"src/module_{i}.py" in content, f"module_{i} missing from T1"
    # 4th and beyond should NOT appear in T1
    assert "src/module_3.py" not in content


def test_t2_contains_edge_cases() -> None:
    """FR15: T2 segment contains top-5 hotspots and top-2 edge cases."""
    from trw_mcp.channels.codex._agents_hotspots import _content_for_tier_factory

    sidecar = _make_sidecar(hotspot_count=10, convention_count=10, edge_case_count=5)
    cb = _content_for_tier_factory(sidecar)
    content = cb("T2")

    # Top-5 hotspot files
    for i in range(5):
        assert f"src/module_{i}.py" in content, f"module_{i} missing from T2"
    # 6th file should NOT appear in T2
    assert "src/module_5.py" not in content

    # Top-2 edge cases
    assert "Edge case 0" in content
    assert "Edge case 1" in content
    # 3rd edge case should NOT appear
    assert "Edge case 2" not in content


def test_t1_contains_top3_conventions() -> None:
    """FR15: T1 segment contains top-3 coding conventions."""
    from trw_mcp.channels.codex._agents_hotspots import _content_for_tier_factory

    sidecar = _make_sidecar(convention_count=10)
    cb = _content_for_tier_factory(sidecar)
    content = cb("T1")

    assert "convention 0" in content
    assert "convention 1" in content
    assert "convention 2" in content
    assert "convention 3" not in content


# ---------------------------------------------------------------------------
# FR16 — Token budget (proper tokenizer with fallback)
# ---------------------------------------------------------------------------


def test_t1_token_budget() -> None:
    """FR16: T1 rendered segment is <= 400 token estimate."""
    from trw_mcp.channels.codex._agents_hotspots import (
        _content_for_tier_factory,
        _count_tokens_estimate,
    )

    sidecar = _make_sidecar(hotspot_count=3, convention_count=3)
    cb = _content_for_tier_factory(sidecar)
    content = cb("T1")

    tokens = _count_tokens_estimate(content)
    assert tokens <= 400, f"T1 segment has {tokens} estimated tokens (budget: 400)"


def test_t2_token_budget() -> None:
    """FR16: T2 rendered segment is <= 900 token estimate."""
    from trw_mcp.channels.codex._agents_hotspots import (
        _content_for_tier_factory,
        _count_tokens_estimate,
    )

    sidecar = _make_sidecar(hotspot_count=5, convention_count=5, edge_case_count=2)
    cb = _content_for_tier_factory(sidecar)
    content = cb("T2")

    tokens = _count_tokens_estimate(content)
    assert tokens <= 900, f"T2 segment has {tokens} estimated tokens (budget: 900)"


def test_token_count_fallback_when_tiktoken_absent() -> None:
    """FR16: fallback to char/4 + 20% overhead when tiktoken not available."""
    import sys

    test_text = "Hello world " * 100  # 1200 chars → ~300 raw tokens → ~360 with buffer

    # Simulate tiktoken being unavailable by removing it from sys.modules
    # and replacing with None so import inside the function raises ImportError.
    original = sys.modules.get("tiktoken", "NOT_PRESENT")
    sys.modules["tiktoken"] = None  # type: ignore[assignment]
    try:
        # Re-import module to ensure fresh binding
        import importlib
        import trw_mcp.channels.codex._agents_hotspots as mod
        importlib.reload(mod)

        # Fallback: char/4 * 1.2; 1200/4 = 300, * 1.2 = 360
        expected_fallback = int(len(test_text) // 4 * 1.2)
        estimate = mod._count_tokens_estimate(test_text)
        assert estimate == expected_fallback, (
            f"Expected fallback estimate {expected_fallback}, got {estimate}"
        )
    finally:
        if original == "NOT_PRESENT":
            del sys.modules["tiktoken"]
        else:
            sys.modules["tiktoken"] = original  # type: ignore[assignment]
        # Reload to restore real module state
        import importlib
        import trw_mcp.channels.codex._agents_hotspots as restore_mod
        importlib.reload(restore_mod)


# ---------------------------------------------------------------------------
# NFR03 — Render performance < 200 ms
# ---------------------------------------------------------------------------


def test_render_performance_under_200ms() -> None:
    """NFR03: segment render time (excluding I/O) under 200 ms."""
    from trw_mcp.channels.codex._agents_hotspots import _content_for_tier_factory

    sidecar = _make_sidecar(hotspot_count=5, convention_count=5, edge_case_count=2)
    cb = _content_for_tier_factory(sidecar)

    start = time.monotonic()
    for _ in range(10):
        cb("T2")
    elapsed = (time.monotonic() - start) * 1000 / 10  # ms per call

    assert elapsed < 200, f"Render took {elapsed:.1f} ms (budget: 200 ms)"


# ---------------------------------------------------------------------------
# ChannelEntry factory
# ---------------------------------------------------------------------------


def test_build_codex_channel_entry_defaults() -> None:
    """build_codex_channel_entry() produces a valid ChannelEntry with expected defaults."""
    from trw_mcp.channels.codex._agents_hotspots import (
        HOTSPOTS_BEGIN,
        HOTSPOTS_END,
        build_codex_channel_entry,
    )

    entry = build_codex_channel_entry()
    assert entry.id == "codex-agents-md-hotspots"
    assert entry.client == "codex"
    assert entry.tier_default == "T1"
    assert entry.markers.start == HOTSPOTS_BEGIN
    assert entry.markers.end == HOTSPOTS_END
    assert entry.quota_total_bytes == 8192
    assert entry.ttl_commits == 20
    assert entry.ttl_days == 7


def test_build_codex_channel_entry_t2_override() -> None:
    """build_codex_channel_entry() accepts tier_override=T2."""
    from trw_mcp.channels.codex._agents_hotspots import build_codex_channel_entry

    entry = build_codex_channel_entry(tier_default="T2")
    assert entry.tier_default == "T2"

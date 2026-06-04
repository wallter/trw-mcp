"""Tests for channels/opencode/_ip_filter.py — IP path filter.

PRD-DIST-2403 FR07 / P2-10.
"""

from __future__ import annotations

import pytest


def test_trw_distill_paths_excluded() -> None:
    """Paths starting with trw-distill/ are removed."""
    from trw_mcp.channels.opencode._ip_filter import filter_proprietary_paths

    paths = [
        "src/module.py",
        "trw-distill/trw_distill/emit/cursor/_mdc_builder.py",
        "backend/routers/proprietary.py",
        "trw-distill/internal/secret.py",
    ]
    result = filter_proprietary_paths(paths)
    assert result == ["src/module.py", "backend/routers/proprietary.py"]


def test_trw_mcp_paths_preserved() -> None:
    """Paths starting with trw-mcp/ are NOT filtered."""
    from trw_mcp.channels.opencode._ip_filter import filter_proprietary_paths

    paths = [
        "trw-mcp/src/trw_mcp/channels/__init__.py",
        "src/module.py",
    ]
    result = filter_proprietary_paths(paths)
    assert result == paths


def test_empty_list_returns_empty() -> None:
    """Empty input returns empty output."""
    from trw_mcp.channels.opencode._ip_filter import filter_proprietary_paths

    assert filter_proprietary_paths([]) == []


def test_all_proprietary_returns_empty() -> None:
    """All-proprietary list returns empty."""
    from trw_mcp.channels.opencode._ip_filter import filter_proprietary_paths

    paths = [
        "trw-distill/a.py",
        "trw-distill/b.py",
        "trw-distill/subdir/c.py",
    ]
    assert filter_proprietary_paths(paths) == []


def test_mixed_list_order_preserved() -> None:
    """Non-proprietary paths are returned in original order."""
    from trw_mcp.channels.opencode._ip_filter import filter_proprietary_paths

    paths = [
        "z_module.py",
        "trw-distill/internal.py",
        "a_module.py",
        "m_module.py",
    ]
    result = filter_proprietary_paths(paths)
    assert result == ["z_module.py", "a_module.py", "m_module.py"]


def test_ip_filtered_paths_count_logged(caplog: object) -> None:
    """filter_proprietary_paths logs the count of excluded paths."""

    from trw_mcp.channels.opencode._ip_filter import filter_proprietary_paths

    with __import__("structlog").testing.capture_logs() as cap:
        result = filter_proprietary_paths(
            [
                "src/ok.py",
                "trw-distill/internal.py",
                "trw-distill/another.py",
            ]
        )

    assert result == ["src/ok.py"]
    # Verify the debug log event was emitted with ip_filtered_paths=2
    filtered_events = [e for e in cap if e.get("ip_filtered_paths") == 2]
    assert filtered_events, "Expected log event with ip_filtered_paths=2"


def test_path_must_start_with_prefix() -> None:
    """Only paths STARTING with trw-distill/ are filtered (not containing)."""
    from trw_mcp.channels.opencode._ip_filter import filter_proprietary_paths

    # Path containing but not starting with trw-distill/
    paths = ["src/not-trw-distill/module.py", "trw-distill/secret.py"]
    result = filter_proprietary_paths(paths)
    assert "src/not-trw-distill/module.py" in result
    assert "trw-distill/secret.py" not in result


@pytest.fixture(autouse=True)
def _structlog_defaults_for_capture() -> object:
    """File-scoped: reset structlog to defaults so ``capture_logs()`` sees WARN.

    A prior test's ``configure_logging()`` (server import / init_project) installs
    a filtering wrapper that drops WARN before ``capture_logs``'s processor, so
    these warning-assertion tests fail only in full-suite ordering. Save+restore
    (file-scoped, never a global reset — avoids the alphabetical-leak hazard).
    """
    import structlog

    _saved = structlog.get_config()
    structlog.reset_defaults()
    yield
    structlog.configure(**_saved)

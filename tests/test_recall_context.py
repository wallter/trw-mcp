"""Tests for RecallContext dataclass (PRD-CORE-102, Task 1)."""

import pytest


def test_default_fields() -> None:
    """RecallContext() has all None/empty defaults."""
    from trw_mcp.scoring._recall import RecallContext

    ctx = RecallContext()
    assert ctx.current_phase is None
    assert ctx.active_domains == []
    assert ctx.team_id is None
    assert ctx.active_prd_ids == []
    assert ctx.modified_files == []


def test_all_none_safe() -> None:
    """RecallContext with all defaults doesn't crash."""
    from trw_mcp.scoring._recall import RecallContext

    ctx = RecallContext()
    # Accessing all fields should not raise
    _ = ctx.current_phase
    _ = ctx.active_domains
    _ = ctx.team_id
    _ = ctx.active_prd_ids
    _ = ctx.modified_files


def test_importable_from_scoring() -> None:
    """from trw_mcp.scoring import RecallContext works."""
    from trw_mcp.scoring import RecallContext

    ctx = RecallContext(current_phase="IMPLEMENT")
    assert ctx.current_phase == "IMPLEMENT"


def test_frozen() -> None:
    """RecallContext is immutable — assigning attribute raises FrozenInstanceError."""
    from dataclasses import FrozenInstanceError

    from trw_mcp.scoring._recall import RecallContext

    ctx = RecallContext(current_phase="PLAN")
    with pytest.raises(FrozenInstanceError):
        ctx.current_phase = "IMPLEMENT"  # type: ignore[misc]


def test_field_values_preserved() -> None:
    """RecallContext stores the values passed to it correctly."""
    from trw_mcp.scoring._recall import RecallContext

    ctx = RecallContext(
        current_phase="VALIDATE",
        active_domains=["auth", "middleware"],
        team_id="team-alpha",
        active_prd_ids=["PRD-CORE-102"],
        modified_files=["src/auth/middleware.py"],
    )
    assert ctx.current_phase == "VALIDATE"
    assert ctx.active_domains == ["auth", "middleware"]
    assert ctx.team_id == "team-alpha"
    assert ctx.active_prd_ids == ["PRD-CORE-102"]
    assert ctx.modified_files == ["src/auth/middleware.py"]

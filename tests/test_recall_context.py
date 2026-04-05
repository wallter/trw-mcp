"""Tests for RecallContext dataclass (PRD-CORE-102, PRD-CORE-116)."""

import pytest


def test_default_fields() -> None:
    """RecallContext() has all empty defaults (PRD-CORE-116)."""
    from trw_mcp.scoring._recall import RecallContext

    ctx = RecallContext()
    assert ctx.current_phase is None
    assert ctx.inferred_domains == set()
    assert ctx.team == ""
    assert ctx.prd_knowledge_ids == set()
    assert ctx.modified_files == []
    assert ctx.client_profile == ""
    assert ctx.model_family == ""


def test_all_none_safe() -> None:
    """RecallContext with all defaults doesn't crash."""
    from trw_mcp.scoring._recall import RecallContext

    ctx = RecallContext()
    # Accessing all fields should not raise
    _ = ctx.current_phase
    _ = ctx.inferred_domains
    _ = ctx.team
    _ = ctx.prd_knowledge_ids
    _ = ctx.modified_files
    _ = ctx.client_profile
    _ = ctx.model_family


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
    """RecallContext stores the values passed to it correctly (PRD-CORE-116 field names)."""
    from trw_mcp.scoring._recall import RecallContext

    ctx = RecallContext(
        current_phase="VALIDATE",
        inferred_domains={"auth", "middleware"},
        team="team-alpha",
        prd_knowledge_ids={"PRD-CORE-102"},
        modified_files=["src/auth/middleware.py"],
        client_profile="opencode",
        model_family="claude-4",
    )
    assert ctx.current_phase == "VALIDATE"
    assert ctx.inferred_domains == {"auth", "middleware"}
    assert ctx.team == "team-alpha"
    assert ctx.prd_knowledge_ids == {"PRD-CORE-102"}
    assert ctx.modified_files == ["src/auth/middleware.py"]
    assert ctx.client_profile == "opencode"
    assert ctx.model_family == "claude-4"


def test_deprecated_alias_active_domains() -> None:
    """Deprecated active_domains constructor arg populates inferred_domains."""
    from trw_mcp.scoring._recall import RecallContext

    ctx = RecallContext(active_domains=["payments", "auth"])
    assert ctx.inferred_domains == {"payments", "auth"}


def test_deprecated_alias_team_id() -> None:
    """Deprecated team_id constructor arg populates team."""
    from trw_mcp.scoring._recall import RecallContext

    ctx = RecallContext(team_id="checkout")
    assert ctx.team == "checkout"


def test_deprecated_alias_active_prd_ids() -> None:
    """Deprecated active_prd_ids constructor arg populates prd_knowledge_ids."""
    from trw_mcp.scoring._recall import RecallContext

    ctx = RecallContext(active_prd_ids=["PRD-CORE-102"])
    assert ctx.prd_knowledge_ids == {"PRD-CORE-102"}

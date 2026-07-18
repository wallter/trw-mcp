"""Behavior tests for audit-role application."""

from __future__ import annotations

from trw_mcp.dispatch import ROLE_TEMPLATES, apply_role


def test_known_role_wraps_prompt_with_preamble() -> None:
    out = apply_role("code-review", "Look at file X.")
    assert out.endswith("Look at file X.")
    assert out != "Look at file X."
    assert ROLE_TEMPLATES["code-review"] in out


def test_all_roles_enforce_read_only_and_severity() -> None:
    for role in ("code-review", "design-audit", "architectural-audit", "adversarial-audit"):
        preamble = ROLE_TEMPLATES[role]
        assert "read-only" in preamble.lower()
        assert "severity" in preamble.lower()
        # do-not-edit contract present
        assert "do not edit" in preamble.lower() or "do NOT edit" in preamble


def test_none_role_passes_prompt_through_unchanged() -> None:
    assert apply_role(None, "raw prompt") == "raw prompt"


def test_empty_role_passes_prompt_through_unchanged() -> None:
    assert apply_role("", "raw prompt") == "raw prompt"


def test_unknown_role_passes_prompt_through_unchanged() -> None:
    assert apply_role("totally-made-up", "raw prompt") == "raw prompt"


def test_adversarial_role_distinct_from_code_review() -> None:
    assert ROLE_TEMPLATES["adversarial-audit"] != ROLE_TEMPLATES["code-review"]

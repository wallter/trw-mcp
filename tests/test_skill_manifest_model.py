from __future__ import annotations

from pathlib import Path

from trw_mcp.models.skill_manifest import validate_skill_markdown


def test_strict_manifest_normalizes_kebab_case_aliases() -> None:
    markdown = """---
name: code-review
description: Review code safely
user-invocable: true
argument-hint: path to changed files
allowed-tools:
  - trw_recall
forbidden-tools:
  - rm
requires-verification: true
ordered-steps:
  - Inspect
  - Verify
strict-execution: true
meta-discovery: true
risk-level: medium
---
## Inspect
Use trw_recall before edits.
## Verify
Run project-native tests.
"""

    result = validate_skill_markdown(markdown, path=Path("skills/code-review/SKILL.md"), mode="strict")

    assert result.ok is True
    assert result.manifest is not None
    assert result.manifest.user_invocable is True
    assert result.manifest.argument_hint == "path to changed files"
    assert result.manifest.allowed_tools == ("trw_recall",)
    assert result.manifest.forbidden_tools == ("rm",)
    assert result.manifest.requires_verification is True
    assert result.manifest.ordered_steps == ("Inspect", "Verify")
    assert result.manifest.strict_execution is True
    assert result.manifest.meta_discovery is True
    assert result.manifest.risk_level == "medium"


def test_unknown_fields_warn_in_compat_and_fail_in_strict() -> None:
    markdown = """---
name: legacy-skill
description: Legacy skill
unexpected-field: still accepted by compatibility validation
---
Body.
"""

    compat = validate_skill_markdown(markdown, path=Path("legacy/SKILL.md"), mode="compat")
    strict = validate_skill_markdown(markdown, path=Path("legacy/SKILL.md"), mode="strict")

    assert compat.ok is True
    assert compat.manifest is not None
    assert compat.warnings[0].path == "legacy/SKILL.md"
    assert compat.warnings[0].field == "unexpected-field"
    assert "unknown field" in compat.warnings[0].reason
    assert strict.ok is False
    assert strict.errors[0].path == "legacy/SKILL.md"
    assert strict.errors[0].field == "unexpected-field"
    assert "strict mode" in strict.errors[0].reason


def test_ordered_steps_fail_strict_when_missing_duplicate_or_out_of_order() -> None:
    missing = """---
name: bad-skill
description: Bad skill
ordered_steps: [Plan, Verify]
---
## Plan
Only planning is present.
"""
    duplicate = """---
name: bad-skill
description: Bad skill
ordered_steps: [Plan, Verify]
---
## Plan
## Verify
Repeat Verify after completion.
"""
    out_of_order = """---
name: bad-skill
description: Bad skill
ordered_steps: [Plan, Verify]
---
## Verify
## Plan
"""

    missing_result = validate_skill_markdown(missing, path=Path("missing/SKILL.md"), mode="strict")
    duplicate_result = validate_skill_markdown(duplicate, path=Path("duplicate/SKILL.md"), mode="strict")
    out_of_order_result = validate_skill_markdown(out_of_order, path=Path("order/SKILL.md"), mode="strict")

    assert missing_result.ok is False
    assert missing_result.errors[0].field == "ordered_steps"
    assert "missing" in missing_result.errors[0].reason
    assert duplicate_result.ok is False
    assert duplicate_result.errors[0].field == "ordered_steps"
    assert "duplicate" in duplicate_result.errors[0].reason
    assert out_of_order_result.ok is False
    assert out_of_order_result.errors[0].field == "ordered_steps"
    assert "out of order" in out_of_order_result.errors[0].reason


def test_ordered_steps_fail_strict_when_declared_more_than_once() -> None:
    markdown = """---
name: bad-skill
description: Bad skill
ordered_steps: [Plan, Plan]
---
## Plan
"""

    result = validate_skill_markdown(markdown, path=Path("duplicate-declared/SKILL.md"), mode="strict")

    assert result.ok is False
    assert result.errors[0].field == "ordered_steps"
    assert "declared more than once" in result.errors[0].reason


def test_tool_constraints_warn_in_compat_and_fail_strict_execution() -> None:
    markdown = """---
name: tool-skill
description: Tool constrained skill
allowed_tools:
  - trw_recall
forbidden_tools:
  - rm
strict_execution: true
---
Use trw_checkpoint and then run `rm -rf build`.
"""

    compat = validate_skill_markdown(markdown, path=Path("tool/SKILL.md"), mode="compat")
    strict = validate_skill_markdown(markdown, path=Path("tool/SKILL.md"), mode="strict")

    assert compat.ok is True
    assert {warning.field for warning in compat.warnings} == {"forbidden_tools", "allowed_tools"}
    assert strict.ok is False
    assert {error.field for error in strict.errors} == {"forbidden_tools", "allowed_tools"}
    assert any("forbidden tool" in error.reason for error in strict.errors)
    assert any("undeclared tool" in error.reason for error in strict.errors)

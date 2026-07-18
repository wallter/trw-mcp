"""Keep the audit skill concise without weakening its evidence contract."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "trw-mcp" / "src" / "trw_mcp" / "data"

if not (ROOT / "scripts").is_dir():
    pytest.skip("monorepo-only audit skill projection invariant", allow_module_level=True)

AUDIT_SKILLS = (
    ROOT / ".agents" / "skills" / "trw-audit" / "SKILL.md",
    ROOT / ".claude" / "skills" / "trw-audit" / "SKILL.md",
    ROOT / ".github" / "skills" / "trw-audit" / "SKILL.md",
    ROOT / ".cursor" / "skills" / "trw-audit" / "SKILL.md",
    DATA / "skills" / "trw-audit" / "SKILL.md",
    DATA / "codex" / "skills" / "trw-audit" / "SKILL.md",
    DATA / "copilot" / "skills" / "trw-audit" / "SKILL.md",
)


def test_audit_skill_keeps_operational_rules_not_motivational_duplicates() -> None:
    for path in AUDIT_SKILLS:
        content = path.read_text(encoding="utf-8")
        assert "## Why This Exists" not in content
        assert "## Rationalization Watchlist" not in content
        assert content.count("scratch/audits") == 1

        for constraint in (
            'NEVER accept "tests pass" as evidence of spec compliance',
            "NEVER skip NFR checklist items",
            "NEVER use PARTIAL to soften a failed acceptance criterion",
        ):
            assert constraint in content

        for required in (
            "### Step 3: Locate Code and Tests",
            "### Step 4: Audit Each FR",
            "### Step 5: NFR Checklist",
            "### Step 7: Write Audit Report",
            "nfr_audit:",
            "verdict: PASS|FAIL|NA",
        ):
            assert required in content


def test_audit_uses_behavioral_evidence_not_naming_or_test_reachability() -> None:
    for path in AUDIT_SKILLS:
        content = path.read_text(encoding="utf-8")
        for required in (
            "Naming overlap is not behavioral",
            "declared verification method",
            "Test-only reachability is not production wiring",
            "Mark non-applicable items `NA` only with concrete justification",
            "non-obvious reusable pattern, not for routine audit status",
        ):
            assert required in content
        for forbidden in (
            "Score < 50%",
            "If a FR has NO tests: mark as UNTESTED (P1) immediately",
            "command table, test, or integration path",
            "Run EVERY item against EVERY endpoint/component",
            "If findings exist, call `trw_learn`",
        ):
            assert forbidden not in content


def test_cursor_audit_projection_matches_generic_contract() -> None:
    generic = (DATA / "skills" / "trw-audit" / "SKILL.md").read_text(encoding="utf-8")
    cursor = (ROOT / ".cursor" / "skills" / "trw-audit" / "SKILL.md").read_text(encoding="utf-8")
    assert cursor == generic
    assert "score < 0.85: abort" not in cursor

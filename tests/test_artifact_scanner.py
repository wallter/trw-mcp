"""Tests for artifact_scanner — PRD-CORE-106 knowledge requirements extraction.

Covers: scan_artifact, scan_artifacts, KnowledgeRequirements.merge().
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.artifact_scanner import (
    KnowledgeRequirements,
    scan_artifact,
    scan_artifacts,
)


class TestScanEmptyFile:
    """test_scan_empty_file: Empty file yields empty KnowledgeRequirements."""

    def test_empty_file_returns_empty_requirements(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.md"
        f.write_text("", encoding="utf-8")
        result = scan_artifact(f)
        assert result.learning_ids == set()
        assert result.domains == set()
        assert result.checks == []
        assert result.research_notes == []
        assert result.prd_references == set()
        assert result.phase_requirements == {}


class TestScanInlineLearningIds:
    """test_scan_inline_learning_ids: Inline L-xxxx references extracted."""

    def test_extracts_learning_ids(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.md"
        f.write_text("See L-a3Fq and L-Bx2m for context.\n", encoding="utf-8")
        result = scan_artifact(f)
        assert result.learning_ids == {"L-a3Fq", "L-Bx2m"}

    def test_deduplicates_learning_ids(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.md"
        f.write_text("L-abcd appears twice: L-abcd\n", encoding="utf-8")
        result = scan_artifact(f)
        assert result.learning_ids == {"L-abcd"}

    def test_no_false_positives_short_ids(self, tmp_path: Path) -> None:
        """IDs shorter than 4 chars should NOT match."""
        f = tmp_path / "doc.md"
        f.write_text("L-ab is too short\n", encoding="utf-8")
        result = scan_artifact(f)
        assert result.learning_ids == set()

    def test_max_length_learning_id(self, tmp_path: Path) -> None:
        """IDs up to 8 chars should match."""
        f = tmp_path / "doc.md"
        f.write_text("L-abcdefgh is valid\n", encoding="utf-8")
        result = scan_artifact(f)
        assert result.learning_ids == {"L-abcdefgh"}

    def test_too_long_learning_id_no_match(self, tmp_path: Path) -> None:
        """IDs longer than 8 chars should NOT match."""
        f = tmp_path / "doc.md"
        f.write_text("L-abcdefghi is too long\n", encoding="utf-8")
        result = scan_artifact(f)
        assert result.learning_ids == set()


class TestScanInlinePrdRefs:
    """test_scan_inline_prd_refs: PRD-XXX-NNN references extracted."""

    def test_extracts_prd_reference(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.md"
        f.write_text("This implements PRD-CORE-110.\n", encoding="utf-8")
        result = scan_artifact(f)
        assert result.prd_references == {"PRD-CORE-110"}

    def test_extracts_multiple_prd_refs(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.md"
        f.write_text(
            "See PRD-CORE-106 and PRD-QUAL-042 for details.\n",
            encoding="utf-8",
        )
        result = scan_artifact(f)
        assert result.prd_references == {"PRD-CORE-106", "PRD-QUAL-042"}


class TestScanKnowledgeRequirementsYaml:
    """test_scan_knowledge_requirements_yaml: YAML block extraction."""

    def test_extracts_domains_and_learning_ids(self, tmp_path: Path) -> None:
        f = tmp_path / "prd.md"
        f.write_text(
            """\
# My PRD

knowledge_requirements:
  domains:
    - testing
    - security
  learning_ids:
    - L-abc1234
    - L-def5678
  checks:
    - verify auth middleware
  research_notes:
    - investigate rate limiting

Some trailing text.
""",
            encoding="utf-8",
        )
        result = scan_artifact(f)
        assert "testing" in result.domains
        assert "security" in result.domains
        assert "L-abc1234" in result.learning_ids
        assert "L-def5678" in result.learning_ids
        assert "verify auth middleware" in result.checks
        assert "investigate rate limiting" in result.research_notes

    def test_extracts_phase_keyed_requirements(self, tmp_path: Path) -> None:
        """Phase-keyed knowledge_requirements should be extracted per-phase."""
        f = tmp_path / "plan.md"
        f.write_text(
            """\
# Execution Plan

knowledge_requirements:
  implement:
    domains:
      - persistence
    checks:
      - run migration
  validate:
    domains:
      - testing
    checks:
      - run full suite
""",
            encoding="utf-8",
        )
        result = scan_artifact(f)
        assert "implement" in result.phase_requirements
        assert "validate" in result.phase_requirements
        assert result.phase_requirements["implement"]["domains"] == ["persistence"]
        assert result.phase_requirements["implement"]["checks"] == ["run migration"]
        assert result.phase_requirements["validate"]["domains"] == ["testing"]
        assert result.phase_requirements["validate"]["checks"] == ["run full suite"]


class TestScanStripsFencedCode:
    """test_scan_strips_fenced_code: IDs inside code blocks are NOT extracted."""

    def test_learning_id_in_code_block_not_extracted(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.md"
        f.write_text(
            """\
Some text.

```python
# This is a code block with L-fake1 inside
print("L-fake2")
```

But L-real1 outside is extracted.
""",
            encoding="utf-8",
        )
        result = scan_artifact(f)
        assert "L-fake1" not in result.learning_ids
        assert "L-fake2" not in result.learning_ids
        assert "L-real1" in result.learning_ids

    def test_prd_ref_in_code_block_not_extracted(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.md"
        f.write_text(
            """\
```
PRD-FAKE-999
```

PRD-REAL-001 is outside.
""",
            encoding="utf-8",
        )
        result = scan_artifact(f)
        assert "PRD-FAKE-999" not in result.prd_references
        assert "PRD-REAL-001" in result.prd_references


class TestScanMalformedYaml:
    """test_scan_malformed_yaml_continues: Malformed YAML logs warning, continues."""

    def test_malformed_yaml_does_not_crash(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.md"
        f.write_text(
            """\
knowledge_requirements:
  domains:
    - valid_domain
  bad_indent:
 broken: [
  not_closed
""",
            encoding="utf-8",
        )
        # Should not raise; may extract partial data or skip gracefully
        result = scan_artifact(f)
        # At minimum, inline refs from the non-YAML portion still work
        assert isinstance(result, KnowledgeRequirements)


class TestScanMissingFile:
    """test_scan_missing_file_skips: Missing path skips with warning."""

    def test_missing_file_skips(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.md"
        result = scan_artifacts([missing])
        assert result.learning_ids == set()
        assert result.domains == set()

    def test_missing_file_among_valid_continues(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.md"
        valid = tmp_path / "valid.md"
        valid.write_text("L-abcd is here\n", encoding="utf-8")
        result = scan_artifacts([missing, valid])
        assert result.learning_ids == {"L-abcd"}


class TestScanMultipleArtifactsMerges:
    """test_scan_multiple_artifacts_merges: Two files -> merged results."""

    def test_merges_learning_ids(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.md"
        f1.write_text("L-aaaa is in file A.\n", encoding="utf-8")
        f2 = tmp_path / "b.md"
        f2.write_text("L-bbbb is in file B.\n", encoding="utf-8")
        result = scan_artifacts([f1, f2])
        assert result.learning_ids == {"L-aaaa", "L-bbbb"}

    def test_merges_prd_refs(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.md"
        f1.write_text("PRD-CORE-001\n", encoding="utf-8")
        f2 = tmp_path / "b.md"
        f2.write_text("PRD-QUAL-002\n", encoding="utf-8")
        result = scan_artifacts([f1, f2])
        assert result.prd_references == {"PRD-CORE-001", "PRD-QUAL-002"}


class TestKnowledgeRequirementsMerge:
    """test_knowledge_requirements_merge: Direct merge() method tests."""

    def test_merge_unions_sets(self) -> None:
        a = KnowledgeRequirements(
            learning_ids={"L-aaaa"},
            domains={"testing"},
            prd_references={"PRD-CORE-001"},
        )
        b = KnowledgeRequirements(
            learning_ids={"L-bbbb"},
            domains={"security"},
            prd_references={"PRD-QUAL-002"},
        )
        a.merge(b)
        assert a.learning_ids == {"L-aaaa", "L-bbbb"}
        assert a.domains == {"testing", "security"}
        assert a.prd_references == {"PRD-CORE-001", "PRD-QUAL-002"}

    def test_merge_concatenates_lists(self) -> None:
        a = KnowledgeRequirements(checks=["check1"], research_notes=["note1"])
        b = KnowledgeRequirements(checks=["check2"], research_notes=["note2"])
        a.merge(b)
        assert a.checks == ["check1", "check2"]
        assert a.research_notes == ["note1", "note2"]

    def test_merge_phase_requirements(self) -> None:
        a = KnowledgeRequirements(phase_requirements={"implement": {"domains": ["persistence"]}})
        b = KnowledgeRequirements(
            phase_requirements={
                "implement": {"checks": ["run tests"]},
                "validate": {"domains": ["testing"]},
            }
        )
        a.merge(b)
        assert a.phase_requirements["implement"]["domains"] == ["persistence"]
        assert a.phase_requirements["implement"]["checks"] == ["run tests"]
        assert a.phase_requirements["validate"]["domains"] == ["testing"]

    def test_merge_empty_into_populated(self) -> None:
        a = KnowledgeRequirements(learning_ids={"L-aaaa"}, domains={"testing"})
        b = KnowledgeRequirements()
        a.merge(b)
        assert a.learning_ids == {"L-aaaa"}
        assert a.domains == {"testing"}


class TestScanPhaseKeyedRequirements:
    """test_scan_phase_keyed_requirements: Phase-keyed YAML extraction."""

    def test_mixed_flat_and_phase_keyed(self, tmp_path: Path) -> None:
        """File with both flat domains and phase-keyed requirements."""
        f = tmp_path / "mixed.md"
        f.write_text(
            """\
knowledge_requirements:
  domains:
    - general_domain
  implement:
    domains:
      - impl_domain
    checks:
      - impl_check
""",
            encoding="utf-8",
        )
        result = scan_artifact(f)
        assert "general_domain" in result.domains
        assert "implement" in result.phase_requirements
        assert result.phase_requirements["implement"]["domains"] == ["impl_domain"]
        assert result.phase_requirements["implement"]["checks"] == ["impl_check"]

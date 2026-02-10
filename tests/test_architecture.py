"""Tests for architecture encoding -- models, fitness functions, ADR support.

PRD-QUAL-007: Architecture encoding with fitness functions, dependency rules,
conventions, bounded contexts, and ADR integration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.architecture import (
    ArchitectureConfig,
    ArchitectureFitnessResult,
    ArchitectureStyle,
    BoundedContext,
    Convention,
    ConventionSeverity,
    ConventionViolation,
    DependencyRule,
    ImportViolation,
    TestLayerConfig,
)
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.learning import LearningEntry
from trw_mcp.state.architecture import (
    analyze_import_direction,
    check_architecture_fitness,
    check_conventions,
    generate_import_linter_config,
    load_architecture_config,
)
from trw_mcp.state.claude_md import (
    collect_adrs_for_context,
    render_bounded_context_claude_md,
)
from trw_mcp.state.persistence import FileStateWriter


@pytest.fixture
def domain_file(tmp_path: Path) -> Path:
    """Create a domain/model.py file and return its path."""
    f = tmp_path / "domain" / "model.py"
    f.parent.mkdir(parents=True)
    return f


@pytest.fixture
def domain_rules() -> list[DependencyRule]:
    """Dependency rules forbidding domain -> infrastructure imports."""
    return [
        DependencyRule(layer="domain", may_not_import=["infrastructure"]),
        DependencyRule(layer="infrastructure"),
    ]


@pytest.fixture
def no_star_imports_convention() -> Convention:
    """Convention forbidding star imports at the implement gate."""
    return Convention(
        name="no_star_imports",
        gate="implement",
        check_method="no_star_imports",
        severity=ConventionSeverity.ERROR,
    )


@pytest.fixture
def adr_entries_dir(tmp_path: Path) -> Path:
    """Create .trw/learnings/entries/ and return the entries directory."""
    entries_dir = tmp_path / ".trw" / "learnings" / "entries"
    entries_dir.mkdir(parents=True)
    return entries_dir


class TestArchitectureStyle:
    """ArchitectureStyle enum serialization."""

    def test_values(self) -> None:
        assert ArchitectureStyle.HEXAGONAL.value == "hexagonal"
        assert ArchitectureStyle.DDD.value == "ddd"
        assert ArchitectureStyle.CUSTOM.value == "custom"

    def test_from_string(self) -> None:
        assert ArchitectureStyle("vertical_slices") == ArchitectureStyle.VERTICAL_SLICES


class TestDependencyRule:
    """DependencyRule model."""

    def test_create_minimal(self) -> None:
        rule = DependencyRule(layer="domain")
        assert rule.layer == "domain"
        assert rule.may_import == []
        assert rule.may_not_import == []

    def test_create_full(self) -> None:
        rule = DependencyRule(
            layer="application",
            may_import=["domain"],
            may_not_import=["infrastructure", "presentation"],
        )
        assert "domain" in rule.may_import
        assert "infrastructure" in rule.may_not_import


class TestConvention:
    """Convention model."""

    def test_defaults(self) -> None:
        conv = Convention(name="type_annotations")
        assert conv.gate == "implement"
        assert conv.severity == "warning"

    def test_error_severity(self) -> None:
        conv = Convention(name="no_star_imports", severity=ConventionSeverity.ERROR)
        assert conv.severity == "error"


class TestBoundedContext:
    """BoundedContext model."""

    def test_create(self) -> None:
        ctx = BoundedContext(name="tools", path="src/trw_mcp/tools")
        assert ctx.name == "tools"
        assert ctx.description == ""


class TestTestLayerConfig:
    """TestLayerConfig model."""

    def test_coverage_default(self) -> None:
        layer = TestLayerConfig(layer="unit")
        assert layer.coverage_target == 0.80

    def test_coverage_bounds(self) -> None:
        with pytest.raises(Exception):
            TestLayerConfig(layer="unit", coverage_target=1.5)


class TestArchitectureConfig:
    """ArchitectureConfig composite model."""

    def test_defaults(self) -> None:
        cfg = ArchitectureConfig()
        assert cfg.style == "custom"
        assert cfg.dependency_rules == []
        assert cfg.bounded_contexts == []
        assert cfg.conventions == []

    def test_full_config(self) -> None:
        cfg = ArchitectureConfig(
            style=ArchitectureStyle.HEXAGONAL,
            dependency_rules=[
                DependencyRule(layer="domain", may_not_import=["infrastructure"]),
            ],
            bounded_contexts=[
                BoundedContext(name="core", path="src/core"),
            ],
            conventions=[
                Convention(name="type_hints", gate="implement"),
            ],
            testing_layers=[
                TestLayerConfig(layer="unit", coverage_target=0.90),
            ],
        )
        assert cfg.style == "hexagonal"
        assert len(cfg.dependency_rules) == 1
        assert len(cfg.bounded_contexts) == 1


class TestArchitectureFitnessResult:
    """ArchitectureFitnessResult model."""

    def test_clean_result(self) -> None:
        result = ArchitectureFitnessResult(phase="implement")
        assert result.score == 1.0
        assert result.violations == []

    def test_result_with_violations(self) -> None:
        result = ArchitectureFitnessResult(
            phase="validate",
            checks_run=3,
            violations=[
                ImportViolation(
                    file="src/domain/model.py",
                    line=5,
                    from_layer="domain",
                    to_layer="infrastructure",
                ),
            ],
            score=0.67,
        )
        assert len(result.violations) == 1
        assert result.score == 0.67


class TestViolationModels:
    """ImportViolation and ConventionViolation models."""

    def test_import_violation(self) -> None:
        v = ImportViolation(
            file="src/core.py",
            line=10,
            importing_module="core",
            imported_module="infra.db",
            from_layer="domain",
            to_layer="infrastructure",
        )
        assert v.line == 10
        assert v.to_layer == "infrastructure"

    def test_convention_violation(self) -> None:
        v = ConventionViolation(
            file="src/handler.py",
            convention_name="type_annotations",
            message="Missing return type",
            severity=ConventionSeverity.WARNING,
        )
        assert v.convention_name == "type_annotations"
        assert v.severity == "warning"


class TestConfigArchitectureFields:
    """TRWConfig architecture fields (PRD-QUAL-007)."""

    def test_defaults(self) -> None:
        config = TRWConfig()
        assert config.architecture_style == ""
        assert config.architecture_fitness_enabled is False

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_ARCHITECTURE_FITNESS_ENABLED", "true")
        config = TRWConfig()
        assert config.architecture_fitness_enabled is True


class TestLoadArchitectureConfig:
    """load_architecture_config from .trw/config.yaml."""

    def test_no_config_file(self, tmp_path: Path) -> None:
        assert load_architecture_config(tmp_path) is None

    def test_no_architecture_key(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_text(
            "framework_version: v18.0\n", encoding="utf-8",
        )
        assert load_architecture_config(tmp_path) is None

    def test_valid_config(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        writer = FileStateWriter()
        writer.write_yaml(trw_dir / "config.yaml", {
            "architecture": {
                "style": "hexagonal",
                "dependency_rules": [
                    {"layer": "domain", "may_not_import": ["infrastructure"]},
                ],
            },
        })
        result = load_architecture_config(tmp_path)
        assert result is not None
        assert result.style == "hexagonal"
        assert len(result.dependency_rules) == 1


class TestAnalyzeImportDirection:
    """AST-based import direction analysis."""

    def test_clean_code(self, domain_file: Path, tmp_path: Path) -> None:
        domain_file.write_text("import os\nx = 1\n", encoding="utf-8")
        rules = [DependencyRule(layer="domain", may_not_import=["infrastructure"])]
        violations = analyze_import_direction(domain_file, rules, tmp_path)
        assert violations == []

    def test_detects_violation(
        self, domain_file: Path, domain_rules: list[DependencyRule], tmp_path: Path,
    ) -> None:
        domain_file.write_text(
            "from infrastructure.db import connect\n", encoding="utf-8",
        )
        violations = analyze_import_direction(domain_file, domain_rules, tmp_path)
        assert len(violations) == 1
        assert violations[0].from_layer == "domain"
        assert violations[0].to_layer == "infrastructure"

    def test_skips_type_checking_block(
        self, domain_file: Path, domain_rules: list[DependencyRule], tmp_path: Path,
    ) -> None:
        domain_file.write_text(
            "from __future__ import annotations\n"
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from infrastructure.db import Engine\n"
            "x = 1\n",
            encoding="utf-8",
        )
        violations = analyze_import_direction(domain_file, domain_rules, tmp_path)
        assert violations == []

    def test_relative_import_skipped(self, domain_file: Path, tmp_path: Path) -> None:
        domain_file.write_text("from . import utils\n", encoding="utf-8")
        rules = [DependencyRule(layer="domain", may_not_import=["infrastructure"])]
        violations = analyze_import_direction(domain_file, rules, tmp_path)
        assert violations == []

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        f = tmp_path / "nonexistent.py"
        rules = [DependencyRule(layer="domain")]
        assert analyze_import_direction(f, rules, tmp_path) == []


class TestCheckConventions:
    """Convention checking."""

    def test_no_star_imports_clean(
        self, tmp_path: Path, no_star_imports_convention: Convention,
    ) -> None:
        f = tmp_path / "clean.py"
        f.write_text("import os\nfrom pathlib import Path\n", encoding="utf-8")
        violations = check_conventions([f], [no_star_imports_convention], "implement")
        assert violations == []

    def test_no_star_imports_violation(
        self, tmp_path: Path, no_star_imports_convention: Convention,
    ) -> None:
        f = tmp_path / "bad.py"
        f.write_text("from os.path import *\n", encoding="utf-8")
        violations = check_conventions([f], [no_star_imports_convention], "implement")
        assert len(violations) == 1
        assert violations[0].convention_name == "no_star_imports"

    def test_wrong_gate_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.py"
        f.write_text("from os.path import *\n", encoding="utf-8")
        convention = Convention(
            name="no_star_imports",
            gate="validate",
            check_method="no_star_imports",
        )
        violations = check_conventions([f], [convention], "implement")
        assert violations == []


class TestCheckArchitectureFitness:
    """Orchestrated fitness checks."""

    def test_clean_project(self, tmp_path: Path) -> None:
        config = ArchitectureConfig(
            dependency_rules=[DependencyRule(layer="domain")],
        )
        src = tmp_path / "trw-mcp" / "src" / "domain" / "model.py"
        src.parent.mkdir(parents=True)
        src.write_text("x = 1\n", encoding="utf-8")
        run_path = tmp_path / "run"
        run_path.mkdir()
        result = check_architecture_fitness("implement", run_path, config, tmp_path)
        assert result.score == 1.0
        assert result.checks_run >= 1

    def test_disabled_by_default(self) -> None:
        config = TRWConfig()
        assert config.architecture_fitness_enabled is False


class TestGenerateImportLinterConfig:
    """Import-linter config generation (FR08)."""

    def test_generates_contract(self) -> None:
        rules = [
            DependencyRule(
                layer="domain",
                may_not_import=["infrastructure", "presentation"],
            ),
        ]
        config = generate_import_linter_config(rules)
        assert "[importlinter]" in config
        assert "root_package = trw_mcp" in config
        assert "domain layer isolation" in config
        assert "trw_mcp.infrastructure" in config

    def test_empty_rules(self) -> None:
        config = generate_import_linter_config([])
        assert "[importlinter]" in config
        assert "contract" not in config.split("[importlinter]")[1]


class TestADRLearningEntry:
    """ADR fields on LearningEntry (PRD-QUAL-007-FR04)."""

    def test_adr_fields_optional(self) -> None:
        entry = LearningEntry(id="L-adr01", summary="Test", detail="Detail")
        assert entry.adr_status is None
        assert entry.affected_paths == []
        assert entry.verification_criteria == []

    def test_adr_fields_populated(self) -> None:
        entry = LearningEntry(
            id="L-adr02",
            summary="Use hexagonal architecture",
            detail="Decision to use hexagonal architecture for core domain",
            tags=["adr", "architecture"],
            adr_status="accepted",
            affected_paths=["src/trw_mcp/models/*", "src/trw_mcp/state/*"],
            verification_criteria=["No infrastructure imports in domain layer"],
        )
        assert entry.adr_status == "accepted"
        assert len(entry.affected_paths) == 2
        assert len(entry.verification_criteria) == 1

    def test_non_adr_entry_unaffected(self) -> None:
        entry = LearningEntry(
            id="L-reg01",
            summary="Regular learning",
            detail="No ADR fields",
            impact=0.8,
        )
        assert entry.adr_status is None
        assert entry.impact == 0.8


class TestCollectADRsForContext:
    """collect_adrs_for_context matching."""

    @staticmethod
    def _write_adr(
        entries_dir: Path,
        *,
        adr_id: str,
        summary: str,
        detail: str,
        adr_status: str,
        affected_paths: list[str],
    ) -> None:
        writer = FileStateWriter()
        writer.write_yaml(entries_dir / f"{adr_id}.yaml", {
            "id": adr_id,
            "summary": summary,
            "detail": detail,
            "adr_status": adr_status,
            "affected_paths": affected_paths,
            "tags": ["adr"],
            "impact": 0.8,
            "status": "active",
        })

    def test_matches_path(self, adr_entries_dir: Path, tmp_path: Path) -> None:
        self._write_adr(
            adr_entries_dir,
            adr_id="L-adr01",
            summary="Use hexagonal",
            detail="Hexagonal architecture",
            adr_status="accepted",
            affected_paths=["src/trw_mcp/tools/*"],
        )
        trw_dir = tmp_path / ".trw"
        adrs = collect_adrs_for_context(trw_dir, "src/trw_mcp/tools/learning.py")
        assert len(adrs) == 1

    def test_no_match(self, adr_entries_dir: Path, tmp_path: Path) -> None:
        self._write_adr(
            adr_entries_dir,
            adr_id="L-adr02",
            summary="Use DDD",
            detail="DDD for core",
            adr_status="proposed",
            affected_paths=["src/core/*"],
        )
        trw_dir = tmp_path / ".trw"
        adrs = collect_adrs_for_context(trw_dir, "src/trw_mcp/tools/learning.py")
        assert adrs == []


class TestRenderBoundedContextClaudeMd:
    """Sub-CLAUDE.md rendering per bounded context."""

    def test_renders_with_adrs_and_learnings(self) -> None:
        content = render_bounded_context_claude_md(
            "Tools",
            "src/trw_mcp/tools",
            [{"summary": "Use foreground agents"}],
            [{"adr_status": "accepted", "summary": "Adopt hexagonal arch"}],
        )
        assert "# Tools" in content
        assert "`src/trw_mcp/tools`" in content
        assert "Architecture Decisions" in content
        assert "[accepted]" in content
        assert "Key Learnings" in content

    def test_renders_empty(self) -> None:
        content = render_bounded_context_claude_md("Core", "src/core", [], [])
        assert "# Core" in content
        assert "Architecture Decisions" not in content

    def test_max_lines_enforced(self) -> None:
        learnings = [{"summary": f"Learning {i}"} for i in range(100)]
        content = render_bounded_context_claude_md(
            "Big", "src/big", learnings, [], max_lines=10,
        )
        lines = content.split("\n")
        assert len(lines) <= 12

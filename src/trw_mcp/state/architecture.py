"""Architecture fitness functions -- import analysis, convention checking, phase gate integration."""

from __future__ import annotations

import ast
from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.architecture import (
    ArchitectureConfig,
    ArchitectureFitnessResult,
    Convention,
    ConventionViolation,
    DependencyRule,
    ImportViolation,
)
from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger()

_TYPE_CHECKING_GUARDS = frozenset({"if TYPE_CHECKING:", "if typing.TYPE_CHECKING:"})


def load_architecture_config(project_root: Path) -> ArchitectureConfig | None:
    """Load architecture config from .trw/config.yaml 'architecture' key."""
    config_path = project_root / ".trw" / "config.yaml"
    if not config_path.exists():
        return None

    from trw_mcp.state.persistence import FileStateReader

    reader = FileStateReader()
    try:
        data = reader.read_yaml(config_path)
    except (StateError, ValueError, TypeError):
        return None

    arch_data = data.get("architecture")
    if not isinstance(arch_data, dict):
        return None

    try:
        return ArchitectureConfig(**arch_data)
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("architecture_config_parse_error", path=str(config_path), error=str(exc))
        return None


def _resolve_module_layer(
    module_name: str,
    dependency_rules: list[DependencyRule],
) -> str | None:
    """Map a dotted module path to its dependency layer via substring/suffix matching."""
    for rule in dependency_rules:
        if rule.layer in module_name or module_name.endswith(rule.layer):
            return rule.layer
    return None


def _find_rule_for_layer(
    layer: str,
    dependency_rules: list[DependencyRule],
) -> DependencyRule | None:
    """Return the dependency rule matching *layer*, or None."""
    for rule in dependency_rules:
        if rule.layer == layer:
            return rule
    return None


def _is_in_type_checking_block(node: ast.stmt, source_lines: list[str]) -> bool:
    """Return True if *node* is inside an ``if TYPE_CHECKING:`` guard."""
    line_idx = node.lineno - 1
    import_indent = len(source_lines[line_idx]) - len(source_lines[line_idx].lstrip())

    for i in range(line_idx - 1, max(line_idx - 20, -1), -1):
        if i < 0:
            break
        stripped = source_lines[i].strip()

        if stripped in _TYPE_CHECKING_GUARDS:
            guard_indent = len(source_lines[i]) - len(source_lines[i].lstrip())
            if import_indent > guard_indent:
                return True

        # Stop at a non-comment, non-if top-level line when the import is indented
        if stripped and not stripped.startswith("#") and not stripped.startswith("if"):
            line_indent = len(source_lines[i]) - len(source_lines[i].lstrip())
            if line_indent <= 0 and import_indent > 0:
                break

    return False


def _check_import_module(
    module_name: str,
    node: ast.stmt,
    source_lines: list[str],
    dependency_rules: list[DependencyRule],
    rule: DependencyRule,
    from_layer: str,
    relative_path: str,
) -> ImportViolation | None:
    """Check a single imported module against dependency rules.

    Returns an ImportViolation if the import is forbidden, None otherwise.
    """
    if _is_in_type_checking_block(node, source_lines):
        return None

    to_layer = _resolve_module_layer(module_name, dependency_rules)
    if to_layer and to_layer in rule.may_not_import:
        return ImportViolation(
            file=relative_path,
            line=node.lineno,
            importing_module=relative_path,
            imported_module=module_name,
            from_layer=from_layer,
            to_layer=to_layer,
        )
    return None


def analyze_import_direction(
    file_path: Path,
    dependency_rules: list[DependencyRule],
    project_root: Path,
) -> list[ImportViolation]:
    """Analyze a Python file for import direction violations using AST.

    Parses the file, walks all Import/ImportFrom nodes, and checks each
    against the dependency rules. Imports inside TYPE_CHECKING blocks are skipped.
    """
    if not file_path.exists() or file_path.suffix != ".py":
        return []

    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (SyntaxError, OSError):
        return []

    relative = str(file_path.relative_to(project_root)).replace("\\", "/")
    from_layer = _resolve_module_layer(relative, dependency_rules)
    if from_layer is None:
        return []

    rule = _find_rule_for_layer(from_layer, dependency_rules)
    if rule is None:
        return []

    source_lines = source.split("\n")
    violations: list[ImportViolation] = []

    for node in ast.walk(tree):
        module_names: list[str] = []

        if isinstance(node, ast.Import):
            module_names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            module_names = [node.module]
        else:
            continue

        for module_name in module_names:
            violation = _check_import_module(
                module_name, node, source_lines,
                dependency_rules, rule, from_layer, relative,
            )
            if violation is not None:
                violations.append(violation)

    return violations


def check_conventions(
    files: list[Path],
    conventions: list[Convention],
    gate: str,
) -> list[ConventionViolation]:
    """Check conventions applicable to the current phase gate.

    Only conventions whose ``gate`` matches are checked.
    Currently supports the ``no_star_imports`` check method.
    """
    applicable = [c for c in conventions if c.gate == gate]
    if not applicable:
        return []

    violations: list[ConventionViolation] = []

    for conv in applicable:
        if conv.check_method == "no_star_imports":
            for f in files:
                if not f.exists() or f.suffix != ".py":
                    continue
                try:
                    source = f.read_text(encoding="utf-8")
                    tree = ast.parse(source, filename=str(f))
                except (SyntaxError, OSError):
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            if alias.name == "*":
                                violations.append(ConventionViolation(
                                    file=str(f),
                                    convention_name=conv.name,
                                    message=f"Star import from {node.module or '?'}",
                                    severity=conv.severity,
                                ))

    return violations


def check_architecture_fitness(
    phase: str,
    run_path: Path,
    config: ArchitectureConfig,
    project_root: Path | None = None,
    trw_config: TRWConfig | None = None,
) -> ArchitectureFitnessResult:
    """Orchestrate per-phase architecture fitness checks."""
    if project_root is None:
        from trw_mcp.state._paths import resolve_project_root
        project_root = resolve_project_root()

    cfg = trw_config or TRWConfig()
    src_dir = project_root / cfg.source_package_path

    all_violations: list[ImportViolation | ConventionViolation] = []
    warnings: list[str] = []
    checks_run = 0

    if phase in ("implement", "validate") and config.dependency_rules:
        checks_run += 1
        if src_dir.exists():
            for py_file in src_dir.rglob("*.py"):
                all_violations.extend(
                    analyze_import_direction(py_file, config.dependency_rules, project_root)
                )

    if config.conventions:
        checks_run += 1
        py_files = list(src_dir.rglob("*.py")) if src_dir.exists() else []
        all_violations.extend(check_conventions(py_files, config.conventions, phase))

    if all_violations:
        penalty = cfg.gate_architecture_score_penalty
        score = max(0.0, 1.0 - len(all_violations) * penalty)
    else:
        score = 1.0

    return ArchitectureFitnessResult(
        phase=phase,
        checks_run=checks_run,
        violations=all_violations,
        warnings=warnings,
        score=score,
    )


def generate_import_linter_config(
    dependency_rules: list[DependencyRule],
    package_name: str | None = None,
) -> str:
    """Generate .importlinter INI config from dependency rules."""
    pkg = package_name or TRWConfig().source_package_name
    lines: list[str] = ["[importlinter]", f"root_package = {pkg}", ""]

    for i, rule in enumerate(dependency_rules, 1):
        if not rule.may_not_import:
            continue
        contract_name = f"contract_{i}_{rule.layer}"
        lines.append(f"[importlinter:contract:{contract_name}]")
        lines.append(f"name = {rule.layer} layer isolation")
        lines.append("type = forbidden")
        lines.append(f"source_modules = {pkg}.{rule.layer}")
        forbidden = "\n    ".join(f"{pkg}.{m}" for m in rule.may_not_import)
        lines.append(f"forbidden_modules =\n    {forbidden}")
        lines.append("")

    return "\n".join(lines)

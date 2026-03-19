"""Semantic review automation beyond syntax checks.

PRD-QUAL-040: Runs regex-based pattern checks against file content
or git diff output to catch semantic issues that pytest/mypy miss.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class SemanticFinding:
    """A semantic issue found by automated pattern check."""

    check_id: str
    description: str
    severity: str  # error, warning, info
    file_path: str
    line_number: int
    matched_text: str


@dataclass
class SemanticCheckResult:
    """Aggregate result of running semantic checks."""

    findings: list[SemanticFinding] = field(default_factory=list)
    checks_run: int = 0
    files_scanned: int = 0

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "info")


@dataclass
class SemanticCheck:
    """A single semantic check definition."""

    id: str
    description: str
    severity: str
    automated: bool
    pattern: str | None = None
    language: str = "any"


def load_semantic_checks(rubric_path: Path | None = None) -> list[SemanticCheck]:
    """Load semantic check definitions from YAML rubric.

    Args:
        rubric_path: Path to semantic_checks.yaml. Auto-discovers if None.

    Returns:
        List of SemanticCheck definitions.
    """
    if rubric_path is None:
        # Auto-discover bundled rubric
        data_dir = Path(__file__).resolve().parent.parent / "data"
        rubric_path = data_dir / "semantic_checks.yaml"

    if not rubric_path.exists():
        logger.debug("semantic_checks_rubric_not_found", path=str(rubric_path))
        return []

    try:
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        data = yaml.load(rubric_path)
    except Exception:  # justified: fail-open, rubric parse failure degrades to no semantic checks
        logger.debug("semantic_checks_rubric_parse_error", path=str(rubric_path))
        return []

    checks: list[SemanticCheck] = []
    raw_checks = data.get("checks", []) if isinstance(data, dict) else []
    for item in raw_checks:
        if not isinstance(item, dict):
            continue
        checks.append(
            SemanticCheck(
                id=str(item.get("id", "")),
                description=str(item.get("description", "")),
                severity=str(item.get("severity", "info")),
                automated=bool(item.get("automated", False)),
                pattern=item.get("pattern"),
                language=str(item.get("language", "any")),
            )
        )

    return checks


def _get_language_for_file(file_path: str) -> str:
    """Infer language from file extension."""
    if file_path.endswith(".py"):
        return "python"
    if file_path.endswith((".ts", ".tsx", ".js", ".jsx")):
        return "typescript"
    if file_path.endswith(".go"):
        return "go"
    return "any"


def run_semantic_checks(
    file_paths: list[str],
    *,
    checks: list[SemanticCheck] | None = None,
    rubric_path: Path | None = None,
) -> SemanticCheckResult:
    """Run automated semantic checks against a set of files.

    Only runs checks where automated=True and the check has a regex pattern.
    Matches the check's language filter against the file extension.

    Args:
        file_paths: Files to scan.
        checks: Pre-loaded check definitions. Loaded from rubric if None.
        rubric_path: Path to rubric YAML (used if checks is None).

    Returns:
        SemanticCheckResult with all findings.
    """
    if checks is None:
        checks = load_semantic_checks(rubric_path)

    # Filter to automated checks with patterns
    auto_checks = [c for c in checks if c.automated and c.pattern]

    result = SemanticCheckResult(checks_run=len(auto_checks))

    for file_path_str in file_paths:
        path = Path(file_path_str)
        if not path.is_file():
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        result.files_scanned += 1
        file_lang = _get_language_for_file(file_path_str)

        for check in auto_checks:
            # Skip if language doesn't match
            if check.language not in ("any", file_lang):
                continue

            if check.pattern is None:
                continue

            try:
                compiled = re.compile(check.pattern)
            except re.error:
                continue

            for line_num, line in enumerate(content.splitlines(), start=1):
                match = compiled.search(line)
                if match:
                    result.findings.append(
                        SemanticFinding(
                            check_id=check.id,
                            description=check.description,
                            severity=check.severity,
                            file_path=file_path_str,
                            line_number=line_num,
                            matched_text=match.group(0) or "",
                        )
                    )

    logger.debug(
        "semantic_checks_complete",
        checks_run=result.checks_run,
        files_scanned=result.files_scanned,
        findings=len(result.findings),
    )
    return result


def format_semantic_report(result: SemanticCheckResult) -> str:
    """Format semantic check results as markdown for build report.

    Args:
        result: SemanticCheckResult from run_semantic_checks.

    Returns:
        Markdown-formatted report section.
    """
    if not result.findings:
        return (
            f"## Semantic Warnings\n\n"
            f"No semantic issues found ({result.checks_run} checks, "
            f"{result.files_scanned} files).\n"
        )

    lines: list[str] = [
        "## Semantic Warnings\n",
        f"Found {len(result.findings)} issue(s) across "
        f"{result.files_scanned} file(s) "
        f"({result.checks_run} automated checks).\n",
    ]

    # Group by severity
    for severity in ("error", "warning", "info"):
        severity_findings = [f for f in result.findings if f.severity == severity]
        if not severity_findings:
            continue

        icon = {"error": "!!!", "warning": "!!", "info": "i"}.get(severity, "")
        lines.append(f"\n### {severity.upper()} ({len(severity_findings)})\n")

        for finding in severity_findings[:20]:  # Cap per severity
            lines.append(
                f"- [{icon}] **{finding.check_id}**: "
                f"`{finding.file_path}:{finding.line_number}` — "
                f"{finding.description}"
            )
            if finding.matched_text:
                # Truncate long matches
                text = finding.matched_text[:80]
                lines.append(f"  `{text}`")

    return "\n".join(lines) + "\n"

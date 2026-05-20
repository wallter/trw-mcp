"""Strict skill manifest model and read-only markdown validator.

The parser in this module is intentionally pure: callers provide markdown
content and receive structured diagnostics. Bootstrap/server integration can
choose compatibility or strict mode without this module touching project state.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

SkillValidationMode: TypeAlias = Literal["compat", "strict"]
SkillRiskLevel: TypeAlias = Literal["low", "medium", "high", "critical"]
IssueSeverity: TypeAlias = Literal["warning", "error"]

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name",),
    "description": ("description",),
    "user_invocable": ("user_invocable", "user-invocable"),
    "argument_hint": ("argument_hint", "argument-hint"),
    "allowed_tools": ("allowed_tools", "allowed-tools"),
    "forbidden_tools": ("forbidden_tools", "forbidden-tools"),
    "requires_verification": ("requires_verification", "requires-verification"),
    "ordered_steps": ("ordered_steps", "ordered-steps"),
    "strict_execution": ("strict_execution", "strict-execution"),
    "meta_discovery": ("meta_discovery", "meta-discovery"),
    "risk_level": ("risk_level", "risk-level"),
}
_ALIAS_TO_FIELD: dict[str, str] = {alias: field for field, aliases in _FIELD_ALIASES.items() for alias in aliases}
_MCP_TOOL_RE = re.compile(r"\btrw_[A-Za-z][A-Za-z0-9_]*\b")
_SHELL_COMMAND_RE = re.compile(r"`\s*([A-Za-z][A-Za-z0-9_.-]*)\b[^`]*`")


class SkillManifestIssue(BaseModel):
    """Structured skill manifest warning/error with file, field, and reason."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    path: str
    field: str
    reason: str
    mode: SkillValidationMode
    severity: IssueSeverity


class SkillManifest(BaseModel):
    """Versioned skill metadata normalized from ``SKILL.md`` frontmatter."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, populate_by_name=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    user_invocable: bool = Field(default=True, validation_alias=AliasChoices("user_invocable", "user-invocable"))
    argument_hint: str | None = Field(default=None, validation_alias=AliasChoices("argument_hint", "argument-hint"))
    allowed_tools: tuple[str, ...] = Field(default_factory=tuple, validation_alias=AliasChoices("allowed_tools", "allowed-tools"))
    forbidden_tools: tuple[str, ...] = Field(
        default_factory=tuple,
        validation_alias=AliasChoices("forbidden_tools", "forbidden-tools"),
    )
    requires_verification: bool = Field(
        default=False,
        validation_alias=AliasChoices("requires_verification", "requires-verification"),
    )
    ordered_steps: tuple[str, ...] = Field(
        default_factory=tuple,
        validation_alias=AliasChoices("ordered_steps", "ordered-steps"),
    )
    strict_execution: bool = Field(default=False, validation_alias=AliasChoices("strict_execution", "strict-execution"))
    meta_discovery: bool = Field(default=True, validation_alias=AliasChoices("meta_discovery", "meta-discovery"))
    risk_level: SkillRiskLevel = Field(default="low", validation_alias=AliasChoices("risk_level", "risk-level"))

    @field_validator("name", "description", "argument_hint")
    @classmethod
    def _strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if stripped == "":
            raise ValueError("must not be blank")
        return stripped

    @field_validator("allowed_tools", "forbidden_tools", "ordered_steps", mode="before")
    @classmethod
    def _string_sequence(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value.strip(),)
        if not isinstance(value, Sequence):
            raise TypeError("must be a sequence of strings")
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError("must contain only strings")
            stripped = item.strip()
            if stripped == "":
                raise ValueError("must not contain blank strings")
            normalized.append(stripped)
        return tuple(normalized)


class SkillManifestValidationResult(BaseModel):
    """Result of parsing and linting a skill manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    manifest: SkillManifest | None = None
    body: str = ""
    warnings: tuple[SkillManifestIssue, ...] = ()
    errors: tuple[SkillManifestIssue, ...] = ()

    @property
    def ok(self) -> bool:
        """Return whether validation produced no errors."""

        return len(self.errors) == 0


def validate_skill_markdown(
    markdown: str,
    *,
    path: str | Path,
    mode: SkillValidationMode = "compat",
) -> SkillManifestValidationResult:
    """Parse and validate a ``SKILL.md`` markdown document."""

    path_text = str(path)
    frontmatter, body, envelope_errors = _extract_frontmatter(markdown, path_text, mode)
    if envelope_errors:
        return SkillManifestValidationResult(errors=tuple(envelope_errors), body=body)

    raw_data, yaml_errors = _load_frontmatter(frontmatter, path_text, mode)
    if yaml_errors:
        return SkillManifestValidationResult(errors=tuple(yaml_errors), body=body)

    data, unknown_issues, alias_issues = _normalize_frontmatter(raw_data, path_text, mode)
    warnings = [issue for issue in (*unknown_issues, *alias_issues) if issue.severity == "warning"]
    errors = [issue for issue in (*unknown_issues, *alias_issues) if issue.severity == "error"]

    manifest = _build_manifest(data, path_text, mode, errors)
    if manifest is not None:
        lint_issues = _lint_manifest_body(manifest, body, path_text, mode)
        warnings.extend(issue for issue in lint_issues if issue.severity == "warning")
        errors.extend(issue for issue in lint_issues if issue.severity == "error")

    return SkillManifestValidationResult(
        manifest=manifest,
        body=body,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def _issue(
    *,
    path: str,
    field: str,
    reason: str,
    mode: SkillValidationMode,
    severity: IssueSeverity,
) -> SkillManifestIssue:
    return SkillManifestIssue(path=path, field=field, reason=reason, mode=mode, severity=severity)


def _extract_frontmatter(
    markdown: str,
    path: str,
    mode: SkillValidationMode,
) -> tuple[str, str, list[SkillManifestIssue]]:
    if not markdown.startswith("---\n"):
        return "", markdown, [_issue(path=path, field="frontmatter", reason="missing YAML frontmatter", mode=mode, severity="error")]

    closing = markdown.find("\n---", 4)
    if closing == -1:
        return "", markdown, [
            _issue(path=path, field="frontmatter", reason="unterminated YAML frontmatter", mode=mode, severity="error")
        ]

    frontmatter = markdown[4:closing]
    body_start = closing + len("\n---")
    if body_start < len(markdown) and markdown[body_start] == "\n":
        body_start += 1
    return frontmatter, markdown[body_start:], []


def _load_frontmatter(
    frontmatter: str,
    path: str,
    mode: SkillValidationMode,
) -> tuple[dict[str, object], list[SkillManifestIssue]]:
    yaml = YAML(typ="safe")
    try:
        loaded = yaml.load(frontmatter)
    except YAMLError as exc:
        return {}, [_issue(path=path, field="frontmatter", reason=f"invalid YAML: {exc}", mode=mode, severity="error")]

    if loaded is None:
        return {}, []
    if not isinstance(loaded, Mapping):
        return {}, [_issue(path=path, field="frontmatter", reason="frontmatter must be a mapping", mode=mode, severity="error")]

    data: dict[str, object] = {}
    for key, value in loaded.items():
        if not isinstance(key, str):
            return {}, [_issue(path=path, field="frontmatter", reason="frontmatter keys must be strings", mode=mode, severity="error")]
        data[key] = value
    return data, []


def _normalize_frontmatter(
    raw_data: Mapping[str, object],
    path: str,
    mode: SkillValidationMode,
) -> tuple[dict[str, object], list[SkillManifestIssue], list[SkillManifestIssue]]:
    normalized: dict[str, object] = {}
    unknown_issues: list[SkillManifestIssue] = []
    alias_issues: list[SkillManifestIssue] = []

    for key, value in raw_data.items():
        canonical = _ALIAS_TO_FIELD.get(key)
        if canonical is None:
            severity: IssueSeverity = "error" if mode == "strict" else "warning"
            reason = "unknown field is not allowed in strict mode" if mode == "strict" else "unknown field ignored by compatibility mode"
            unknown_issues.append(_issue(path=path, field=key, reason=reason, mode=mode, severity=severity))
            continue

        if canonical in normalized:
            severity = "error" if mode == "strict" else "warning"
            alias_issues.append(
                _issue(
                    path=path,
                    field=key,
                    reason=f"duplicate alias for {canonical}",
                    mode=mode,
                    severity=severity,
                )
            )
            continue
        if mode == "compat" and canonical == "argument_hint" and isinstance(value, str) and not value.strip():
            normalized[canonical] = None
            continue
        normalized[canonical] = value

    return normalized, unknown_issues, alias_issues


def _build_manifest(
    data: Mapping[str, object],
    path: str,
    mode: SkillValidationMode,
    existing_errors: list[SkillManifestIssue],
) -> SkillManifest | None:
    if existing_errors and mode == "strict":
        return None

    try:
        return SkillManifest.model_validate(data)
    except ValidationError as exc:
        existing_errors.extend(
            _issue(
                path=path,
                field=_validation_field(error.get("loc", ())),
                reason=str(error.get("msg", "invalid manifest field")),
                mode=mode,
                severity="error",
            )
            for error in exc.errors()
        )
        return None


def _validation_field(location: object) -> str:
    if isinstance(location, tuple) and location:
        return str(location[0])
    if isinstance(location, list) and location:
        return str(location[0])
    return "manifest"


def _lint_manifest_body(
    manifest: SkillManifest,
    body: str,
    path: str,
    mode: SkillValidationMode,
) -> tuple[SkillManifestIssue, ...]:
    issues: list[SkillManifestIssue] = []
    issues.extend(_lint_ordered_steps(manifest, body, path, mode))
    issues.extend(_lint_tool_constraints(manifest, body, path, mode))
    return tuple(issues)


def _lint_ordered_steps(
    manifest: SkillManifest,
    body: str,
    path: str,
    mode: SkillValidationMode,
) -> list[SkillManifestIssue]:
    issues: list[SkillManifestIssue] = []
    declared_steps = set()
    duplicate_steps = set()
    for step in manifest.ordered_steps:
        if step in declared_steps:
            duplicate_steps.add(step)
        declared_steps.add(step)
    issues.extend(
        _lint_issue(path=path, field="ordered_steps", reason=f"ordered step {step!r} is declared more than once", mode=mode)
        for step in sorted(duplicate_steps)
    )
    previous_index = -1
    for step in manifest.ordered_steps:
        count = body.count(step)
        if count == 0:
            issues.append(
                _lint_issue(path=path, field="ordered_steps", reason=f"ordered step {step!r} is missing", mode=mode)
            )
            continue
        if count > 1:
            issues.append(
                _lint_issue(path=path, field="ordered_steps", reason=f"ordered step {step!r} is duplicate", mode=mode)
            )
        current_index = body.find(step)
        if current_index < previous_index:
            issues.append(
                _lint_issue(path=path, field="ordered_steps", reason=f"ordered step {step!r} is out of order", mode=mode)
            )
        previous_index = current_index
    return issues


def _lint_tool_constraints(
    manifest: SkillManifest,
    body: str,
    path: str,
    mode: SkillValidationMode,
) -> list[SkillManifestIssue]:
    referenced_tools = _referenced_tools(body)
    if not referenced_tools:
        return []

    issues: list[SkillManifestIssue] = []
    forbidden = set(manifest.forbidden_tools)
    allowed = set(manifest.allowed_tools)

    issues.extend(
        _lint_issue(path=path, field="forbidden_tools", reason=f"forbidden tool {tool!r} is referenced", mode=mode)
        for tool in sorted(referenced_tools & forbidden)
    )

    if manifest.strict_execution:
        undeclared = referenced_tools - allowed
        issues.extend(
            _lint_issue(path=path, field="allowed_tools", reason=f"undeclared tool {tool!r} is referenced", mode=mode)
            for tool in sorted(undeclared)
        )

    return issues


def _lint_issue(*, path: str, field: str, reason: str, mode: SkillValidationMode) -> SkillManifestIssue:
    severity: IssueSeverity = "error" if mode == "strict" else "warning"
    return _issue(path=path, field=field, reason=reason, mode=mode, severity=severity)


def _referenced_tools(body: str) -> set[str]:
    tools = set(_MCP_TOOL_RE.findall(body))
    tools.update(match.group(1) for match in _SHELL_COMMAND_RE.finditer(body))
    return tools

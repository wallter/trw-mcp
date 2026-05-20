"""Assemble AgentWorkEvidence v1 documents from TRW run state."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from trw_mcp.exceptions import StateError
from trw_mcp.models.agent_work_evidence import (
    AgentInfo,
    AgentWorkEvidence,
    ArtifactEvidence,
    ChangedFileEvidence,
    EvidenceEvent,
    EvidenceEventSummary,
    EvidenceTimestamps,
    ReviewEvidence,
    RunIdentity,
    VerificationEvidence,
    with_agent_work_evidence_integrity,
)
from trw_mcp.models.build import BuildStatus
from trw_mcp.models.run import RunState
from trw_mcp.state import _paths
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.report import parse_run_events

_PRD_RE = re.compile(r"PRD-[A-Z]+-\d+")
_FR_RE = re.compile(r"FR-?0*(\d+)", re.IGNORECASE)
_SECRETISH_RE = re.compile(r"(secret|token|password|credential|private|transcript)", re.IGNORECASE)


def assemble_agent_work_evidence(
    run_path: Path,
    *,
    include_events: bool = False,
    reader: FileStateReader | None = None,
) -> AgentWorkEvidence:
    """Assemble schema-valid AgentWorkEvidence from a run directory."""

    state_reader = reader or FileStateReader()
    meta_path = run_path / "meta"
    run_data = state_reader.read_yaml(meta_path / "run.yaml")
    run_state = RunState.model_validate(run_data)
    events_path = meta_path / "events.jsonl"
    events = state_reader.read_jsonl(events_path) if events_path.exists() else []
    event_summary_model, _, duration, _ = parse_run_events(events)
    warnings: list[str] = []
    verification = _assemble_verification(run_path, state_reader, warnings)
    artifacts = _assemble_artifacts(run_path, run_state.artifacts, warnings)
    changed_files = _collect_changed_files(warnings)
    event_refs = _safe_event_refs(events) if include_events else []
    generated_at = _generated_at(events, verification)
    evidence = AgentWorkEvidence(
        identity=RunIdentity(run_id=run_state.run_id, run_path=_safe_relpath(run_path)),
        task=run_state.task,
        prd_scope=run_state.prd_scope,
        phase=str(run_state.phase),
        status=str(run_state.status),
        agent=AgentInfo(agent_id=run_state.owner_session_id or "unknown", role="implementer"),
        timestamps=EvidenceTimestamps(started_at=duration.start_ts or "", generated_at=generated_at),
        intent=run_state.objective,
        plan_summary=f"checkpoint_count={_checkpoint_count(meta_path, state_reader)}; event_count={len(events)}",
        changed_files=changed_files,
        verification=verification,
        review=_assemble_review(run_path, state_reader),
        artifacts=artifacts,
        event_summary=EvidenceEventSummary(
            total_count=event_summary_model.total_count,
            by_type=event_summary_model.by_type,
        ),
        events=event_refs,
        warnings=warnings,
    )
    return with_agent_work_evidence_integrity(evidence)


def _assemble_verification(run_path: Path, reader: FileStateReader, warnings: list[str]) -> VerificationEvidence:
    build_path = _infer_trw_dir(run_path) / "context" / "build-status.yaml"
    if not build_path.exists():
        warnings.append("build-status.yaml missing; verification status set to missing")
        return VerificationEvidence(
            id="verification:missing:build-status",
            status="missing",
            command="build-status.yaml",
            scope="missing",
        )
    try:
        build = BuildStatus.model_validate(reader.read_yaml(build_path))
    except ValidationError as exc:
        raise StateError(f"Invalid build-status.yaml: {exc}", path=str(build_path)) from exc
    checks_clean = build.static_checks_clean if build.static_checks_clean is not None else build.mypy_clean
    passed = build.tests_passed and checks_clean and not build.timed_out and build.failure_count == 0
    status: Literal["passed", "failed"] = "passed" if passed else "failed"
    return VerificationEvidence(
        id=f"verification:{_slug(build.scope)}:build-status",
        status=status,
        tests_passed=build.tests_passed,
        static_checks_clean=checks_clean,
        coverage_pct=build.coverage_pct,
        failure_count=build.failure_count,
        command="build-status.yaml",
        scope=build.scope,
    )


def _assemble_review(run_path: Path, reader: FileStateReader) -> list[ReviewEvidence]:
    review_path = run_path / "meta" / "review.yaml"
    review: list[ReviewEvidence] = [
        ReviewEvidence(
            id="review:self:privacy",
            category="self",
            status="passed",
            summary="Evidence export excludes raw diffs, transcript bodies, and sensitive artifact content.",
        )
    ]
    if not review_path.exists():
        return review

    review_data = reader.read_yaml(review_path)
    verdict = _review_status(str(review_data.get("verdict", "unknown")))
    review_id = _slug(str(review_data.get("review_id", "review-yaml")))
    review.append(
        ReviewEvidence(
            id=f"review:{review_id}:verdict",
            category="verdict",
            status=verdict,
            summary=f"review.yaml verdict={review_data.get('verdict', 'unknown')}",
        )
    )
    findings = review_data.get("findings")
    if isinstance(findings, list):
        for index, finding in enumerate(findings):
            if not isinstance(finding, Mapping):
                continue
            category = str(finding.get("category", "finding"))
            severity = str(finding.get("severity", "unknown"))
            description = str(finding.get("description", "")).strip()
            review.append(
                ReviewEvidence(
                    id=f"review:{review_id}:finding-{index + 1}",
                    category=category,
                    status=_finding_status(severity),
                    summary=description[:240] or f"review finding severity={severity}",
                )
            )
    return review


def _collect_changed_files(warnings: list[str]) -> list[ChangedFileEvidence]:
    root = _git_root()
    if root is None:
        warnings.append("git metadata unavailable; changed_files is empty")
        return []
    status = _git(["status", "--porcelain=v1", "--untracked-files=all", "-z"], root)
    if status is None:
        warnings.append("git status unavailable; changed_files is empty")
        return []
    entries = _parse_porcelain_z(status)
    return [
        ChangedFileEvidence(
            id=f"changed-file:{path}",
            path=path,
            change_type=_change_type(code),
            diff_hash=_diff_hash(root, path, code),
            related_prds=_related_prds(path),
            related_frs=_related_frs(path),
        )
        for code, path in entries
        if not _SECRETISH_RE.search(path)
    ]


def _git_root() -> Path | None:
    git_executable = shutil.which("git")
    if git_executable is None:
        return None
    try:
        root = subprocess.run(  # noqa: S603
            [git_executable, "rev-parse", "--show-toplevel"],
            cwd=_paths.resolve_project_root(),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return Path(root) if root else None


def _git(args: list[str], cwd: Path) -> str | None:
    git_executable = shutil.which("git")
    if git_executable is None:
        return None
    try:
        return subprocess.run(  # noqa: S603
            [git_executable, *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def _parse_porcelain_z(raw: str) -> list[tuple[str, str]]:
    parts = [part for part in raw.split("\0") if part]
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(parts):
        entry = parts[index]
        code = entry[:2]
        path = entry[3:]
        if code.startswith(("R", "C")):
            index += 1
            if index < len(parts):
                path = parts[index]
        entries.append((code, path))
        index += 1
    return entries


def _diff_hash(root: Path, path: str, code: str) -> str | None:
    if "?" in code:
        return None
    diff = _git(["diff", "--", path], root)
    if not diff:
        diff = _git(["diff", "--cached", "--", path], root)
    if not diff:
        return None
    return hashlib.sha256(diff.encode("utf-8")).hexdigest()


def _change_type(code: str) -> Literal["added", "modified", "deleted", "renamed", "copied", "unknown"]:
    compact = code.strip()
    if "R" in compact:
        return "renamed"
    if "C" in compact:
        return "copied"
    if "D" in compact:
        return "deleted"
    if "A" in compact or "?" in compact:
        return "added"
    if "M" in compact:
        return "modified"
    return "unknown"


def _assemble_artifacts(run_path: Path, artifact_paths: list[str], warnings: list[str]) -> list[ArtifactEvidence]:
    artifacts: list[ArtifactEvidence] = []
    resolved_run_path = run_path.resolve()
    for artifact_path in artifact_paths:
        if _SECRETISH_RE.search(artifact_path):
            warnings.append("artifact skipped by privacy filter")
            continue
        resolved = (run_path / artifact_path).resolve()
        try:
            resolved.relative_to(resolved_run_path)
        except ValueError:
            warnings.append("artifact skipped because it is outside the run directory")
            continue
        content_hash = _file_hash(resolved) if resolved.exists() and resolved.is_file() else None
        artifacts.append(
            ArtifactEvidence(
                id=f"artifact:{_artifact_category(artifact_path)}:{_slug(artifact_path)}",
                category=_artifact_category(artifact_path),
                path=artifact_path,
                content_hash=content_hash,
            )
        )
    return artifacts


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_event_refs(events: list[dict[str, object]]) -> list[EvidenceEvent]:
    refs: list[EvidenceEvent] = []
    for index, event in enumerate(events):
        event_type = str(event.get("event", "unknown"))
        refs.append(EvidenceEvent(id=f"event:{index + 1}:{_slug(event_type)}", event_type=event_type, ts=str(event.get("ts", ""))))
    return refs


def _generated_at(events: list[dict[str, object]], verification: VerificationEvidence) -> str:
    if events:
        return str(events[-1].get("ts", "")) or "unknown"
    if verification.scope:
        return f"verification:{verification.scope}"
    return "unknown"


def _checkpoint_count(meta_path: Path, reader: FileStateReader) -> int:
    checkpoints_path = meta_path / "checkpoints.jsonl"
    return len(reader.read_jsonl(checkpoints_path)) if checkpoints_path.exists() else 0


def _infer_trw_dir(run_path: Path) -> Path:
    for parent in [run_path, *run_path.parents]:
        if parent.name == ".trw":
            return parent
    return run_path.parents[2] / ".trw" if len(run_path.parents) > 2 else Path(".trw")


def _artifact_category(path: str) -> str:
    first = path.split("/", maxsplit=1)[0]
    if first in {"meta", "reports", "scratch", "reviews", "build"}:
        return "run" if first == "meta" else first
    return "artifact"


def _review_status(verdict: str) -> Literal["passed", "warn", "failed", "missing", "unknown"]:
    normalized = verdict.strip().lower()
    if normalized in {"pass", "passed"}:
        return "passed"
    if normalized in {"warn", "warning"}:
        return "warn"
    if normalized in {"block", "blocked", "fail", "failed", "critical"}:
        return "failed"
    if normalized == "missing":
        return "missing"
    return "unknown"


def _finding_status(severity: str) -> Literal["passed", "warn", "failed", "missing", "unknown"]:
    normalized = severity.strip().lower()
    if normalized in {"critical", "high", "block", "failed"}:
        return "failed"
    if normalized in {"warning", "warn", "medium", "low"}:
        return "warn"
    if normalized in {"info", "passed", "pass"}:
        return "passed"
    if normalized == "missing":
        return "missing"
    return "unknown"


def _related_prds(text: str) -> list[str]:
    return sorted(set(_PRD_RE.findall(text)))


def _related_frs(text: str) -> list[str]:
    return [f"FR-{int(match)}" for match in sorted(set(_FR_RE.findall(text)), key=int)]


def _safe_relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_paths.resolve_project_root()))
    except ValueError:
        return str(path)


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug or "unknown"

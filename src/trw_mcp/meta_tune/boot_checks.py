"""Boot-time validation for SAFE-001 defaults."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.meta_tune.errors import (
    KillSwitchNotFoundError,
    MetaTuneBootValidationError,
)
from trw_mcp.models.config._main import TRWConfig

logger = structlog.get_logger(__name__)

_IS_LINUX: bool = platform.system() == "Linux"

try:  # pragma: no cover - import guard
    import pyseccomp  # type: ignore[import-untyped, import-not-found, unused-ignore]

    _HAS_SECCOMP = True
except ImportError:  # pragma: no cover - optional dep
    pyseccomp = None  # type: ignore[assignment,unused-ignore]
    _HAS_SECCOMP = False


@dataclass(frozen=True)
class BootValidationFailure:
    key: str
    actual: str
    remediation: str


def _resolve_repo_root(*, repo_root: Path | None = None, cwd: Path | None = None) -> Path:
    if repo_root is not None:
        return repo_root.resolve()
    if cwd is not None:
        start = cwd.resolve()
        for candidate in (start, *start.parents):
            if (candidate / ".trw").exists():
                return candidate
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607 — git is on PATH; partial-path is intentional
            capture_output=True,
            check=True,
            timeout=2.0,
            text=True,
            cwd=str(cwd) if cwd is not None else None,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    else:
        root = Path(out.stdout.strip())
        if root.exists():
            return root.resolve()
    start = (cwd or Path.cwd()).resolve()
    for candidate in (start, *start.parents):
        if (candidate / ".trw").exists():
            return candidate
    raise KillSwitchNotFoundError("Unable to locate repo root for SAFE-001 kill switch")


def _resolve_repo_path(path_str: str, *, repo_root: Path) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return (repo_root / path).resolve()


def resolve_kill_switch_path(
    config: TRWConfig | Any,
    *,
    repo_root: Path | None = None,
    cwd: Path | None = None,
) -> Path:
    base = _resolve_repo_root(repo_root=repo_root, cwd=cwd)
    kill_switch_path = getattr(config, "kill_switch_path", ".trw/config.yaml")
    return _resolve_repo_path(str(kill_switch_path), repo_root=base)


def _ensure_parent_writable(path: Path) -> bool:
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(parent, os.W_OK)


def _validate_sandbox(image_tag: str) -> BootValidationFailure | None:
    normalized = image_tag.strip()
    if normalized in {"", "subprocess-seccomp-v1"}:
        if not _IS_LINUX:
            return BootValidationFailure(
                key="sandbox",
                actual=f"platform={platform.system()}",
                remediation="Run SAFE-001 on Linux or keep meta_tune.enabled=false.",
            )
        if not _HAS_SECCOMP:
            return BootValidationFailure(
                key="sandbox",
                actual="pyseccomp unavailable",
                remediation="Install pyseccomp before enabling SAFE-001.",
            )
        if shutil.which("unshare") is None:
            return BootValidationFailure(
                key="sandbox",
                actual="unshare missing from PATH",
                remediation="Install util-linux or provide a containerized sandbox implementation.",
            )
        return None

    if normalized.startswith(("docker://", "docker:")):
        runtime = "docker"
    elif normalized.startswith(("podman://", "podman:")):
        runtime = "podman"
    else:
        return BootValidationFailure(
            key="sandbox",
            actual=f"unsupported sandbox_image_tag={normalized}",
            remediation="Use subprocess-seccomp-v1 or a docker:/podman: image reference.",
        )

    if shutil.which(runtime) is None:
        return BootValidationFailure(
            key="sandbox",
            actual=f"{runtime} runtime missing for {normalized}",
            remediation=f"Install {runtime} or configure sandbox_image_tag=subprocess-seccomp-v1.",
        )
    return None


def audit_defaults(config: TRWConfig | None = None, *, repo_root: Path | None = None) -> dict[str, Any]:
    cfg = config or TRWConfig()
    root = _resolve_repo_root(repo_root=repo_root)
    kill_switch = resolve_kill_switch_path(cfg.meta_tune, repo_root=root)
    audit_log = _resolve_repo_path(cfg.meta_tune.audit_log_path, repo_root=root)
    corpus_path = _resolve_repo_path(cfg.meta_tune.corpus_path, repo_root=root)
    fixture_path = _resolve_repo_path(cfg.meta_tune.eval_gaming_fixture_path, repo_root=root)
    sandbox_failure = _validate_sandbox(cfg.meta_tune.sandbox_image_tag)
    fixture_count = len(list(fixture_path.glob("*.yaml"))) if fixture_path.exists() else 0
    return {
        "kill_switch_path": {
            "resolved": kill_switch.exists(),
            "parent_writable": _ensure_parent_writable(kill_switch),
            "path": str(kill_switch),
        },
        "audit_log_path": {
            "parent_writable": _ensure_parent_writable(audit_log),
            "path": str(audit_log),
        },
        "corpus_path": {
            "resolved": corpus_path.exists(),
            "has_version_subdir": any(p.is_dir() for p in corpus_path.iterdir()) if corpus_path.exists() else False,
            "path": str(corpus_path),
        },
        "eval_gaming_fixture_path": {
            "resolved": fixture_path.exists(),
            "fixture_count": fixture_count,
            "path": str(fixture_path),
        },
        "sandbox": {
            "ready": sandbox_failure is None,
            "image_tag": cfg.meta_tune.sandbox_image_tag,
            "reason": sandbox_failure.actual if sandbox_failure else None,
        },
    }


def validate_defaults(config: TRWConfig | None = None, *, repo_root: Path | None = None) -> None:
    cfg = config or TRWConfig()
    root = _resolve_repo_root(repo_root=repo_root)
    failures: list[BootValidationFailure] = []

    kill_switch = resolve_kill_switch_path(cfg.meta_tune, repo_root=root)
    if not kill_switch.exists():
        failures.append(
            BootValidationFailure(
                key="kill_switch_path",
                actual=f"missing: {kill_switch}",
                remediation="Create .trw/config.yaml or configure MetaTuneConfig.kill_switch_path explicitly.",
            )
        )
    elif not kill_switch.is_file():
        failures.append(
            BootValidationFailure(
                key="kill_switch_path",
                actual=f"not a file: {kill_switch}",
                remediation="Point kill_switch_path at a config file, not a directory.",
            )
        )
    elif not _ensure_parent_writable(kill_switch):
        failures.append(
            BootValidationFailure(
                key="kill_switch_path",
                actual=f"parent not writable: {kill_switch.parent}",
                remediation="Grant write access to the kill-switch directory before enabling meta-tune.",
            )
        )

    audit_log = _resolve_repo_path(cfg.meta_tune.audit_log_path, repo_root=root)
    if not _ensure_parent_writable(audit_log):
        failures.append(
            BootValidationFailure(
                key="audit_log_path",
                actual=f"parent not writable: {audit_log.parent}",
                remediation="Grant write access to the SAFE-001 audit directory before enabling meta-tune.",
            )
        )

    corpus_path = _resolve_repo_path(cfg.meta_tune.corpus_path, repo_root=root)
    if not corpus_path.exists() or not any(p.is_dir() for p in corpus_path.iterdir()):
        failures.append(
            BootValidationFailure(
                key="corpus_path",
                actual=f"missing or empty corpus root: {corpus_path}",
                remediation="Provision the SAFE-001 held-out corpus with at least one versioned subdirectory.",
            )
        )

    fixture_path = _resolve_repo_path(cfg.meta_tune.eval_gaming_fixture_path, repo_root=root)
    fixture_count = len(list(fixture_path.glob("*.yaml"))) if fixture_path.exists() else 0
    if fixture_count < 5:
        failures.append(
            BootValidationFailure(
                key="eval_gaming_fixture_path",
                actual=f"fixture_count={fixture_count} at {fixture_path}",
                remediation="Ship at least five DGM attack fixtures before enabling SAFE-001.",
            )
        )

    sandbox_failure = _validate_sandbox(cfg.meta_tune.sandbox_image_tag)
    if sandbox_failure is not None:
        failures.append(sandbox_failure)

    if failures:
        for failure in failures:
            logger.error(
                "meta_tune_boot_validation_failed",
                component="meta_tune.boot_checks",
                op="validate_defaults",
                outcome="error",
                key=failure.key,
                actual=failure.actual,
            )
        details = "\n".join(
            f"- {failure.key}: {failure.actual}. Remediation: {failure.remediation}" for failure in failures
        )
        raise MetaTuneBootValidationError(f"SAFE-001 boot validation failed ({len(failures)} issue(s)):\n{details}")

    logger.info(
        "meta_tune_boot_validation_ok",
        component="meta_tune.boot_checks",
        op="validate_defaults",
        outcome="ok",
    )


__all__ = [
    "BootValidationFailure",
    "audit_defaults",
    "resolve_kill_switch_path",
    "validate_defaults",
]

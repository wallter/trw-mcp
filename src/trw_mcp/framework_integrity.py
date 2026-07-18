"""Read-only inspection and explicit repair for deployed framework artifacts.

Source-mirror parity is a monorepo concern handled by
``scripts/check-aaref-sync.py``.  This module handles the separate installed
runtime concern: effective version pins, deployed bodies, and ``VERSION.yaml``
must describe the same FRAMEWORK/AARE-F content.

The module intentionally depends only on the Python standard library so the
repository integrity CLI can load it directly without starting the MCP server.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.framework_deployment import (
    DEPLOYMENT_RELATIVE_PATH,
    deploy_framework_generation,
    rollback_framework_generation,
)

_FRAMEWORK_RUNTIME_PATH = Path(".trw/frameworks/FRAMEWORK.md")
_AAREF_RUNTIME_PATH = Path(".trw/frameworks/AARE-F-FRAMEWORK.md")
_VERSION_PATH = Path(".trw/frameworks/VERSION.yaml")
_CONFIG_PATH = Path(".trw/config.yaml")


@dataclass(frozen=True)
class FrameworkIntegrityReport:
    """Integrity result for one deployed project root."""

    target: Path
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


def _yaml_scalar(text: str, field: str) -> str | None:
    match = re.search(
        rf"^{re.escape(field)}:\s*['\"]?([^\s#'\"]+)['\"]?\s*(?:#.*)?$",
        text,
        re.MULTILINE,
    )
    return match.group(1) if match else None


def _framework_body_version(text: str) -> str | None:
    match = re.match(r"^(v[0-9]+(?:\.[0-9]+)?_TRW)(?=\s|—|-)", text)
    return match.group(1) if match else None


def _aaref_body_version(text: str) -> str | None:
    match = re.search(r"^\*\*Version\*\*:\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$", text, re.MULTILINE)
    return f"v{match.group(1)}" if match else None


def _read(path: Path, errors: list[str], label: str) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append(f"{label} missing: {path}")
    except OSError as exc:
        errors.append(f"{label} unreadable: {path}: {exc}")
    return None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inspect_framework_runtime(
    target: Path,
    *,
    framework_source: str,
    aaref_source: str,
    framework_version: str,
    aaref_version: str,
    registry_digest: str | None = None,
) -> FrameworkIntegrityReport:
    """Compare effective version pins, deployed bodies, and version stamp.

    An absent project config pin is valid because the package default remains
    effective.  When a pin is present it must match the package/source version.
    Deployed bodies are byte-compared with the bundled authoring bodies, not
    merely searched for a version token; a version-string match never waives the
    byte comparison (PRD-INFRA-164 FR04). When ``registry_digest`` is supplied,
    the ``VERSION.yaml`` deployment stamp must carry a matching ``registry_digest``
    field; a stamp lacking it is reported as ``needs_upgrade`` (warning), not
    current, so legacy pre-digest generations are visible without a hard failure.
    """
    target = target.resolve()
    errors: list[str] = []
    warnings: list[str] = []

    source_versions = (
        ("framework authoring body", _framework_body_version(framework_source), framework_version),
        ("AARE-F authoring body", _aaref_body_version(aaref_source), aaref_version),
    )
    for label, actual, expected in source_versions:
        if actual != expected:
            errors.append(f"{label} declares {actual or 'no version'}; expected {expected}")

    config_path = target / _CONFIG_PATH
    if config_path.is_file():
        config_text = _read(config_path, errors, "project config")
        if config_text is not None:
            for field, expected in (
                ("framework_version", framework_version),
                ("aaref_version", aaref_version),
            ):
                configured = _yaml_scalar(config_text, field)
                if configured is not None and configured != expected:
                    errors.append(f"effective config pin {field}={configured}; expected {expected}")
    else:
        warnings.append(f"project config absent; package defaults apply: {config_path}")

    for label, rel_path, source_text, version_reader, expected_version in (
        ("FRAMEWORK", _FRAMEWORK_RUNTIME_PATH, framework_source, _framework_body_version, framework_version),
        ("AARE-F", _AAREF_RUNTIME_PATH, aaref_source, _aaref_body_version, aaref_version),
    ):
        deployed_path = target / rel_path
        deployed = _read(deployed_path, errors, f"deployed {label}")
        if deployed is None:
            continue
        actual_version = version_reader(deployed)
        if actual_version != expected_version:
            errors.append(
                f"deployed {label} body declares {actual_version or 'no version'}; expected {expected_version}"
            )
        if deployed != source_text:
            errors.append(
                f"deployed {label} body_digest_mismatch: differs from bundled authoring source "
                f"({_sha256(deployed)} != {_sha256(source_text)}): {deployed_path}"
            )

    version_path = target / _VERSION_PATH
    version_text = _read(version_path, errors, "deployment version stamp")
    if version_text is not None:
        for field, expected in (
            ("framework_version", framework_version),
            ("aaref_version", aaref_version),
        ):
            stamped = _yaml_scalar(version_text, field)
            if stamped != expected:
                errors.append(f"deployment stamp {field}={stamped or 'missing'}; expected {expected}")
        if registry_digest is not None:
            stamped_digest = _yaml_scalar(version_text, "registry_digest")
            if stamped_digest is None:
                warnings.append("deployment stamp missing registry_digest; needs_upgrade")
            elif stamped_digest != registry_digest:
                errors.append(f"deployment stamp registry_digest={stamped_digest}; expected {registry_digest}")

    if registry_digest is not None:
        receipt_path = target / DEPLOYMENT_RELATIVE_PATH
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            warnings.append("authoritative DEPLOYMENT.json missing; needs_upgrade")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"authoritative deployment receipt unreadable: {receipt_path}: {exc}")
        else:
            if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
                errors.append("authoritative deployment receipt has unsupported schema")
            elif receipt.get("registry_digest") != registry_digest:
                errors.append(
                    "authoritative deployment receipt registry_digest="
                    f"{receipt.get('registry_digest') or 'missing'}; expected {registry_digest}"
                )
            else:
                digests = receipt.get("artifact_digests")
                if not isinstance(digests, dict) or not digests:
                    errors.append("authoritative deployment receipt artifact_digests missing")
                else:
                    for relative_text, expected_digest in digests.items():
                        relative = Path(str(relative_text))
                        if relative.is_absolute() or ".." in relative.parts:
                            errors.append(f"authoritative deployment receipt path escapes target: {relative}")
                            continue
                        artifact = target / relative
                        try:
                            actual_digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
                        except OSError as exc:
                            errors.append(f"deployed receipt artifact missing/unreadable: {artifact}: {exc}")
                            continue
                        if actual_digest != expected_digest:
                            errors.append(
                                f"deployed receipt artifact digest mismatch: {relative} "
                                f"({actual_digest} != {expected_digest})"
                            )

    return FrameworkIntegrityReport(target=target, errors=tuple(errors), warnings=tuple(warnings))


def _replace_or_append_scalar(text: str, field: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(field)}:.*$", re.MULTILINE)
    replacement = f"{field}: {value}"
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    suffix = "" if not text or text.endswith("\n") else "\n"
    return f"{text}{suffix}{replacement}\n"


def repair_framework_runtime(
    target: Path,
    *,
    framework_source: str,
    aaref_source: str,
    framework_version: str,
    aaref_version: str,
    trw_mcp_version: str | None = None,
    registry_digest: str | None = None,
    failure_after_promotions: int | None = None,
    additional_artifacts: Mapping[Path, bytes] | None = None,
) -> FrameworkIntegrityReport:
    """Explicitly regenerate managed bodies/stamp and update existing pins.

    All unrelated config and stamp keys are preserved.  Missing config files are
    not created; absence means package defaults apply.  This operation is the
    opt-in repair path used for ignored nested runtime state. The stamp is
    written last (after bodies), and when ``registry_digest`` is supplied the
    stamp records ``registry_digest`` plus per-body digests so the deployed
    generation is byte-bound (PRD-INFRA-164 FR04).
    """
    target = target.resolve()
    artifacts: dict[Path, bytes] = {
        _FRAMEWORK_RUNTIME_PATH: framework_source.encode("utf-8"),
        _AAREF_RUNTIME_PATH: aaref_source.encode("utf-8"),
    }
    if additional_artifacts:
        artifacts.update(additional_artifacts)

    config_path = target / _CONFIG_PATH
    if config_path.is_file():
        config_text = config_path.read_text(encoding="utf-8")
        for field, value in (
            ("framework_version", framework_version),
            ("aaref_version", aaref_version),
        ):
            if re.search(rf"^{re.escape(field)}:", config_text, re.MULTILINE):
                config_text = _replace_or_append_scalar(config_text, field, value)
        artifacts[_CONFIG_PATH] = config_text.encode("utf-8")

    version_path = target / _VERSION_PATH
    version_text = version_path.read_text(encoding="utf-8") if version_path.is_file() else ""
    for field, value in (
        ("framework_version", framework_version),
        ("aaref_version", aaref_version),
    ):
        version_text = _replace_or_append_scalar(version_text, field, value)
    if trw_mcp_version is not None:
        version_text = _replace_or_append_scalar(version_text, "trw_mcp_version", trw_mcp_version)
    if registry_digest is not None:
        version_text = _replace_or_append_scalar(version_text, "registry_digest", registry_digest)
        version_text = _replace_or_append_scalar(version_text, "framework_digest", _sha256(framework_source))
        version_text = _replace_or_append_scalar(version_text, "aaref_digest", _sha256(aaref_source))
    deployed_at = datetime.now(timezone.utc).isoformat()
    version_text = _replace_or_append_scalar(version_text, "deployed_at", f"'{deployed_at}'")
    artifacts[_VERSION_PATH] = version_text.encode("utf-8")

    # The deployment receipt is authoritative only when a registry digest is
    # available. Legacy callers still receive atomic body/stamp deployment,
    # bound to an explicit legacy marker rather than an invented digest.
    deploy_framework_generation(
        target,
        artifacts=artifacts,
        registry_digest=registry_digest or "legacy-unbound",
        framework_version=framework_version,
        aaref_version=aaref_version,
        failure_after_promotions=failure_after_promotions,
    )

    return inspect_framework_runtime(
        target,
        framework_source=framework_source,
        aaref_source=aaref_source,
        framework_version=framework_version,
        aaref_version=aaref_version,
        registry_digest=registry_digest,
    )


def rollback_framework_runtime(target: Path, rollback_id: str) -> None:
    """Restore a receipt-bound complete generation from its retained snapshot."""
    rollback_framework_generation(target, rollback_id)


__all__ = [
    "FrameworkIntegrityReport",
    "inspect_framework_runtime",
    "repair_framework_runtime",
    "rollback_framework_runtime",
]

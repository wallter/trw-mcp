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
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH, deploy_framework_generation

_FRAMEWORK_RUNTIME_PATH = Path(".trw/frameworks/FRAMEWORK.md")
_AAREF_RUNTIME_PATH = Path(".trw/frameworks/AARE-F-FRAMEWORK.md")
_VERSION_PATH = Path(".trw/frameworks/VERSION.yaml")
_CONFIG_PATH = Path(".trw/config.yaml")
FORCE_DEPLOY_ENV = "TRW_FRAMEWORK_FORCE_DEPLOY"
_LOG = logging.getLogger(__name__)


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


def _version_key(version: str | None) -> tuple[int, ...] | None:
    """Numeric key of a framework version (``v1.2_TRW``) or a package version (``v3.2.1``); None when it does not parse.

    Trailing zero components are insignificant, so ``v1.2`` == ``v1.2.0``. The examples are not the current
    version on purpose: a literal current version here is a stale copy the next framework bump has to find.
    """
    match = re.fullmatch(r"v?([0-9]{1,4}(?:\.[0-9]{1,4})*)(?:_TRW)?", (version or "").strip())
    if not match:
        return None
    parts = [int(part) for part in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


@dataclass(frozen=True)
class NewerGeneration:
    """A deployed generation newer than the running bundle, and where it was read."""

    running: str
    deployed: str
    source: str
    kind: str = "the project's framework"

    @property
    def nudge(self) -> str:
        return (
            f"this trw-mcp ({self.running}) is older than {self.kind} ({self.deployed}, "
            f"from {self.source}); upgrade trw-mcp, or set {FORCE_DEPLOY_ENV}=1 if that value is wrong"
        )


def newer_deployed_generation(
    target: Path,
    *,
    framework_source: str,
    aaref_source: str,
    package_version: str | None = None,
) -> NewerGeneration | None:
    """The newer deployed generation when this bundle is older, else None.

    A running package older than the deployed generation (e.g. an older install
    launched by another client) must not overwrite it. The running generation is
    the version the bundled bodies declare; the deployed generation is the
    highest of the ``VERSION.yaml`` stamp, the deployed body and the config pin,
    per document. Unparseable versions never report newer (caller keeps its
    normal behaviour). With *package_version* the receipt's recorded package version is compared too (PEP 440),
    which catches an older package writing over a newer one under an EQUAL framework version string.
    ``TRW_FRAMEWORK_FORCE_DEPLOY=1`` disables the guard.
    """
    if os.environ.get("TRW_FRAMEWORK_FORCE_DEPLOY") == "1":
        return None
    target = target.resolve()
    frameworks = target / ".trw" / "frameworks"
    texts: dict[str, str] = {}
    for name in ("VERSION.yaml", "FRAMEWORK.md", "AARE-F-FRAMEWORK.md"):
        texts[name] = _read_or_log(frameworks / name)
    config_text = _read_or_log(target / _CONFIG_PATH)
    for field, running, body_reader, body_name in (
        ("framework_version", _framework_body_version(framework_source), _framework_body_version, "FRAMEWORK.md"),
        ("aaref_version", _aaref_body_version(aaref_source), _aaref_body_version, "AARE-F-FRAMEWORK.md"),
    ):
        running_key = _version_key(running)
        if running is None or running_key is None:
            continue
        for candidate, source in (
            (_yaml_scalar(texts["VERSION.yaml"], field), f"VERSION.yaml stamp {field}"),
            (body_reader(texts[body_name]), f"deployed {body_name} body"),
            (_yaml_scalar(config_text, field), f"config pin {field} in .trw/config.yaml"),
        ):
            key = _version_key(candidate)
            if candidate is not None and key is not None and key > running_key:
                return NewerGeneration(running, candidate, source)
    return _newer_package(target, package_version) if package_version else None


def _newer_package(target: Path, package_version: str) -> NewerGeneration | None:
    """The receipt's package version when it is newer than *package_version* (PEP 440: ``8.1.0.dev26`` < ``8.1.0``)."""
    try:
        receipt = json.loads(_read_or_log(target / DEPLOYMENT_RELATIVE_PATH) or "null")
    except (
        json.JSONDecodeError
    ):  # trw-fail-silent-allow: an unreadable receipt claims no package version; the guard does not fire
        return None
    deployed = receipt.get("package_version") if isinstance(receipt, dict) else None
    if not isinstance(deployed, str) or not deployed:
        return None
    try:
        from packaging.version import InvalidVersion, Version

        deployed_v, running_v = Version(deployed), Version(package_version)
    except (
        ImportError,
        InvalidVersion,
    ):  # trw-fail-silent-allow: an unparseable version cannot be ordered, so it never blocks
        return None
    if deployed_v > running_v:
        return NewerGeneration(
            package_version,
            deployed,
            "DEPLOYMENT.json package_version",
            kind="the trw-mcp that last deployed this project's framework",
        )
    return None


def _read_or_log(path: Path) -> str:
    """File text, or "" (logged unless simply absent) so the guard degrades visibly."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:  # trw-fail-silent-allow: absent file means no version claim (guard does not fire)
        return ""
    except (OSError, UnicodeDecodeError) as exc:  # trw-fail-silent-allow: logged; unreadable means guard cannot fire
        _LOG.warning("framework downgrade guard could not read %s: %s", path, exc)
        return ""


def _read(path: Path, errors: list[str], label: str) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append(f"{label} missing: {path}")
    except (OSError, UnicodeDecodeError) as exc:
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
    registry_digest: str | None = None,
    failure_after_promotions: int | None = None,
    package_version: str | None = None,
) -> FrameworkIntegrityReport:
    """Explicitly regenerate managed bodies/stamp and update existing pins.

    All unrelated config and stamp keys are preserved.  Missing config files are
    not created; absence means package defaults apply.  This operation is the
    opt-in repair path used for ignored nested runtime state. The stamp is
    written last (after bodies), and when ``registry_digest`` is supplied the
    stamp records ``registry_digest`` plus per-body digests so the deployed
    generation is byte-bound (PRD-INFRA-164 FR04).

    Only the canon bodies are receipt-bound. The
    project config and the human ``VERSION.yaml`` stamp are deployed in the same
    atomic generation but stay unbound, because writers outside this deployer own
    their bytes: the standalone installer persists ``target_platforms`` into
    ``.trw/config.yaml`` after the receipt is promoted, and operators edit that
    file by design. Digest-binding them made every fresh bundle install — and
    every subsequent config edit — fail its own ``framework_integrity`` check
    (L-QhRy). Their version pins and registry digest are still verified
    field-by-field by :func:`inspect_framework_runtime`.
    """
    target = target.resolve()
    artifacts: dict[Path, bytes] = {
        _FRAMEWORK_RUNTIME_PATH: framework_source.encode("utf-8"),
        _AAREF_RUNTIME_PATH: aaref_source.encode("utf-8"),
    }
    mutable_artifacts: dict[Path, bytes] = {}

    config_path = target / _CONFIG_PATH
    if config_path.is_file():
        config_text = config_path.read_text(encoding="utf-8")
        for field, value in (
            ("framework_version", framework_version),
            ("aaref_version", aaref_version),
        ):
            if re.search(rf"^{re.escape(field)}:", config_text, re.MULTILINE):
                config_text = _replace_or_append_scalar(config_text, field, value)
        mutable_artifacts[_CONFIG_PATH] = config_text.encode("utf-8")

    version_path = target / _VERSION_PATH
    version_text = version_path.read_text(encoding="utf-8") if version_path.is_file() else ""
    # PRD-INFRA-192 FR12: strip any stale trw_mcp_version/trw_memory_version
    # stamp so an older deployment's package-version lines never survive a repair —
    # the manifest's ``packages`` map is now the one record of resolved versions.
    for stale_field in ("trw_mcp_version", "trw_memory_version"):
        version_text = re.sub(rf"^{stale_field}:.*\n?", "", version_text, flags=re.MULTILINE)
    for field, value in (
        ("framework_version", framework_version),
        ("aaref_version", aaref_version),
    ):
        version_text = _replace_or_append_scalar(version_text, field, value)
    if registry_digest is not None:
        version_text = _replace_or_append_scalar(version_text, "registry_digest", registry_digest)
        version_text = _replace_or_append_scalar(version_text, "framework_digest", _sha256(framework_source))
        version_text = _replace_or_append_scalar(version_text, "aaref_digest", _sha256(aaref_source))
    deployed_at = datetime.now(timezone.utc).isoformat()
    version_text = _replace_or_append_scalar(version_text, "deployed_at", f"'{deployed_at}'")
    mutable_artifacts[_VERSION_PATH] = version_text.encode("utf-8")

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
        mutable_artifacts=mutable_artifacts,
        package_version=package_version,
    )

    return inspect_framework_runtime(
        target,
        framework_source=framework_source,
        aaref_source=aaref_source,
        framework_version=framework_version,
        aaref_version=aaref_version,
        registry_digest=registry_digest,
    )


__all__ = [
    "FORCE_DEPLOY_ENV",
    "FrameworkIntegrityReport",
    "NewerGeneration",
    "inspect_framework_runtime",
    "newer_deployed_generation",
    "repair_framework_runtime",
]

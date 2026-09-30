"""One deploy decision for both framework deploy paths (CODEX-P0-C).

``update-project``/``init-project`` (``bootstrap._utils._write_version_yaml``) and ``trw_init``
(``tools._orchestration_helpers._deploy_frameworks``) each used to decide "is the deployed generation current, is the
package too old to write, was a body edited" with their own, slightly different rules: one required the integrity
warnings to be empty, the other only the errors, and only one of them knew about the package version. This module is the
single rule. The answer is one of three:

- ``current``: a redeploy would write exactly what is deployed; write nothing (a no-op update stays a no-op).
- ``skip_stale``: this trw-mcp is older than what wrote the deployed generation (framework version stamp, or the
  package version recorded in ``DEPLOYMENT.json``); leave the project alone and say why.
- ``deploy``: write the bundle. ``edited`` names canon bodies whose bytes match neither the last receipt nor this
  bundle; they are replaced (the generated reference self-heals) and the caller says so, with where the old bytes are.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH
from trw_mcp.framework_integrity import NewerGeneration, inspect_framework_runtime, newer_deployed_generation

FRAMEWORK_PATH = Path(".trw/frameworks/FRAMEWORK.md")
AAREF_PATH = Path(".trw/frameworks/AARE-F-FRAMEWORK.md")


@dataclass(frozen=True)
class DeployDecision:
    action: Literal["current", "skip_stale", "deploy"]
    stale: NewerGeneration | None = None
    edited: tuple[str, ...] = ()
    edited_digests: dict[str, str] = field(default_factory=dict)  # sha256 of each edited body BEFORE it is replaced


def _generation_current(
    target: Path,
    expected: dict[Path, bytes],
    *,
    framework_source: str,
    aaref_source: str,
    framework_version: str,
    aaref_version: str,
    registry_digest: str,
) -> bool:
    """True when the integrity check is clean and the receipt records exactly the bundle's digests.

    Only a ``needs_upgrade`` warning blocks: "project config absent; package defaults apply" is a valid state, and
    treating it as not-current redeployed (and re-stamped) such a project on every update.
    """
    report = inspect_framework_runtime(
        target,
        framework_source=framework_source,
        aaref_source=aaref_source,
        framework_version=framework_version,
        aaref_version=aaref_version,
        registry_digest=registry_digest,
    )
    if report.errors or any("needs_upgrade" in warning for warning in report.warnings):
        return False
    try:
        receipt = json.loads((target / DEPLOYMENT_RELATIVE_PATH).read_text(encoding="utf-8"))
    except (
        OSError,
        ValueError,
    ):  # trw-fail-silent-allow: unreadable means "not current", which redeploys (the safe direction)
        return False
    digests = {rel.as_posix(): hashlib.sha256(data).hexdigest() for rel, data in expected.items()}
    return isinstance(receipt, dict) and receipt.get("artifact_digests") == digests


def deploy_decision(
    target: Path,
    *,
    framework_source: str,
    aaref_source: str,
    framework_version: str,
    aaref_version: str,
    registry_digest: str,
    package_version: str,
) -> DeployDecision:
    """Decide what a framework deploy over *target* should do; reads only, writes nothing."""
    from trw_mcp.bootstrap._framework_modified_guard import modified_canon_bodies

    target = target.resolve()
    expected = {FRAMEWORK_PATH: framework_source.encode("utf-8"), AAREF_PATH: aaref_source.encode("utf-8")}
    if _generation_current(
        target,
        expected,
        framework_source=framework_source,
        aaref_source=aaref_source,
        framework_version=framework_version,
        aaref_version=aaref_version,
        registry_digest=registry_digest,
    ):
        return DeployDecision("current")
    stale = newer_deployed_generation(
        target, framework_source=framework_source, aaref_source=aaref_source, package_version=package_version
    )
    if stale is not None:
        return DeployDecision("skip_stale", stale=stale)
    edited = tuple(modified_canon_bodies(target, expected))
    digests = {name: hashlib.sha256((target / name).read_bytes()).hexdigest() for name in edited}
    return DeployDecision("deploy", edited=edited, edited_digests=digests)


__all__ = ["AAREF_PATH", "FRAMEWORK_PATH", "DeployDecision", "deploy_decision"]

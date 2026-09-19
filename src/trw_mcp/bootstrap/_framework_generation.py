"""Is the deployed framework generation already what a redeploy would write?

Belongs to ``_utils._write_version_yaml`` (split out for the 350-eLOC gate).
PRD-INFRA-190 FR03: a no-op update must write nothing, and a redeploy always
moves ``deployed_at`` and leaves a ``.rollback`` snapshot behind.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def framework_generation_current(
    target_dir: Path, expected: dict[Path, bytes], registry_digest: str, pkg_version: str
) -> bool:
    """True when a redeploy would write exactly what is deployed (PRD-INFRA-190 FR03).

    Redeploying anyway only moves ``deployed_at`` and leaves a ``.rollback``
    snapshot behind, so a no-op update would never be a no-op. "Current" needs
    the integrity check clean, the stamp on this package version, and the
    receipt recording exactly the bundle's digests.
    """
    from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH
    from trw_mcp.framework_integrity import inspect_framework_runtime
    from trw_mcp.models.config import get_config

    config = get_config()
    report = inspect_framework_runtime(
        target_dir,
        framework_source=expected[Path(".trw/frameworks/FRAMEWORK.md")].decode("utf-8"),
        aaref_source=expected[Path(".trw/frameworks/AARE-F-FRAMEWORK.md")].decode("utf-8"),
        framework_version=config.framework_version,
        aaref_version=config.aaref_version,
        registry_digest=registry_digest,
    )
    if report.errors or report.warnings:
        return False
    try:
        stamp = (target_dir / ".trw" / "frameworks" / "VERSION.yaml").read_text(encoding="utf-8")
        receipt = json.loads((target_dir / DEPLOYMENT_RELATIVE_PATH).read_text(encoding="utf-8"))
    except (
        OSError,
        ValueError,
    ):  # trw-fail-silent-allow: unreadable means "not current", which redeploys (the safe direction)
        return False
    digests = {str(rel): hashlib.sha256(data).hexdigest() for rel, data in expected.items()}
    current_stamp = f"trw_mcp_version: {pkg_version}" in stamp.splitlines()
    return current_stamp and isinstance(receipt, dict) and receipt.get("artifact_digests") == digests

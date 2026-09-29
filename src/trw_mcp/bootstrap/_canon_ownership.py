"""Is a dirty canon file TRW's own last deploy? (M1 dry run release blocker)

Belongs to ``_version_manifest.preserve_uncommitted_changes``. That guard keeps any git-dirty file whose
pre-run bytes TRW did not record, and the canon was never recorded in the manifest's ``content_hashes``: in a
repository that has not committed ``.trw/``, every canon file is untracked, so ``update-project`` redeployed
the new canon and then restored the old one as "uncommitted user changes". The canon's own deployment receipt
is the record instead:

* ``.trw/frameworks/VERSION.yaml`` and ``DEPLOYMENT.json`` are the deployer's stamp and receipt;
* a runtime canon body is TRW's when its bytes hash to the receipt's ``artifact_digests`` entry;
* a root reference copy (``FRAMEWORK.md``, ``AARE-F-FRAMEWORK.md``) is TRW's when it is byte-identical to its
  receipt-proven runtime counterpart (both are installed from the same bundled resource).

A body the user edited after the deploy matches neither, so it is still preserved.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

_DEPLOYER_STATE = frozenset({".trw/frameworks/VERSION.yaml", ".trw/frameworks/DEPLOYMENT.json"})


def _receipt_digests(snapshot_root: Path) -> dict[str, str]:
    from trw_mcp.framework_deployment import DEPLOYMENT_RELATIVE_PATH

    try:
        receipt = json.loads((snapshot_root / DEPLOYMENT_RELATIVE_PATH).read_text(encoding="utf-8"))
    except (
        OSError,
        ValueError,
    ):  # trw-fail-silent-allow: no readable receipt proves nothing, so the file stays preserved
        return {}
    digests = receipt.get("artifact_digests") if isinstance(receipt, dict) else None
    return {str(k): str(v) for k, v in digests.items()} if isinstance(digests, dict) else {}


def _runtime_counterparts() -> dict[str, str]:
    """Root reference target -> the runtime target installed from the same bundled resource."""
    from trw_mcp.canons.registry import install_view, load_registry

    by_resource: dict[str, list[str]] = {}
    for resource, destination in install_view(load_registry()):
        by_resource.setdefault(resource, []).append(destination)
    pairs: dict[str, str] = {}
    for destinations in by_resource.values():
        runtime = [d for d in destinations if d.startswith(".trw/")]
        for root in (d for d in destinations if "/" not in d):
            if runtime:
                pairs[root] = runtime[0]
    return pairs


def is_trw_deployed_canon(snapshot_root: Path, rel: str) -> bool:
    """True when *rel*'s pre-run bytes (in *snapshot_root*) are provably TRW's last canon deploy."""
    if rel in _DEPLOYER_STATE:
        return True
    before = snapshot_root / rel
    if not before.is_file() or before.is_symlink():
        return False
    digests = _receipt_digests(snapshot_root)
    if not digests:
        return False
    data = before.read_bytes()
    runtime = _runtime_counterparts().get(rel, rel)
    counterpart = snapshot_root / runtime
    if runtime != rel and (not counterpart.is_file() or counterpart.read_bytes() != data):
        return False
    return digests.get(runtime) == hashlib.sha256(data).hexdigest()


def is_trw_owned_runtime_canon(rel: str) -> bool:
    """True when *rel* is one of the canon registry's runtime install targets.

    Currently ``.trw/frameworks/FRAMEWORK.md`` and ``.trw/frameworks/AARE-F-FRAMEWORK.md``, matched by exact
    path. A hand edit to one is drift the redeploy repairs. Root reference copies (``FRAMEWORK.md``), user files
    added under ``.trw/frameworks/``, and ``.trw/config.yaml`` are not covered and keep the uncommitted-changes
    guard.
    """
    return rel in set(_runtime_counterparts().values())

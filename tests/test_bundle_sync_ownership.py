"""Pin bundle-to-client repair ownership for shipped TRW assets."""

from __future__ import annotations

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT, requires_monorepo

# Every case exercises scripts absent from the standalone package.
pytestmark = requires_monorepo

SCRIPT = (MONOREPO_ROOT or PACKAGE_ROOT.parent) / "scripts" / "check-bundle-sync.sh"


def test_bundle_sync_fix_never_overwrites_shipped_source_from_client_projection() -> None:
    content = SCRIPT.read_text(encoding="utf-8")
    assert "Bundled data is the source of truth" in content
    assert 'cp "$bundled_file" "$dev_file"' in content
    assert 'cp "$dev_file" "$bundled_file"' not in content
    assert "copy bundled→dev to resolve" in content

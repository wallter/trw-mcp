"""Finding 2 (2026-07-30 hardening pass): guard the root channel manifest against re-drift.

``.trw/channels/manifest.yaml`` at the repo root is a hand-maintained union of
the six bundled per-client manifests
(``trw-mcp/src/trw_mcp/data/{opencode,antigravity,claude_code,copilot,cursor,
codex}/channels/manifest-*.yaml``). ``test_artifact_registry.py``'s
``MINIMUM_ENTRIES`` comment records that it already drifted once — the root
manifest briefly carried only 15 channels, missing
``copilot-pretooluse-hint`` and ``cursor-pretooluse-hint``, and was reconciled
against the six bundled manifests on 2026-07-28. Nothing asserted the
invariant, so nothing would catch the next drift. This module is that guard.

Per ``docs/documentation/wiring-defect-patterns.md`` §P11 ("Subset registry
with no derivation"): the mechanical predicate is
``set(canonical) - set(local)`` must be **empty or justified per element** —
never a weakened assertion. ``_EXCLUDED_FROM_ROOT`` is that per-element
justification mechanism; it is empty today (verified 2026-07-30: the root
manifest's 17 ids equal the union of the six bundled manifests' 17 ids
exactly) and must stay empty unless a real, reviewed divergence is
introduced with a named reason.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT_MANIFEST_RELATIVE_PATH = ".trw/channels/manifest.yaml"
BUNDLED_MANIFEST_GLOB = "trw-mcp/src/trw_mcp/data/*/channels/manifest-*.yaml"

# Channel ids present in a bundled per-client manifest that are deliberately
# NOT expected in the root union, each with a reason. Empty today — see the
# module docstring. Add an entry here only for a real, reviewed difference;
# `test_excluded_from_root_has_no_stale_entries` fails if an entry no longer
# names a channel any bundled manifest actually declares.
EXCLUDED_FROM_ROOT: dict[str, str] = {}


def _channel_ids(path: Path) -> set[str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    channels = raw.get("channels") if isinstance(raw, dict) else None
    if not isinstance(channels, list):
        return set()
    ids: set[str] = set()
    for entry in channels:
        if isinstance(entry, dict):
            channel_id = entry.get("id")
            if isinstance(channel_id, str) and channel_id:
                ids.add(channel_id)
    return ids


def _bundled_manifest_paths(repo_root: Path) -> list[Path]:
    return sorted(repo_root.glob(BUNDLED_MANIFEST_GLOB))


def _bundled_union(repo_root: Path) -> set[str]:
    union: set[str] = set()
    for path in _bundled_manifest_paths(repo_root):
        union |= _channel_ids(path)
    return union


def test_bundled_manifests_fixture_is_non_vacuous(repo_root: Path) -> None:
    """Guard the guard: an empty glob would make the union test pass on nothing."""
    paths = _bundled_manifest_paths(repo_root)
    assert len(paths) == 6, f"expected 6 bundled per-client manifests, found {len(paths)}: {paths}"
    assert _bundled_union(repo_root), "bundled manifests yielded zero channel ids — fixture rotted"


def test_root_channel_manifest_equals_the_union_of_bundled_manifests(repo_root: Path) -> None:
    """The invariant that already drifted once (2026-07-28) — now asserted, not assumed."""
    root_ids = _channel_ids(repo_root / ROOT_MANIFEST_RELATIVE_PATH)
    expected = _bundled_union(repo_root) - set(EXCLUDED_FROM_ROOT)

    missing = expected - root_ids
    assert not missing, (
        f"root manifest ({ROOT_MANIFEST_RELATIVE_PATH}) is missing channel(s) present in the "
        f"bundled per-client manifests: {sorted(missing)} — reconcile the root manifest, or "
        f"add a named, reasoned exclusion to EXCLUDED_FROM_ROOT if the omission is deliberate"
    )

    extra = root_ids - expected
    assert not extra, (
        f"root manifest ({ROOT_MANIFEST_RELATIVE_PATH}) declares channel(s) absent from every "
        f"bundled per-client manifest: {sorted(extra)} — drift in the other direction"
    )


def test_excluded_from_root_has_no_stale_entries(repo_root: Path) -> None:
    """A named exclusion for a channel id no bundled manifest declares anymore is stale."""
    union = _bundled_union(repo_root)
    stale = {channel_id for channel_id in EXCLUDED_FROM_ROOT if channel_id not in union}
    assert not stale, (
        f"EXCLUDED_FROM_ROOT names {sorted(stale)}, absent from every bundled manifest — remove the stale exclusion"
    )

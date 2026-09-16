"""The package taxonomy is read from the manifest that owns it, never restated.

``release-packages.yaml`` lives at the monorepo root and is deliberately NOT in
the ``trw-mcp`` subtree, so a public install does not have it. That absence is
load-bearing: a published wheel must not be able to enumerate TRW's proprietary
siblings. Until 2026-09-12 the taxonomy was hand-copied into shipped source in
three places, two of which named proprietary packages, and the copies had
drifted from each other and from the manifest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state._release_topology import PUBLIC_PACKAGE_DIRS, package_dirs, read_topology

_MANIFEST = "packages:\n"


def _write(root: Path, body: str) -> Path:
    (root / "release-packages.yaml").write_text(body, encoding="utf-8")
    return root


class TestPackageDirs:
    def test_a_public_install_sees_only_the_public_packages(self, tmp_path: Path) -> None:
        """No manifest is the NORMAL public case, and the answer must be exactly
        the two published packages — not an empty tuple, and not a leak."""
        assert package_dirs(tmp_path) == PUBLIC_PACKAGE_DIRS

    def test_the_monorepo_taxonomy_is_read_from_the_manifest(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            _MANIFEST
            + "  - key: trw-mcp\n    dir: trw-mcp\n    tier: public\n"
            + "  - key: secret-one\n    dir: secret-one\n    tier: proprietary\n"
            + "  - key: nested\n    dir: packages/nested\n    tier: proprietary\n",
        )
        dirs = package_dirs(tmp_path)
        assert dirs[: len(PUBLIC_PACKAGE_DIRS)] == PUBLIC_PACKAGE_DIRS
        assert "secret-one" in dirs
        assert "packages/nested" in dirs, "a nested package directory was dropped"
        assert dirs.count("trw-mcp") == 1, "a package declared in both places was duplicated"

    def test_an_unreadable_manifest_falls_open_to_public_not_empty(self, tmp_path: Path) -> None:
        """Empty would be the dangerous answer: a caller cannot tell "no manifest"
        from "no packages", and the difference decides whether a path resolves."""
        (tmp_path / "release-packages.yaml").mkdir()  # a directory, so read_text raises
        assert package_dirs(tmp_path) == PUBLIC_PACKAGE_DIRS

    def test_a_malformed_manifest_does_not_raise(self, tmp_path: Path) -> None:
        _write(tmp_path, "this: is not\n  the expected: shape\n")
        assert package_dirs(tmp_path) == PUBLIC_PACKAGE_DIRS


class TestReadTopology:
    def test_tier_and_manifest_kind_survive_the_parse(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            _MANIFEST + "  - key: k\n    dir: d\n    tier: proprietary\n    manifest_kind: package.json\n",
        )
        entries = read_topology(tmp_path)
        assert [(e.key, e.directory, e.tier, e.manifest_kind) for e in entries] == [
            ("k", "d", "proprietary", "package.json")
        ]

    def test_the_real_repository_manifest_parses(self) -> None:
        """Non-vacuity partner. Every test above uses a synthetic manifest, so a
        parser that only understood the synthetic shape would pass them all. The
        shipped file is the one that has to work."""
        repo_root = Path(__file__).resolve().parents[2]
        if not (repo_root / "release-packages.yaml").exists():
            pytest.skip("not running inside the monorepo")
        entries = read_topology(repo_root)
        assert len(entries) > 5, f"the real manifest yielded only {len(entries)} entries"
        assert {e.tier for e in entries} <= {"public", "proprietary"}
        assert any(e.tier == "public" for e in entries)
        # Deliberately NOT asserting that a nested directory appears here. That line
        # existed while packages/memory-ts was in the manifest; 559e978f88 retired the
        # package and the assertion started pinning a repo shape that no longer holds.
        # The parser's nested-path handling is a property of the PARSER and is tested
        # against the synthetic manifest above (packages/nested); this partner's job is
        # only to prove the shipped file parses at all.

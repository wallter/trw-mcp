"""Tests for trw_mcp.release_builder — release bundle packaging."""

from __future__ import annotations

import argparse
import hashlib
import tarfile
from pathlib import Path

import pytest

from trw_mcp import release_builder as rb_mod
from trw_mcp.release_builder import _read_version, _sha256, build_release_bundle
from trw_mcp.server._subcommands_release import (
    _run_build_release,
    assert_version_status_compatible,
    collect_version_status,
)

#: Synthetic stand-ins for a nested ``package.json`` package. Never a real one:
#: this file is tracked and the public mirror ships every tracked file.
TS_PACKAGE_KEY = "example-ts-client"
TS_PACKAGE_DIR = "packages/example-ts-client"


def _write_version_root(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mcp_version: str,
    framework_version: str,
    installed_mcp_version: str | None = None,
    installed_memory_version: str = "0.8.3",
) -> None:
    """Create the version manifests needed by release status checks.

    PRD-INFRA-192 FR12: package versions are compared against
    ``.trw/managed-artifacts.yaml`` ``packages`` (the manifest) and the
    interpreter's actually-installed versions (``resolved_package_versions``),
    not a VERSION.yaml stamp. *installed_mcp_version* defaults to *mcp_version*
    so the two sides agree unless a test is deliberately proving drift.
    """
    (root / "trw-mcp").mkdir()
    (root / "trw-mcp" / "pyproject.toml").write_text(f'[project]\nversion = "{mcp_version}"\n')
    (root / "trw-memory").mkdir()
    (root / "trw-memory" / "pyproject.toml").write_text('[project]\nversion = "0.8.3"\n')
    # A SYNTHETIC nested package.json package. Deliberately not the real one: this
    # file is tracked, the public mirror ships every tracked file, and naming a
    # proprietary package's path here is the very leak the module under test was
    # fixed for. `check-release-leak-boundary` flags it, correctly.
    (root / TS_PACKAGE_DIR).mkdir(parents=True)
    (root / TS_PACKAGE_DIR / "package.json").write_text('{"version":"0.4.0"}\n')
    # TRW publishes no package.json package, so the shipped module hardcodes none.
    # A nested package reaches version status ONLY through the monorepo-root
    # topology manifest, which is what this writes. Without the manifest it is
    # absent entirely — pinned by the two public-install assertions below.
    (root / "release-packages.yaml").write_text(
        f"packages:\n  - key: {TS_PACKAGE_KEY}\n    dir: {TS_PACKAGE_DIR}\n    manifest_kind: package.json\n",
        encoding="utf-8",
    )
    (root / ".trw" / "frameworks").mkdir(parents=True)
    (root / ".trw" / "frameworks" / "VERSION.yaml").write_text(f"framework_version: {framework_version}\n")
    (root / ".trw" / "managed-artifacts.yaml").write_text(
        f"packages:\n  trw-mcp: {mcp_version}\n  trw-memory: {installed_memory_version}\n"
    )
    monkeypatch.setattr(
        "trw_mcp.bootstrap._version_manifest.resolved_package_versions",
        lambda: {
            "trw-mcp": installed_mcp_version if installed_mcp_version is not None else mcp_version,
            "trw-memory": installed_memory_version,
        },
    )


# ---------------------------------------------------------------------------
# _sha256 tests
# ---------------------------------------------------------------------------


class TestSha256:
    """Tests for _sha256 helper."""

    def test_sha256_known_content(self, tmp_path: Path) -> None:
        """SHA-256 matches hashlib reference for known bytes."""
        p = tmp_path / "hello.bin"
        content = b"hello world"
        p.write_bytes(content)

        result = _sha256(p)
        expected = hashlib.sha256(content).hexdigest()
        assert result == expected

    def test_sha256_empty_file(self, tmp_path: Path) -> None:
        """SHA-256 of an empty file equals sha256(b'')."""
        p = tmp_path / "empty.bin"
        p.write_bytes(b"")

        result = _sha256(p)
        expected = hashlib.sha256(b"").hexdigest()
        assert result == expected

    def test_sha256_large_file(self, tmp_path: Path) -> None:
        """SHA-256 is correct for a file larger than the 8192-byte chunk size."""
        p = tmp_path / "large.bin"
        content = b"x" * (8192 * 3 + 100)
        p.write_bytes(content)

        result = _sha256(p)
        expected = hashlib.sha256(content).hexdigest()
        assert result == expected


# ---------------------------------------------------------------------------
# _read_version tests
# ---------------------------------------------------------------------------


def _fake_file(tmp_path: Path) -> Path:
    """Create a fake __file__ path so parent^3 resolves to tmp_path.

    _read_version does: Path(__file__).parent.parent.parent
    (file is at src/trw_mcp/release_builder.py, 3 parents up = package root)
    So we need: tmp_path / L1 / L2 / release_builder.py
    parent chain from file: L2 -> L1 -> tmp_path  (3 .parent calls)
    """
    fake = tmp_path / "a" / "b" / "release_builder.py"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("")
    return fake


class TestReadVersion:
    """Tests for _read_version helper."""

    def test_reads_version_from_real_pyproject(self) -> None:
        """Returns a real version from the repo's pyproject.toml."""
        result = _read_version()
        assert result != "0.0.0"
        assert result  # non-empty

    def test_reads_version_from_synthetic_pyproject_double_quotes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Parses version with double-quoted value."""
        fake = _fake_file(tmp_path)
        monkeypatch.setattr(rb_mod, "__file__", str(fake))

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text('[project]\nversion = "5.6.7"\n')

        result = _read_version()
        assert result == "5.6.7"

    def test_reads_version_from_synthetic_pyproject_single_quotes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Parses version with single-quoted value."""
        fake = _fake_file(tmp_path)
        monkeypatch.setattr(rb_mod, "__file__", str(fake))

        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("version = '1.2.3'\n")

        result = _read_version()
        assert result == "1.2.3"

    def test_falls_back_to_dunder_version(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When pyproject.toml is missing, falls back to trw_mcp.__version__."""
        fake = _fake_file(tmp_path)
        monkeypatch.setattr(rb_mod, "__file__", str(fake))
        # No pyproject.toml at tmp_path

        # Ensure __version__ is available as fallback
        monkeypatch.setattr("trw_mcp.__version__", "9.8.7")

        result = _read_version()
        assert result == "9.8.7"

    def test_returns_000_when_both_fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Returns '0.0.0' when pyproject.toml missing AND __version__ unavailable."""
        fake = _fake_file(tmp_path)
        monkeypatch.setattr(rb_mod, "__file__", str(fake))
        # No pyproject.toml at tmp_path

        # Remove __version__ from trw_mcp so ImportError path is taken
        import trw_mcp

        monkeypatch.delattr(trw_mcp, "__version__", raising=False)

        # Also need to ensure the cached import doesn't return the old value.
        # The function does `from trw_mcp import __version__` which goes through
        # sys.modules. Deleting the attribute is sufficient since importlib
        # will find the module but getattr will fail with AttributeError.
        result = _read_version()
        assert result == "0.0.0"


class TestUnifiedStatusTaxonomy:
    """PRD-INFRA-164 FR09: doctor/version-status/trw://framework/versions share one
    taxonomy with the frozen live-process fingerprint + historical installer section."""

    def test_version_status_and_mcp_resource_share_live_fingerprint_taxonomy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from fastmcp import FastMCP

        from trw_mcp.canons.fingerprint import (
            freeze_fingerprint,
            reset_frozen_fingerprint,
            set_frozen_fingerprint,
        )
        from trw_mcp.canons.registry import load_registry, managed_source_digests
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.server._live_fingerprint import build_realized_surface
        from trw_mcp.tools.learning import register_learning_tools

        reset_frozen_fingerprint()
        registry = load_registry()
        server = FastMCP("test")
        register_learning_tools(server)
        fp = freeze_fingerprint(
            trw_mcp_version="1.2.3",
            framework_version="v99.9_TRW",
            aaref_version="v3.2.0",
            template_version="3.2",
            registry_digest=registry.digest,
            source_digests=managed_source_digests(registry),
            surface=build_realized_surface(server),
        )
        set_frozen_fingerprint(fp)
        try:
            monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
            _write_version_root(
                tmp_path, monkeypatch, mcp_version="1.2.3", framework_version=TRWConfig().framework_version
            )
            # A historical installer snapshot must appear in the historical section only.
            (tmp_path / ".trw" / "installer-meta.yaml").write_text(
                "framework_version: v24.4_TRW\npackage_version: 0.1.0\nlast_updated: '2025-01-01T00:00:00Z'\n"
            )

            status = collect_version_status(tmp_path)

            # Shared taxonomy names the live-process and historical layers.
            assert "live_process" in status["taxonomy"]
            assert "historical" in status["taxonomy"]

            # Live-process layer carries the frozen fingerprint and a CURRENT verdict
            # (this process's frozen generation matches the bundled generation).
            live = status["live_process"]
            assert live["present"] is True
            assert live["digest"] == fp.digest
            assert live["currentness"] == "current"
            assert status["compatible"] is True
            assert "cannot attest" in str(live["attest_note"])

            # Historical installer snapshot is reported as history, never a current
            # must_match pair, and its old framework value is NOT current authority.
            historical = status["historical"]
            assert historical["record_kind"] == "historical_install_snapshot"
            assert historical["framework_version_at_install"] == "v24.4_TRW"
            matrix = status["compatibility_matrix"]
            flat_must_match = str(matrix["must_match"])
            assert "installer" not in flat_must_match and "historical" not in flat_must_match

            # The MCP resource projects the SAME taxonomy/fingerprint (shared source).
            from trw_mcp.state import _paths

            monkeypatch.setattr(_paths, "resolve_project_root", lambda: tmp_path)
            from trw_mcp.resources import config as res_config

            monkeypatch.setattr(res_config, "resolve_project_root", lambda: tmp_path)
            server2 = FastMCP("res-test")
            res_config.register_config_resources(server2)
            from tests.conftest import get_resources_sync

            resources = get_resources_sync(server2)
            versions_fn = resources["trw://framework/versions"].fn
            rendered = versions_fn()
            assert fp.digest in rendered
            assert "live_process" in rendered
            assert "historical_install_snapshot" in rendered
        finally:
            reset_frozen_fingerprint()

    def test_missing_live_fingerprint_is_unknown_never_green(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A fresh CLI with no frozen fingerprint reports unknown currentness — never current."""
        from trw_mcp.canons.fingerprint import reset_frozen_fingerprint
        from trw_mcp.models.config import TRWConfig

        reset_frozen_fingerprint()
        monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
        _write_version_root(tmp_path, monkeypatch, mcp_version="1.2.3", framework_version=TRWConfig().framework_version)
        status = collect_version_status(tmp_path)
        assert status["live_process"]["currentness"] == "unknown"
        assert status["live_process"]["present"] is False
        assert status["compatible"] is False
        assert status["mismatches"] == ["live_process_currentness_unknown"]
        assert status["errors"] == ["live process currentness is unknown; release requires current"]
        with pytest.raises(SystemExit, match="live_process_currentness_unknown"):
            assert_version_status_compatible(tmp_path)

    def test_stale_live_fingerprint_blocks_release_status(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from trw_mcp.canons.fingerprint import (
            RealizedSurface,
            freeze_fingerprint,
            reset_frozen_fingerprint,
            set_frozen_fingerprint,
        )
        from trw_mcp.models.config import TRWConfig

        reset_frozen_fingerprint()
        stale = freeze_fingerprint(
            trw_mcp_version="1.2.3",
            framework_version=TRWConfig().framework_version,
            aaref_version="v3.2.0",
            template_version="3.2",
            registry_digest="stale-registry-digest",
            source_digests={},
            surface=RealizedSurface(tools=(), resources=(), prompts=()),
        )
        set_frozen_fingerprint(stale)
        try:
            monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
            _write_version_root(
                tmp_path, monkeypatch, mcp_version="1.2.3", framework_version=TRWConfig().framework_version
            )

            status = collect_version_status(tmp_path)

            assert status["live_process"]["currentness"] == "stale"
            assert status["compatible"] is False
            assert status["mismatches"] == ["live_process_currentness_stale"]
            assert status["errors"] == ["live process currentness is stale; release requires current"]
            with pytest.raises(SystemExit, match="live_process_currentness_stale"):
                assert_version_status_compatible(tmp_path)
        finally:
            reset_frozen_fingerprint()


class TestVersionStatus:
    """Tests for authoritative release version gate status."""

    @pytest.fixture(autouse=True)
    def _current_live_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "trw_mcp.server._subcommands_release.live_process_layer",
            lambda: {"currentness": "current", "present": True},
        )

    def test_collects_labeled_version_taxonomy(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Status separates package, framework, installed asset, and live server versions."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
        _write_version_root(tmp_path, monkeypatch, mcp_version="1.2.3", framework_version=TRWConfig().framework_version)

        status = collect_version_status(tmp_path)

        versions = status["versions"]
        assert isinstance(versions, dict)
        assert versions["live_server_version"] == "1.2.3"
        assert versions["manifest_packages"]["trw-mcp"] == "1.2.3"
        assert versions["installed_packages"]["trw-mcp"] == "1.2.3"
        assert status["compatible"] is True
        assert "package_version" in status["taxonomy"]
        assert "must_match" in status["compatibility_matrix"]

    def test_detects_manifest_asset_drift(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Release status fails when trw-mcp package and manifest-recorded versions drift."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
        _write_version_root(
            tmp_path,
            monkeypatch,
            mcp_version="1.2.3",
            framework_version=TRWConfig().framework_version,
            installed_mcp_version="9.9.9",
        )

        status = collect_version_status(tmp_path)

        assert status["compatible"] is False
        assert "trw_mcp_installed_vs_manifest" in status["mismatches"]
        with pytest.raises(SystemExit):
            assert_version_status_compatible(tmp_path)

    @pytest.mark.parametrize("manifest_text", [None, "version: 2\n", "packages: [1, 2]\n"])
    def test_manifest_without_packages_is_a_mismatch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, manifest_text: str | None
    ) -> None:
        """An absent manifest, or one written before ``packages`` existed, reports ``manifest_packages_missing``."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
        _write_version_root(tmp_path, monkeypatch, mcp_version="1.2.3", framework_version=TRWConfig().framework_version)
        manifest = tmp_path / ".trw" / "managed-artifacts.yaml"
        if manifest_text is None:
            manifest.unlink()
        else:
            manifest.write_text(manifest_text, encoding="utf-8")

        status = collect_version_status(tmp_path)

        assert "manifest_packages_missing" in status["mismatches"]
        assert status["versions"]["manifest_packages"] == {}
        assert status["compatible"] is False

    @pytest.mark.parametrize(
        ("manifest_text", "installed", "expected"),
        [
            ("packages: {}\n", {"trw-mcp": "1.2.3", "trw-memory": "0.8.3"}, "trw_mcp_manifest_entry_missing"),
            (
                "packages:\n  trw-mcp: 1.2.3\n",
                {"trw-mcp": "1.2.3", "trw-memory": "0.8.3"},
                "trw_memory_manifest_entry_missing",
            ),
            ("packages:\n  trw-mcp: 1.2.3\n  trw-memory: 0.8.3\n", {"trw-mcp": "1.2.3"}, "trw_memory_not_installed"),
        ],
    )
    def test_a_missing_required_package_on_either_side_is_a_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        manifest_text: str,
        installed: dict[str, str],
        expected: str,
    ) -> None:
        """Codex review of 14ae6dd34: comparing only when both keys exist read ``packages: {}`` as current."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
        _write_version_root(tmp_path, monkeypatch, mcp_version="1.2.3", framework_version=TRWConfig().framework_version)
        (tmp_path / ".trw" / "managed-artifacts.yaml").write_text(manifest_text, encoding="utf-8")
        monkeypatch.setattr("trw_mcp.bootstrap._version_manifest.resolved_package_versions", lambda: installed)

        status = collect_version_status(tmp_path)

        assert expected in status["mismatches"]
        assert status["compatible"] is False
        distribution = "trw-mcp" if expected.startswith("trw_mcp") else "trw-memory"
        assert any(distribution in error for error in status["errors"]), status["errors"]

    def test_detects_trw_memory_drift_against_the_manifest(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
        _write_version_root(tmp_path, monkeypatch, mcp_version="1.2.3", framework_version=TRWConfig().framework_version)
        monkeypatch.setattr(
            "trw_mcp.bootstrap._version_manifest.resolved_package_versions",
            lambda: {"trw-mcp": "1.2.3", "trw-memory": "9.0.0"},
        )

        status = collect_version_status(tmp_path)

        assert "trw_memory_installed_vs_manifest" in status["mismatches"]
        assert "trw_mcp_installed_vs_manifest" not in status["mismatches"]
        assert status["versions"]["installed_packages"]["trw-memory"] == "9.0.0"
        assert status["versions"]["manifest_packages"]["trw-memory"] == "0.8.3"

    def test_collects_installed_project_status_without_monorepo_manifests(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Installed user projects should not need monorepo package manifest files.

        Also the leak guard: with no ``release-packages.yaml`` this IS a public
        install, and a public install must not learn a nested monorepo sibling
        exists. The shipped module hardcoded one, by name and by path, until
        2026-09-12.
        """
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
        (tmp_path / ".trw" / "frameworks").mkdir(parents=True)
        (tmp_path / ".trw" / "frameworks" / "VERSION.yaml").write_text(
            f"framework_version: {TRWConfig().framework_version}\n",
            encoding="utf-8",
        )
        (tmp_path / ".trw" / "managed-artifacts.yaml").write_text(
            "packages:\n  trw-mcp: 1.2.3\n  trw-memory: 0.8.3\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            "trw_mcp.bootstrap._version_manifest.resolved_package_versions",
            lambda: {"trw-mcp": "1.2.3", "trw-memory": "0.8.3"},
        )

        status = collect_version_status(tmp_path)
        versions = status["versions"]

        assert isinstance(versions, dict)
        assert versions["packages"]["trw-mcp"] == "1.2.3"
        assert TS_PACKAGE_KEY not in versions["packages"], (
            "a public install enumerated a nested monorepo sibling; a package.json "
            "package must come only from the non-shipped release-packages.yaml"
        )
        assert status["compatible"] is True

    def test_absent_optional_package_manifests_do_not_hide_asset_drift(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Optional package manifests may be absent, but authoritative asset drift still fails closed."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
        (tmp_path / ".trw" / "frameworks").mkdir(parents=True)
        (tmp_path / ".trw" / "frameworks" / "VERSION.yaml").write_text(
            f"framework_version: {TRWConfig().framework_version}\n",
            encoding="utf-8",
        )
        (tmp_path / ".trw" / "managed-artifacts.yaml").write_text(
            "packages:\n  trw-mcp: 9.9.9\n  trw-memory: 0.8.3\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            "trw_mcp.bootstrap._version_manifest.resolved_package_versions",
            lambda: {"trw-mcp": "1.2.3", "trw-memory": "0.8.3"},
        )

        status = collect_version_status(tmp_path)

        assert status["compatible"] is False
        assert TS_PACKAGE_KEY not in status["versions"]["packages"], (
            "a public install enumerated a nested monorepo sibling; a package.json "
            "package must come only from the non-shipped release-packages.yaml"
        )
        assert status["mismatches"] == ["trw_mcp_installed_vs_manifest"]

    def test_missing_installed_asset_manifest_is_explicit_and_fails_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The gate should not silently pass when no installed asset version can be verified."""
        monkeypatch.setattr("trw_mcp.__version__", "1.2.3")

        status = collect_version_status(tmp_path)

        assert status["compatible"] is False
        assert status["versions"]["installed_asset_present"] is False
        assert "installed_asset_manifest_missing" in status["mismatches"]
        with pytest.raises(SystemExit, match="installed_asset_manifest_missing"):
            assert_version_status_compatible(tmp_path)

    def test_malformed_optional_package_manifest_warns_without_crashing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Invalid independent package manifests should degrade to unknown instead of crashing status output."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
        _write_version_root(tmp_path, monkeypatch, mcp_version="1.2.3", framework_version=TRWConfig().framework_version)
        (tmp_path / TS_PACKAGE_DIR / "package.json").write_text("{not-json", encoding="utf-8")

        status = collect_version_status(tmp_path)

        assert status["compatible"] is True
        assert status["versions"]["packages"][TS_PACKAGE_KEY] == "unknown"
        assert status["warnings"]
        assert any(TS_PACKAGE_KEY in warning for warning in status["warnings"])

    def test_collects_extended_monorepo_package_versions(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Version status covers proprietary and newer monorepo packages, not only public packages."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
        _write_version_root(tmp_path, monkeypatch, mcp_version="1.2.3", framework_version=TRWConfig().framework_version)
        # The proprietary/newer package taxonomy is sourced from the monorepo-root
        # release-packages.yaml (NOT the shipped trw-mcp subtree), so the public
        # wheel enumerates no proprietary siblings. Provide it here to prove
        # config-driven discovery covers those packages.
        (tmp_path / "release-packages.yaml").write_text(
            "packages:\n"
            f"  - key: {TS_PACKAGE_KEY}\n"
            f"    dir: {TS_PACKAGE_DIR}\n"
            "    manifest_kind: package.json\n"
            "  - key: trw-autoresearch\n"
            "    dir: trw-autoresearch\n"
            "    manifest_kind: pyproject\n"
            "  - key: platform\n"
            "    dir: platform\n"
            "    manifest_kind: package.json\n"
            "  - key: trw-video\n"
            "    dir: trw-video\n"
            "    manifest_kind: package.json\n",
            encoding="utf-8",
        )
        (tmp_path / "trw-autoresearch").mkdir()
        (tmp_path / "trw-autoresearch" / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
        (tmp_path / "platform").mkdir()
        (tmp_path / "platform" / "package.json").write_text('{"version":"0.32.9"}\n')
        (tmp_path / "trw-video").mkdir()
        (tmp_path / "trw-video" / "package.json").write_text('{"version":"0.1.0"}\n')

        status = collect_version_status(tmp_path)

        packages = status["versions"]["packages"]
        assert packages["trw-autoresearch"] == "0.1.0"
        assert packages["platform"] == "0.32.9"
        assert packages["trw-video"] == "0.1.0"
        assert "trw-autoresearch" in status["compatibility_matrix"]["independent_packages"]

    def test_malformed_installed_asset_manifest_fails_closed_without_crashing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Unreadable authoritative asset metadata should be reported as a gate error, not raised."""
        monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
        (tmp_path / ".trw" / "frameworks").mkdir(parents=True)
        (tmp_path / ".trw" / "frameworks" / "VERSION.yaml").write_text(
            "framework_version: [unterminated\n",
            encoding="utf-8",
        )

        status = collect_version_status(tmp_path)

        assert status["compatible"] is False
        assert "installed_asset_manifest_unreadable" in status["mismatches"]
        assert status["errors"]


# ---------------------------------------------------------------------------
# build-release handler gate tests
# ---------------------------------------------------------------------------


class TestBuildReleaseHandler:
    """The CLI handler gates version compatibility before artifact side effects."""

    def test_incompatible_status_blocks_before_build(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from trw_mcp.canons.fingerprint import reset_frozen_fingerprint
        from trw_mcp.models.config import TRWConfig

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("trw_mcp.__version__", "1.2.3")
        _write_version_root(tmp_path, monkeypatch, mcp_version="1.2.3", framework_version=TRWConfig().framework_version)
        reset_frozen_fingerprint()
        monkeypatch.setattr(
            "trw_mcp.release_builder.build_release_bundle",
            lambda **_kwargs: pytest.fail("release artifact built before compatibility gate"),
        )
        output_dir = tmp_path / "dist"

        with pytest.raises(SystemExit, match="live_process_currentness_unknown"):
            _run_build_release(argparse.Namespace(output_dir=output_dir))

        assert not output_dir.exists()

    def test_healthy_gate_runs_before_builder(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        events: list[str] = []

        def gate(_root: Path) -> dict[str, object]:
            events.append("gate")
            return {"compatible": True}

        def build(**_kwargs: object) -> dict[str, object]:
            assert events == ["gate"]
            events.append("build")
            return {
                "path": str(tmp_path / "release.tar.gz"),
                "version": "1.2.3",
                "checksum": "abc",
                "size_bytes": 1,
            }

        monkeypatch.setattr("trw_mcp.server._subcommands_release.assert_version_status_compatible", gate)
        monkeypatch.setattr("trw_mcp.release_builder.build_release_bundle", build)

        with pytest.raises(SystemExit) as exc_info:
            _run_build_release(argparse.Namespace(output_dir=tmp_path))

        assert exc_info.value.code == 0
        assert events == ["gate", "build"]


# ---------------------------------------------------------------------------
# build_release_bundle tests
# ---------------------------------------------------------------------------


class TestBuildReleaseBundle:
    """Tests for build_release_bundle."""

    def test_creates_tar_gz_with_explicit_version(self, tmp_path: Path) -> None:
        """Bundle is created with the given version string."""
        result = build_release_bundle(version="1.0.0", output_dir=tmp_path)

        bundle_path = Path(str(result["path"]))
        assert bundle_path.exists()
        assert bundle_path.name == "trw-release-1.0.0.tar.gz"
        assert result["version"] == "1.0.0"

    def test_result_contains_required_keys(self, tmp_path: Path) -> None:
        """Result dict has path, version, checksum, size_bytes."""
        result = build_release_bundle(version="2.0.0", output_dir=tmp_path)

        assert set(result.keys()) == {"path", "version", "checksum", "size_bytes", "framework_version"}

    def test_checksum_matches_file(self, tmp_path: Path) -> None:
        """Returned checksum matches an independent SHA-256 of the bundle."""
        result = build_release_bundle(version="1.0.0", output_dir=tmp_path)

        bundle_path = Path(str(result["path"]))
        expected = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        assert result["checksum"] == expected

    def test_size_bytes_matches_file(self, tmp_path: Path) -> None:
        """Returned size_bytes matches actual file size."""
        result = build_release_bundle(version="1.0.0", output_dir=tmp_path)

        bundle_path = Path(str(result["path"]))
        assert result["size_bytes"] == bundle_path.stat().st_size

    def test_size_bytes_is_positive_int(self, tmp_path: Path) -> None:
        """size_bytes is a positive integer."""
        result = build_release_bundle(version="1.0.0", output_dir=tmp_path)
        size = result["size_bytes"]
        assert isinstance(size, int)
        assert size > 0

    def test_tar_contains_data_directory(self, tmp_path: Path) -> None:
        """The .tar.gz archive contains a top-level 'data' entry."""
        result = build_release_bundle(version="1.0.0", output_dir=tmp_path)

        bundle_path = Path(str(result["path"]))
        with tarfile.open(bundle_path, "r:gz") as tar:
            names = tar.getnames()
            assert any(n == "data" or n.startswith("data/") for n in names)

    def test_tar_has_no_absolute_paths(self, tmp_path: Path) -> None:
        """Archive members use relative paths, not absolute."""
        result = build_release_bundle(version="1.0.0", output_dir=tmp_path)

        bundle_path = Path(str(result["path"]))
        with tarfile.open(bundle_path, "r:gz") as tar:
            for member in tar.getmembers():
                assert not member.name.startswith("/"), f"Absolute path in archive: {member.name}"

    def test_creates_output_dir_if_missing(self, tmp_path: Path) -> None:
        """output_dir is created (with parents) when it doesn't exist."""
        nested = tmp_path / "a" / "b" / "c"
        assert not nested.exists()

        result = build_release_bundle(version="1.0.0", output_dir=nested)

        assert nested.is_dir()
        assert Path(str(result["path"])).exists()

    def test_uses_read_version_when_version_is_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When version=None, delegates to _read_version()."""
        monkeypatch.setattr(rb_mod, "_read_version", lambda: "42.0.0")

        result = build_release_bundle(version=None, output_dir=tmp_path)

        assert result["version"] == "42.0.0"
        assert "42.0.0" in Path(str(result["path"])).name

    def test_explicit_version_skips_read_version(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When version is provided, _read_version is not called."""
        called = False

        def boom() -> str:
            nonlocal called
            called = True
            return "should-not-appear"

        monkeypatch.setattr(rb_mod, "_read_version", boom)

        result = build_release_bundle(version="3.0.0", output_dir=tmp_path)

        assert not called
        assert result["version"] == "3.0.0"

    def test_defaults_to_cwd_when_output_dir_is_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When output_dir=None, bundle lands in the current working directory."""
        monkeypatch.chdir(tmp_path)

        result = build_release_bundle(version="1.0.0", output_dir=None)

        bundle_path = Path(str(result["path"]))
        assert bundle_path.parent == tmp_path.resolve()

    def test_bundle_is_valid_gzip(self, tmp_path: Path) -> None:
        """Produced file is a valid gzip-compressed tar archive."""
        result = build_release_bundle(version="1.0.0", output_dir=tmp_path)

        bundle_path = Path(str(result["path"]))
        with tarfile.open(bundle_path, "r:gz") as tar:
            members = tar.getmembers()
            assert len(members) > 0

    def test_different_versions_produce_different_filenames(
        self,
        tmp_path: Path,
    ) -> None:
        """Two builds with different versions produce distinct bundle files."""
        r1 = build_release_bundle(version="1.0.0", output_dir=tmp_path)
        r2 = build_release_bundle(version="2.0.0", output_dir=tmp_path)

        assert r1["path"] != r2["path"]
        assert Path(str(r1["path"])).exists()
        assert Path(str(r2["path"])).exists()

    def test_path_is_resolved_absolute(self, tmp_path: Path) -> None:
        """Returned path is an absolute resolved path."""
        result = build_release_bundle(version="1.0.0", output_dir=tmp_path)
        p = Path(str(result["path"]))
        assert p.is_absolute()

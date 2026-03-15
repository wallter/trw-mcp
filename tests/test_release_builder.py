"""Tests for trw_mcp.release_builder — release bundle packaging."""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

import pytest

from trw_mcp import release_builder as rb_mod
from trw_mcp.release_builder import _read_version, _sha256, build_release_bundle

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
    """Create a fake __file__ path so parent^4 resolves to tmp_path.

    _read_version does: Path(__file__).parent.parent.parent.parent
    So we need: tmp_path / L1 / L2 / L3 / release_builder.py
    parent chain: L3 -> L2 -> L1 -> tmp_path  (3 .parent calls from dir)
    But the code does 4 .parent calls from the *file* path:
      file.parent = L3, .parent = L2, .parent = L1, .parent = tmp_path
    """
    fake = tmp_path / "a" / "b" / "c" / "release_builder.py"
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

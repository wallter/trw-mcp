"""PRD-CORE-337 FR09 -- the credentials writer moves onto ``trw_memory.safe_fs``.

``write_credentials_key`` used to ``write_text`` the file, THEN ``os.chmod(0o600)`` -- a window
in which the file is briefly readable at the process umask's default mode -- and it never checked
whether the leaf or a parent component (``.trw``) was a symlink before writing through it. FR09
closes both: the file is created at mode 0600 directly via ``safe_fs.write_beneath`` (no separate
chmod call) and a symlinked leaf or parent is refused with ``UnsafeWriteError`` rather than
followed. The served entrypoint (``trw-mcp auth login`` -> ``run_auth_login``) surfaces that
refusal as a clean, typed CLI error -- never a raw traceback.
"""

from __future__ import annotations

import os
import stat
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from trw_memory.exceptions import UnsafeWriteError

from trw_mcp.cli.auth import run_auth_login
from trw_mcp.models.config._credentials import (
    credentials_path_for,
    migrate_for_update_project,
    read_key_from_file,
    write_credentials_key,
)

_LOGIN_RESULT: dict[str, object] = {
    "api_key": "trw_dk_attacker_would_love_this",
    "org_name": "acme-corp",
    "user_email": "dev@acme.com",
}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink refusal; Windows is best-effort by design (NFR02)")
def test_run_auth_login_refuses_a_symlinked_credentials_leaf(tmp_path: Path) -> None:
    """(a) leaf symlink: the served CLI auth path refuses rather than writing through it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "not-yours.yaml"
    outside_file.write_text("do-not-touch\n", encoding="utf-8")

    config_path = tmp_path / "project" / ".trw" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('installation_id: "test"\n', encoding="utf-8")
    creds_path = credentials_path_for(config_path)
    creds_path.symlink_to(outside_file)

    with patch("trw_mcp.cli.auth.device_auth_login", return_value=_LOGIN_RESULT):
        exit_code = run_auth_login("https://api.example.com", config_path)

    assert exit_code == 1
    assert outside_file.read_text(encoding="utf-8") == "do-not-touch\n"
    assert not creds_path.is_symlink() or creds_path.resolve() == outside_file.resolve()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink refusal; Windows is best-effort by design (NFR02)")
def test_run_auth_login_refuses_a_symlinked_credentials_parent(tmp_path: Path) -> None:
    """(b) parent-component symlink: ``.trw`` itself points outside the project."""
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()

    project = tmp_path / "project"
    project.mkdir()
    (project / ".trw").symlink_to(outside_dir, target_is_directory=True)
    config_path = project / ".trw" / "config.yaml"

    with patch("trw_mcp.cli.auth.device_auth_login", return_value=_LOGIN_RESULT):
        exit_code = run_auth_login("https://api.example.com", config_path)

    assert exit_code == 1
    assert list(outside_dir.iterdir()) == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink refusal; Windows is best-effort by design (NFR02)")
def test_write_credentials_key_refuses_a_symlinked_leaf_directly(tmp_path: Path) -> None:
    outside_file = tmp_path / "outside.yaml"
    outside_file.write_text("untouched\n", encoding="utf-8")
    creds = tmp_path / ".trw" / "credentials.yaml"
    creds.parent.mkdir(parents=True)
    creds.symlink_to(outside_file)

    with pytest.raises(UnsafeWriteError):
        write_credentials_key(creds, "trw_dk_should_not_land")

    assert outside_file.read_text(encoding="utf-8") == "untouched\n"


def test_write_credentials_key_sets_mode_at_creation_without_a_separate_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(c) the create-then-chmod window is closed: no ``os.chmod`` call is needed at all."""
    creds = tmp_path / ".trw" / "credentials.yaml"

    chmod_calls: list[tuple[object, int]] = []
    real_chmod = os.chmod

    def _tracking_chmod(path: object, mode: int, *args: object, **kwargs: object) -> None:
        chmod_calls.append((path, mode))
        real_chmod(path, mode, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "chmod", _tracking_chmod)

    write_credentials_key(creds, "trw_dk_abc")

    assert chmod_calls == []
    if sys.platform != "win32":
        mode = stat.S_IMODE(os.stat(creds).st_mode)
        assert mode == 0o600
    assert read_key_from_file(creds) == "trw_dk_abc"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits are not meaningful on Windows (NFR02)")
def test_credentials_file_is_never_observable_at_a_wider_mode_than_0600(tmp_path: Path) -> None:
    """A concurrent stat never observes a mode other than 0600 -- there is no create-then-chmod window.

    A watcher thread repeatedly stats the credentials file while a second thread calls
    ``write_credentials_key`` in a loop, under a wide umask (0o022) that would have made the old
    two-step ``write_text`` + ``os.chmod`` implementation's window observable at 0o644.
    """
    creds = tmp_path / ".trw" / "credentials.yaml"
    creds.parent.mkdir(parents=True)

    observed: set[int] = set()
    stop = threading.Event()

    def _watch() -> None:
        while not stop.is_set():
            try:
                observed.add(stat.S_IMODE(os.stat(creds).st_mode))
            except FileNotFoundError:  # trw-fail-silent-allow: the file may not exist yet between iterations
                pass

    old_umask = os.umask(0o022)
    try:
        watcher = threading.Thread(target=_watch, daemon=True)
        watcher.start()
        for i in range(300):
            write_credentials_key(creds, f"trw_dk_{i}")
        stop.set()
        watcher.join(timeout=10)
    finally:
        os.umask(old_umask)

    assert observed <= {0o600}, f"observed a wider-than-0600 mode: {sorted(oct(m) for m in observed)}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlink refusal; Windows is best-effort by design (NFR02)")
def test_update_project_migration_refuses_a_symlinked_credentials_target_without_data_loss(tmp_path: Path) -> None:
    """Review round-1 triage: the update-project migration path has no symlink-refusal regression test.

    Driven through ``migrate_for_update_project`` rather than the full served ``update_project`` CLI
    path: the latter's ``repo`` fixture requires ``init_project`` plus the daemon/pin/grant machinery
    (see ``test_bootstrap_config_defaults.py``), none of which bears on the claim under test here --
    ``migrate_for_update_project`` IS ``_run_post_update_phases``'s own call site (unconditional, first
    post-update phase) and is already the object of ``test_credentials_migration.py``'s regression
    suite for this exact function, so this follows established precedent rather than re-deriving a
    heavier fixture for no additional coverage.
    """
    outside_file = tmp_path / "outside.yaml"
    outside_file.write_text("do-not-touch\n", encoding="utf-8")

    config_path = tmp_path / ".trw" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('installation_id: "test"\nplatform_api_key: "trw_dk_tracked"\n', encoding="utf-8")
    creds_path = credentials_path_for(config_path)
    creds_path.symlink_to(outside_file)

    result: dict[str, list[str]] = {"updated": [], "warnings": [], "errors": []}

    # (a) no exception escapes.
    migrate_for_update_project(config_path, result)

    # (b) the refusal is recorded as a skipped-migration warning.
    assert any("Credential migration skipped" in w for w in result["warnings"])

    # (c) the outside symlink target is byte-identical -- the refused write never touched it.
    assert outside_file.read_text(encoding="utf-8") == "do-not-touch\n"

    # (d) config.yaml still holds the tracked key: the move failed, so it must NOT be blanked.
    # migrate_config_key writes credentials.yaml BEFORE blanking config.yaml, so a refused write
    # raises before `_blank_config_key` ever runs -- this is the intended fail-open semantics
    # (`migrate_for_update_project`'s docstring: "a refused symlinked write target never raises --
    # the update continues"), not a data-loss bug. This test pins that ordering.
    config_text = config_path.read_text(encoding="utf-8")
    assert 'platform_api_key: "trw_dk_tracked"' in config_text

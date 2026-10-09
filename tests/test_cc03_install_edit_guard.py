"""CC03-INSTALL-OVERWRITES-EDITS: an enabled CC-03 sync keeps a hand-edited hook (HB-2)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from trw_mcp.bootstrap import _claude_code_distill_channels as ch

HOOK = "pre-tool-distill-hint.sh"


def _result() -> dict[str, list[str]]:
    return {"created": [], "updated": [], "preserved": [], "errors": [], "warnings": []}


@pytest.fixture
def bundled(monkeypatch: pytest.MonkeyPatch) -> str:
    content = "#!/bin/sh\necho trw-new\n"
    monkeypatch.setattr(ch, "_get_hook_content", lambda name: content)
    return content


def _dest(root: Path) -> Path:
    dest = root / ".claude" / "hooks" / HOOK
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def test_install_hook_edited_without_manifest_record_kept(tmp_path: Path, bundled: str) -> None:
    dest = _dest(tmp_path)
    dest.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    result = _result()
    ch._install_hook(tmp_path, HOOK, result, None)
    assert dest.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"
    assert result["updated"] == []
    assert any("kept because it was edited" in w for w in result["warnings"])


def test_install_hook_drifted_from_manifest_record_kept(tmp_path: Path, bundled: str) -> None:
    dest = _dest(tmp_path)
    old = "#!/bin/sh\necho trw-old\n"
    dest.write_text("#!/bin/sh\necho trw-old edited\n", encoding="utf-8")
    hashes = {HOOK: hashlib.sha256(old.encode()).hexdigest()}
    result = _result()
    ch._install_hook(tmp_path, HOOK, result, hashes)
    assert "edited" in dest.read_text(encoding="utf-8")
    assert result["updated"] == []


def test_install_hook_recorded_old_trw_bytes_refreshed(tmp_path: Path, bundled: str) -> None:
    dest = _dest(tmp_path)
    old = "#!/bin/sh\necho trw-old\n"
    dest.write_text(old, encoding="utf-8")
    result = _result()
    ch._install_hook(tmp_path, HOOK, result, {HOOK: hashlib.sha256(old.encode()).hexdigest()})
    assert dest.read_text(encoding="utf-8") == bundled
    assert result["updated"] == [f".claude/hooks/{HOOK}"]
    assert result["warnings"] == []


def test_install_hook_absent_created(tmp_path: Path, bundled: str) -> None:
    dest = _dest(tmp_path)
    result = _result()
    ch._install_hook(tmp_path, HOOK, result, None)
    assert dest.read_text(encoding="utf-8") == bundled
    assert result["created"] == [f".claude/hooks/{HOOK}"]


def test_sync_passes_manifest_hashes_to_install(tmp_path: Path, bundled: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ch, "read_cc03_config", lambda root: {"cc03_hook_enabled": True})
    dest = _dest(tmp_path)
    old = "#!/bin/sh\necho trw-old\n"
    dest.write_text(old, encoding="utf-8")
    result = _result()
    ch.sync_cc03_hook_files(tmp_path, result, {HOOK: hashlib.sha256(old.encode()).hexdigest()})
    assert dest.read_text(encoding="utf-8") == bundled


def test_install_hook_edit_warning_names_a_remedy_that_works(tmp_path: Path, bundled: str) -> None:
    """--reprovision refuses a path with no tombstone; delete-and-run-again is the sequence that restores it."""
    _dest(tmp_path).write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    result = _result()
    ch._install_hook(tmp_path, HOOK, result, None)
    assert "delete it and run update-project again" in result["warnings"][0]
    assert "--reprovision" not in result["warnings"][0]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo")
def test_install_hook_fifo_at_the_name_left_untouched_without_blocking(tmp_path: Path, bundled: str) -> None:
    dest = _dest(tmp_path)
    os.mkfifo(dest)
    result = _result()
    ch._install_hook(tmp_path, HOOK, result, None)
    assert dest.is_fifo()
    assert result["warnings"] == [f".claude/hooks/{HOOK}: left untouched (not a regular file)"]


def test_install_hook_symlinked_hooks_dir_never_read_or_written(tmp_path: Path, bundled: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / HOOK).write_text("#!/bin/sh\necho outside\n", encoding="utf-8")
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "hooks").symlink_to(outside)
    result = _result()
    ch._install_hook(project, HOOK, result, None)
    assert (outside / HOOK).read_text(encoding="utf-8") == "#!/bin/sh\necho outside\n"
    assert result["created"] == result["updated"] == []
    assert any("left untouched" in w for w in result["warnings"])

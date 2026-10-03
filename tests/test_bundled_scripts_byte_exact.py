"""UF-BOOT-07-KI1-LEGACY-CRLF: bundled scripts are written byte-exact, and a CRLF copy from an older Windows
install is still recognised as TRW's own.

(b) A str passed to ``write_checkout_file`` is newline-translated to ``os.linesep``, so on Windows a script got
CRLF while its ownership baseline is the LF bundle (and a shell script with CRLF breaks under sh). Each
writer now passes bytes. Simulated here by setting ``os.linesep`` to CRLF, the exact branch Windows takes.

(a) A copy an older TRW already wrote with CRLF hashes differently from the LF manifest record, so it read
as a user edit and never refreshed. When every line ending is CRLF and the LF-normalised bytes match the
bundle or the recorded hash, it is TRW's own; a mixed or otherwise changed file is still a user edit.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

CRLF = "\r\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _result() -> dict[str, list[str]]:
    return {"created": [], "updated": [], "preserved": [], "warnings": [], "errors": []}


@pytest.fixture
def windows_newlines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "linesep", CRLF)


# ---- (b) byte-exact writers ------------------------------------------------------------------------


@pytest.mark.usefixtures("windows_newlines")
@pytest.mark.parametrize("hook", ["pre-tool-distill-hint.sh", "lib-distill-hint.sh"])
def test_cc03_hooks_are_written_byte_exact(tmp_path: Path, hook: str) -> None:
    from trw_mcp.bootstrap._claude_code_distill_channels import _get_hook_content, _install_hook

    result = _result()
    _install_hook(tmp_path, hook, result)
    expected = _get_hook_content(hook)
    assert expected is not None and not result["errors"], result
    assert (tmp_path / ".claude" / "hooks" / hook).read_bytes() == expected.encode("utf-8")


@pytest.mark.usefixtures("windows_newlines")
def test_the_antigravity_before_edit_hook_is_written_byte_exact(tmp_path: Path) -> None:
    from trw_mcp.channels.antigravity._before_edit_hook import generate_hook_script, install_before_edit_hook

    out = install_before_edit_hook(tmp_path)
    assert out["installed"] and out["error"] is None, out
    assert Path(out["hook_script_path"]).read_bytes() == generate_hook_script().encode("utf-8")


@pytest.mark.usefixtures("windows_newlines")
def test_the_codex_post_tool_use_hook_is_written_byte_exact(tmp_path: Path) -> None:
    from trw_mcp.channels.codex._post_tool_use_telemetry import generate_hook_script, install_hook_script

    out = install_hook_script(tmp_path)
    assert Path(out["path"]).read_bytes() == generate_hook_script().encode("utf-8")


def test_the_git_post_commit_shim_is_written_byte_exact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap._git_hooks import install_git_post_commit_hook, render_post_commit_shim

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.setattr(os, "linesep", CRLF)
    result = install_git_post_commit_hook(tmp_path)
    assert not result["errors"], result
    hook = tmp_path / ".git" / "hooks" / "post-commit"
    assert hook.read_bytes() == render_post_commit_shim(None).encode("utf-8")
    assert b"\r" not in hook.read_bytes(), "a CRLF shell shim breaks under sh"


# ---- (a) a legacy CRLF copy is TRW's own --------------------------------------------------------------


def test_a_legacy_crlf_cc03_copy_refreshes_and_a_mixed_edit_is_kept(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._claude_code_distill_channels import _get_hook_content, _install_hook

    hook = "lib-distill-hint.sh"
    rel = f".claude/hooks/{hook}"
    bundled = (_get_hook_content(hook) or "").encode("utf-8")
    older = bundled + b"# an older TRW shipped this line\n"
    dest = tmp_path / rel
    dest.parent.mkdir(parents=True)
    dest.write_bytes(older.replace(b"\n", b"\r\n"))  # what an older TRW wrote on Windows
    manifest = {rel: _sha(older)}  # recorded against the LF bytes

    result = _result()
    _install_hook(tmp_path, hook, result, manifest_hashes=manifest)
    assert dest.read_bytes() == bundled, "an untouched legacy CRLF copy must refresh"
    assert rel not in result["preserved"], result

    edited = older.replace(b"\n", b"\r\n", 1)  # mixed endings: someone else touched it
    dest.write_bytes(edited)
    result = _result()
    _install_hook(tmp_path, hook, result, manifest_hashes=manifest)
    assert dest.read_bytes() == edited and rel in result["preserved"], "a mixed-ending file is a user edit"


def test_a_legacy_crlf_copilot_c5_copy_refreshes(tmp_path: Path) -> None:
    from trw_mcp.bootstrap._copilot_distill_channels import install_copilot_distill_channels
    from trw_mcp.bootstrap._managed_client_artifacts import _copilot_hook_scripts

    install_copilot_distill_channels(tmp_path)
    scripts = {rel: data for rel, data in _copilot_hook_scripts().items() if (tmp_path / rel).exists()}
    assert scripts
    older = {rel: data + b"# older\n" for rel, data in scripts.items()}
    for rel, data in older.items():
        (tmp_path / rel).write_bytes(data.replace(b"\n", b"\r\n"))
    result = install_copilot_distill_channels(tmp_path, manifest_hashes={r: _sha(d) for r, d in older.items()})
    for rel, data in scripts.items():
        assert (tmp_path / rel).read_bytes() == data, f"{rel}: a legacy CRLF copy was kept as an edit"
        assert rel not in result.get("preserved", []), result

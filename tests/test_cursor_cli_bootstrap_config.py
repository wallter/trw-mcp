"""Unit tests for cursor-cli bootstrap config generators (PRD-CORE-137)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _read_cli_json(tmp_path: Path) -> dict:
    return json.loads((tmp_path / ".cursor" / "cli.json").read_text())


class TestCliConfigFresh:
    """test_cli_config_fresh: fresh write creates all baseline tokens."""

    def test_file_created(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["created"]
        assert (tmp_path / ".cursor" / "cli.json").is_file()

    def test_baseline_allow_tokens(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import _DEFAULT_ALLOW, generate_cursor_cli_config

        generate_cursor_cli_config(tmp_path)
        config = _read_cli_json(tmp_path)
        for token in _DEFAULT_ALLOW:
            assert token in config["permissions"]["allow"], f"Missing allow token: {token}"

    def test_baseline_deny_tokens(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import _DEFAULT_DENY, generate_cursor_cli_config

        generate_cursor_cli_config(tmp_path)
        config = _read_cli_json(tmp_path)
        for token in _DEFAULT_DENY:
            assert token in config["permissions"]["deny"], f"Missing deny token: {token}"

    def test_has_note_key(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        generate_cursor_cli_config(tmp_path)
        config = _read_cli_json(tmp_path)
        assert "_note" in config

    def test_tty_reminder_in_info(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        result = generate_cursor_cli_config(tmp_path)
        info = result.get("info", [])
        all_info = " ".join(info)
        assert "TTY" in all_info
        assert "tmux" in all_info


class TestCliConfigSmartMerge:
    """test_cli_config_smart_merge_preserves_user_allow: user tokens survive merge."""

    def test_preserves_user_allow_token(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        user_config = {"permissions": {"allow": ["Shell(my-tool)"], "deny": []}}
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["updated"]
        merged = _read_cli_json(tmp_path)
        assert "Shell(my-tool)" in merged["permissions"]["allow"]
        assert "Shell(git)" in merged["permissions"]["allow"]

    def test_preserves_user_deny_token(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        user_config = {"permissions": {"allow": [], "deny": ["Write(secret.key)"]}}
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        generate_cursor_cli_config(tmp_path)
        merged = _read_cli_json(tmp_path)
        assert "Write(secret.key)" in merged["permissions"]["deny"]
        assert "Read(.env*)" in merged["permissions"]["deny"]

    def test_preserves_extra_top_level_keys(self, tmp_path: Path) -> None:
        """test_cli_config_preserves_extra_keys: model_defaults survives merge."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        user_config = {
            "permissions": {"allow": [], "deny": []},
            "model_defaults": {"temperature": 0.7},
        }
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        generate_cursor_cli_config(tmp_path)
        merged = _read_cli_json(tmp_path)
        assert "model_defaults" in merged
        assert merged["model_defaults"]["temperature"] == 0.7


class TestCliConfigNoDuplicates:
    """test_cli_config_no_duplicate_tokens: no duplicate entries when user has TRW token."""

    def test_no_duplicate_allow_tokens(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        user_config = {"permissions": {"allow": ["Shell(git)"], "deny": []}}
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        generate_cursor_cli_config(tmp_path)
        merged = _read_cli_json(tmp_path)
        allow = merged["permissions"]["allow"]
        assert allow.count("Shell(git)") == 1, "Shell(git) should appear exactly once"

    def test_no_duplicate_deny_tokens(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        user_config = {"permissions": {"allow": [], "deny": ["Read(.env*)"]}}
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        generate_cursor_cli_config(tmp_path)
        merged = _read_cli_json(tmp_path)
        deny = merged["permissions"]["deny"]
        assert deny.count("Read(.env*)") == 1, "Read(.env*) should appear exactly once"


class TestCliConfigMalformed:
    """test_cli_config_malformed_fallback: malformed JSON triggers overwrite + warning."""

    def test_malformed_json_overwrites(self, tmp_path: Path) -> None:
        from trw_mcp.bootstrap._cursor_cli import (
            _DEFAULT_ALLOW,
            _DEFAULT_DENY,
            generate_cursor_cli_config,
        )

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "cli.json").write_text("{ not valid json {{{{")

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["updated"]
        merged = _read_cli_json(tmp_path)
        for token in _DEFAULT_ALLOW:
            assert token in merged["permissions"]["allow"]
        for token in _DEFAULT_DENY:
            assert token in merged["permissions"]["deny"]

    def test_empty_file_overwrites(self, tmp_path: Path) -> None:
        """Empty file (valid empty string, invalid JSON) triggers overwrite."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "cli.json").write_text("")

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["updated"]
        merged = _read_cli_json(tmp_path)
        assert "permissions" in merged

    def test_non_object_root_overwrites(self, tmp_path: Path) -> None:
        """A JSON array (not object) at root triggers overwrite with defaults."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "cli.json").write_text("[1, 2, 3]")

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["updated"]
        merged = _read_cli_json(tmp_path)
        assert "permissions" in merged

    def test_malformed_emits_warning_in_info(self, tmp_path: Path) -> None:
        """Malformed JSON overwrite appends a warning to result['info']."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "cli.json").write_text("not_json")

        result = generate_cursor_cli_config(tmp_path)
        info = result.get("info", [])
        all_info = " ".join(info)
        assert "malformed" in all_info.lower() or "WARNING" in all_info

    def test_permissions_not_a_dict_overwrites(self, tmp_path: Path) -> None:
        """permissions key exists but is a non-dict (e.g. a string) → overwrite."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "cli.json").write_text(json.dumps({"permissions": "should-be-dict"}))

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["updated"]
        merged = _read_cli_json(tmp_path)
        assert isinstance(merged["permissions"], dict)

    def test_non_list_allow_overwrites(self, tmp_path: Path) -> None:
        """permissions.allow is a non-list (a string) → overwrite, never crash.

        Regression: a string ``allow`` previously reached ``allow.append`` and
        raised an uncaught ``AttributeError`` (AttributeError was not in the
        caller's except tuple).
        """
        from trw_mcp.bootstrap._cursor_cli import _DEFAULT_ALLOW, generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        (cursor_dir / "cli.json").write_text(json.dumps({"permissions": {"allow": "Shell(git)", "deny": []}}))

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["updated"]
        merged = _read_cli_json(tmp_path)
        assert isinstance(merged["permissions"]["allow"], list)
        for token in _DEFAULT_ALLOW:
            assert token in merged["permissions"]["allow"]


class TestCliConfigUnreadable:
    """Hardening: non-UTF-8 / unreadable cli.json must not crash or leak detail."""

    def test_non_utf8_overwrites(self, tmp_path: Path) -> None:
        """A non-UTF-8 cli.json is overwritten with defaults, not crashed on.

        ``read_text(encoding='utf-8')`` would raise ``UnicodeDecodeError``; the
        structural seam returns ``None`` so the document is rewritten cleanly.
        """
        from trw_mcp.bootstrap._cursor_cli import (
            _DEFAULT_ALLOW,
            _DEFAULT_DENY,
            generate_cursor_cli_config,
        )

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        # 0xFF/0xFE are never valid as the leading bytes of UTF-8 text.
        (cursor_dir / "cli.json").write_bytes(b"\xff\xfe\x00not utf-8 at all")

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["updated"]
        merged = _read_cli_json(tmp_path)
        for token in _DEFAULT_ALLOW:
            assert token in merged["permissions"]["allow"]
        for token in _DEFAULT_DENY:
            assert token in merged["permissions"]["deny"]
        info = " ".join(result.get("info", []))
        assert "WARNING" in info

    def test_unreadable_cli_json_does_not_crash(self, tmp_path: Path) -> None:
        """A read/write error (cli.json is a directory) degrades, never raises.

        Routing the read through ``read_json_object`` swallows the ``OSError``;
        the guarded write records a content-free structural error instead of
        propagating raw ``OSError`` text to the fail-open caller's ``{exc}`` log.
        """
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        # A directory where the file is expected: read_bytes and write_text both
        # raise IsADirectoryError (an OSError) regardless of uid.
        (cursor_dir / "cli.json").mkdir()

        result = generate_cursor_cli_config(tmp_path)  # must not raise

        assert (cursor_dir / "cli.json").is_dir(), "the directory must be untouched"
        errors = result.get("errors", [])
        assert errors, "an unwritable path should record a structural error"
        joined = " ".join(errors)
        assert "unwritable" in joined
        # Content-free: no raw OSError strerror / errno / absolute path payload.
        assert "Errno" not in joined and str(cursor_dir / "cli.json") not in joined

    def test_valid_merge_not_flagged_corrupt(self, tmp_path: Path) -> None:
        """Regression: a valid existing config must NOT emit a corrupt warning."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        user_config = {"permissions": {"allow": ["Shell(my-tool)"], "deny": []}}
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        result = generate_cursor_cli_config(tmp_path)
        assert ".cursor/cli.json" in result["updated"]
        assert not result.get("errors")
        info = " ".join(result.get("info", []))
        assert "malformed" not in info.lower()
        merged = _read_cli_json(tmp_path)
        assert "Shell(my-tool)" in merged["permissions"]["allow"]


class TestCliConfigSmartMergeParameterized:
    """PRD-CORE-137-FR03 acceptance: 5 parameterized smart-merge cases."""

    def test_case2_user_tokens_preserved_alongside_trw_defaults(self, tmp_path: Path) -> None:
        """Case 2: User Shell(my-custom-tool) allow + Write(secret.key) deny survive."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        user_config = {
            "permissions": {
                "allow": ["Shell(my-custom-tool)"],
                "deny": ["Write(secret.key)"],
            }
        }
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        generate_cursor_cli_config(tmp_path)
        merged = _read_cli_json(tmp_path)
        assert "Shell(my-custom-tool)" in merged["permissions"]["allow"]
        assert "Write(secret.key)" in merged["permissions"]["deny"]
        assert "Shell(git)" in merged["permissions"]["allow"]
        assert "Read(.env*)" in merged["permissions"]["deny"]

    def test_case3_user_shell_rm_allow_coexists_with_trw_shell_rm_rf_deny(self, tmp_path: Path) -> None:
        """Case 3: User's Shell(rm) allow + TRW's Shell(rm -rf) deny both kept."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        user_config = {"permissions": {"allow": ["Shell(rm)"], "deny": []}}
        (cursor_dir / "cli.json").write_text(json.dumps(user_config))

        generate_cursor_cli_config(tmp_path)
        merged = _read_cli_json(tmp_path)
        assert "Shell(rm)" in merged["permissions"]["allow"]
        assert "Shell(rm -rf)" in merged["permissions"]["deny"]


class TestCliConfigTokenPlacement:
    """Verify tokens appear in the correct allow/deny list (not just key existence)."""

    @pytest.mark.parametrize(
        "token",
        [
            "Read(**/*)",
            "Shell(git)",
            "Shell(grep)",
            "Shell(find)",
            "Shell(rg)",
            "Shell(ls)",
            "Shell(cat)",
            "Shell(pytest)",
            "Shell(npm)",
            "Shell(python)",
            "Shell(trw-mcp)",
        ],
    )
    def test_each_allow_token_in_allow_list(self, tmp_path: Path, token: str) -> None:
        """Each _DEFAULT_ALLOW token appears in permissions.allow, not permissions.deny."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        generate_cursor_cli_config(tmp_path)
        config = _read_cli_json(tmp_path)
        assert token in config["permissions"]["allow"], f"Missing in allow: {token}"
        assert token not in config["permissions"]["deny"], f"Incorrectly in deny: {token}"

    @pytest.mark.parametrize(
        "token",
        [
            "Shell(rm -rf)",
            "Shell(curl)",
            "Shell(wget)",
            "Read(.env*)",
            "Read(**/.env.local)",
            "Read(**/secrets.yaml)",
            "Write(.env*)",
            "Write(.git/**/*)",
            "Write(.trw/**/*)",
            "Write(node_modules/**/*)",
        ],
    )
    def test_each_deny_token_in_deny_list(self, tmp_path: Path, token: str) -> None:
        """Each _DEFAULT_DENY token appears in permissions.deny, not permissions.allow."""
        from trw_mcp.bootstrap._cursor_cli import generate_cursor_cli_config

        generate_cursor_cli_config(tmp_path)
        config = _read_cli_json(tmp_path)
        assert token in config["permissions"]["deny"], f"Missing in deny: {token}"
        assert token not in config["permissions"]["allow"], f"Incorrectly in allow: {token}"

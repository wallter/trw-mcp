"""Tests for the structural-safe bootstrap JSON read seam.

``read_json_object`` collapses five outcomes — absent, unreadable, non-UTF-8,
malformed JSON, and non-object top level — into a single ``None`` return with a
content-free diagnostic. Before this seam, advisory/bootstrap config readers
(`_merge_mcp_json`, `generate_copilot_hooks`, ...) crashed with an uncaught
``UnicodeDecodeError`` on non-UTF-8 bytes and an uncaught ``AttributeError`` on
a top-level JSON array/scalar, because their ``except`` clauses only covered
``(json.JSONDecodeError, OSError)``.

These tests pin both the seam's contract and the two highest-value consumers:
``.mcp.json`` (the MCP connection file) and Copilot ``hooks.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.bootstrap._copilot import _COPILOT_HOOKS_PATH, generate_copilot_hooks
from trw_mcp.bootstrap._file_ops import read_json_object, read_settings_for_merge
from trw_mcp.bootstrap._mcp_json import _merge_mcp_json

# A token that must never appear in any operator-facing diagnostic. Embedding it
# in malformed/non-object payloads guards the "structural, content-free" rule.
_SECRET = "sk-super-secret-token-DO-NOT-LEAK"


def _new_result() -> dict[str, list[str]]:
    return {"created": [], "updated": [], "preserved": [], "errors": []}


@pytest.mark.unit
class TestReadJsonObject:
    """Unit-test the seam's five outcomes in isolation."""

    def test_valid_object_returned(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"a": 1, "b": [2, 3]}), encoding="utf-8")
        assert read_json_object(path, context="t") == {"a": 1, "b": [2, 3]}

    def test_absent_file_returns_none(self, tmp_path: Path) -> None:
        assert read_json_object(tmp_path / "missing.json", context="t") is None

    def test_unreadable_returns_none(self, tmp_path: Path) -> None:
        # A directory at the path raises OSError (IsADirectoryError) on read.
        target = tmp_path / "adir"
        target.mkdir()
        assert read_json_object(target, context="t") is None

    def test_non_utf8_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_bytes(b"\xff\xfe{\x00")
        assert read_json_object(path, context="t") is None

    def test_malformed_json_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text("{not valid json", encoding="utf-8")
        assert read_json_object(path, context="t") is None

    @pytest.mark.parametrize("payload", ["[]", "42", '"a string"', "true", "null"])
    def test_non_object_top_level_returns_none(self, tmp_path: Path, payload: str) -> None:
        path = tmp_path / "config.json"
        path.write_text(payload, encoding="utf-8")
        assert read_json_object(path, context="t") is None

    def test_empty_object_is_a_valid_object(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json"
        path.write_text("{}", encoding="utf-8")
        assert read_json_object(path, context="t") == {}

    def test_diagnostic_is_content_free(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """A malformed payload carrying a secret must not surface in logs."""
        path = tmp_path / "config.json"
        path.write_text("{not json: " + _SECRET, encoding="utf-8")
        assert read_json_object(path, context="t") is None
        captured = capsys.readouterr()
        assert _SECRET not in (captured.out + captured.err)


@pytest.mark.unit
class TestReadSettingsForMerge:
    """The settings-merge seam shared by the Gemini + Antigravity CLI readers.

    Distinct from ``read_json_object``: corrupt content is preserved to a
    ``.bak`` and recovered (``{}``) rather than collapsed to ``None``, and a
    genuine ``OSError`` aborts (``None``) instead of starting fresh.
    """

    def test_valid_object_returned(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"mcpServers": {"x": {}}}), encoding="utf-8")
        result = _new_result()
        assert read_settings_for_merge(path, rel_path="settings.json", result=result) == {"mcpServers": {"x": {}}}
        assert result.get("warnings", []) == []
        assert result["errors"] == []

    def test_absent_returns_empty_base(self, tmp_path: Path) -> None:
        result = _new_result()
        assert read_settings_for_merge(tmp_path / "missing.json", rel_path="missing.json", result=result) == {}
        assert result["errors"] == []
        assert result.get("warnings", []) == []

    def test_empty_file_returns_empty_base(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text("   \n", encoding="utf-8")
        result = _new_result()
        assert read_settings_for_merge(path, rel_path="settings.json", result=result) == {}
        assert result.get("warnings", []) == []
        assert not path.with_suffix(".json.bak").exists()  # nothing to preserve

    def test_unreadable_aborts_with_error(self, tmp_path: Path) -> None:
        # A directory at the path raises OSError on read_bytes -> abort (None).
        target = tmp_path / "settings.json"
        target.mkdir()
        result = _new_result()
        assert read_settings_for_merge(target, rel_path="settings.json", result=result) is None
        assert any("settings.json" in e for e in result["errors"])
        assert result.get("warnings", []) == []

    @pytest.mark.parametrize(
        ("label", "content", "reason_fragment"),
        [
            ("non_utf8", b"\xff\xfe{\x00", "UTF-8"),
            ("malformed", b"{bad json", "JSON"),
            ("array", b"[1, 2, 3]", "JSON object"),
            ("scalar", b"42", "JSON object"),
        ],
    )
    def test_corrupt_content_backed_up_and_recovered(
        self, tmp_path: Path, label: str, content: bytes, reason_fragment: str
    ) -> None:
        path = tmp_path / "settings.json"
        path.write_bytes(content)
        result = _new_result()

        assert read_settings_for_merge(path, rel_path="settings.json", result=result) == {}

        backup = path.with_suffix(".json.bak")
        assert backup.exists()
        assert backup.read_bytes() == content  # exact bytes preserved
        warnings = result.get("warnings", [])
        assert any(reason_fragment in w and "backed up" in w for w in warnings), warnings
        assert result["errors"] == []

    def test_warning_is_content_free(self, tmp_path: Path) -> None:
        """A secret in corrupt content must not surface in the recovery warning."""
        path = tmp_path / "settings.json"
        path.write_text("{not json: " + _SECRET, encoding="utf-8")
        result = _new_result()

        read_settings_for_merge(path, rel_path="settings.json", result=result)

        assert all(_SECRET not in w for w in result.get("warnings", []))
        assert all(_SECRET not in e for e in result["errors"])


@pytest.mark.unit
class TestMergeMcpJsonStructuralSafety:
    """`.mcp.json` is the connection-critical file — malformed input must not crash."""

    @pytest.mark.parametrize(
        ("label", "content"),
        [("array", b"[]"), ("scalar", b"42"), ("non_utf8", b"\xff\xfe{"), ("malformed", b"{bad")],
    )
    def test_malformed_falls_open_to_fresh_trw_entry(self, tmp_path: Path, label: str, content: bytes) -> None:
        (tmp_path / ".mcp.json").write_bytes(content)
        result = _new_result()
        _merge_mcp_json(tmp_path, result)  # must not raise
        data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        assert "trw" in data["mcpServers"]

    def test_valid_user_server_preserved(self, tmp_path: Path) -> None:
        (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "foo"}}}), encoding="utf-8")
        result = _new_result()
        _merge_mcp_json(tmp_path, result)
        data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        assert "other" in data["mcpServers"]
        assert "trw" in data["mcpServers"]

    def test_non_object_mcpservers_key_does_not_crash(self, tmp_path: Path) -> None:
        # Valid object, but ``mcpServers`` is the wrong shape (list) — the
        # existing isinstance guard handles it; assert no regression.
        (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": []}), encoding="utf-8")
        result = _new_result()
        _merge_mcp_json(tmp_path, result)
        data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
        assert "trw" in data["mcpServers"]


@pytest.mark.unit
class TestGenerateCopilotHooksStructuralSafety:
    """Copilot hooks.json bootstrap must not crash or leak on malformed input."""

    @pytest.mark.parametrize(
        ("label", "content"),
        [("array", b"[]"), ("scalar", b"42"), ("non_utf8", b"\xff\xfe{"), ("malformed", b"{bad")],
    )
    def test_malformed_existing_left_untouched_with_error(self, tmp_path: Path, label: str, content: bytes) -> None:
        hooks_path = tmp_path / _COPILOT_HOOKS_PATH
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_bytes(content)

        result = generate_copilot_hooks(tmp_path)  # must not raise

        assert hooks_path.read_bytes() == content  # untouched
        assert result["errors"], "a structural error should be recorded"
        assert any(_COPILOT_HOOKS_PATH in e for e in result["errors"])

    def test_secret_in_malformed_file_not_leaked_to_errors(self, tmp_path: Path) -> None:
        hooks_path = tmp_path / _COPILOT_HOOKS_PATH
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text("{not json " + _SECRET, encoding="utf-8")

        result = generate_copilot_hooks(tmp_path)

        assert all(_SECRET not in e for e in result["errors"])

    def test_force_overwrites_malformed_existing(self, tmp_path: Path) -> None:
        hooks_path = tmp_path / _COPILOT_HOOKS_PATH
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_bytes(b"[]")  # non-object

        result = generate_copilot_hooks(tmp_path, force=True)

        data = json.loads(hooks_path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert "sessionStart" in data["hooks"]
        assert not result["errors"]

    def test_valid_existing_user_hook_preserved(self, tmp_path: Path) -> None:
        hooks_path = tmp_path / _COPILOT_HOOKS_PATH
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "hooks": {
                        "sessionStart": [
                            {"description": "user hook", "hooks": [{"type": "command", "command": "echo hi"}]}
                        ]
                    },
                }
            ),
            encoding="utf-8",
        )

        generate_copilot_hooks(tmp_path)

        data = json.loads(hooks_path.read_text(encoding="utf-8"))
        descriptions = [group["description"] for group in data["hooks"]["sessionStart"]]
        assert "user hook" in descriptions
        assert any(d.startswith("TRW managed:") for d in descriptions)

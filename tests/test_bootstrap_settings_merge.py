"""Hardening tests for ``_merge_settings_json`` (``.claude/settings.json``).

Covers the robustness + leak-discipline contract: corrupt/non-UTF-8/non-object
inputs on either the bundled or the existing side must never raise and never
echo the file's bytes into logs/results, while the happy-path smart-merge
(preserve existing env, add missing bundled env/hooks) is unchanged.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from trw_mcp.bootstrap._template_updater import _merge_settings_json

_SECRET = "s3cr3t-token-DO-NOT-LEAK"


def _new_result() -> dict[str, list[str]]:
    return {"created": [], "updated": [], "preserved": [], "errors": []}


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _bundled(path: Path) -> Path:
    src = path / "bundled" / "settings.json"
    _write(
        src,
        json.dumps(
            {
                "env": {"ENABLE_TOOL_SEARCH": "true", "TRW_NEW_FLAG": "1"},
                "hooks": {"SessionStart": [{"matcher": "startup"}]},
            }
        ),
    )
    return src


# ── happy path ─────────────────────────────────────────────────────────────


def test_valid_merge_preserves_existing_and_adds_missing(tmp_path: Path) -> None:
    src = _bundled(tmp_path)
    dest = tmp_path / "proj" / ".claude" / "settings.json"
    _write(
        dest,
        json.dumps(
            {
                "env": {"ENABLE_TOOL_SEARCH": "false"},
                "permissions": {"allow": ["Bash(ls)"]},
                "hooks": {"PreToolUse": [{"matcher": "x"}]},
            }
        ),
    )
    result = _new_result()

    _merge_settings_json(src, dest, result)

    merged = json.loads(dest.read_text(encoding="utf-8"))
    # Existing opt-out preserved, missing bundled env added.
    assert merged["env"]["ENABLE_TOOL_SEARCH"] == "false"
    assert merged["env"]["TRW_NEW_FLAG"] == "1"
    # Existing hook preserved, missing bundled hook event added.
    assert merged["hooks"]["PreToolUse"] == [{"matcher": "x"}]
    assert merged["hooks"]["SessionStart"] == [{"matcher": "startup"}]
    # Unrelated top-level key preserved.
    assert merged["permissions"] == {"allow": ["Bash(ls)"]}
    assert str(dest) in result["updated"]
    assert not result["errors"]


# ── existing-side corruption ────────────────────────────────────────────────


def test_existing_non_utf8_does_not_raise_or_leak(tmp_path: Path, caplog) -> None:
    src = _bundled(tmp_path)
    dest = tmp_path / "proj" / ".claude" / "settings.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Invalid UTF-8 bytes wrapped around a secret-looking marker.
    dest.write_bytes(b"\xff\xfe" + _SECRET.encode("utf-8") + b"\xff")
    result = _new_result()

    with caplog.at_level(logging.DEBUG):
        _merge_settings_json(src, dest, result)  # must not raise

    # Recovered by copying the valid bundled template.
    recovered = json.loads(dest.read_text(encoding="utf-8"))
    assert recovered["env"]["TRW_NEW_FLAG"] == "1"
    # No secret marker leaked into logs or results.
    blob = caplog.text + json.dumps(result)
    assert _SECRET not in blob


def test_existing_top_level_non_object_does_not_raise(tmp_path: Path) -> None:
    src = _bundled(tmp_path)
    dest = tmp_path / "proj" / ".claude" / "settings.json"
    _write(dest, json.dumps(["not", "an", "object"]))
    result = _new_result()

    _merge_settings_json(src, dest, result)  # must not raise

    recovered = json.loads(dest.read_text(encoding="utf-8"))
    assert isinstance(recovered, dict)
    assert recovered["env"]["TRW_NEW_FLAG"] == "1"


def test_existing_malformed_json_fallback_is_content_free(tmp_path: Path, caplog) -> None:
    src = _bundled(tmp_path)
    dest = tmp_path / "proj" / ".claude" / "settings.json"
    _write(dest, '{"env": {"ENABLE_TOOL_SEARCH": "' + _SECRET + '"')  # truncated JSON
    result = _new_result()

    with caplog.at_level(logging.DEBUG):
        _merge_settings_json(src, dest, result)  # must not raise

    blob = caplog.text + json.dumps(result)
    assert _SECRET not in blob
    # Recovered to a valid document.
    json.loads(dest.read_text(encoding="utf-8"))


# ── bundled-side corruption ─────────────────────────────────────────────────


def test_bundled_invalid_does_not_overwrite_existing(tmp_path: Path) -> None:
    src = tmp_path / "bundled" / "settings.json"
    _write(src, "{ this is not valid json")
    dest = tmp_path / "proj" / ".claude" / "settings.json"
    user_doc = {"env": {"ENABLE_TOOL_SEARCH": "false"}, "mine": True}
    _write(dest, json.dumps(user_doc))
    result = _new_result()

    _merge_settings_json(src, dest, result)  # must not raise

    # User's settings untouched.
    assert json.loads(dest.read_text(encoding="utf-8")) == user_doc
    assert any("bundled template invalid" in e for e in result["errors"])
    assert not result["updated"]


def test_bundled_non_object_does_not_overwrite_existing(tmp_path: Path) -> None:
    src = tmp_path / "bundled" / "settings.json"
    _write(src, json.dumps([1, 2, 3]))
    dest = tmp_path / "proj" / ".claude" / "settings.json"
    user_doc = {"env": {"X": "1"}}
    _write(dest, json.dumps(user_doc))
    result = _new_result()

    _merge_settings_json(src, dest, result)

    assert json.loads(dest.read_text(encoding="utf-8")) == user_doc
    assert any("bundled template invalid" in e for e in result["errors"])


# ── misc invariants ──────────────────────────────────────────────────────────


def test_missing_src_is_noop(tmp_path: Path) -> None:
    src = tmp_path / "bundled" / "settings.json"  # does not exist
    dest = tmp_path / "proj" / ".claude" / "settings.json"
    _write(dest, json.dumps({"env": {"X": "1"}}))
    result = _new_result()

    _merge_settings_json(src, dest, result)

    assert json.loads(dest.read_text(encoding="utf-8")) == {"env": {"X": "1"}}
    assert not result["errors"] and not result["updated"]


def test_new_install_copies_bundled(tmp_path: Path) -> None:
    src = _bundled(tmp_path)
    dest = tmp_path / "proj" / ".claude" / "settings.json"  # does not exist
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = _new_result()

    _merge_settings_json(src, dest, result)

    assert json.loads(dest.read_text(encoding="utf-8"))["env"]["TRW_NEW_FLAG"] == "1"
    assert str(dest) in result["created"]


def test_existing_non_object_env_block_preserved(tmp_path: Path) -> None:
    """A hand-edited non-object ``env`` must not crash the merge."""
    src = _bundled(tmp_path)
    dest = tmp_path / "proj" / ".claude" / "settings.json"
    _write(dest, json.dumps({"env": "oops-a-string", "keep": 1}))
    result = _new_result()

    _merge_settings_json(src, dest, result)  # must not raise

    merged = json.loads(dest.read_text(encoding="utf-8"))
    assert merged["env"] == "oops-a-string"  # preserved untouched
    assert merged["keep"] == 1
    assert str(dest) in result["updated"]

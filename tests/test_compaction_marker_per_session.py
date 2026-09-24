"""The pre-compaction marker is per session: one session's compaction gates only itself.

Observed 2026-09-23 in a four-session formation sharing one checkout: every
auto-compaction of any member wrote the single project-wide
``.trw/context/pre_compact_state.json``, and every OTHER member's next ``trw_*``
call was blocked with ``post_compaction_recovery_required`` (markers at
03:30:12Z and 03:40:32Z; the lead never compacted). The same file also replayed
the compacted member's run into the others' post-compaction recovery text.

Each MCP server is one stdio process per client session, so "two sessions" here
means two processes: :func:`reset_state` between them models the second
process, and each carries its own ``TRW_SESSION_ID``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from mcp.types import TextContent

from tests._test_ceremony_middleware_gate_support import (
    FakeContext,
    FakeMessage,
    FakeMiddlewareContext,
    FakeRequestContext,
    FakeToolResult,
    _clean_state,  # noqa: F401  # autouse: resets middleware module state per test
)
from trw_mcp.client_profiles.session_identity import known_session_id_env_vars
from trw_mcp.middleware.ceremony import CeremonyMiddleware, reset_state
from trw_mcp.state.pre_compact_marker import (
    pre_compact_marker_path,
    read_pre_compact_marker,
    write_pre_compact_marker,
)

_HOOKS = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks"
_MARKER = {"timestamp": "2026-09-23T03:30:12+00:00", "trigger": "auto", "directive": "resume slice 3"}


async def _recall(trw_dir: Path) -> Any:
    """One ``trw_recall`` call through a fresh middleware, as a separate process would make it."""

    async def call_next(_ctx: Any) -> Any:
        return FakeToolResult(content=[TextContent(type="text", text="tool ok")])

    ctx = FakeMiddlewareContext(
        message=FakeMessage(name="trw_recall"),
        fastmcp_context=FakeContext(request_context=FakeRequestContext(session_id="mcp-ctx")),
    )
    with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
        return await CeremonyMiddleware().on_call_tool(ctx, call_next)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_compacting_one_session_leaves_the_other_sessions_tools_ungated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = tmp_path / ".trw"

    monkeypatch.setenv("TRW_SESSION_ID", "session-a")
    write_pre_compact_marker(_MARKER, trw_dir=trw_dir)

    monkeypatch.setenv("TRW_SESSION_ID", "session-b")
    reset_state()
    other = await _recall(trw_dir)
    assert other.structured_content is None, "a session that did not compact owes no recovery"
    assert read_pre_compact_marker(trw_dir) is None, "session B must not read session A's recovery state"

    monkeypatch.setenv("TRW_SESSION_ID", "session-a")
    reset_state()
    compacted = await _recall(trw_dir)
    assert compacted.structured_content is not None
    assert compacted.structured_content["error"] == "post_compaction_recovery_required"
    marker = read_pre_compact_marker(trw_dir)
    assert marker is not None and marker.directive == "resume slice 3"


_LONG_A = "a" * 150 + "-session-one"
_LONG_B = "a" * 150 + "-session-two"


def _hex(key: str) -> str:
    return f"pre_compact/{key.encode().hex()}.json"


@pytest.fixture(autouse=True)
def _no_ambient_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("TRW_SESSION_ID", *known_session_id_env_vars()):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("11111111-2222-3333-4444-555555555555", _hex("11111111-2222-3333-4444-555555555555")),
        ("../escape", _hex("../escape")),
        (".hidden", _hex(".hidden")),
        ("a" * 120, _hex("a" * 120)),
        ("a" * 121, f"pre_compact/sha256-{hashlib.sha256(b'a' * 121).hexdigest()}.json"),
        ("", "pre_compact_state.json"),
    ],
)
def test_the_marker_path_is_a_hex_encoded_session_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, expected: str
) -> None:
    monkeypatch.setenv("TRW_SESSION_ID", key)
    assert pre_compact_marker_path(tmp_path) == tmp_path / "context" / expected


def test_ids_that_differ_only_in_unsafe_characters_get_distinct_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``run:a`` and ``run:b`` once both fell back to the one project-wide file."""
    monkeypatch.setenv("TRW_SESSION_ID", "run:a")
    first = pre_compact_marker_path(tmp_path)
    monkeypatch.setenv("TRW_SESSION_ID", "run:b")
    second = pre_compact_marker_path(tmp_path)
    assert first != second
    assert first.parent.name == second.parent.name == "pre_compact"


def test_two_long_ids_get_distinct_bounded_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin identity accepts ids up to 200 characters; past 120 bytes they once shared the flat marker."""
    names = []
    for key in (_LONG_A, _LONG_B):
        monkeypatch.setenv("TRW_SESSION_ID", key)
        names.append(pre_compact_marker_path(tmp_path).name)
    assert names[0] != names[1]
    assert all(name.startswith("sha256-") and len(name) <= 255 for name in names)


def test_the_client_session_variable_keys_the_marker_when_trw_session_id_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server resolves the identity hook-env.sh exports as TRW_SESSION_ID."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "cc-session")
    assert pre_compact_marker_path(tmp_path) == tmp_path / "context" / _hex("cc-session")


def test_disabling_pin_isolation_does_not_move_the_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hook cannot see ``ctx_isolation_enabled``, so the marker must not depend on it."""
    from trw_mcp.models.config import get_config

    monkeypatch.setattr(get_config(), "ctx_isolation_enabled", False, raising=False)
    monkeypatch.setenv("TRW_SESSION_ID", "session-a")
    assert pre_compact_marker_path(tmp_path) == tmp_path / "context" / _hex("session-a")


def test_the_hook_resolves_the_same_variables_in_the_same_order_as_the_server() -> None:
    lib = (_HOOKS / "lib-trw.sh").read_text(encoding="utf-8")
    line = next(ln for ln in lib.splitlines() if ln.strip().startswith("_pcs_key="))
    assert re.findall(r"\$\{([A-Z_]+):-", line) == ["TRW_SESSION_ID", *known_session_id_env_vars()]


def _install_hooks(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (root / ".trw" / "context").mkdir(parents=True)
    for name in ("lib-trw.sh", "pre-compact.sh", "post-compact.sh"):
        (hooks / name).write_text((_HOOKS / name).read_text(encoding="utf-8"), encoding="utf-8")
    return root


def _run_hook(name: str, root: Path, identity: dict[str, str], payload: dict[str, str]) -> str:
    drop = ("TRW_SESSION_ID", *known_session_id_env_vars())
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update({"CLAUDE_PROJECT_DIR": str(root), "TRW_PROJECT_ROOT": str(root), **identity})
    return subprocess.run(
        ["sh", str(root / ".claude" / "hooks" / name)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=root,
        env=env,
        check=False,
    ).stdout


def test_the_precompact_hook_writes_only_the_compacting_sessions_marker(tmp_path: Path) -> None:
    root = _install_hooks(tmp_path)

    _run_hook("pre-compact.sh", root, {"TRW_SESSION_ID": "session-a"}, {"trigger": "auto", "session_id": "session-a"})

    context = root / ".trw" / "context"
    assert (context / _hex("session-a")).is_file()
    assert not (context / "pre_compact_state.json").exists(), "the project-wide marker gates every session"
    with patch.dict(os.environ, {"TRW_SESSION_ID": "session-b"}):
        assert not pre_compact_marker_path(root / ".trw").exists()


@pytest.mark.parametrize(
    "identity",
    [
        {"TRW_SESSION_ID": "run:a"},
        {"CLAUDE_CODE_SESSION_ID": "cc-\u00e9-session"},
        {"TRW_SESSION_ID": _LONG_A},
        {"TRW_SESSION_ID": "a" * 100},
    ],
    ids=["unsafe-trw-id", "client-var-only", "long-id", "repeated-bytes"],
)
def test_the_hook_and_the_server_name_the_same_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identity: dict[str, str]
) -> None:
    root = _install_hooks(tmp_path)
    _run_hook("pre-compact.sh", root, identity, {"trigger": "auto"})
    for name, value in identity.items():
        monkeypatch.setenv(name, value)
    marker = pre_compact_marker_path(root / ".trw")
    assert marker.parent.name == "pre_compact"
    assert marker.is_file()


def test_the_hook_keeps_two_long_ids_on_separate_markers(tmp_path: Path) -> None:
    root = _install_hooks(tmp_path)
    _run_hook("pre-compact.sh", root, {"TRW_SESSION_ID": _LONG_A}, {"trigger": "auto"})

    context = root / ".trw" / "context"
    assert not (context / "pre_compact_state.json").exists()
    with patch.dict(os.environ, {"TRW_SESSION_ID": _LONG_A}):
        assert pre_compact_marker_path(root / ".trw").is_file()
    with patch.dict(os.environ, {"TRW_SESSION_ID": _LONG_B}):
        assert not pre_compact_marker_path(root / ".trw").exists()

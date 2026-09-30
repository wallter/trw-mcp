"""Every client whose TRW hook sees an edit must leave the same change evidence (INC-115).

The unpinned deliver gate decides "did this session change code?" from ``file_modified`` records in
``.trw/context/session-events.jsonl``. Only claude-code's PostToolUse hook wrote them, so on cursor-ide and codex
the count read 0 and ``trw_deliver`` succeeded with no build. This module runs each client's REAL edit hook against a
throwaway git checkout and asks the gate's own reader what it counted.

The census below classifies every built-in client, so a new client cannot ship without either a writer or a recorded
reason it has none (a client with no writer is the server half's job: fail closed, INC-115 (b)).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trw_mcp.channels.codex._post_tool_use_telemetry import HOOK_SCRIPT_CONTENT
from trw_mcp.models.config import builtin_client_ids
from trw_mcp.tools._delivery_event_checks import unpinned_session_changed_files

_HOOKS = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "hooks"
_LONG_AGO = datetime(2000, 1, 1, tzinfo=timezone.utc)

#: Clients whose edit hook is exercised below: each must produce a file_modified record the gate counts.
WRITES_CHANGE_EVIDENCE = frozenset({"claude-code", "cursor-ide", "codex"})

#: Clients with NO change-evidence writer today, and why. Recorded, not silent: the server must fail closed for them.
NO_CHANGE_EVIDENCE_WRITER = {
    "cursor-cli": "the CLI surface registers no afterFileEdit hook",
    "copilot": "the postToolUse edit hook only logs to .trw/logs/hooks.jsonl",
    "opencode": "no TRW edit hook is installed for this client",
    "antigravity-cli": "no TRW edit hook is installed for this client",
    "grok": "no TRW edit hook is installed for this client",
}


def _repo(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=project, check=True)
    (project / "app.py").write_text("def f():\n    return 2\n", encoding="utf-8")
    return project


def _run(argv: list[str], payload: dict[str, object], project: Path, env: dict[str, str]) -> None:
    subprocess.run(
        argv,
        input=json.dumps(payload),
        text=True,
        cwd=project,
        env={"PATH": os.environ["PATH"], "HOME": str(project.parent), **env},
        capture_output=True,
        timeout=60,
        check=False,
    )


def _claude(project: Path) -> None:
    _run(
        ["sh", str(_HOOKS / "post-tool-event.sh")],
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(project / "app.py")},
            "tool_response": {},
            "session_id": "s1",
            "hook_event_name": "PostToolUse",
        },
        project,
        {"CLAUDE_PROJECT_DIR": str(project), "TRW_SESSION_ID": "s1"},
    )


def _cursor(project: Path) -> None:
    _run(
        ["bash", str(_HOOKS / "cursor" / "trw-after-file-edit.sh")],
        {"hook_event_name": "afterFileEdit", "file_path": str(project / "app.py"), "edits": []},
        project,
        {"CURSOR_PROJECT_DIR": str(project)},
    )


def _codex(project: Path) -> None:
    script = project / ".codex" / "hooks" / "trw_post_edit_telemetry.py"
    script.parent.mkdir(parents=True)
    script.write_text(HOOK_SCRIPT_CONTENT, encoding="utf-8")
    envelope = "*** Begin Patch\n*** Update File: app.py\n@@\n-    return 1\n+    return 2\n*** End Patch"
    _run(
        [sys.executable, str(script)],
        {"hook_event_name": "PostToolUse", "tool_name": "apply_patch", "tool_input": {"command": envelope}},
        project,
        {},
    )


_WRITERS = {"claude-code": _claude, "cursor-ide": _cursor, "codex": _codex}


def test_every_builtin_client_is_classified_exactly_once() -> None:
    classified = WRITES_CHANGE_EVIDENCE | set(NO_CHANGE_EVIDENCE_WRITER)

    assert set(builtin_client_ids()) == classified, "a new client must declare a change-evidence writer or a reason"
    assert not WRITES_CHANGE_EVIDENCE & set(NO_CHANGE_EVIDENCE_WRITER)
    assert set(_WRITERS) == WRITES_CHANGE_EVIDENCE


@pytest.mark.parametrize("client", sorted(WRITES_CHANGE_EVIDENCE))
def test_the_real_edit_hook_leaves_a_file_modified_record_the_gate_counts(client: str, tmp_path: Path) -> None:
    if client == "claude-code" and shutil.which("jq") is None and shutil.which("python3") is None:
        pytest.skip("skip-category: platform — the claude hook needs jq or python3")
    project = _repo(tmp_path)

    _WRITERS[client](project)

    stream = project / ".trw" / "context" / "session-events.jsonl"
    records = [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines() if line.strip()]
    edits = [r for r in records if r.get("event") == "file_modified"]
    assert [r["file"] for r in edits] == ["app.py"], f"{client}: expected one repo-relative file_modified for app.py"
    assert edits[0]["pinned"] is False
    # The server counts by its own key where the client publishes none (unscoped_since); claude's key is its host id.
    counted = unpinned_session_changed_files(
        project / ".trw",
        "s1" if client == "claude-code" else "server-key",
        unscoped_since=None if client == "claude-code" else _LONG_AGO,
    )
    assert counted == 1, f"{client}: the deliver gate's reader counted {counted}, so the gate would stay inert"


def test_a_symlinked_context_dir_is_not_written_through(tmp_path: Path) -> None:
    project = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".trw").mkdir()
    (project / ".trw" / "context").symlink_to(outside)

    _cursor(project)
    _codex(project)

    assert list(outside.iterdir()) == [], "a hook wrote change evidence through a symlinked .trw/context"


def test_the_codex_hook_reads_the_apply_patch_envelope_and_ignores_shell_commands(tmp_path: Path) -> None:
    project = _repo(tmp_path)
    script = project / ".codex" / "hooks" / "trw_post_edit_telemetry.py"
    script.parent.mkdir(parents=True)
    script.write_text(HOOK_SCRIPT_CONTENT, encoding="utf-8")
    envelope = "*** Begin Patch\n*** Add File: new.py\n+x\n*** Update File: app.py\n@@\n-a\n+b\n*** Delete File: old.py\n*** End Patch"

    _run([sys.executable, str(script)], {"tool_name": "apply_patch", "tool_input": {"command": envelope}}, project, {})
    # A shell command that merely mentions the markers is not an edit the hook can vouch for.
    _run([sys.executable, str(script)], {"tool_name": "Bash", "tool_input": {"command": envelope}}, project, {})

    stream = project / ".trw" / "context" / "session-events.jsonl"
    files = [json.loads(line)["file"] for line in stream.read_text(encoding="utf-8").splitlines()]
    assert files == ["new.py", "app.py", "old.py"]


def _codex_module(project: Path) -> object:
    """The shipped codex hook script, loaded in-process so its file operations can be observed."""
    import types

    module = types.ModuleType("codex_hook")
    exec(compile(HOOK_SCRIPT_CONTENT, str(project / "hook.py"), "exec"), module.__dict__)
    return module


def test_the_codex_writer_opens_every_component_below_the_root_relative_to_a_pinned_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _repo(tmp_path)
    module = _codex_module(project)
    calls: list[tuple[str, bool]] = []
    real_open = os.open

    def spy(path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        calls.append((str(path), dir_fd is not None))
        return real_open(path, flags, mode, dir_fd=dir_fd)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", spy)
    module._record_file_modified(project, "apply_patch", ["app.py"], "s")  # type: ignore[attr-defined]

    below_root = [(name, pinned) for name, pinned in calls if name != str(project)]
    assert below_root and all(pinned for _, pinned in below_root), f"a path-based open below the root: {below_root}"


def test_diff_like_text_inside_an_apply_patch_envelope_does_not_name_a_file() -> None:
    from types import ModuleType

    module = ModuleType("codex_hook")
    exec(compile(HOOK_SCRIPT_CONTENT, "hook.py", "exec"), module.__dict__)
    envelope = "*** Begin Patch\n*** Update File: app.py\n@@\n+++ b/phantom.py\n+x\n*** End Patch"

    assert module._patch_files(envelope) == ["app.py"]  # type: ignore[attr-defined]
    assert module._patch_files("--- a/one.py\n+++ b/one.py\n") == ["one.py"]  # a plain unified diff still works


@pytest.mark.parametrize("client", ["cursor-ide", "codex"])
def test_long_distinct_paths_stay_distinct_and_an_oversized_session_id_is_bounded(client: str, tmp_path: Path) -> None:
    project = _repo(tmp_path)
    deep = project / ("d" * 200) / ("e" * 200) / ("f" * 200)
    deep.mkdir(parents=True)
    for name in ("one.py", "two.py"):
        (deep / name).write_text("x = 1\n", encoding="utf-8")
    _WRITERS_FOR = {
        "cursor-ide": lambda path: _run(
            ["bash", str(_HOOKS / "cursor" / "trw-after-file-edit.sh")],
            {"file_path": str(path), "conversation_id": "c" * 5000},
            project,
            {"CURSOR_PROJECT_DIR": str(project)},
        ),
        "codex": lambda path: _run(
            [sys.executable, str(_codex_script(project))],
            {
                "tool_name": "apply_patch",
                "session_id": "c" * 5000,
                "tool_input": {
                    "command": f"*** Begin Patch\n*** Update File: {path.relative_to(project)}\n*** End Patch"
                },
            },
            project,
            {},
        ),
    }
    for name in ("one.py", "two.py"):
        _WRITERS_FOR[client](deep / name)

    stream = project / ".trw" / "context" / "session-events.jsonl"
    records = [json.loads(line) for line in stream.read_text(encoding="utf-8").splitlines()]
    assert len({r["file"] for r in records}) == 2, "two long paths collapsed into one evidence record"
    assert all(len(r["session_id"]) <= 200 for r in records)


def _codex_script(project: Path) -> Path:
    script = project / ".codex" / "hooks" / "trw_post_edit_telemetry.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(HOOK_SCRIPT_CONTENT, encoding="utf-8")
    return script


@pytest.mark.parametrize("client", ["cursor-ide", "codex"])
def test_a_full_stream_gets_an_unknown_marker_not_silence_and_a_hard_full_stream_gets_nothing(
    client: str, tmp_path: Path
) -> None:
    from trw_mcp.tools._delivery_event_checks import change_evidence_unknown

    project = _repo(tmp_path)
    stream = project / ".trw" / "context" / "session-events.jsonl"
    stream.parent.mkdir(parents=True)
    with stream.open("wb") as fh:
        fh.write(b"\n" * (8 * 1024 * 1024 + 10))  # past the soft cap; blank lines, which every reader skips
    writer = {"cursor-ide": _cursor, "codex": _codex}[client]
    if client == "codex":
        _codex_script(project)
        writer = lambda p: _run(  # noqa: E731
            [sys.executable, str(_codex_script(p))],
            {
                "tool_name": "apply_patch",
                "tool_input": {"command": "*** Begin Patch\n*** Update File: app.py\n*** End Patch"},
            },
            p,
            {},
        )

    writer(project)

    tail = stream.read_bytes()[-200:].decode("utf-8", "replace").strip().splitlines()[-1]
    assert json.loads(tail)["event"] == "change_evidence_unknown"
    assert change_evidence_unknown(project) is True, "a full stream must read as unknown evidence, not as no change"

    with stream.open("ab") as fh:
        fh.write(b"\n" * (1024 * 1024))  # past the hard cap
    before = stream.stat().st_size
    writer(project)
    assert stream.stat().st_size == before

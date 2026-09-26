"""T29: no bundled hook extracts a JSON field with grep/sed.

A ``grep -o '"key"[[:space:]]*:[[:space:]]*"[^"]*"' | sed ...`` pipeline reads
the first matching pair anywhere in the payload, whatever object it sits in and
whether the text parses at all, so its answer is a guess presented as a value.
The hooks read JSON with jq, or python3's json module when jq is absent
(lib-trw.sh ``_json_get``). With neither, each one logs a ``jq_unavailable=1``
diagnostic, and the edit-evidence writer records ``change_evidence_unknown`` so
the deliver gate blocks rather than counting zero.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

import trw_mcp.tools._delivery_helpers  # noqa: F401  (import-cycle order guard)
from tests._layout import PACKAGE_ROOT

_DATA = PACKAGE_ROOT / "src" / "trw_mcp" / "data"
#: A grep that pulls a quoted key's quoted value out of JSON text.
_EXTRACTOR = re.compile(r"""grep\s+-[A-Za-z]*o[A-Za-z]*\s+['"][^'"\n]*"[A-Za-z_]+"\[\[:space:\]\]\*:""")


def _bundled_scripts() -> list[Path]:
    return sorted(p for p in _DATA.rglob("*.sh") if p.is_file())


def test_the_extractor_pattern_recognises_the_removed_shape() -> None:
    """Non-vacuity: the scan below would have caught the parser this change deleted."""
    removed = """_x=$(printf '%s' "$_payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1)"""
    assert _EXTRACTOR.search(removed)


def test_no_bundled_hook_parses_json_with_grep() -> None:
    offenders = [
        f"{path.relative_to(_DATA)}:{number}"
        for path in _bundled_scripts()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if not line.lstrip().startswith("#") and _EXTRACTOR.search(line)
    ]
    assert offenders == [], f"shell JSON parsers remain (read with _json_get instead): {offenders}"


def _jq_free_path(tmp_path: Path, *extra: str) -> str:
    """A PATH without jq (and without python3 unless *extra* names it)."""
    bin_dir = tmp_path / "bin-nojq"
    bin_dir.mkdir()
    base = ("sh", "cat", "date", "dirname", "pwd", "printf", "mkdir", "tr", "wc", "cut", "sed", "grep", "head")
    for tool in (*base, *extra):
        resolved = shutil.which(tool)
        if resolved is not None:
            (bin_dir / tool).symlink_to(resolved)
    assert not (bin_dir / "jq").exists()
    return str(bin_dir)


def test_with_python3_but_no_jq_the_edit_is_recorded(tmp_path: Path) -> None:
    """jq absent, python3 present: _json_get reads the payload, so the edit is real evidence."""
    root = tmp_path / "project"
    (root / ".trw" / "context").mkdir(parents=True)
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(root / "src/a.py")}, "session_id": "h"})
    completed = subprocess.run(
        ["sh", str(_DATA / "hooks" / "post-tool-event.sh")],
        input=payload,
        capture_output=True,
        text=True,
        env={
            "PATH": _jq_free_path(tmp_path, "python3", "awk"),
            "CLAUDE_PROJECT_DIR": str(root),
            "TRW_SESSION_ID": "sess-1",
        },
        check=False,
    )
    assert completed.returncode == 0

    rows = [json.loads(line) for line in (root / ".trw/context/session-events.jsonl").read_text().splitlines()]
    assert [(row["event"], row["tool"], row["file"]) for row in rows] == [("file_modified", "Edit", "src/a.py")]


@pytest.mark.parametrize("context_dir_exists", [True, False], ids=["context-present", "fresh-checkout"])
def test_with_no_json_parser_an_edit_reaches_the_gate_as_unknown_and_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, context_dir_exists: bool
) -> None:
    """End to end: the REAL hook with neither jq nor python3, then the REAL delivery decision.

    ``fresh-checkout`` has no ``.trw/context`` yet; the marker must still land there,
    or the gate reads the missing stream as zero changes.
    """
    from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates

    root = tmp_path / "project"
    (root / ".trw").mkdir(parents=True)
    if context_dir_exists:
        (root / ".trw" / "context").mkdir()
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(root / "src/a.py")}, "session_id": "h"})
    completed = subprocess.run(
        ["sh", str(_DATA / "hooks" / "post-tool-event.sh")],
        input=payload,
        capture_output=True,
        text=True,
        env={"PATH": _jq_free_path(tmp_path), "CLAUDE_PROJECT_DIR": str(root), "TRW_SESSION_ID": "sess-1"},
        check=False,
    )
    assert completed.returncode == 0

    rows = [json.loads(line) for line in (root / ".trw/context/session-events.jsonl").read_text().splitlines()]
    assert [row["event"] for row in rows] == ["change_evidence_unknown"], "no guessed file_modified row"

    (root / ".trw" / "context" / "ceremony-state.json").write_text(
        json.dumps({"session_started": True, "session_build_results": {}}), encoding="utf-8"
    )
    monkeypatch.setenv("TRW_SESSION_ID", "sess-1")
    results: dict[str, Any] = {}
    assert evaluate_delivery_gates({}, cast("Any", results), [], None, root / ".trw", False, "") is True
    assert "could not be read" in str(results["delivery_blocked"])


# --------------------------------------------------------------------------- #
# PRD-FIX-154 FR02/FR03: the write side. `_json_object` replaces every `jq -n`
# builder, so a jq-less host with python3 keeps every field instead of
# dropping four of pre-compact's ten keys or writing "unknown"/"(jq
# unavailable)" placeholders.
# --------------------------------------------------------------------------- #

from _ownership_harness import (
    CLIENT_SESSION_VAR,
    SESSION_ID,
    build_project,
    write_hook_env,
)


def _jq_free_env(root: Path, tmp_path: Path, **extra: str) -> dict[str, str]:
    """Real hook env (PATH/HOME/etc.) with jq hidden and python3 present."""
    from tests._layout import path_without

    env = {
        "PATH": path_without(tmp_path, {"jq"}),
        "HOME": str(root),
        "CLAUDE_PROJECT_DIR": str(root),
        CLIENT_SESSION_VAR: SESSION_ID,
    }
    env.update(extra)
    assert shutil.which("jq", path=env["PATH"]) is None
    assert shutil.which("python3", path=env["PATH"]) is not None, "the fallback needs python3"
    return env


def test_pre_compact_snapshot_keeps_all_fields_without_jq(tmp_path: Path) -> None:
    """FR02: with jq hidden, the snapshot carries the same ten keys as with jq."""
    root, own, _foreign = build_project(tmp_path, own_events=1)
    write_hook_env(root)
    (own / "meta" / "checkpoints.jsonl").write_text(
        json.dumps({"message": "did the thing", "pending_decisions": "ship it?"}) + "\n", encoding="utf-8"
    )
    (own / "meta" / "wave_manifest.yaml").write_text("status: in_progress\n", encoding="utf-8")
    task_dir = own / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "task.json").write_text(json.dumps({"status": "in_progress"}), encoding="utf-8")

    env = _jq_free_env(root, tmp_path)
    result = subprocess.run(
        ["sh", str(_DATA / "hooks" / "pre-compact.sh")],
        input=json.dumps({"trigger": "manual", "session_id": SESSION_ID}),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    marker = root / ".trw" / "context" / "pre_compact" / f"{SESSION_ID.encode().hex()}.json"
    snapshot = json.loads(marker.read_text(encoding="utf-8"))
    assert set(snapshot) == {
        "ts",
        "trigger",
        "run_path",
        "phase",
        "events_logged",
        "last_checkpoint",
        "wave_manifest",
        "active_tasks",
        "pending_decisions",
        "ownership",
    }
    assert snapshot["run_path"] == f"{own}/"
    assert snapshot["trigger"] == "manual"
    assert snapshot["ownership"] == "owned"
    assert snapshot["last_checkpoint"] == "did the thing"
    assert snapshot["wave_manifest"] == "in_progress"
    assert snapshot["active_tasks"] == 1
    assert snapshot["pending_decisions"] == "ship it?"


def test_pre_compact_snapshot_escapes_a_hostile_run_path(tmp_path: Path) -> None:
    """FR02: a run_path with a quote and a backslash round-trips through json.loads."""
    root, own, _foreign = build_project(tmp_path, own_events=1)
    # Give the owned run a path containing a double quote and a backslash by
    # renaming its directory and re-pointing pins.json at the new name.
    hostile_dir = own.parent / 'r"un\\path'
    own.rename(hostile_dir)
    pins_path = root / ".trw" / "runtime" / "pins.json"
    pins = json.loads(pins_path.read_text(encoding="utf-8"))
    pins[SESSION_ID]["run_path"] = str(hostile_dir)
    pins_path.write_text(json.dumps(pins), encoding="utf-8")
    write_hook_env(root)

    env = _jq_free_env(root, tmp_path)
    result = subprocess.run(
        ["sh", str(_DATA / "hooks" / "pre-compact.sh")],
        input=json.dumps({"trigger": "manual", "session_id": SESSION_ID}),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    marker = root / ".trw" / "context" / "pre_compact" / f"{SESSION_ID.encode().hex()}.json"
    snapshot = json.loads(marker.read_text(encoding="utf-8"))
    assert snapshot["run_path"] == f"{hostile_dir}/"


@pytest.mark.parametrize(
    ("hook_name", "log_relpath", "payload_key", "expect_key"),
    [
        ("subagent-start.sh", ".trw/logs/subagent-events.jsonl", "agent_type", "agent_type"),
        ("subagent-stop.sh", ".trw/logs/subagent-events.jsonl", "agent_type", "agent_type"),
    ],
)
def test_subagent_telemetry_carries_the_payload_value_without_jq(
    tmp_path: Path, hook_name: str, log_relpath: str, payload_key: str, expect_key: str
) -> None:
    """FR03: agent_type reviewer survives the round trip with jq hidden."""
    root, _own, _foreign = build_project(tmp_path)
    (root / ".trw" / "logs").mkdir(parents=True, exist_ok=True)
    write_hook_env(root)

    env = _jq_free_env(root, tmp_path)
    result = subprocess.run(
        ["sh", str(_DATA / "hooks" / hook_name)],
        input=json.dumps({payload_key: "reviewer", "session_id": SESSION_ID}),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    lines = (root / log_relpath).read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines]
    assert any(row.get(expect_key) == "reviewer" for row in rows), rows


def test_instructions_loaded_carries_file_and_reason_without_jq(tmp_path: Path) -> None:
    """FR03: file_path/load_reason survive the round trip with jq hidden, no placeholder."""
    root, _own, _foreign = build_project(tmp_path)
    write_hook_env(root)

    env = _jq_free_env(root, tmp_path)
    result = subprocess.run(
        ["sh", str(_DATA / "hooks" / "instructions-loaded.sh")],
        input=json.dumps({"file_path": "CLAUDE.md", "load_reason": "path-scoped", "session_id": SESSION_ID}),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    log_path = root / ".trw" / "telemetry" / "instructions-loaded.jsonl"
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["file"] == "CLAUDE.md"
    assert rows[-1]["load_reason"] == "path-scoped"
    assert "(jq unavailable)" not in json.dumps(rows[-1])


# --------------------------------------------------------------------------- #
# PRD-FIX-154 FR07: a census keeps every jq call inside `_json_get`/`_json_object`
# in lib-trw.sh, or names it on this allowlist with a reason. A new,
# unlisted call fails naming its file and line rather than passing silently.
# --------------------------------------------------------------------------- #

_JQ_WORD = re.compile(r"\bjq\b")
_FN_DEF = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{\s*$")

#: (relative-to-data path, enclosing function or "" at top level, a fragment of the call's line)
#: -> one-line reason the call stays. Keyed by content, not line number: an edit elsewhere in a
#: hook no longer breaks the census, while a new call, or an edit to a listed one, still fails
#: until it is reviewed here (rc9; the sqlite census moved the same way in 78edb34cc).
_JQ_ALLOWLIST: dict[tuple[str, str, str], str] = {
    # FR06 decision: keep. lib-intent-guard.sh deliberately never sources
    # lib-trw.sh in the deciding shell; without jq both reads return 1 and the
    # slower Python decision runs instead -- correct, only slower.
    (
        "hooks/lib-intent-guard.sh",
        "_trw_fast_target",
        "command -v jq",
    ): "FR06: fast-path jq-absence defers to the Python decision (kept by design)",
    (
        "hooks/lib-intent-guard.sh",
        "_trw_fast_target",
        "_trw_fp_raw=$(printf '%s' \"$_payload\" | jq -r",
    ): "FR06: fast-path jq read",
    (
        "hooks/lib-intent-guard.sh",
        "_trw_mcp_json_interpreter",
        "command -v jq",
    ): "FR06: .mcp.json launcher jq-absence falls back to PATH resolution",
    (
        "hooks/lib-intent-guard.sh",
        "_trw_mcp_json_interpreter",
        "jq -r '.mcpServers.trw.command",
    ): "FR06: .mcp.json launcher jq read",
    # Pre-existing dual-path helpers in lib-trw.sh that already carry their own
    # dedicated fallback (awk or python3) and are out of this PRD's FR01 scope
    # (they do not build a JSON object -- they parse a log or a pins.json row).
    ("hooks/lib-trw.sh", "_trw_has_json_parser", "command -v jq"): "capability probe (command -v), not a parse call",
    (
        "hooks/lib-trw.sh",
        "has_recent_session_tool_deliver",
        "command -v jq",
    ): "pre-existing jq path, awk fallback below",
    (
        "hooks/lib-trw.sh",
        "has_recent_session_tool_deliver",
        'jq -e -R --arg cut "$_hrstd_cut"',
    ): "jq log scan (fromjson? per line); _json_get reads one object, not a stream",
    ("hooks/lib-trw.sh", "_trw_pin_rows", "command -v jq"): "pre-existing jq path, python3 fallback below",
    (
        "hooks/lib-trw.sh",
        "_trw_pin_rows",
        'jq -r \'if type == "object"',
    ): "pins.json row walk; _json_get reads fixed paths, not entries",
    ("hooks/lib-trw.sh", "_trw_scan_log_for_trw_call", "command -v jq"): "pre-existing jq path, fallback below",
    (
        "hooks/lib-trw.sh",
        "_trw_scan_log_for_trw_call",
        'jq -e -R --arg cut "$_tottc_since"',
    ): "jq log scan (fromjson? per line); _json_get reads one object, not a stream",
    # FR05: the degenerate-result hook's plain field reads go through _json_get (rc9). The two
    # below stay jq: a validity check of the whole payload, and a walk of every string leaf of
    # tool_response, whatever its shape; the hook exits early without jq, so neither degrades.
    (
        "hooks/post-tool-degenerate-result.sh",
        "",
        "command -v jq >/dev/null 2>&1 || exit 0",
    ): "FR05: early jq-absence exit; the walk below needs jq",
    ("hooks/post-tool-degenerate-result.sh", "", "jq -e . >/dev/null"): "FR05: whole-payload validity check",
    (
        "hooks/post-tool-degenerate-result.sh",
        "",
        'jq -r \'.tool_response // "" | [.. | strings]',
    ): "FR05: every string leaf of tool_response, whatever its shape",
    (
        "hooks/cursor/trw-before-edit-hint.sh",
        "",
        "command -v jq",
    ): "FR04 deferred: jq-absence allows and exits, no fallback yet",
    ("hooks/cursor/trw-before-edit-hint.sh", "", "jq -r '.tool_name // empty'"): "FR04 deferred: tool_name read",
    ("hooks/cursor/trw-before-edit-hint.sh", "", "jq -r '.tool_input.file_path"): "FR04 deferred: file_path read",
    # PRD-CORE-301 cut 2 (added after PRD-FIX-154): the session-scoped dedup
    # extracts agent_message from the JSON envelope this SAME hook already
    # built. jq is required earlier in this file (it exits via _allow_and_exit
    # when absent), so here it is guaranteed present -- there is no jq-absent
    # path for _json_get to fall back from. Sourcing lib-trw.sh to reach
    # _json_get is not a like-for-like substitution either: lib-trw.sh's
    # top-level hooks_enabled=false branch calls `exit 0` directly (not
    # `return`), which mid-script here would discard the response this hook
    # already computed and was about to emit.
    (
        "hooks/cursor/trw-before-edit-hint.sh",
        "",
        "jq -r '.agent_message // empty'",
    ): "PRD-CORE-301 cut 2: dedup read, jq required earlier",
    (
        "copilot/hooks/trw-copilot-distill-hint.sh",
        "",
        "command -v jq",
    ): "FR04 deferred: jq-absence prints nothing, no fallback yet",
    (
        "copilot/hooks/trw-copilot-distill-hint.sh",
        "",
        "_file_path=$(printf '%s' \"$_payload\" | jq -r",
    ): "FR04 deferred: file_path read",
}


def _allowlisted(relpath: str, fn: str, source: str) -> bool:
    return any(path == relpath and owner == fn and needle in source for path, owner, needle in _JQ_ALLOWLIST)


#: Helper names inside lib-trw.sh whose jq call IS the converted, shared
#: builder/reader this PRD introduced -- always allowed, regardless of line.
_LIB_TRW_ALLOWED_FUNCTIONS = {"_json_get", "_json_object"}


def _bundled_hook_dirs() -> list[Path]:
    return [d for d in (_DATA / "hooks", _DATA / "hooks" / "cursor", _DATA / "copilot" / "hooks") if d.is_dir()]


def _census_jq_calls(path: Path) -> list[tuple[int, str, str]]:
    """(line_no, enclosing_function_or_empty, source_line) for every jq call.

    Tracks lib-trw.sh's ``_TRW_JSON_GET_PY`` python heredoc as belonging to
    ``_json_get`` (it is that function's fallback body), so a `# ... jq ...`
    comment inside it is not misattributed as an unallowed top-level call.
    """
    calls: list[tuple[int, str, str]] = []
    current_fn = ""
    in_json_get_py = False
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("_TRW_JSON_GET_PY='"):
            in_json_get_py = True
            continue
        if in_json_get_py and stripped == "'":
            in_json_get_py = False
            continue
        match = _FN_DEF.match(stripped)
        if match:
            current_fn = match.group(1)
        elif stripped == "}":
            current_fn = ""
        if stripped.startswith("#"):
            continue
        if _JQ_WORD.search(line):
            effective_fn = current_fn or ("_json_get" if in_json_get_py else "")
            calls.append((lineno, effective_fn, stripped))
    return calls


def test_census_every_jq_call_is_in_the_shared_helpers_or_allowlisted() -> None:
    offenders: list[str] = []
    for hook_dir in _bundled_hook_dirs():
        for path in sorted(hook_dir.glob("*.sh")):
            relpath = str(path.relative_to(_DATA))
            for lineno, fn, _source in _census_jq_calls(path):
                if path.name == "lib-trw.sh" and fn in _LIB_TRW_ALLOWED_FUNCTIONS:
                    continue
                if _allowlisted(relpath, fn, _source):
                    continue
                offenders.append(f"{relpath}:{lineno}")
    assert offenders == [], f"unlisted jq call(s), add to _json_get/_json_object or the allowlist: {offenders}"


def test_census_catches_a_planted_new_jq_call(tmp_path: Path) -> None:
    """Non-vacuity: a jq call outside every helper and off the allowlist fails."""
    planted = tmp_path / "planted-hook.sh"
    planted.write_text(
        '#!/bin/sh\n_x=$(printf "%s" "$1" | jq -r ".a")\n',
        encoding="utf-8",
    )
    calls = _census_jq_calls(planted)
    assert calls, "the scanner must recognise the planted call"
    lineno, fn, _source = calls[0]
    assert fn == ""
    assert not _allowlisted(str(planted.name), fn, _source)


def test_the_census_is_keyed_by_content_so_a_line_shift_keeps_listed_calls_listed(tmp_path: Path) -> None:
    """rc9: the allowlist was keyed by line number, so an unrelated edit above a listed call
    moved it off the list (10 false offenders at int 3754453bb)."""
    real = _DATA / "hooks" / "lib-trw.sh"
    shifted = tmp_path / "lib-trw.sh"
    shifted.write_text("# padding\n" * 7 + real.read_text(encoding="utf-8"), encoding="utf-8")
    unlisted = [
        (lineno, fn)
        for lineno, fn, source in _census_jq_calls(shifted)
        if fn not in _LIB_TRW_ALLOWED_FUNCTIONS and not _allowlisted("hooks/lib-trw.sh", fn, source)
    ]
    assert unlisted == []

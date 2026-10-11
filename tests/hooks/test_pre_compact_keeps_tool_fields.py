"""PRE-COMPACT-KEEP: what the checkpoint tool recorded for a session survives that session's PreCompact hook.

``trw_checkpoint(pre_compact=True, run_path=..., directive=..., context_anchor=...)`` writes the per-session
marker. The PreCompact hook then rewrote the whole document from the pin alone, so the directive and the anchor
were gone, and a session with no pinned run recovered no run at all. The hook now reads those three back first:
from the per-session file only, only under one identity, only from a document the TOOL wrote (never from an
earlier snapshot of the hook itself), and a named run only when no pin resolved one and it is a run of this
project.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from _ownership_harness import (
    BUNDLED_HOOKS,
    CLIENT_SESSION_VAR,
    FOREIGN_RUN_ID,
    OWN_RUN_ID,
    SESSION_ID,
    project,
    shell_env,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX sh hooks")

_LITERAL = "pre-compaction safety checkpoint"


def _marker_path(root: Path, monkeypatch: pytest.MonkeyPatch, *, identified: bool = True) -> Path:
    from trw_mcp.state.pre_compact_marker import pre_compact_marker_path

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    if identified:
        monkeypatch.setenv(CLIENT_SESSION_VAR, SESSION_ID)
    else:
        monkeypatch.delenv(CLIENT_SESSION_VAR, raising=False)
    return pre_compact_marker_path(root / ".trw")


def _tool_wrote(path: Path, **fields: str) -> None:
    """The document the checkpoint tool leaves, reduced to the fields this hook reads back."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"timestamp": "2026-10-11T01:00:00+00:00", "trigger": "manual", **fields}))


def _hook(
    root: Path, name: str, payload: dict[str, str], *, identified: bool = True, env: dict[str, str] | None = None
) -> str:
    env = env or shell_env(root, **({CLIENT_SESSION_VAR: SESSION_ID} if identified else {}))
    done = subprocess.run(
        ["sh", str(BUNDLED_HOOKS / name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def _compact(root: Path, *, identified: bool = True) -> None:
    _hook(root, "pre-compact.sh", {"session_id": SESSION_ID, "trigger": "auto"}, identified=identified)


def _checkpoints(run: Path, *messages: str) -> None:
    lines = [json.dumps({"ts": "2026-10-11T01:00:00Z", "message": message}) for message in messages]
    (run / "meta" / "checkpoints.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_an_unpinned_session_keeps_the_run_the_tool_named_and_recovers_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, own = project(tmp_path, own_pin=False)
    marker = _marker_path(root, monkeypatch)
    _tool_wrote(marker, run_path=str(own), directive="finish FR-08", context_anchor="orchestration.py")
    _checkpoints(own, "cache fix committed; next: review", _LITERAL)

    _compact(root)

    after = json.loads(marker.read_text())
    assert after["run_path"].rstrip("/") == str(own), "the hook dropped the run the tool had named"
    assert after["ownership"] == "marker"
    assert (after["directive"], after["context_anchor"]) == ("finish FR-08", "orchestration.py")
    assert after["last_checkpoint"] == "cache fix committed; next: review"
    assert after["trigger"] == "auto", "non-vacuity: this is the hook's document, not the tool's left untouched"

    replay = _hook(root, "session-start.sh", {"source": "compact", "session_id": SESSION_ID})
    assert "RECOVERED: Run at" in replay and OWN_RUN_ID in replay
    assert FOREIGN_RUN_ID not in replay


def test_a_pinned_run_wins_over_whatever_the_marker_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, own = project(tmp_path, own_pin=True)
    foreign = root / ".trw" / "runs" / "foreign-task" / FOREIGN_RUN_ID
    marker = _marker_path(root, monkeypatch)
    _tool_wrote(marker, run_path=str(foreign), directive="keep me")

    _compact(root)

    after = json.loads(marker.read_text())
    assert after["run_path"].rstrip("/") == str(own) and after["ownership"] == "owned"
    assert after["directive"] == "keep me"


@pytest.mark.parametrize(
    "kind",
    [
        "outside the project",
        "not a run",
        "a link out of the project",
        "dot-dot",
        "a control character",
        "a trailing newline on a real run",
    ],
)
def test_a_named_path_that_is_not_a_run_of_this_project_is_not_recovered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    root, own = project(tmp_path, own_pin=False)
    outside = tmp_path / "elsewhere" / "run"
    (outside / "meta").mkdir(parents=True)
    (outside / "meta" / "run.yaml").write_text("task: foreign\nphase: deliver\n", encoding="utf-8")
    if kind == "outside the project":
        named = outside
    elif kind == "not a run":
        named = root / "src"
        named.mkdir()
    elif kind == "a link out of the project":
        named = root / ".trw" / "runs" / "linked"
        named.symlink_to(outside, target_is_directory=True)
    elif kind == "dot-dot":
        named = Path(f"{own}/../../../../../elsewhere/run")
    elif kind == "a control character":
        named_text = f"{own}\x07"
    else:
        # The shell drops trailing newlines from a command substitution: this name must not pass as the run itself.
        named_text = f"{own}\n"
    if kind not in {"a control character", "a trailing newline on a real run"}:
        named_text = str(named)
    marker = _marker_path(root, monkeypatch)
    _tool_wrote(marker, run_path=named_text, directive="still kept")

    _compact(root)

    after = json.loads(marker.read_text())
    assert (after["run_path"], after["ownership"], after["phase"]) == ("", "unowned", "")
    assert after["directive"] == "still kept"


def test_the_project_wide_marker_carries_nothing_over(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With no session identity the file is shared, so what it holds may be another session's (PRD-FIX-118)."""
    root, own = project(tmp_path, own_pin=False)
    shared = _marker_path(root, monkeypatch, identified=False)
    assert shared.name == "pre_compact_state.json"
    _tool_wrote(shared, run_path=str(own), directive="another session's", context_anchor="theirs.py")

    _compact(root, identified=False)

    after = json.loads(shared.read_text())
    assert (after["run_path"], after["ownership"]) == ("", "unowned")
    assert (after["directive"], after["context_anchor"]) == ("", "")


def test_only_fixed_text_lines_leave_the_fixed_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, own = project(tmp_path, own_pin=True)
    marker = _marker_path(root, monkeypatch)
    _checkpoints(own, _LITERAL, _LITERAL)

    _compact(root)

    assert json.loads(marker.read_text())["last_checkpoint"] == _LITERAL


@pytest.mark.parametrize("ownership", ["owned", "unowned", "marker"])
def test_an_earlier_snapshot_of_the_hook_itself_is_never_revived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ownership: str
) -> None:
    """Review, 2026-10-11: a run this hook once derived from a pin must not come back from its own old snapshot
    after the pin is gone. Only a document the tool wrote (no ``ownership`` key) is a handoff."""
    root, own = project(tmp_path, own_pin=False)
    marker = _marker_path(root, monkeypatch)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "ts": "2026-10-10T20:00:00Z",
                "timestamp": "2026-10-10T20:00:00+00:00",
                "run_path": f"{own}/",
                "ownership": ownership,
                "session_key": SESSION_ID,
                "directive": "from the last compaction",
            }
        )
    )

    _compact(root)

    after = json.loads(marker.read_text())
    assert (after["run_path"], after["ownership"], after["directive"]) == ("", "unowned", "")
    replay = _hook(root, "session-start.sh", {"source": "compact", "session_id": SESSION_ID})
    assert "RECOVERED: Run at" not in replay


def test_a_payload_that_names_another_session_carries_nothing_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review, 2026-10-11: the marker file is named from the environment and the pin from the payload. When the two
    name different sessions, what the file holds is not this compaction's handoff."""
    root, own = project(tmp_path, own_pin=False)
    marker = _marker_path(root, monkeypatch)
    _tool_wrote(marker, run_path=str(own), directive="for the session in the environment")

    _hook(root, "pre-compact.sh", {"session_id": "99999999-8888-7777-6666-000000000000", "trigger": "auto"})

    after = json.loads(marker.read_text())
    assert (after["run_path"], after["directive"]) == ("", "")


def test_the_handoff_survives_without_jq(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_no_shell_json_parser import _jq_free_env

    root, own = project(tmp_path, own_pin=False)
    marker = _marker_path(root, monkeypatch)
    _tool_wrote(marker, run_path=str(own), directive="finish FR-08", context_anchor="orchestration.py")

    _hook(root, "pre-compact.sh", {"session_id": SESSION_ID, "trigger": "auto"}, env=_jq_free_env(root, tmp_path))

    after = json.loads(marker.read_text())
    assert after["run_path"].rstrip("/") == str(own) and after["ownership"] == "marker"
    assert (after["directive"], after["context_anchor"]) == ("finish FR-08", "orchestration.py")


def test_pending_decisions_come_from_the_same_line_as_the_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, own = project(tmp_path, own_pin=True)
    marker = _marker_path(root, monkeypatch)
    lines = [
        json.dumps({"message": "waiting on the operator", "pending_decisions": "ship or hold?"}),
        json.dumps({"message": _LITERAL}),
    ]
    (own / "meta" / "checkpoints.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    _compact(root)

    after = json.loads(marker.read_text())
    assert (after["last_checkpoint"], after["pending_decisions"]) == ("waiting on the operator", "ship or hold?")

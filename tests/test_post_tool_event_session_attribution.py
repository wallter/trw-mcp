"""Session/run attribution regressions for the PostToolUse edit hook."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def _hook_project(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)

    source_hooks = Path(__file__).parents[1] / "src" / "trw_mcp" / "data" / "hooks"
    hook_dir = project / ".claude" / "hooks"
    hook_dir.mkdir(parents=True)
    for name in ("post-tool-event.sh", "lib-trw.sh"):
        shutil.copy2(source_hooks / name, hook_dir / name)

    run_a = project / ".trw" / "runs" / "task-a" / "20260711T100000Z-a"
    run_b = project / ".trw" / "runs" / "task-b" / "20260711T110000Z-b"
    for run in (run_a, run_b):
        (run / "meta").mkdir(parents=True)
        (run / "meta" / "run.yaml").write_text(f"run_id: {run.name}\n", encoding="utf-8")
        (run / "meta" / "events.jsonl").touch()
    pins_path = project / ".trw" / "runtime" / "pins.json"
    pins_path.parent.mkdir(parents=True)
    return project, hook_dir / "post-tool-event.sh", run_a, run_b


def _run_hook(project: Path, hook: Path, *, env_session_id: str | None) -> None:
    env = dict(os.environ)
    if env_session_id is None:
        env.pop("TRW_SESSION_ID", None)
    else:
        env["TRW_SESSION_ID"] = env_session_id

    payload = json.dumps(
        {
            "session_id": "transport-x",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(project / "src" / "changed.py")},
        }
    )
    subprocess.run(
        ["sh", str(hook)],
        cwd=project,
        input=payload,
        text=True,
        env=env,
        check=True,
    )


def _events(run: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in (run / "meta" / "events.jsonl").read_text().splitlines()]


def test_hook_prefers_stable_session_and_writes_to_its_pinned_run(tmp_path: Path) -> None:
    project, hook, run_a, run_b = _hook_project(tmp_path)
    pins_path = project / ".trw" / "runtime" / "pins.json"
    pins_path.write_text(json.dumps({"stable-a": {"run_path": str(run_a)}}), encoding="utf-8")

    _run_hook(project, hook, env_session_id="stable-a")

    events_a = _events(run_a)
    assert len(events_a) == 1
    assert events_a[0]["session_id"] == "stable-a"
    assert events_a[0]["host_session_id"] == "transport-x"
    assert (run_b / "meta" / "events.jsonl").read_text() == ""


def test_hook_without_stable_id_emits_unscoped_countable_event(tmp_path: Path) -> None:
    project, hook, run_a, run_b = _hook_project(tmp_path)
    pins_path = project / ".trw" / "runtime" / "pins.json"
    pins_path.write_text(json.dumps({"transport-x": {"run_path": str(run_a)}}), encoding="utf-8")

    _run_hook(project, hook, env_session_id=None)

    events_a = _events(run_a)
    assert len(events_a) == 1
    assert events_a[0]["session_id"] == ""
    assert events_a[0]["host_session_id"] == "transport-x"
    assert (run_b / "meta" / "events.jsonl").read_text() == ""


def test_identified_session_without_pin_does_not_pollute_newest_run(tmp_path: Path) -> None:
    project, hook, run_a, run_b = _hook_project(tmp_path)

    _run_hook(project, hook, env_session_id="stable-a")

    assert _events(run_a) == []
    assert _events(run_b) == []


def test_host_session_without_pin_does_not_pollute_newest_run(tmp_path: Path) -> None:
    project, hook, run_a, run_b = _hook_project(tmp_path)

    _run_hook(project, hook, env_session_id=None)

    assert _events(run_a) == []
    assert _events(run_b) == []


def test_in_project_non_run_pin_is_rejected(tmp_path: Path) -> None:
    project, hook, run_a, run_b = _hook_project(tmp_path)
    fake_run = project / "src" / "package"
    (fake_run / "meta").mkdir(parents=True)
    (fake_run / "meta" / "run.yaml").write_text("run_id: package\n", encoding="utf-8")
    (fake_run / "meta" / "events.jsonl").touch()
    pins_path = project / ".trw" / "runtime" / "pins.json"
    pins_path.write_text(json.dumps({"stable-a": {"run_path": str(fake_run)}}), encoding="utf-8")

    _run_hook(project, hook, env_session_id="stable-a")

    assert (fake_run / "meta" / "events.jsonl").read_text() == ""
    assert _events(run_a) == []
    assert _events(run_b) == []

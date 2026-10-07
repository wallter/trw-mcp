"""Shared fixtures for the UserPromptSubmit auto-recall hook tests.

Both ``test_user_prompt_submit_hook.py`` (payload, phase and distribution
contract) and ``test_auto_recall_scoring.py`` (PRD-FIX-124 scoring, tunables and
deadline) drive the SAME real hook script as a subprocess. The fixture builders
live here so neither test module has to import the other, and so there is
exactly one definition of what a fixture project looks like.

Nothing in this module reimplements any part of the hook: it lays out a project,
runs the shipped script, and reads back what it did. The one stand-in is the
STORE: the hook's filtered read (``python -m trw_mcp.state._auto_recall_hook``,
PRD-CORE-333 FR03) reaches the checkout's daemon, so ``TRW_PYTHON`` points the
hook at a wrapper that runs that real module -- scorer, diagnostic, emission --
with ``read_rows`` answered from a fixture file of stored rows. What the real
store read filters is proven against a real backend in
``tests/test_no_direct_entries_read.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
#: Every live copy of the hook / settings file (a formerly-vendored third
#: mirror was deleted wholesale in `a77650f238`; only these two remain).
#:
#: Deliberately NOT filtered with `if path.exists()`: every copy listed here
#: MUST be present, and skipping absent ones would turn a deleted hook into a
#: silent pass. If a third distribution copy is reintroduced, add it here.
_HOOK_PATHS = (
    _ROOT.parent / ".claude" / "hooks" / "user-prompt-submit.sh",
    _ROOT / "src" / "trw_mcp" / "data" / "hooks" / "user-prompt-submit.sh",
)
_LIB_PATHS = (
    _ROOT.parent / ".claude" / "hooks" / "lib-trw.sh",
    _ROOT / "src" / "trw_mcp" / "data" / "hooks" / "lib-trw.sh",
)
_SETTINGS_PATHS = (
    _ROOT.parent / ".claude" / "settings.json",
    _ROOT / "src" / "trw_mcp" / "data" / "settings.json",
)

#: A learning that a normal engineering prompt in these tests will match.
_MATCHING_SUMMARY = "Structlog event keyword gotcha"
_MATCHING_PROMPT = "structlog event keyword"


@dataclass(frozen=True)
class HookRun:
    """One real-subprocess hook invocation, plus the project it ran against."""

    stdout: str
    stderr: str
    returncode: int
    project_root: Path


def _copy_hook_to_temp(
    tmp_path: Path,
    source_hook: Path,
) -> tuple[Path, Path, Path]:
    source_path = source_hook.as_posix()
    hook_label = "bundled" if "/src/trw_mcp/data/hooks/" in source_path else "dev"
    project_root = tmp_path / hook_label
    hooks_dir = project_root / ".claude" / "hooks"
    context_dir = project_root / ".trw" / "context"

    hooks_dir.mkdir(parents=True, exist_ok=True)
    context_dir.mkdir(parents=True, exist_ok=True)
    rows_file = project_root / ".trw" / "fixture-store-rows.json"
    rows_file.write_text("[]", encoding="utf-8")

    hook_path = hooks_dir / "user-prompt-submit.sh"
    hook_path.write_text(source_hook.read_text(encoding="utf-8"), encoding="utf-8")
    hook_path.chmod(0o755)

    # The shipped library beside the hook (its JSON reader included), with the
    # phase ladder and the log sink replaced so these tests observe the hook's
    # OWN decisions. A hook a test rewrote into a staging directory has no
    # library beside it and runs the bundled one. The fourth `detail` field is the
    # PRD-FIX-124-FR05 diagnostic; log_hook_execution's real four-argument
    # contract is asserted separately against the shipped lib-trw.sh.
    shipped_lib = source_hook.parent / "lib-trw.sh"
    if not shipped_lib.is_file():
        shipped_lib = _LIB_PATHS[-1]
    lib_hook = hooks_dir / "lib-trw.sh"
    lib_hook.write_text(
        f"""#!/bin/sh
. "{shipped_lib}"
init_hook_timer() {{ :; }}
infer_phase() {{ printf '%s' "${{TRW_TEST_PHASE:-implement}}"; }}
get_repo_root() {{ printf '%s' "$TRW_PROJECT_ROOT"; }}
log_hook_execution() {{ printf '%s|%s|%s|%s\\n' "$1" "$2" "$3" "$4" >> "$TRW_HOOK_LOG"; }}
""",
        encoding="utf-8",
    )
    return project_root, hook_path, rows_file


def _write_learning(
    rows_file: Path,
    learning_id: str,
    *,
    status: str,
    summary: str,
    tags: list[str] | None = None,
) -> None:
    """Add one stored learning to the fixture store, in the order the store returns rows."""
    _write_learnings(rows_file, [{"learning_id": learning_id, "status": status, "summary": summary, "tags": tags}])


def _write_learnings(rows_file: Path, learnings: list[dict[str, Any]]) -> None:
    """Add stored learnings to the fixture store in one write, in the order the store returns them."""
    rows = json.loads(rows_file.read_text(encoding="utf-8"))
    rows.extend(
        {"id": row["learning_id"], "status": row["status"], "summary": row["summary"], "tags": row.get("tags") or []}
        for row in learnings
    )
    rows_file.write_text(json.dumps(rows), encoding="utf-8")


#: The fixture store: the REAL hook module, with ``read_rows`` answered from the rows file.
#: No rows file stands for an unreachable store. TRW_TEST_RECALL_TIMEOUT_NS forces the
#: FR08 scan deadline, the only way to observe a mid-scan expiry deterministically.
#: TRW_TEST_READ_ROWS_SLEEP stalls the store read, to meet the in-process hook deadline.
_FIXTURE_STORE = """
import json, os, sys, time
from pathlib import Path
from trw_mcp.state import _auto_recall_hook as hook
from trw_mcp.state._store_selection import StoreUnavailableError
if os.environ.get("TRW_TEST_RECALL_TIMEOUT_NS"):
    hook.TIMEOUT_NS = int(os.environ["TRW_TEST_RECALL_TIMEOUT_NS"])
def read_rows(root, cap):
    time.sleep(float(os.environ.get("TRW_TEST_READ_ROWS_SLEEP", "0")))
    path = root / ".trw" / "fixture-store-rows.json"
    if not path.is_file():
        raise StoreUnavailableError("the fixture has no store")
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [hook.Candidate(r["id"], r["status"], r["summary"], tuple(r["tags"])) for r in rows][:cap]
sys.exit(hook.main(sys.argv[1:], read_rows=read_rows))
"""


def fixture_store_python(tmp_path: Path) -> Path:
    """An executable standing in for the project interpreter, serving the fixture store.

    The hook starts the recall module as ``python -c <boot> <module> <args...>``: the boot code arms the
    in-process deadline watchdog and then runs ``<module>``. The stand-in keeps that boot code and swaps in
    the fixture module, so the watchdog under test is the shipped one.
    """
    module_dir = tmp_path / "fixture-store-module"
    module_dir.mkdir(exist_ok=True)
    (module_dir / "fixture_store_main.py").write_text(_FIXTURE_STORE, encoding="utf-8")
    wrapper = tmp_path / "fixture-store-python"
    wrapper.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "-c" ]; then code=$2; shift 3; PYTHONPATH="{module_dir}${{PYTHONPATH:+:$PYTHONPATH}}" '
        f'exec "{sys.executable}" -c "$code" fixture_store_main "$@"; fi\n'
        f'[ "$1" = "-m" ] && shift 2\n'
        f'exec "{sys.executable}" -c \'{_FIXTURE_STORE}\' "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


def _make_path_without_jq(tmp_path: Path) -> str:
    bin_dir = tmp_path / "bin-no-jq"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for tool_name in (
        "sh",
        "python3",
        "grep",
        "head",
        "sed",
        "tr",
        "cat",
        "dirname",
        "mkdir",
        "rm",
        "mktemp",
        "sleep",
        "date",
    ):
        tool_path = shutil.which(tool_name)
        assert tool_path is not None, f"Required tool missing in test environment: {tool_name}"
        (bin_dir / tool_name).symlink_to(tool_path)
    return str(bin_dir)


def _run_hook(
    tmp_path: Path,
    source_hook: Path,
    *,
    prompt: str,
    phase: str,
    cached_phase: str | None = None,
    env_overrides: dict[str, str] | None = None,
    raw_input: str | None = None,
    learnings: list[dict[str, Any]] | None = None,
    config_yaml: str | None = None,
    path_override: str | None = None,
) -> HookRun:
    """Drive the REAL hook script against a fixture project (FR10)."""
    project_root, hook_path, rows_file = _copy_hook_to_temp(tmp_path, source_hook)
    if cached_phase is not None:
        (project_root / ".trw" / "context" / "last_ups_phase").write_text(cached_phase, encoding="utf-8")
    _write_learnings(rows_file, learnings or [])
    if config_yaml is not None:
        (project_root / ".trw" / "config.yaml").write_text(config_yaml, encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "TRW_PROJECT_ROOT": str(project_root),
            "TRW_TEST_PHASE": phase,
            "TRW_HOOK_LOG": str(project_root / "hook.log"),
            "TRW_PYTHON": str(fixture_store_python(project_root)),
        }
    )
    if path_override is not None:
        env["PATH"] = path_override
    if env_overrides:
        env.update(env_overrides)

    completed = subprocess.run(
        ["sh", str(hook_path)],
        input=raw_input if raw_input is not None else json.dumps({"prompt": prompt}),
        text=True,
        capture_output=True,
        cwd=project_root,
        env=env,
        check=False,
    )
    return HookRun(completed.stdout, completed.stderr, completed.returncode, project_root)


def _hook_log(run: HookRun) -> str:
    return (run.project_root / "hook.log").read_text(encoding="utf-8")


def diagnostic(project_root: Path) -> dict[str, str]:
    """Parse the single FR05 ``event=AutoRecall`` record out of the stub hook log.

    Asserts there is exactly one: "one record per prompt" is the requirement, so
    a helper that silently took the last of several would hide a regression.
    """
    records = [
        line.split("|", 3)[3]
        for line in (project_root / "hook.log").read_text(encoding="utf-8").splitlines()
        if line.count("|") >= 3 and line.split("|", 3)[3].startswith("event=AutoRecall")
    ]
    assert len(records) == 1, f"expected exactly one AutoRecall record, got {records}"
    return dict(token.split("=", 1) for token in records[0].split() if "=" in token)

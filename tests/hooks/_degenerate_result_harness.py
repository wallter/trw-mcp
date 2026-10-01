"""Shared harness for the PRD-CORE-250-FR06 degenerate-result adapter tests.

Both test modules drive the REAL shipped hook over ``sh`` with a JSON payload on
stdin. Nothing here re-implements the adapter's logic: the helpers build fixture
projects, build payloads, and read the two sanctioned advisory channels back.

Used by ``test_degenerate_result_adapter.py`` (FR06 + FR10 behaviour) and
``test_degenerate_result_nfrs.py`` (NFR01-NFR06). Split because one module
carrying both crossed the 350 effective-LOC gate.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parents[2] / "src" / "trw_mcp" / "data" / "hooks"
_SRC = Path(__file__).resolve().parents[2] / "src" / "trw_mcp"
_ADAPTER = _HOOKS / "post-tool-degenerate-result.sh"

_ADVISORY_KEY = "additionalContext"
_MARKER = "more lines]"

pytest_skip_no_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")
pytest_skip_no_jq = pytest.mark.skipif(shutil.which("jq") is None, reason="jq unavailable")

# A garbage/absent deadline tunable falls back to the PRODUCTION default (50 ms,
# the same NFR01 budget `test_p95_latency_under_budget` measures), so any test
# that exercises the FALLBACK is itself wall-clock-bound, not just the latency
# test. Confirmed 2026-09-04: `test_a_non_numeric_config_value_falls_back_to_
# the_default` and `test_dash_executes_the_adapter_end_to_end` both failed under
# a `make test-release` run with concurrent box load, passing 3/3 serially.
#: The real ``date``, resolved once from the runner's PATH, for everything but the deadline probe.
_REAL_DATE = shutil.which("date") or "/bin/date"
#: What the frozen clock answers to the hook's ``date +%s%N`` deadline probe.
_FROZEN_NS = "1700000000000000000"


def frozen_clock_path(root: Path) -> str:
    """A directory whose ``date`` answers the hook's deadline probe with one constant instant.

    The adapter bounds itself with ``date +%s%N`` (NFR01, clamped to at most 500 ms). Under a loaded
    release gate (``pytest -n 8`` beside other suites) a cold ``sh``+``jq`` run overshoots that and the
    hook goes silent -- the right product behaviour, and the release-gate flake
    (DEGENERATE-HOOK-TEST-FLAKES). Behaviour tests therefore run on a frozen clock: elapsed time is always
    0, so the outcome no longer depends on machine load. Every other ``date`` call passes through. The
    deadline itself is tested on the real clock (``real_clock=True``), and its latency budget only under
    ``requires_local_timing``.
    """
    shim = root / ".test-clock"
    shim.mkdir(exist_ok=True)
    date = shim / "date"
    date.write_text(
        f'#!/bin/sh\nif [ "$1" = "+%s%N" ]; then echo {_FROZEN_NS}; exit 0; fi\nexec {shlex.quote(_REAL_DATE)} "$@"\n',
        encoding="utf-8",
    )
    date.chmod(0o755)
    return str(shim)


def _project(tmp_path: Path, name: str = "proj") -> Path:
    root = tmp_path / name
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / ".trw" / "context").mkdir(parents=True)
    for hook in (_ADAPTER.name, "lib-trw.sh"):
        shutil.copy2(_HOOKS / hook, root / ".claude" / "hooks" / hook)
    return root


def _payload(response: object, *, tool: str = "Read", command: str | None = None, session: str = "s-1") -> str:
    body: dict[str, object] = {
        "session_id": session,
        "tool_name": tool,
        "tool_response": response,
        "hook_event_name": "PostToolUse",
    }
    if command is not None:
        body["tool_input"] = {"command": command}
    return json.dumps(body)


def _run(
    root: Path,
    payload: str,
    *,
    env: dict[str, str] | None = None,
    real_clock: bool = False,
) -> subprocess.CompletedProcess[str]:
    child = dict(os.environ)
    child["CLAUDE_PROJECT_DIR"] = str(root)
    child.pop("HOOKS_ENABLED", None)
    child.pop("TRW_HOOKS_ENABLED", None)
    child.pop("TRW_SESSION_ID", None)
    # The adapter enforces a 50 ms self-deadline and exits silently past it
    # (NFR01). That is correct in production and a flake source here: a loaded
    # test machine blows 50 ms and an assertion about CLASSIFICATION comes back
    # as an assertion about scheduling. Tests that measure or configure the
    # deadline pass their own value; everything else gets headroom.
    child.setdefault("TRW_DEGENERATE_RESULT_DEADLINE_MS", "5000")
    child.update(env or {})
    if not real_clock:
        child["PATH"] = frozen_clock_path(root) + os.pathsep + child.get("PATH", "")
    return subprocess.run(
        ["sh", str(root / ".claude" / "hooks" / _ADAPTER.name)],
        input=payload,
        text=True,
        capture_output=True,
        cwd=root,
        env=child,
        timeout=30,
        check=False,
    )


def _advisories(result: subprocess.CompletedProcess[str]) -> list[str]:
    """Advisory lines on EITHER sanctioned channel — stdout JSON or stderr."""
    found: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            document = json.loads(line)
        except ValueError:
            continue
        block = document.get("hookSpecificOutput", {})
        if block.get("hookEventName") == "PostToolUse" and block.get(_ADVISORY_KEY):
            found.append(str(block[_ADVISORY_KEY]))
    found += [line for line in result.stderr.splitlines() if "could not look" in line]
    return found

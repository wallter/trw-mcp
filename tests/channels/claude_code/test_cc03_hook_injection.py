"""The CC-03 hook must not execute a model-controlled ``file_path``.

``pre-tool-distill-hint.sh`` built its ``python -c`` program as a DOUBLE-quoted
shell string and spliced ``${_file_path}`` into it::

    result = compute_before_edit_hint(file_path='${_file_path}')

``file_path`` arrives from the PreToolUse payload — i.e. from whatever the model
asked to edit. A payload naming::

    x.py'+__import__("os").system("...")+'.py

closed the Python literal and ran arbitrary code as the developer, with the hook
then printing its ordinary beacon and exiting 0. A PreToolUse hook needs no tool
approval, so this bypassed the harness permission prompt entirely.

Two properties made it durable:

* **P10 — hardened once, copied N times.** Three sibling hooks pass the identical
  field through the environment into a SINGLE-quoted program, and
  ``git_hooks/trw-post-commit.sh`` states the invariant outright: *"reaches Python
  through the ENVIRONMENT, never through source interpolation — a repo path
  containing quotes or newlines must not be able to inject code into the -c
  program."* Only the Claude Code hook interpolated.
* **P7 — prose outranking the code.** ``TRW_CC04_FILE_PATH`` was already exported
  for this very subprocess, and a comment six lines below the injection site read
  *"Untrusted hook fields arrive via the environment, never interpolated into
  source"*. That was true of the exception handler it described and false of the
  program around it.

The fix makes the mistake unrepeatable rather than merely absent: the program is
single-quoted, so no ``$`` can be expanded, and reintroducing interpolation would
require visibly changing the quoting.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HOOK = (
    Path(__file__).parent.parent.parent.parent
    / "src"
    / "trw_mcp"
    / "data"
    / "claude_code"
    / "hooks"
    / "pre-tool-distill-hint.sh"
)


def _prepare_project(tmp_path: Path) -> None:
    """Enable CC-03 and point it at an interpreter that can import trw_mcp.

    Without a resolvable interpreter the hook short-circuits to the beacon and
    the test would pass without ever reaching the program under test.
    """
    channels = tmp_path / ".trw" / "channels"
    channels.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".trw" / "config.yaml").write_text("cc03_hook_enabled: true\n", encoding="utf-8")
    (channels / "cc03-python.txt").write_text(sys.executable, encoding="utf-8")


def _run(payload_file_path: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    payload = json.dumps(
        {
            "tool_use_id": "toolu-injection-001",
            "tool_name": "Edit",
            "tool_input": {"file_path": payload_file_path},
        }
    )
    return subprocess.run(
        ["sh", str(_HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "TRW_PROJECT_DIR": str(tmp_path),
        },
    )


def test_the_hook_actually_launches_a_python_subprocess(tmp_path: Path) -> None:
    """Non-vacuity control, and it must come first.

    If the hook exits early — gate off, interpreter unresolvable, extension
    skipped — the injection test below passes while proving nothing.

    Stdout is NOT the signal to use here, tempting as it looks: the timeout
    fallback at the end of the pipeline prints the same ``[TRW]`` beacon the
    program prints, so asserting on it cannot tell "ran" from "never started".
    The correlation record can: it is written by a *separate* subprocess using
    the same interpreter resolution, so its existence proves the gate passed and
    a Python actually launched.
    """
    _prepare_project(tmp_path)

    result = _run("src/module.py", tmp_path)

    record = tmp_path / ".trw" / "context" / "cc03-hints" / "toolu-injection-001.json"
    assert record.exists(), f"no CC-04 record — the hook never reached Python: {result.stdout!r}"
    assert result.returncode == 0


@pytest.mark.parametrize(
    "template",
    [
        # Close the single-quoted literal the old program used.
        'x.py\'+__import__("pathlib").Path("{marker}").write_text("pwned")+\'.py',
        # Same shape via a double-quoted literal, in case the quoting is flipped
        # rather than removed — a fix that only swapped quote characters would
        # pass the first case and fail this one.
        'x.py"+__import__("pathlib").Path("{marker}").write_text("pwned")+".py',
    ],
    ids=["single-quote-break", "double-quote-break"],
)
def test_a_malicious_file_path_cannot_execute_code(tmp_path: Path, template: str) -> None:
    """The finding, reproduced. RED before the fix: the marker file was created."""
    _prepare_project(tmp_path)
    marker = tmp_path / "PWNED"

    result = _run(template.format(marker=marker), tmp_path)

    assert not marker.exists(), (
        "the CC-03 hook executed model-controlled input from tool_input.file_path — "
        f"arbitrary code execution. stdout={result.stdout!r}"
    )
    # The hook must still be well-behaved on the way out: FR26 says it never
    # exits non-zero, and a crash here would be its own regression.
    assert result.returncode == 0


def test_an_apostrophe_in_a_real_filename_does_not_break_the_hook(tmp_path: Path) -> None:
    """The benign half of the same defect — but read what this does and does not prove.

    Interpolating into source made ``don't.py`` a *compile-time* SyntaxError, so
    the program's own ``except`` handler never ran, no ``exception_fallback``
    record was written, and the failure was mislabelled as a timeout by the very
    correlation record that exists to stop mislabelling timeouts.

    **This test is an FR26 regression guard, not a detector for that.** Measured
    on both trees, the recorded ``distill_status`` is ``timeout_fallback`` before
    AND after the fix, because on an embeddings-bearing install the 2.5s budget
    expires on its own — a separate, still-open finding about the budget, not
    about quoting. Asserting on the status here would look like a discriminating
    check while actually pinning the timeout bug. So this asserts only the
    contract that genuinely holds: a legitimate apostrophe must not make the hook
    fail or go silent.
    """
    _prepare_project(tmp_path)

    result = _run("src/don't.py", tmp_path)

    assert result.returncode == 0
    assert "[TRW]" in result.stdout, "a legitimate filename with an apostrophe broke the hook"

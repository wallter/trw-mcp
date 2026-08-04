"""The before-edit hint hooks must fit their PreToolUse budget.

Every bundled edit-hint hook spawns a FRESH interpreter per edit and bounds it
with ``timeout 2.5``. Measured on a warm dev box (7 runs each, seconds):

===========================================================  =============
phase                                                        seconds
===========================================================  =============
bare interpreter start                                       0.06
``import trw_mcp.tools.before_edit_hint``                    1.07 - 1.15
``resolve_current_sidecar`` (the T2 read the hook exists for) 0.003
embedding cold start in a fresh process                      14.48
``compute_before_edit_hint``, embeddings ON                  14.2 - 14.6
``compute_before_edit_hint``, embeddings OFF                 0.68 - 1.04
===========================================================  =============

The embedding cold start (torch 1.76 + sentence-transformers 5.95 + MiniLM load
6.56 + first encode 0.22) is paid in full on EVERY edit because the process is
new every time. Against a 2.5s budget that made ``timeout_fallback`` the only
reachable outcome on this machine — so ``sidecar_missing`` ("we looked, there is
nothing"), ``hint_available`` ("we looked and found something") and
``timeout_fallback`` ("we never finished looking") were indistinguishable in the
CC-04 record. That collapse is the defect; the budget number is not.

Raising the budget is not available: it is capped by the 3000ms registered hook
timeout (NFR06), and covering a 14.5s model load would add ~15s of latency to
every edit. So the work shrinks instead — lexical recall keeps T1 alive at
0.44s first query / 0.03s after, and the long-lived MCP server is untouched
(there the model is warm and a query costs 0.23s).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "trw_mcp" / "data"

#: The knob under test. A misspelling here is silent — pydantic-settings ignores
#: unknown env vars — which is exactly why a test asserts it flips the config.
_ENV_ASSIGNMENT = "TRW_EMBEDDINGS_ENABLED=false"


def _bounded_hint_hooks() -> list[Path]:
    """Every bundled hook that runs ``compute_before_edit_hint`` under a timeout.

    Discovered, never enumerated. A hand-written list of three would be correct
    the day it is written and silently wrong when a fourth client adapter copies
    the pattern — which is how this hook shape reached three clients with the
    same defect in the first place.
    """
    return sorted(
        path
        for path in _DATA_DIR.rglob("*.sh")
        if "compute_before_edit_hint" in (body := path.read_text(encoding="utf-8"))
        and re.search(r"\btimeout\s+[\d.]+\s", body)
    )


def test_discovery_finds_every_known_hook() -> None:
    """Non-vacuity control for the discovery above.

    A predicate that matched nothing would make every parametrized case below
    vacuously pass. Assert the three known client adapters are found.
    """
    names = {path.name for path in _bounded_hint_hooks()}

    # Named, not counted. A `len(names) >= 3` sat here and `make census-check`
    # rejected it: a literal standing in for the cardinality of a population that
    # grows elsewhere. The set assertion below is strictly stronger anyway — it
    # fails if discovery returns three of the WRONG hooks, which a count cannot.
    assert names >= {
        "pre-tool-distill-hint.sh",  # claude-code CC-03
        "trw-before-edit-hint.sh",  # cursor CUR-06
        "trw-copilot-distill-hint.sh",  # copilot C5
    }


def _env_prefix_of_bounded_call(body: str) -> list[str]:
    """The backslash-continued env prefix attached to the ``timeout ... "$_py"`` call.

    Reading the whole file would let the assignment satisfy the check from a
    comment or an unrelated block. Only assignments on the continuation lines
    immediately above the bounded invocation actually reach that subprocess.
    """
    lines = body.splitlines()
    index = next(i for i, line in enumerate(lines) if re.search(r'\btimeout\s+[\d.]+\s+"\$_py"', line))
    prefix: list[str] = []
    cursor = index - 1
    while cursor >= 0 and lines[cursor].rstrip().endswith("\\"):
        prefix.append(lines[cursor].strip().rstrip("\\").strip())
        cursor -= 1
    return prefix


@pytest.mark.parametrize("hook", _bounded_hint_hooks(), ids=lambda p: p.name)
def test_bounded_hook_disables_embeddings(hook: Path) -> None:
    """No hook may load an embeddings model inside its PreToolUse budget."""
    prefix = _env_prefix_of_bounded_call(hook.read_text(encoding="utf-8"))

    assert _ENV_ASSIGNMENT in prefix, (
        f"{hook.name} bounds compute_before_edit_hint with a timeout but does not "
        f"set {_ENV_ASSIGNMENT} on that invocation; a fresh interpreter pays a "
        f"measured 14.48s embedding cold start, so the hook can only ever time "
        f"out. Env prefix found: {prefix}"
    )


@pytest.mark.parametrize("hook", _bounded_hint_hooks(), ids=lambda p: p.name)
def test_bounded_hook_parses_under_posix_sh(hook: Path) -> None:
    """The env addition must not break POSIX sh parsing (NFR08)."""
    completed = subprocess.run(["sh", "-n", str(hook)], capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr


def test_env_var_actually_reaches_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hooks' knob must be a real, consumed config field.

    ``TRW_EMBEDDINGS_ENABLED`` is set by three shell files that cannot fail
    loudly: pydantic-settings ignores an unknown env var, so a typo would leave
    every hook timing out with nothing to show for the fix.
    """
    from trw_mcp.models.config import _reset_config, get_config

    monkeypatch.delenv("TRW_EMBEDDINGS_ENABLED", raising=False)
    _reset_config()
    assert get_config().embeddings_enabled is True, "control: default must be enabled"

    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
    _reset_config()
    assert get_config().embeddings_enabled is False

    _reset_config()


@pytest.mark.slow
def test_hint_path_loads_no_embedding_model_when_disabled(tmp_path: Path) -> None:
    """Behavioural proof, in a real subprocess, that torch stays out.

    The string assertions above prove the hooks *say* the right thing. This
    proves the saying has the effect: a fresh interpreter running the hook's
    exact call must finish without importing torch or sentence-transformers,
    whose combined cold start is 14.48s of a 2.5s budget.
    """
    program = (
        "import os, sys\n"
        "from trw_mcp.tools.before_edit_hint import compute_before_edit_hint\n"
        "r = compute_before_edit_hint(file_path='a.py', repo_root=os.environ['REPO'])\n"
        "print(r.distill_status, 'torch' in sys.modules, 'sentence_transformers' in sys.modules)\n"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "REPO": str(tmp_path),
        "TRW_PROJECT_DIR": str(tmp_path),
        "TRW_EMBEDDINGS_ENABLED": "false",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr[-2000:]
    status, torch_loaded, st_loaded = completed.stdout.strip().split()
    # Non-vacuity: the call must actually have RUN and produced a real status,
    # not failed early in a way that trivially avoids importing torch.
    assert status in {"sidecar_missing", "no_git_sha", "tier_required", "no_repo_root"}
    assert torch_loaded == "False"
    assert st_loaded == "False"

"""PRD-CORE-250-FR07 — the FR06 adapter has a manual equivalent in the canon.

``FRAMEWORK.md`` requires that *every adapter MUST have a tool/manual
equivalent*. Shipping ``post-tool-degenerate-result.sh`` without one would make
the framework's own rule false in the release that adds it — and would leave
every harness that does not run TRW's hooks with no way to apply the check.

The text lives in the hand-edited ``framework.md`` and reaches the mirrors
through ``scripts/check-aaref-sync.py --fix``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests._layout import requires_monorepo

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Canon mirrors live in the monorepo only; the public package repo has nothing to compare.
pytestmark = [pytest.mark.unit, requires_monorepo]
_SOURCE = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "framework.md"

#: The phrase the advisory itself uses, so the adapter and the manual equivalent
#: cannot describe two different checks.
_PHRASE = "could not look"

#: Every tracked projection of the combined canon, plus the reference module the
#: span is compiled into. A mirror that missed the regeneration is the exact
#: drift `check-aaref-sync.py` exists to catch, asserted here too so a change to
#: this text fails on its own terms rather than on a generic parity error.
_MIRRORS = (
    "FRAMEWORK.md",
    ".trw/frameworks/FRAMEWORK.md",
    "trw-mcp/FRAMEWORK.md",
)


def test_manual_equivalent_present_in_all_mirrors() -> None:
    source = _SOURCE.read_text(encoding="utf-8")
    assert _PHRASE in source, "the manual equivalent is missing from the authoring source"

    for relative in _MIRRORS:
        path = _REPO_ROOT / relative
        assert path.is_file(), f"tracked mirror {relative} is missing"
        assert _PHRASE in path.read_text(encoding="utf-8"), (
            f"{relative} did not receive the manual equivalent — regenerate with "
            "python3 scripts/check-aaref-sync.py --fix"
        )


def test_the_manual_equivalent_names_all_three_shapes() -> None:
    """A pointer is not an equivalent: all three rules must be checkable by hand."""
    source = _SOURCE.read_text(encoding="utf-8")
    section = source.split("DEGENERATE-RESULT CHECK", 1)[1].split("\n## ", 1)[0]
    for shape in ("Empty", "Truncated", "Undated"):
        assert shape in section, f"the manual equivalent does not cover the {shape.lower()} shape"
    assert "not measured" in section, "the manual equivalent omits the record-as-not-measured obligation"
    assert "post-tool-degenerate-result.sh" in section, "the manual equivalent does not name the adapter it mirrors"


def test_the_generated_canon_parity_gate_agrees() -> None:
    """FR07's acceptance command, run rather than asserted about."""
    result = subprocess.run(
        # sys.executable, not a bare "python3": the gate imports trw_mcp, which
        # imports trw_memory.storage, and both are editable installs in THIS
        # interpreter's environment. A bare name resolves through PATH, where a
        # foreign interpreter (homebrew's python3, or any venv a shell profile
        # prepends) answers with ModuleNotFoundError and the gate looks broken.
        [sys.executable, "scripts/check-aaref-sync.py"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

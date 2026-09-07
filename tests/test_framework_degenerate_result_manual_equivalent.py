"""PRD-CORE-250-FR07 — the FR06 adapter has a manual equivalent in the canon.

``FRAMEWORK-CORE.md`` requires that *every adapter MUST have a tool/manual
equivalent*. Shipping ``post-tool-degenerate-result.sh`` without one would make
the framework's own rule false in the release that adds it — and would leave
every harness that does not run TRW's hooks with no way to apply the check.

The text lives in the hand-editable ``framework.source.md`` and reaches the
mirrors through ``scripts/compile-framework-canons.py``. It is a REFERENCE-class
span, not a core one, and that is a measured decision rather than a preference:
the compact core is budgeted at ``max_core_ratio`` 0.7 of the combined baseline
and sat at 0.6945 before this change, leaving 281 bytes of headroom — an
operational three-step checklist does not belong in a kernel that tight.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests._layout import requires_monorepo

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Canon mirrors live in the monorepo only; the public package repo has nothing to compare.
pytestmark = [pytest.mark.unit, requires_monorepo]
_SOURCE = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "framework.source.md"

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
    "trw-mcp/src/trw_mcp/data/framework.md",
    "trw-mcp/src/trw_mcp/data/framework-reference.md",
)


def test_manual_equivalent_present_in_all_mirrors() -> None:
    source = _SOURCE.read_text(encoding="utf-8")
    assert _PHRASE in source, "the manual equivalent is missing from the authoring source"

    for relative in _MIRRORS:
        path = _REPO_ROOT / relative
        assert path.is_file(), f"tracked mirror {relative} is missing"
        assert _PHRASE in path.read_text(encoding="utf-8"), (
            f"{relative} did not receive the manual equivalent — regenerate with "
            "python3 scripts/compile-framework-canons.py --write && python3 scripts/check-aaref-sync.py --fix"
        )


def test_the_manual_equivalent_names_all_three_shapes() -> None:
    """A pointer is not an equivalent: all three rules must be checkable by hand."""
    source = _SOURCE.read_text(encoding="utf-8")
    section = source.split("DEGENERATE-RESULT CHECK", 1)[1].split("<!-- trw:span", 1)[0]
    for shape in ("Empty", "Truncated", "Undated"):
        assert shape in section, f"the manual equivalent does not cover the {shape.lower()} shape"
    assert "not measured" in section, "the manual equivalent omits the record-as-not-measured obligation"
    assert "post-tool-degenerate-result.sh" in section, "the manual equivalent does not name the adapter it mirrors"


def test_the_span_is_reference_class_so_the_core_budget_holds() -> None:
    """The core ratio gate is real: 0.6945 at HEAD against a 0.7 ceiling.

    A ``dest=core`` span here measured 0.712 and the compiler refused to write.
    Pinning the destination stops a later edit from silently reintroducing that.
    """
    source = _SOURCE.read_text(encoding="utf-8")
    assert "<!-- trw:span id=fw-degenerate-result-check dest=reference class=example -->" in source


def test_the_generated_canon_parity_gate_agrees() -> None:
    """FR07's acceptance command, run rather than asserted about."""
    result = subprocess.run(
        ["python3", "scripts/check-aaref-sync.py"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

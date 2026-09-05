"""PRD-CORE-247-FR06: the framework canon states what RIGID means offline.

The compiled core is asserted, never the authoring source: an edit to
``framework.source.md`` that was never compiled would leave every consumer
reading the previous generation, and the compiled file is what
``.trw/frameworks/FRAMEWORK-CORE.md`` is deployed from.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_REPO = _ROOT.parent

if not (_REPO / "scripts").is_dir():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )

_SOURCE = _ROOT / "src" / "trw_mcp" / "data" / "framework.source.md"
_CORE = _ROOT / "src" / "trw_mcp" / "data" / "framework-core.md"
_SECTION_HEADING = "## WHEN THE TRANSPORT IS DOWN"


def test_compiled_core_carries_the_transport_down_section() -> None:
    """FR06 acceptance: the section is in the compiled core, and it says three things."""
    core = _CORE.read_text(encoding="utf-8")
    assert _SECTION_HEADING in core, "the transport-down section is missing from the compiled core"

    body = core.split(_SECTION_HEADING, 1)[1].split("\n## ", 1)[0]

    # 1. The obligation persists and transfers.
    assert "RIGID is an obligation, not a tool call" in body
    assert "does not lapse" in body
    assert "trw-mcp local" in body
    assert "reports/" in body, "the build-check substitute must name where the evidence is written"

    # 2. The gate does not weaken.
    assert "gate_evaluated: false" in body
    assert "not a fourth path" in body

    # 3. Offline writes are marked and reported.
    assert "source_identity=local_cli" in body
    assert "trw-reconcile-pending" in body
    assert "trw_session_start" in body


def test_the_section_is_authored_as_a_marked_span_in_the_source() -> None:
    """FR06: authored with the marker syntax, so the compiler owns the placement.

    A hand-edit to the compiled file would be reverted by the next ``--write``
    and would hard-fail ``check_generation`` against the frozen baseline digest,
    with neither failure naming the real cause.
    """
    source = _SOURCE.read_text(encoding="utf-8")
    assert "<!-- trw:span id=fw-transport-down dest=core class=normative -->" in source
    assert _SECTION_HEADING in source


def test_compile_check_reports_no_drift() -> None:
    """FR06 acceptance: ``compile-framework-canons.py --check`` exits 0.

    Guards the whole chain the source edit had to clear: the frozen baseline
    digest was re-frozen, the compact core stayed under ``max_core_ratio``, every
    required normative anchor survived, and the generated outputs on disk match a
    fresh compile.
    """
    result = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "compile-framework-canons.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=_REPO,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_the_deliver_gate_is_stated_in_full_exactly_once_in_the_core() -> None:
    """FR09 acceptance for the framework carrier.

    The three-path gate was stated in full twice in the compiled core. The
    EXECUTION MODEL SUMMARY statement is the one that stays; the copy inside the
    rigid-tool list is now a pointer to it. Exactly once, never zero.
    """
    core = _CORE.read_text(encoding="utf-8")
    # The discriminating substring is the THIRD path: only a full enumeration of
    # the gate names it. "acceptable-failure record" is a poor predicate — the
    # machine-enforcement paragraph legitimately refers to "the structured
    # acceptable-failure record above", which is a pointer, not a restatement.
    full_statements = core.count("(3) an authorized operator")
    assert "Deliver gate (no fourth path)" in core, "the carrier must never end up gate-less"
    assert full_statements == 1, (
        f"the three-path gate is stated in full {full_statements} times in the compiled core; "
        "the invariant is exactly one full statement per carrier, pointers elsewhere"
    )
    # The rigid-tool list keeps a pointer, so a reader who lands there is not
    # left without the gate — collapsing to zero statements is the failure mode
    # this half of the assertion exists for.
    rigid = core.split("## RIGID / FLEXIBLE TOOL CLASSIFICATION", 1)[1].split("\n## ", 1)[0]
    assert "Deliver gate (no fourth path)" in rigid, "the rigid list must point at the single statement"
    assert "(3) an authorized operator" not in rigid, "the rigid list must not restate the gate in full"

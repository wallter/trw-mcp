"""doctor checks cursor-ide's only carrier, .cursor/rules/trw-ceremony.mdc (DoD-5 DOCTOR-CURSOR-IDE-CARRIER-UNSCANNED).

The instruction-gate scan excluded the .mdc on the premise that the gate reached cursor
through AGENTS.md, but a cursor-ide install writes no AGENTS.md. A cursor-ide project whose
carrier lost the deliver gate therefore never failed doctor. These tests render a real
cursor-ide install into ``tmp_path`` and run the doctor report over it.
"""

from __future__ import annotations

from pathlib import Path

_MDC = ".cursor/rules/trw-ceremony.mdc"


def _install(tmp_path: Path) -> Path:
    from trw_mcp.bootstrap import init_project

    (tmp_path / ".git").mkdir()
    result = init_project(tmp_path, ide="cursor-ide")
    assert not result["errors"], result["errors"]
    assert not (tmp_path / "AGENTS.md").exists(), "precondition: cursor-ide writes no AGENTS.md"
    return tmp_path / _MDC


def test_the_installed_mdc_carrier_is_scanned_and_passes(tmp_path: Path) -> None:
    from trw_mcp.server._doctor_instruction_gate import instruction_gate_report

    _install(tmp_path)

    status, message = instruction_gate_report(tmp_path)

    assert status == "PASS", message
    assert _MDC in message


def test_a_whole_file_carrier_reduced_to_a_pointer_fails(tmp_path: Path) -> None:
    """Review r1 P2: a pointer-only .mdc must not pass as a single-source pointer."""
    from trw_mcp.server._doctor_instruction_gate import instruction_gate_report

    mdc = _install(tmp_path)
    mdc.write_text("@missing.md\n", encoding="utf-8")

    status, message = instruction_gate_report(tmp_path)

    assert status == "FAIL"
    assert _MDC in message


def test_a_carrier_that_lost_the_deliver_gate_fails(tmp_path: Path) -> None:
    from trw_mcp.server._doctor_instruction_gate import instruction_gate_report
    from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

    mdc = _install(tmp_path)
    mdc.write_text(mdc.read_text(encoding="utf-8").replace(DELIVER_GATE_PHRASE, "(removed)"), encoding="utf-8")

    status, message = instruction_gate_report(tmp_path)

    assert status == "FAIL"
    assert _MDC in message

"""``trw-mcp doctor`` instruction-surface + deliver-gate helpers (PRD-QUAL-106 FR-07).

Belongs to the ``_subcommands_doctor.py`` facade: it owns the heavy lifting for
the ``instruction_surface`` diagnostic check so the parent file stays under the
350 effective-LOC gate. ``_subcommands_doctor._check_instruction_gate`` is the
thin ``CheckResult``-wrapping caller that wires it into the doctor catalogue.

Exposes plain ``(status, message)`` tuples rather than ``CheckResult`` objects
so this module never needs to import back from the parent facade — the same
shape used by the ``_doctor_framework_integrity`` / ``_doctor_stubs`` siblings.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.server._subcommands_uninstall_config import _MANAGED_BLOCK_MARKERS as _BLOCK_MARKERS
from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

# Instruction surfaces deliberately NOT scanned for the deliver-gate statement.
# An exclusion set is the mechanism, not documentation: without one, a surface
# added to the canonical registry is silently skipped here and the omission is
# indistinguishable from "nothing to check" (wiring-defect pattern P11).
_GATE_SCAN_EXCLUSIONS: dict[str, str] = {
    # Cursor rule files carry frontmatter-scoped guidance, not the ceremony block;
    # the deliver gate reaches cursor clients through AGENTS.md.
    ".cursor/rules/trw-ceremony.mdc": "rule file, not a ceremony-block carrier",
}


def _instruction_surfaces() -> tuple[str, ...]:
    """Derive the scanned instruction surfaces from the canonical registry.

    This list used to be hand-maintained here and had drifted: it named five
    files while the registry carried six, so ``ANTIGRAVITY.md`` was never
    inspected and an antigravity-cli project with a broken instruction surface
    reported PASS. Deriving removes the drift by construction — a client added to
    the registry is scanned here, or it appears in ``_GATE_SCAN_EXCLUSIONS`` with
    a stated reason. Nothing can be omitted silently.
    """
    from trw_mcp.client_profiles.catalog import (
        _ROOT_INSTRUCTION_SURFACES,
        _generated_instruction_relpaths,
    )

    relpaths = {relpath for _flag, relpath in _ROOT_INSTRUCTION_SURFACES}
    relpaths |= set(_generated_instruction_relpaths())
    return tuple(sorted(relpaths - set(_GATE_SCAN_EXCLUSIONS)))


def _extract_trw_block(content: str) -> str | None:
    for start_marker, end_marker in _BLOCK_MARKERS:
        start = content.find(start_marker)
        end = content.find(end_marker)
        if start != -1 and end != -1 and end > start:
            return content[start + len(start_marker) : end]
    return None


def instruction_gate_report(target: Path) -> tuple[str, str]:
    """Report deliver-gate-phrase presence across every scanned instruction surface."""
    from trw_mcp.state.claude_md._instruction_carrier import (
        InstructionFileClass,
        classify_instruction_file,
    )

    present: list[str] = []
    missing_gate: list[str] = []
    pointers: list[str] = []  # PRD-CORE-203 FR07: single-source pointers left un-clobbered
    for rel in _instruction_surfaces():
        path = target / rel
        if not path.is_file():
            continue
        present.append(rel)
        # PRD-CORE-203 FR07: a single-source pointer (e.g. CLAUDE.md == @AGENTS.md)
        # correctly carries no inline TRW block — report it as un-clobbered and skip
        # the deliver-gate assertion (the gate lives in the pointed-to file).
        classification = classify_instruction_file(path)
        if classification.kind is InstructionFileClass.POINTER:
            targets = ", ".join(classification.import_targets)
            pointers.append(f"{rel} -> {targets}" if targets else rel)
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            missing_gate.append(rel)
            continue
        block = _extract_trw_block(content)
        if block is None:
            continue  # no TRW-managed block in this surface — nothing to assert.
        # An ``@`` import is not followed: the gate must be stated inline, so a
        # surface still importing a retired ``.trw`` sidecar fails here.
        if DELIVER_GATE_PHRASE not in block:
            missing_gate.append(rel)

    if not present:
        return "WARN", "no TRW instruction surface present yet (run 'trw-mcp init-project .')."
    pointer_note = (
        f" {len(pointers)} single-source pointer(s) left un-clobbered: {'; '.join(pointers)}." if pointers else ""
    )
    if missing_gate:
        return (
            "FAIL",
            f"deliver-gate statement absent from TRW block in: {', '.join(missing_gate)}.{pointer_note}",
        )
    return (
        "PASS",
        f"deliver-gate statement present in {len(present)} surface(s): {', '.join(present)}.{pointer_note}",
    )

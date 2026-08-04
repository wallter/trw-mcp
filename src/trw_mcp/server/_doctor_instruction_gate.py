"""``trw-mcp doctor`` instruction-surface + deliver-gate helpers (PRD-QUAL-106 FR-07).

Belongs to the ``_subcommands_doctor.py`` facade: it owns the heavy lifting for
the ``instruction_carrier`` and ``instruction_surface`` diagnostic checks so the
parent file stays under the 350 effective-LOC gate.
``_subcommands_doctor._check_instruction_carrier_state`` /
``_check_instruction_gate`` are the thin ``CheckResult``-wrapping callers that
wire these into the doctor catalogue.

Exposes plain ``(status, message)`` tuples rather than ``CheckResult`` objects
so this module never needs to import back from the parent facade — the same
shape used by the ``_doctor_framework_integrity`` / ``_doctor_stubs`` siblings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

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


def _resolve_block_imports(path: Path, block: str) -> str:
    """Return *block* with any ``@<relpath>`` import directives resolved inline.

    Under the PRD-CORE-203 IMPORT carrier the marker region holds a single
    ``@.trw/INSTRUCTIONS.md`` line and the ceremony text lives in the sidecar.
    Asserting the deliver-gate phrase against the raw block therefore reported
    FAIL for a *correctly* externalized project — the shipped default for
    claude-code, since ``instruction_externalize`` defaults to ``auto``. Doctor
    told users their instruction surface was broken precisely when it was right.

    The existing POINTER exemption above cannot cover this: it fires only when
    the WHOLE file is import directives, and a real CLAUDE.md carries user prose
    (CONTENT). This resolves imports found *inside the block* instead.

    Single-hop and relative to the containing file's directory, matching Claude
    Code's documented semantics. An unresolvable import contributes nothing, so a
    dangling reference still fails the gate — which is the correct outcome.
    """
    resolved = [block]
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("@") or len(stripped.split()) != 1:
            continue
        candidate = (path.parent / stripped[1:]).resolve()
        try:
            candidate.relative_to(path.parent.resolve())
        except ValueError:
            continue  # never follow an import escaping the project directory
        if candidate.is_file():
            try:
                resolved.append(candidate.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
    return "\n".join(resolved)


CarrierState = Literal["migrated", "legacy_inline", "absent"]


def classify_carrier_state(path: Path) -> CarrierState:
    """Classify one instruction file as migrated / legacy_inline / absent.

    PRD-CORE-240-FR07. Detection is a read of the LIVE FILE, never a state file.
    Three installer state files are explicitly unfit as an "already migrated"
    flag and none is opened here: ``.trw/installer-meta.yaml`` is documented as
    history-only and "never a current runtime authority"
    (``server/_version_status_layers.py``, PRD-INFRA-164 D-26);
    ``.trw/installed-version.json`` is a reload nudge; ``.trw/managed-artifacts.yaml``
    tracks bundled-artifact content hashes, which is the wrong shape. Keying off
    any of them would report a project as migrated because an installer once
    said so, rather than because its file actually carries an include.

    - ``absent``        — no TRW-managed block in the file (or no file).
    - ``migrated``      — the block's only substantive line is an ``@`` import.
    - ``legacy_inline`` — anything else, INCLUDING a malformed region. A
      half-written block is reported as legacy, never as migrated: "we could not
      tell" must not resolve to the reassuring answer.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "absent"

    block = _extract_trw_block(content)
    if block is None:
        return "absent"

    substantive = [line.strip() for line in block.splitlines() if line.strip() and not line.strip().startswith("<!--")]
    if substantive and all(ln.startswith("@") and len(ln.split()) == 1 for ln in substantive):
        return "migrated"
    return "legacy_inline"


def carrier_state_report(target: Path) -> tuple[str, str]:
    """Report per-surface carrier state so a stale install is visible (FR07).

    Advisory by design: an inline block is correct for the clients that cannot
    resolve an include, so ``legacy_inline`` is reported, never failed. What this
    check exists to prevent is the opposite of a false alarm — a project silently
    sitting on injected text with nothing surfacing that it could be converted.
    """
    states: dict[str, CarrierState] = {}
    for rel in _instruction_surfaces():
        path = target / rel
        if not path.is_file():
            continue
        states[rel] = classify_carrier_state(path)

    if not states:
        return "SKIP", "no instruction surface present yet."

    migrated = sorted(r for r, st in states.items() if st == "migrated")
    legacy = sorted(r for r, st in states.items() if st == "legacy_inline")
    absent = sorted(r for r, st in states.items() if st == "absent")

    parts: list[str] = []
    if migrated:
        parts.append(f"{len(migrated)} referencing ({', '.join(migrated)})")
    if legacy:
        parts.append(f"{len(legacy)} inline ({', '.join(legacy)})")
    if absent:
        parts.append(f"{len(absent)} with no TRW block ({', '.join(absent)})")
    detail = "; ".join(parts)

    if legacy and not migrated:
        return (
            "WARN",
            f"{detail}. Run 'trw-mcp update-project .' to convert any surface whose client "
            "supports an in-file include.",
        )
    return "PASS", detail + "."


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
        if DELIVER_GATE_PHRASE not in _resolve_block_imports(path, block):
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

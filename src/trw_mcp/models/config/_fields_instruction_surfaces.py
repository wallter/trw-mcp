"""Instruction-surface hygiene fields (PRD-QUAL-104 FR01).

Separate domain mixin so the size-gate field does not push the
``_fields_ceremony.py`` mixin over the 200-raw-line guard
(``test_domain_mixin_files_under_200_lines``) and stays out of
``_fields_build.py`` (owned by another lane).
"""

from __future__ import annotations

from typing import Literal


class _InstructionSurfaceFields:
    """Instruction-surface hygiene domain mixin — mixed into _TRWConfigFields via MI."""

    # PRD-QUAL-104 FR01: size/density gate mode for the TRW auto-generated
    # instruction block. ``None`` is the unset sentinel — the brownfield
    # resolver (``_agents_md_size_gate.resolve_instruction_size_gate_mode``)
    # then derives the effective mode from the truth table (no config -> block;
    # explicit max_auto_lines -> warn; config without max_auto_lines -> block).
    # An explicit ``warn``/``block`` here always wins over the resolved default.
    instruction_size_gate_mode: Literal["warn", "block"] | None = None

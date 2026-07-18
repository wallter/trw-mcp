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

    # PRD-CORE-203 FR02: externalization of the TRW auto-generated block.
    # Instead of inlining the full block into a client instruction file, write
    # it to a sidecar under ``.trw/`` and place a single ``@<sidecar>`` import
    # directive in the file's marker region — for clients whose profile declares
    # ``instruction_import_syntax == "at_path"`` (Claude Code). This keeps tracked
    # instruction files short and moves the artifact back into ``.trw/``.
    #   ``off``  -> always inline (legacy behaviour; byte-identical to pre-203).
    #   ``auto`` -> externalize for import-capable clients (default).
    #   ``on``   -> force externalization wherever the client can import.
    # ``auto`` and ``on`` behave identically until import-incapable externalization
    # (opencode instructions[] / codex model_instructions_file) lands in a future PRD.
    instruction_externalize: Literal["off", "auto", "on"] = "auto"

    # PRD-CORE-203 FR02: sidecar path (repo-root-relative) that holds the
    # externalized TRW block. Overridable via ``TRW_INSTRUCTION_EXTERNAL_FILENAME``
    # or ``.trw/config.yaml`` — never hardcoded in the write path.
    instruction_external_filename: str = ".trw/INSTRUCTIONS.md"

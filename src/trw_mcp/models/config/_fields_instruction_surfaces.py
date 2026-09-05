"""Instruction-surface hygiene fields (PRD-QUAL-104 FR01).

Separate domain mixin so the size-gate field does not push the
``_fields_ceremony.py`` mixin over the 200-raw-line guard
(``test_domain_mixin_files_under_200_lines``) and stays out of
``_fields_build.py`` (owned by another lane).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field


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

    # PRD-FIX-123-FR02: total-shrink floor. A candidate whose TOTAL byte count
    # falls below ``(1 - fraction)`` times the current file's total is refused
    # without an explicit ``force`` argument. This is the SECONDARY floor — the
    # primary one is measured on the NON-generated region, because the incident
    # that motivated the fix GREW the file (2790 -> 10403 bytes) while
    # destroying 128 hand-written lines, so a total floor alone cannot see it.
    instruction_write_max_total_shrink_fraction: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description=(
            "Largest fraction of an instruction file's total bytes a guarded write "
            "may remove before it is refused without force (PRD-FIX-123-FR02)."
        ),
    )

    # PRD-FIX-123-FR04: how many pre-write copies of one instruction filename are
    # retained under ``instruction_backup_dir``. Oldest copies are pruned first.
    instruction_backup_retention: int = Field(
        default=10,
        ge=1,
        le=1000,
        description=(
            "Pre-write backup copies retained per instruction filename "
            "(PRD-FIX-123-FR04). Out-of-range values fail validation, never clamp."
        ),
    )

    # PRD-FIX-123-FR04: project-root-relative directory holding pre-write copies
    # of every instruction file TRW writes. Refused (fail-closed) when it
    # resolves outside the project root — the containment rule already applied to
    # the PRD-CORE-203 sidecar.
    instruction_backup_dir: str = ".trw/backups/instructions"

    # PRD-FIX-123-FR03/NFR03: upper bound on the unified diff returned by a
    # ``dry_run`` sync, so a caller cannot force an unbounded payload through the
    # MCP transport. Bounding the DIFF is never bounding a FILE.
    instruction_dry_run_diff_max_lines: int = Field(
        default=400,
        ge=1,
        le=100000,
        description=(
            "Maximum unified-diff lines returned per target by a dry-run instruction "
            "sync (PRD-FIX-123-FR03). Exceeding it truncates the DIFF, never a file."
        ),
    )

    # PRD-FIX-123-FR02: slack allowed when deciding whether a total-size drop is
    # explained by the change in size of TRW's OWN marker span. The merge
    # normalises its own separators (it rstrips above the block and lstrips
    # newlines below it), so a drop can legitimately exceed the block delta by a
    # few bytes. Larger than separator churn but far smaller than a lost content
    # line, so an unexplained shrink still reaches the floor.
    instruction_write_block_delta_tolerance_bytes: int = Field(
        default=64,
        ge=0,
        le=65536,
        description=(
            "Bytes of slack when testing whether a total-size drop is explained by the "
            "TRW block's own size change (PRD-FIX-123-FR02)."
        ),
    )

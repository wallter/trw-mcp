"""Instruction-surface size/density gate (PRD-QUAL-104 FR01).

Belongs to the ``_agents_md.py`` render path. Re-exported there for
back-compat and so existing tests can keep patching on ``_agents_md``.

Holds the size-gate logic extracted out of ``_agents_md.py`` (former
lines 291-296 warn site) plus the brownfield-mode resolver and the
block/abort enforcement that FR01 adds. Keeping it here keeps the parent
facade under the 350 effective-LOC gate.

Brownfield default truth table (binding — PRD §FR01):

  (a) no ``.trw/config.yaml``                       -> ``block``
  (b) config sets ``max_auto_lines`` explicitly     -> ``warn``
  (c) config present but no ``max_auto_lines``      -> ``block``

In every row an explicit ``instruction_size_gate_mode`` in config wins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import structlog
from typing_extensions import TypedDict

logger = structlog.get_logger(__name__)

SizeGateMode = Literal["warn", "block"]


class InstructionSurfaceOversizedError(TypedDict):
    """Structured error returned when an oversized surface is blocked."""

    error_code: Literal["instruction_surface_oversized"]
    file: str
    lines: int
    limit: int


def _config_has_explicit_max_auto_lines(project_root: Path) -> bool | None:
    """Return whether ``.trw/config.yaml`` sets ``max_auto_lines`` explicitly.

    Returns:
        - ``None`` when no ``.trw/config.yaml`` is present (truth-table case a).
        - ``True``  when the file sets ``max_auto_lines`` (case b).
        - ``False`` when the file exists but omits ``max_auto_lines`` (case c).

    The raw YAML is read directly (not via the merged ``TRWConfig``) because
    the merged config cannot distinguish an explicitly-set value from the
    field default — and the truth table keys on explicitness.
    """
    config_path = project_root / ".trw" / "config.yaml"
    if not config_path.exists():
        return None

    try:
        from trw_mcp.state.persistence import FileStateReader

        data = FileStateReader().read_yaml(config_path)
    except Exception:  # justified: fail-open — unreadable config -> treat as case c (strict default)
        logger.debug("instruction_size_gate_config_read_failed", path=str(config_path), exc_info=True)
        return False

    if not isinstance(data, dict):
        return False
    return "max_auto_lines" in data


def resolve_instruction_size_gate_mode(
    project_root: Path,
    configured_mode: SizeGateMode | None,
) -> SizeGateMode:
    """Resolve the effective size-gate mode from the brownfield truth table.

    Args:
        project_root: Project root used to locate ``.trw/config.yaml``.
        configured_mode: The explicit ``instruction_size_gate_mode`` from
            config when the operator set it, else ``None``. An explicit value
            always wins over the resolved brownfield default.

    Returns:
        ``"warn"`` or ``"block"``.
    """
    if configured_mode is not None:
        return configured_mode

    has_explicit = _config_has_explicit_max_auto_lines(project_root)
    # case (b): brownfield project already tuned sizing -> permissive warn.
    if has_explicit is True:
        return "warn"
    # cases (a) no config and (c) config without max_auto_lines -> strict block.
    return "block"


def enforce_size_gate(
    file_label: str,
    lines: int,
    limit: int,
    mode: SizeGateMode,
) -> InstructionSurfaceOversizedError | None:
    """Apply the size gate to a rendered TRW section.

    Promotes the former warn-only check (``_agents_md.py:291-296``) to a
    configurable gate. When ``lines`` is within ``limit`` no action is taken.
    When oversized:

    - ``warn`` mode logs ``agents_md_section_oversized`` and returns ``None``
      (no regression for brownfield — generation proceeds).
    - ``block`` mode logs and returns the structured oversized error so the
      caller can abort the write.

    Returns:
        ``None`` when generation may proceed, else the structured error dict.
    """
    if lines <= limit:
        return None

    if mode == "warn":
        logger.warning("agents_md_section_oversized", file=file_label, lines=lines, limit=limit)
        return None

    logger.warning(
        "instruction_surface_oversized",
        file=file_label,
        lines=lines,
        limit=limit,
        mode=mode,
    )
    return {
        "error_code": "instruction_surface_oversized",
        "file": file_label,
        "lines": lines,
        "limit": limit,
    }

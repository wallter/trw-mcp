"""Framework overlay loading and assembly (PRD-CORE-017).

Provides functions to load the shared core, phase overlays,
and assemble a complete framework document for a given phase.
Falls back to monolithic FRAMEWORK.md if overlays are missing.
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger()

_CORE_FILENAME = "trw-core.md"
_MONOLITHIC_FILENAME = "FRAMEWORK.md"
_OVERLAY_SEPARATOR = "\n\n---\n\n"


def _read_if_exists(path: Path) -> str | None:
    """Read a file's text content, returning None if the file does not exist."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def load_core(trw_dir: Path) -> str | None:
    """Load the shared core framework document.

    Args:
        trw_dir: Path to the .trw directory.

    Returns:
        Core document text, or None if not found.
    """
    return _read_if_exists(trw_dir / "frameworks" / _CORE_FILENAME)


def load_overlay(trw_dir: Path, phase: str) -> str | None:
    """Load a phase-specific overlay document.

    Args:
        trw_dir: Path to the .trw directory.
        phase: Phase name (research, plan, implement, validate, review, deliver).

    Returns:
        Overlay document text, or None if not found.
    """
    return _read_if_exists(trw_dir / "frameworks" / "overlays" / f"trw-{phase}.md")


def assemble_framework(trw_dir: Path, phase: str) -> str:
    """Assemble a complete framework document for a given phase.

    Concatenates the shared core with the phase-specific overlay.
    Falls back to the monolithic FRAMEWORK.md if overlays are missing.

    Args:
        trw_dir: Path to the .trw directory.
        phase: Phase name (research, plan, implement, validate, review, deliver).

    Returns:
        Complete framework document text.

    Raises:
        FileNotFoundError: If neither overlays nor monolithic framework exist.
    """
    core = load_core(trw_dir)

    if core is not None:
        overlay = load_overlay(trw_dir, phase)
        if overlay is None:
            logger.debug("framework_core_only", phase=phase)
            return core
        logger.debug(
            "framework_assembled",
            phase=phase,
            core_lines=core.count("\n"),
            overlay_lines=overlay.count("\n"),
        )
        return core + _OVERLAY_SEPARATOR + overlay

    monolithic = _read_if_exists(trw_dir / "frameworks" / _MONOLITHIC_FILENAME)
    if monolithic is not None:
        logger.debug("framework_monolithic_fallback", phase=phase)
        return monolithic

    msg = f"No framework found in {trw_dir / 'frameworks'}"
    raise FileNotFoundError(msg)

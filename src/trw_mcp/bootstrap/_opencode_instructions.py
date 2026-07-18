"""OpenCode + Codex per-client INSTRUCTIONS.md generators.

Belongs to the ``_opencode.py`` facade. Re-exported there for back-compat.

Three publicly-imported helpers:
- ``detect_model_family``  — model.lower() → 'qwen' / 'gpt' / 'claude' / 'generic'
- ``generate_opencode_instructions`` — writes .opencode/INSTRUCTIONS.md
- ``generate_codex_instructions``    — writes .codex/INSTRUCTIONS.md

Extracted as DIST-243 batch 30 to keep the parent ``_opencode.py``
module under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.bootstrap._file_ops import _new_result

logger = structlog.get_logger(__name__)


def _generate_instructions_file(
    target_dir: Path,
    *,
    relative_path: Path,
    render_content: Callable[[], str],
    force: bool,
    manifest_hashes: dict[str, str] | None,
    log_event: str,
) -> dict[str, list[str]]:
    """Write one managed client instruction file without changing user content policy."""
    from trw_mcp.bootstrap._opencode import _is_user_modified

    result = _new_result()
    instructions_path = target_dir / relative_path
    rel_path = str(relative_path)
    try:
        instructions_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result["errors"].append(f"Failed to create directory {instructions_path.parent}: {exc}")
        return result

    content = render_content()
    existed = instructions_path.exists()
    if not force and _is_user_modified(instructions_path, rel_path, manifest_hashes):
        result["preserved"].append(rel_path)
        return result
    if existed and not force and instructions_path.read_text(encoding="utf-8").strip() == content.strip():
        result["preserved"].append(rel_path)
        return result

    try:
        instructions_path.write_text(content, encoding="utf-8")
        result["updated" if existed else "created"].append(rel_path)
    except OSError as exc:
        result["errors"].append(f"Failed to write {instructions_path}: {exc}")

    logger.debug(log_event, created=result["created"], updated=result["updated"])
    return result


def detect_model_family(opencode_json: Mapping[str, Any]) -> str:
    """Detect model family from opencode.json configuration.

    Reads the 'model' field from opencode.json and returns a model family
    identifier that can be used to select appropriate instruction content.

    Args:
        opencode_json: Parsed opencode.json configuration dict.

    Returns:
        Model family string: 'qwen', 'gpt', 'claude', or 'generic'.
    """
    model = opencode_json.get("model", "")
    if not model:
        return "generic"

    model_lower = model.lower()

    if "qwen" in model_lower:
        return "qwen"
    if "gpt" in model_lower or re.match(r"^o[13](?:$|[-_])", model_lower):
        return "gpt"
    if "claude" in model_lower:
        return "claude"
    return "generic"


def generate_opencode_instructions(
    target_dir: Path,
    model_family: str,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Generate or update .opencode/INSTRUCTIONS.md with model-specific content.

    Creates aper-client instruction file with content optimized for the detected
    model family (qwen, gpt, claude, or generic).

    Args:
        target_dir: Target directory for the INSTRUCTIONS.md file.
        model_family: One of 'qwen', 'gpt', 'claude', or 'generic'.
        force: If True, overwrite existing file.

    Returns:
        Dict with 'created', 'updated', 'preserved', 'errors' lists.
    """
    from trw_mcp.state.claude_md._static_sections import render_opencode_instructions

    return _generate_instructions_file(
        target_dir,
        relative_path=Path(".opencode") / "INSTRUCTIONS.md",
        render_content=lambda: render_opencode_instructions(model_family),
        force=force,
        manifest_hashes=manifest_hashes,
        log_event="generate_opencode_instructions",
    )


def generate_codex_instructions(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Generate or update .codex/INSTRUCTIONS.md with Codex-specific content.

    Creates a per-client instruction file with Codex-optimized workflow.

    Args:
        target_dir: Target directory for the INSTRUCTIONS.md file.
        force: If True, overwrite existing file.

    Returns:
        Dict with 'created', 'updated', 'preserved', 'errors' lists.
    """
    from trw_mcp.state.claude_md._static_sections import render_codex_instructions

    return _generate_instructions_file(
        target_dir,
        relative_path=Path(".codex") / "INSTRUCTIONS.md",
        render_content=render_codex_instructions,
        force=force,
        manifest_hashes=manifest_hashes,
        log_event="generate_codex_instructions",
    )

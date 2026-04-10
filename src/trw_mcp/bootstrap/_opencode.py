"""OpenCode-specific bootstrap configuration.

FR11: OpenCode Bootstrap Configuration (PRD-CORE-074)
FR16: opencode.json Smart Merge (PRD-CORE-074)
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.models.typed_dicts._opencode import (
    OpencodeConfig,
    OpencodeServerEntry,
    OpencodeTemplateDict,
)

from ._utils import _DATA_DIR

logger = structlog.get_logger(__name__)

_TRW_START_MARKER = "<!-- trw:start -->"
_TRW_END_MARKER = "<!-- trw:end -->"
_TRW_HEADER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"

_DEFAULT_PERMISSIONS: dict[str, str] = {
    "bash": "ask",
    "write": "ask",
    "edit": "ask",
}

_OPENCODE_DATA_DIR = _DATA_DIR / "opencode"
_OPENCODE_COMMANDS_DIR = _OPENCODE_DATA_DIR / "commands"
_OPENCODE_AGENTS_DIR = _OPENCODE_DATA_DIR / "agents"
_OPENCODE_SKILLS_DIR = _OPENCODE_DATA_DIR / "skills"
_OPENCODE_SKILLS_INVENTORY = _OPENCODE_DATA_DIR / "skills_inventory.yaml"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_trw_mcp_entry() -> OpencodeServerEntry:
    """Return the TRW MCP server entry for opencode.json.

    Uses local stdio transport (one trw-mcp process per instance).
    Falls back to absolute Python path if trw-mcp not on PATH.
    """
    if shutil.which("trw-mcp"):
        command: list[str] = ["trw-mcp", "--debug"]
    else:
        command = [sys.executable, "-m", "trw_mcp.server", "--debug"]

    return {
        "type": "local",
        "command": command,
        "enabled": True,
    }


def _parse_jsonc(content: str) -> OpencodeConfig:
    """Parse JSONC (JSON with comments) by stripping comments.

    Handles // line comments and /* block comments */.
    Returns parsed dict. Raises json.JSONDecodeError on invalid JSON.
    """
    # Remove block comments /* ... */ (including multi-line)
    content = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
    # Remove line comments // ... (but not inside strings)
    # Simple approach: remove // comments that are on their own segment
    # Uses a regex that skips strings
    result_parts: list[str] = []
    i = 0
    in_string = False
    escape_next = False
    while i < len(content):
        ch = content[i]
        if escape_next:
            result_parts.append(ch)
            escape_next = False
            i += 1
            continue
        if ch == "\\" and in_string:
            escape_next = True
            result_parts.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result_parts.append(ch)
            i += 1
            continue
        if not in_string and ch == "/" and i + 1 < len(content) and content[i + 1] == "/":
            # Skip to end of line
            while i < len(content) and content[i] != "\n":
                i += 1
            continue
        result_parts.append(ch)
        i += 1
    stripped = "".join(result_parts)
    result: OpencodeConfig = json.loads(stripped)
    return result


from ._file_ops import _new_result


def _is_user_modified(dest: Path, key: str, manifest_hashes: dict[str, str] | None) -> bool:
    if not manifest_hashes or key not in manifest_hashes or not dest.is_file():
        return False
    try:
        return manifest_hashes[key] != hashlib.sha256(dest.read_bytes()).hexdigest()
    except OSError:
        return False


def _copy_file(
    src: Path,
    dest: Path,
    rel_path: str,
    result: dict[str, list[str]],
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result["errors"].append(f"Failed to create directory {dest.parent}: {exc}")
        return

    if _is_user_modified(dest, rel_path, manifest_hashes):
        result["preserved"].append(rel_path)
        return

    try:
        existed = dest.exists()
        if existed and not force and dest.read_text(encoding="utf-8") == src.read_text(encoding="utf-8"):
            result["preserved"].append(rel_path)
            return
        shutil.copy2(src, dest)
        result["updated" if existed else "created"].append(rel_path)
    except OSError as exc:
        result["errors"].append(f"Failed to copy {src} -> {dest}: {exc}")


def _copy_markdown_dir(
    source_dir: Path,
    target_dir: Path,
    rel_root: str,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    result = _new_result()
    if not source_dir.is_dir():
        return result

    for src in sorted(source_dir.iterdir()):
        if src.suffix != ".md":
            continue
        rel_path = f"{rel_root}/{src.name}"
        _copy_file(src, target_dir / src.name, rel_path, result, force=force, manifest_hashes=manifest_hashes)
    return result


def load_opencode_skill_inventory(data_dir: Path | None = None) -> dict[str, dict[str, str]]:
    """Load the OpenCode skill compatibility inventory."""
    from ruamel.yaml import YAML

    inventory_path = (data_dir or _OPENCODE_DATA_DIR) / "skills_inventory.yaml"
    yaml = YAML(typ="safe")
    data = yaml.load(inventory_path.read_text(encoding="utf-8")) or {}
    raw_skills = data.get("skills", {})
    if not isinstance(raw_skills, dict):
        return {}
    skills: dict[str, dict[str, str]] = {}
    for name, config in raw_skills.items():
        if isinstance(name, str) and isinstance(config, dict):
            skills[name] = {str(k): str(v) for k, v in config.items()}
    return skills


def install_opencode_commands(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Install bundled OpenCode native commands into ``.opencode/commands``."""
    return _copy_markdown_dir(
        _OPENCODE_COMMANDS_DIR,
        target_dir / ".opencode" / "commands",
        ".opencode/commands",
        force=force,
        manifest_hashes=manifest_hashes,
    )


def install_opencode_agents(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Install bundled OpenCode specialist agents into ``.opencode/agents``."""
    return _copy_markdown_dir(
        _OPENCODE_AGENTS_DIR,
        target_dir / ".opencode" / "agents",
        ".opencode/agents",
        force=force,
        manifest_hashes=manifest_hashes,
    )


def install_opencode_skills(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
    data_dir: Path | None = None,
) -> dict[str, list[str]]:
    """Install the curated OpenCode skill subset into ``.opencode/skills``."""
    result = _new_result()
    base_dir = data_dir or _OPENCODE_DATA_DIR
    inventory = load_opencode_skill_inventory(base_dir)
    variant_root = base_dir / "skills"
    dest_root = target_dir / ".opencode" / "skills"

    for skill_name, cfg in sorted(inventory.items()):
        if cfg.get("disposition") == "exclude":
            continue
        skill_dir = variant_root / skill_name
        if not skill_dir.is_dir():
            result["errors"].append(f"Missing OpenCode skill variant for {skill_name}")
            continue
        try:
            dest_skill = dest_root / skill_name
            dest_skill.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result["errors"].append(f"Failed to create directory {dest_root / skill_name}: {exc}")
            continue
        for skill_file in sorted(skill_dir.iterdir()):
            if not skill_file.is_file():
                continue
            rel_path = f".opencode/skills/{skill_name}/{skill_file.name}"
            _copy_file(
                skill_file,
                dest_root / skill_name / skill_file.name,
                rel_path,
                result,
                force=force,
                manifest_hashes=manifest_hashes,
            )
    return result


# ---------------------------------------------------------------------------
# Smart merge (FR16)
# ---------------------------------------------------------------------------


def merge_opencode_json(
    existing: OpencodeConfig,
    trw_entry: OpencodeServerEntry,
) -> OpencodeConfig:
    """Smart merge TRW config into existing opencode.json (FR16).

    Rules:
    - Add/update "trw" entry under "mcp" without removing other servers.
    - Add "permission" defaults only if "permission" key doesn't exist.
    - NEVER overwrite user's "model", "small_model", "agent", "instructions".
    - Preserve all other keys.
    """
    # Start from existing config via unpacking — preserves all user keys
    # (model, small_model, agent, instructions) without dynamic key access.
    result: OpencodeConfig = {**existing}

    # Update "mcp" section: add/update "trw" key, preserve others
    mcp: dict[str, OpencodeServerEntry] = dict(result.get("mcp", {}))
    mcp["trw"] = trw_entry
    result["mcp"] = mcp

    # Add default permissions only when the key is absent
    if "permission" not in result:
        result["permission"] = dict(_DEFAULT_PERMISSIONS)

    return result


# ---------------------------------------------------------------------------
# opencode.json generation (FR11)
# ---------------------------------------------------------------------------


def generate_opencode_config(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate opencode.json with TRW MCP server config.

    If opencode.json already exists, performs smart merge (FR16).
    If not, writes full template.

    Returns dict with 'created', 'updated', 'preserved', 'errors' lists.
    """
    result: dict[str, list[str]] = {
        "created": [],
        "updated": [],
        "preserved": [],
        "errors": [],
    }
    config_path = target_dir / "opencode.json"
    trw_entry = _get_trw_mcp_entry()

    if config_path.exists() and not force:
        # Smart merge path (FR16)
        try:
            raw = config_path.read_text(encoding="utf-8")
            existing = _parse_jsonc(raw)
        except (json.JSONDecodeError, OSError) as exc:
            result["errors"].append(f"Failed to read/parse {config_path}: {exc}")
            return result

        merged = merge_opencode_json(existing, trw_entry)
        try:
            config_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
            result["updated"].append(str(config_path.name))
        except OSError as exc:
            result["errors"].append(f"Failed to write {config_path}: {exc}")
    else:
        # Fresh install: write full template with .opencode/INSTRUCTIONS.md
        template: OpencodeTemplateDict = {
            "$schema": "https://opencode.ai/config.json",
            "instructions": [".opencode/INSTRUCTIONS.md"],
            "permission": dict(_DEFAULT_PERMISSIONS),
            "tools": {"trw*": True},
            "mcp": {"trw": trw_entry},
        }
        try:
            config_path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
            result["created"].append(str(config_path.name))
        except OSError as exc:
            result["errors"].append(f"Failed to write {config_path}: {exc}")

    logger.debug(
        "generate_opencode_config",
        created=result["created"],
        updated=result["updated"],
    )
    return result


# ---------------------------------------------------------------------------
# AGENTS.md generation (FR11)
# ---------------------------------------------------------------------------


def generate_agents_md(
    target_dir: Path,
    trw_section: str,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate or update AGENTS.md with TRW auto-generated section.

    Uses same <!-- trw:start --> / <!-- trw:end --> markers as CLAUDE.md.
    If file exists, replaces only the section between markers.
    If not, creates new file with the section.
    """
    result: dict[str, list[str]] = {
        "created": [],
        "updated": [],
        "preserved": [],
        "errors": [],
    }
    agents_md_path = target_dir / "AGENTS.md"

    new_block = f"{_TRW_HEADER}\n{_TRW_START_MARKER}\n{trw_section}\n{_TRW_END_MARKER}\n"

    if agents_md_path.exists() and not force:
        content = agents_md_path.read_text(encoding="utf-8")
        start_idx = content.find(_TRW_START_MARKER)
        end_idx = content.find(_TRW_END_MARKER)

        if start_idx != -1 and end_idx != -1:
            # Replace existing TRW section, preserve surrounding content
            end_pos = end_idx + len(_TRW_END_MARKER)
            # Capture optional header line before trw:start
            header_idx = content.rfind(_TRW_HEADER, 0, start_idx)
            replace_start = header_idx if header_idx != -1 else start_idx
            updated = content[:replace_start] + new_block + content[end_pos:]
            try:
                agents_md_path.write_text(updated, encoding="utf-8")
                result["updated"].append(str(agents_md_path.name))
            except OSError as exc:
                result["errors"].append(f"Failed to update {agents_md_path}: {exc}")
        elif start_idx == -1 and end_idx == -1:
            # No TRW section yet — append it
            if not content.endswith("\n"):
                content += "\n"
            content += "\n" + new_block
            try:
                agents_md_path.write_text(content, encoding="utf-8")
                result["updated"].append(str(agents_md_path.name))
            except OSError as exc:
                result["errors"].append(f"Failed to update {agents_md_path}: {exc}")
        else:
            result["errors"].append("AGENTS.md has malformed TRW markers — found start but not end")
    else:
        # Create new file
        try:
            agents_md_path.write_text(new_block, encoding="utf-8")
            result["created"].append(str(agents_md_path.name))
        except OSError as exc:
            result["errors"].append(f"Failed to write {agents_md_path}: {exc}")

    logger.debug(
        "generate_agents_md",
        created=result["created"],
        updated=result["updated"],
    )
    return result


# ---------------------------------------------------------------------------
# Model family detection
# ---------------------------------------------------------------------------


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
    if "gpt" in model_lower:
        return "gpt"
    if "claude" in model_lower:
        return "claude"
    return "generic"


# ---------------------------------------------------------------------------
# Per-client instruction generation
# ---------------------------------------------------------------------------


def generate_opencode_instructions(
    target_dir: Path,
    model_family: str,
    *,
    force: bool = False,
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

    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

    instructions_path = target_dir / ".opencode" / "INSTRUCTIONS.md"

    try:
        instructions_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result["errors"].append(f"Failed to create directory {instructions_path.parent}: {exc}")
        return result

    content = render_opencode_instructions(model_family)

    if instructions_path.exists() and not force:
        existing = instructions_path.read_text(encoding="utf-8")
        if existing.strip() == content.strip():
            result["preserved"].append(str(instructions_path.relative_to(target_dir)))
            return result

    try:
        instructions_path.write_text(content, encoding="utf-8")
        if instructions_path.exists() and not force:
            result["updated"].append(str(instructions_path.relative_to(target_dir)))
        else:
            result["created"].append(str(instructions_path.relative_to(target_dir)))
    except OSError as exc:
        result["errors"].append(f"Failed to write {instructions_path}: {exc}")

    logger.debug(
        "generate_opencode_instructions",
        created=result["created"],
        updated=result["updated"],
    )
    return result


def generate_codex_instructions(
    target_dir: Path,
    *,
    force: bool = False,
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

    result: dict[str, list[str]] = {"created": [], "updated": [], "preserved": [], "errors": []}

    instructions_path = target_dir / ".codex" / "INSTRUCTIONS.md"

    try:
        instructions_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        result["errors"].append(f"Failed to create directory {instructions_path.parent}: {exc}")
        return result

    content = render_codex_instructions()

    if instructions_path.exists() and not force:
        existing = instructions_path.read_text(encoding="utf-8")
        if existing.strip() == content.strip():
            result["preserved"].append(str(instructions_path.relative_to(target_dir)))
            return result

    try:
        instructions_path.write_text(content, encoding="utf-8")
        if instructions_path.exists() and not force:
            result["updated"].append(str(instructions_path.relative_to(target_dir)))
        else:
            result["created"].append(str(instructions_path.relative_to(target_dir)))
    except OSError as exc:
        result["errors"].append(f"Failed to write {instructions_path}: {exc}")

    logger.debug(
        "generate_codex_instructions",
        created=result["created"],
        updated=result["updated"],
    )
    return result

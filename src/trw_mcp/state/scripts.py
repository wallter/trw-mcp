"""Script management — save, index, and track reusable scripts.

Extracted from tools/learning.py (Sprint 11) to separate script
persistence from the learning tool registration.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.learning import Script
from trw_mcp.state.persistence import FileStateReader, FileStateWriter, model_to_dict

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()

# Extension mapping for script languages
_EXT_MAP: dict[str, str] = {"bash": ".sh", "python": ".py", "sh": ".sh", "py": ".py"}


def save_script(
    trw_dir: Path,
    name: str,
    content: str,
    description: str,
    language: str = "bash",
) -> tuple[Path, str]:
    """Save or update a reusable script to .trw/scripts/.

    Writes the script file, updates the scripts index, and returns
    the file path and action taken.

    Args:
        trw_dir: Path to the .trw directory.
        name: Script name (used as filename stem).
        content: Script content.
        description: What the script does.
        language: Script language — "bash", "python", etc.

    Returns:
        Tuple of (script_path, action) where action is "created" or "updated".
    """
    scripts_dir = trw_dir / _config.scripts_dir
    _writer.ensure_dir(scripts_dir)

    extension = _EXT_MAP.get(language, f".{language}")
    filename = f"{name}{extension}"
    script_path = scripts_dir / filename
    is_update = script_path.exists()

    _writer.write_text(script_path, content)

    index_path = scripts_dir / "index.yaml"
    index_data: dict[str, object] = {}
    if _reader.exists(index_path):
        index_data = _reader.read_yaml(index_path)

    scripts_list: list[dict[str, object]] = []
    raw_scripts = index_data.get("scripts", [])
    if isinstance(raw_scripts, list):
        scripts_list = [s for s in raw_scripts if isinstance(s, dict)]

    found_script = False
    for s in scripts_list:
        if s.get("name") == name:
            s["description"] = description
            s["last_refined"] = date.today().isoformat()
            usage = s.get("usage_count", 0)
            s["usage_count"] = (int(usage) if isinstance(usage, (int, float)) else 0) + 1
            found_script = True
            break

    if not found_script:
        script_entry = Script(
            name=name, description=description, filename=filename, language=language,
        )
        scripts_list.append(model_to_dict(script_entry))

    index_data["scripts"] = scripts_list
    _writer.write_yaml(index_path, index_data)

    action = "updated" if is_update else "created"
    return script_path, action

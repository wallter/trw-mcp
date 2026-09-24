"""Run the CC-03 distill-hint hook from the layout init_project deploys.

Installed, ``pre-tool-distill-hint.sh`` sits in ``.claude/hooks`` beside
``lib-distill-hint.sh`` and the shared ``lib-trw.sh`` whose ``_json_get`` reads
its payload. The package keeps those in two data directories, so the tests copy
all three into the project first.
"""

from __future__ import annotations

import shutil
from pathlib import Path

_DATA = Path(__file__).resolve().parents[3] / "src" / "trw_mcp" / "data"
_FILES = (
    _DATA / "claude_code" / "hooks" / "pre-tool-distill-hint.sh",
    _DATA / "claude_code" / "hooks" / "lib-distill-hint.sh",
    _DATA / "hooks" / "lib-trw.sh",
)


def deploy_distill_hint(project: Path) -> Path:
    """Copy the hook and its two libraries into *project*/.claude/hooks; return the hook."""
    hooks = project / ".claude" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    for source in _FILES:
        shutil.copy(source, hooks / source.name)
    return hooks / _FILES[0].name

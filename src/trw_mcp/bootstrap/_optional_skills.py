"""Bundled skills that ship only while their feature is on: one table for every client's installer.

A skill listed here is installed when its config flag is true, resolved through the normal cascade
(so the operator's ``~/.trw/config.yaml`` switch counts), and removed on the next install or update
once the flag is false, unless the installed copy was edited, in which case it is preserved and
reported. An opt-in feature fails closed: if the config cannot be read, the skill is not installed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

#: skill directory name -> the TRWConfig flag that turns its feature on
CONDITIONAL_SKILLS: dict[str, str] = {"trw-assess": "assess_enabled"}


def skill_enabled(name: str) -> bool:
    """False for a conditional skill whose feature is off; True for every other skill."""
    flag = CONDITIONAL_SKILLS.get(name)
    if flag is None:
        return True
    try:
        from trw_mcp.models.config import get_config

        return bool(getattr(get_config(), flag, False))
    except Exception:  # trw-fail-silent-allow: opt-in skill fails closed on unreadable config
        logger.info("optional_skill_gate_config_unreadable", skill=name, exc_info=True)
        return False


def retire_disabled_skills(
    dest_root: Path, bundled_root: Path, result: dict[str, list[str]], rel_root: str, *, client: str = "claude-code"
) -> None:
    """Remove installed copies of disabled conditional skills that still match *client*'s render of the bundle."""
    from ._client_skills import skill_files

    for name in CONDITIONAL_SKILLS:
        dest = dest_root / name
        if skill_enabled(name) or not dest.is_dir():
            continue
        shipped = dict(skill_files(client, name, root=bundled_root))
        files = [f for f in dest.iterdir() if f.is_file()]
        pristine = all(shipped.get(f.name) == f.read_bytes() for f in files)
        if pristine and not any(p.is_dir() for p in dest.iterdir()):
            shutil.rmtree(dest)
            result.setdefault("removed", []).append(f"{rel_root}/{name}")
        else:
            result.setdefault("preserved", []).append(f"{rel_root}/{name}")

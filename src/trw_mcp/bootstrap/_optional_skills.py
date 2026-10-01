"""Bundled skills that ship only while their feature is on: one table for every client's installer.

A skill listed here is installed when its config flag is true, resolved through the normal cascade
(so the operator's ``~/.trw/config.yaml`` switch counts), and removed on the next install or update
once the flag is false, unless the installed copy was edited, in which case it is preserved and
reported. An opt-in feature fails closed: if the config cannot be read, the skill is not installed.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

import structlog

from ._safe_remove import remove_tree_if_hash

logger = structlog.get_logger(__name__)

#: skill directory name -> the TRWConfig flag that turns its feature on
CONDITIONAL_SKILLS: dict[str, str] = {"trw-assess": "assess_enabled"}


def skill_enabled(name: str, project_root: Path | None = None) -> bool:
    """False for a conditional skill whose feature is off; True for every other skill.

    Decided for the project being installed into, never by this process's cached config (B71-117): the
    *project_root* an installer passes, else ``resolve_project_root``, which answers with the target
    an enclosing install names (``state._project_root_binding``, which is how the zero-argument
    manifest sources agree with the installers) before this process's own project.
    """
    flag = CONDITIONAL_SKILLS.get(name)
    if flag is None:
        return True
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state._paths import resolve_project_root
        from trw_mcp.tools._assess_enablement import assess_surfaced_in

        if flag == "assess_enabled":
            return assess_surfaced_in(project_root or resolve_project_root())
        return bool(getattr(get_config(), flag, False))
    except Exception:  # trw-fail-silent-allow: opt-in skill fails closed on unreadable config
        logger.info("optional_skill_gate_config_unreadable", skill=name, exc_info=True)
        return False


def retire_disabled_skills(
    dest_root: Path,
    bundled_root: Path,
    result: dict[str, list[str]],
    rel_root: str,
    *,
    client: str = "claude-code",
    project_root: Path | None = None,
) -> None:
    """Remove installed copies of disabled conditional skills that still match *client*'s render of the bundle."""
    from ._client_skills import skill_files

    for name in CONDITIONAL_SKILLS:
        dest = dest_root / name
        if skill_enabled(name, project_root) or not dest.is_dir():
            continue
        shipped = dict(skill_files(client, name, root=bundled_root))
        digests = {rel: hashlib.sha256(data).hexdigest() for rel, data in shipped.items()}
        # Each file is re-hashed and captured into .trw/trash at the act (never rmtree'd), so an edit saved
        # after this check, or a file added to the skill, keeps its bytes and its directory.
        # Per file (the SKILL-DIR-ANCHOR ruling): every unmodified shipped file -- SKILL.md above all, which is
        # what keeps a disabled skill live -- goes to .trw/trash; an edited or unlisted file keeps its bytes and
        # its directory.
        if project_root is None:
            result.setdefault("preserved", []).append(f"{rel_root}/{name} (no project root to hold .trw/trash)")
            continue
        before = {f for f in dest.rglob("*") if f.is_file() and not f.is_symlink()}
        kept = remove_tree_if_hash(dest, project_root, _shipped_digest(dest, digests))
        # Report every captured file as trashed, so the uncommitted-changes guard does not restore it.
        result.setdefault("trashed", []).extend(
            f.relative_to(project_root).as_posix() for f in sorted(before) if not os.path.lexists(f)
        )
        if kept:
            result.setdefault("preserved", []).append(f"{rel_root}/{name}")
            # Warnings are what the CLI prints; the per-file reasons belong where the user sees them.
            result.setdefault("warnings", []).extend(_kept_warning(name, why) for why in kept)
        else:
            result.setdefault("removed", []).append(f"{rel_root}/{name}")


def _kept_warning(name: str, why: str) -> str:
    """The warning for one file of a disabled skill that was left in place: what is off, what that means, how out."""
    flag = CONDITIONAL_SKILLS[name]
    # Only SKILL.md keeps a skill live; any other kept file is the user's own and says nothing about the skill.
    effect = (
        f"While it stays the skill is still active and calls {name.replace('-', '_')}, which is not available."
        if why.partition(" (")[0].endswith("/SKILL.md")
        else "It does not keep the skill active."
    )
    return (
        f"{why}: kept. The {name} skill is off ({flag} is not true), so TRW would have removed this file, "
        f"but left it alone. {effect} Delete the file to remove it, or set {flag}: true to turn the feature on."
    )


def _shipped_digest(dest: Path, digests: dict[str, str]) -> Callable[[Path], set[str]]:
    """The allowed-digest lookup for one skill: a file is TRW's only if it hashes to the bytes shipped at its
    path relative to the skill directory."""

    def allowed(f: Path) -> set[str]:
        rel = f.relative_to(dest).as_posix()
        return {digests[rel]} if rel in digests else set()

    return allowed

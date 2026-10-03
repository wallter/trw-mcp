"""Editor-hook ownership advisory — warns, never blocks (PRD-CORE-265-FR10).

Run as ``python -m trw_mcp.tools._formation_hook_advisory`` by the bundled
intent guard, with the raw hook payload on standard input. It prints at most one
line to standard error and ALWAYS exits zero.

The payload is parsed HERE rather than in the shell so that target-path
extraction, pin-first identity resolution, and the ownership match all live in
one typed place. A second shell-side parser could disagree with the commit
boundary about which file is being written, and the two surfaces contradicting
each other is worse than either being absent.

WHY THIS CAN NEVER BLOCK, AND WHY THAT IS DELIBERATE. The knob vocabulary is
``warn`` and ``off``; ``block`` is not in it. PreToolUse delivery is not reliable
on every supported client, and a gate that fires on some clients and not others
teaches agents to distrust it — so ownership is ENFORCED at the commit boundary
(FR09), which every client crosses, and merely SURFACED here, early, where it is
cheapest to act on. The warning says so, so an operator cannot read the advisory
as enforcement.

IDENTITY IS PIN-FIRST (OQ-3, resolved 2026-09-04). The session pin is the
identity every shell hook already resolves, so it wins. ``TRW_FORMATION_MEMBER``
is honoured only when no pin resolves — for clients whose hook context cannot
read one. When both resolve and DISAGREE the pin is used and the conflict is
named in the warning: silently preferring either would make a
mis-set environment variable indistinguishable from correct attribution.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

__all__ = ["main"]

_MEMBER_ENV = "TRW_FORMATION_MEMBER"


def _pinned_run() -> Path | None:
    """Pin-first, pin-only, through the one resolver the guard shares (RC-010). A hook's parent is not the client."""
    from trw_mcp.state._paths import resolve_pin_key
    from trw_mcp.state._paths_pin_mgmt import run_path_for_pin

    return run_path_for_pin(resolve_pin_key(None))


#: Multi-target tools carry several paths rather than one; they are skipped
#: rather than guessed at, which matches the shell fast path's own refusal to
#: decide a payload shape it cannot reduce to a single target.
_MULTI_TARGET_TOOLS = frozenset({"MultiEdit", "NotebookEdit"})


def _target_path(raw: str) -> str | None:
    """The single file path this payload writes, or ``None``."""
    try:
        payload = json.loads(raw)
    except ValueError:  # trw-fail-silent-allow: this advisory only warns, never blocks (module docstring); a malformed hook payload is silently skipped, not a failure to surface
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("tool_name", "")) in _MULTI_TARGET_TOOLS:
        return None
    target = payload.get("tool_input", {})
    if not isinstance(target, dict):
        return None
    file_path = target.get("file_path")
    return file_path.strip() if isinstance(file_path, str) and file_path.strip() else None


def covers(glob: str, rel_path: str) -> bool:
    """Byte-for-byte the predicate of ``trw_mcp.formation._ownership.declaration_covers`` (a test pins the two
    equal): importing that module costs ~0.7 s, the whole price this pre-check exists to avoid."""
    from fnmatch import fnmatchcase

    return fnmatchcase(rel_path, glob) or rel_path.startswith(glob.rstrip("/") + "/")


def _main_root(project: Path) -> Path | None:
    """The main checkout when *project* is a linked worktree (its formation index may live there), else None."""
    marker = project / ".git"
    if not marker.is_file():
        return None
    text = marker.read_text(encoding="utf-8").strip()
    gitdir = text[len("gitdir:") :].strip() if text.startswith("gitdir:") else ""
    head, sep, _tail = gitdir.partition("/.git/worktrees/")
    if not sep:
        raise ValueError("unrecognised .git file")  # a doubt: the caller fails open
    return Path(head)


def _declared_globs(project: Path) -> list[str]:
    """Every owned/test-owned glob any registered formation declares. Raises on ANY doubt (unreadable, unknown
    shape), so the caller runs the full advisory instead of skipping it (fail open)."""
    import yaml

    globs: list[str] = []
    roots = [project]
    main = _main_root(project)
    if main is not None:
        roots.append(main)
    for root in roots:
        index_path = root / ".trw" / "runtime" / "formations.json"
        if not index_path.exists():
            continue
        index = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(index, dict):
            raise TypeError("formation index is not a mapping")
        for run in index.values():
            if not isinstance(run, str):
                raise TypeError("formation index entry is not a path")
            manifest = yaml.safe_load((Path(run) / "formation.yaml").read_text(encoding="utf-8"))
            members = manifest.get("members") if isinstance(manifest, dict) else None
            if not isinstance(members, list):
                raise TypeError("formation manifest has no member list")
            for member in members:
                if not isinstance(member, dict):
                    raise TypeError("formation member is not a mapping")
                for key in ("owned_paths", "test_owned_paths"):
                    values = member.get(key) or []
                    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                        raise TypeError("ownership globs are not a list of strings")
                    globs.extend(values)
    return globs


def _no_glob_can_cover(target: str) -> bool:
    """True ONLY when no declared glob covers *target* (FORMATION-ADVISORY-COST), by the ownership predicate itself
    over every way the target can be made repo-relative. Anything uncertain (no project root, a target outside it, an
    unreadable or unexpected index or manifest) answers False, so the full advisory runs: never a silent skip.
    """
    root = os.environ.get("TRW_PROJECT_ROOT", "").strip()
    if not root:
        return False
    try:
        project = Path(root)
        rels = set()
        for candidate in (Path(target), Path(os.path.realpath(target))):
            absolute = candidate if candidate.is_absolute() else project / candidate
            for base in (project, Path(os.path.realpath(project))):
                try:
                    rels.add(Path(os.path.normpath(absolute)).relative_to(base).as_posix())
                except (
                    ValueError
                ):  # trw-fail-silent-allow: not under this base; another base or the full advisory decides
                    continue
        if not rels:
            return False
        return not any(covers(glob, rel) for glob in _declared_globs(project) for rel in rels)
    except Exception:  # trw-fail-silent-allow: a doubt runs the full advisory (fail open), it never skips it
        return False


def main(payload_text: str) -> int:
    """Emit at most one advisory line. Returns 0 unconditionally."""
    target = _target_path(payload_text)
    if target is None:
        return 0
    if _no_glob_can_cover(target):
        return 0  # no declared ownership can cover it: skip the 0.7 s formation import
    try:
        from trw_mcp.formation import load, owner_of, settings
        from trw_mcp.state._paths import resolve_project_root

        if settings().hook_ownership_mode == "off":
            return 0
        run_path = _pinned_run()
        context = load(run_path)
        if context is None or context.is_orchestrator:
            return 0
        ownership = owner_of(target, context=context, project_root=resolve_project_root())
    except Exception:
        # An advisory that could fail the hook would be a block by another name.
        # A broken manifest is REFUSED at the commit boundary, which is where
        # fail-closed belongs; here it is silence.
        return 0
    if ownership is None or ownership.member_id is None:
        return 0

    pinned_member = context.member_id
    env_member = os.environ.get(_MEMBER_ENV, "").strip() or None
    caller = pinned_member or env_member
    if ownership.member_id == caller:
        return 0

    conflict = ""
    if pinned_member and env_member and pinned_member != env_member:
        conflict = (
            f" (identity conflict: the session pin says {pinned_member!r} but {_MEMBER_ENV} says "
            f"{env_member!r}; the pin wins)"
        )
    print(
        f"TRW formation advisory: {ownership.path} is owned by member {ownership.member_id!r} "
        f"(glob {ownership.glob!r}), not {caller or '(unresolved)'!r}{conflict}. "
        "This is a warning only — the scoped-commit boundary is the surface that refuses.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.stdin.read()))

"""A hook's registration follows its hook file: a kept file keeps its registration, in every client's settings file.

Belongs to the registration writers of ``_settings_merge`` (Claude Code), ``_claude_code_distill_channels``,
``_codex_hooks``, ``_codex_distill_channels`` and ``_copilot``. A 9.3.0 update kept two hook files the user had
edited and still rewrote their registrations to the new hook's settings (a 5 second timeout meant for the new, faster
hook), so the old hook ran under the new registration. A registration is tied to its file by the
``.claude/hooks/<name>.sh`` path in the entry's command (all three clients run that one physical file); the rule is
that an existing registration whose file the update kept is left exactly as it was, and the run says so once per
hook file. A missing registration is still added.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from ._hook_closure import _HOOK_PATH_RE

CLAUDE_SETTINGS = ".claude/settings.json"


def hook_name_of(command: object) -> str | None:
    """The ``<name>.sh`` hook file a settings entry's *command* runs, or ``None`` for any other command."""
    match = _HOOK_PATH_RE.search(command) if isinstance(command, str) else None
    return match.group(1) if match else None


def default_bundled_dirs() -> tuple[Path, ...]:
    """Where TRW's bundled hook scripts live: the shared hooks and the CC-03 hint pair."""
    from ._claude_code_distill_channels import _HOOKS_DATA_DIR
    from ._utils import _DATA_DIR

    return (Path(_DATA_DIR) / "hooks", _HOOKS_DATA_DIR)


def hook_file_kept(root: Path, name: str, bundled_dirs: Sequence[Path] | None = None) -> bool:
    """``True`` when ``.claude/hooks/<name>`` exists and its bytes are not the bundled script's: the update kept it.

    Called only after the run's hook writers, which replace every copy they can prove TRW wrote, so bytes that
    still differ are the writer's "kept" (it hashes bytes, so a CRLF-only difference counts). An absent file is not
    kept (its registration follows as before), nor is a hook with no bundled script to compare against. A copy that
    cannot be read cannot be shown to be TRW's, so it counts as kept.
    """
    installed = root / ".claude" / "hooks" / name
    bundled = next((d / name for d in (bundled_dirs or default_bundled_dirs()) if (d / name).is_file()), None)
    if bundled is None or not installed.is_file():
        return False
    try:
        return installed.read_bytes() != bundled.read_bytes()
    except OSError:  # trw-fail-silent-allow: unreadable bytes are not provably TRW's: kept
        return True


def entry_hook_names(entry: object) -> list[str]:
    """The hook files an entry or group (``{"hooks": [{"command": ...}]}``) runs, in order."""
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    commands = [h.get("command") for h in hooks if isinstance(h, dict)] if isinstance(hooks, list) else []
    return [name for name in map(hook_name_of, commands) if name]


def kept_hook_of(root: Path, entries: Iterable[object], bundled_dirs: Sequence[Path] | None = None) -> str | None:
    """The first hook file run by any of *entries* that the update kept, or ``None``."""
    for entry in entries:
        for name in entry_hook_names(entry):
            if hook_file_kept(root, name, bundled_dirs):
                return name
    return None


def note_registration_left(result: dict[str, list[str]], name: str, settings_rel: str = CLAUDE_SETTINGS) -> None:
    """One warning per hook file (once per run) saying its registration was left as it was, with the kept-file remedy."""
    message = (
        f"{settings_rel}: left the registration of .claude/hooks/{name} as it was because the hook file was kept "
        f"(for the fresh copy and its new registration, delete the file and run update-project again)"
    )
    warnings = result.setdefault("warnings", [])
    if message not in warnings:
        warnings.append(message)


def merge_owned_groups(
    existing_owned: list[dict[str, object]],
    fresh: list[dict[str, object]],
    *,
    root: Path,
    result: dict[str, list[str]],
    settings_rel: str,
) -> list[dict[str, object]]:
    """The TRW-owned groups a merge writes: *fresh* ones, except that a kept hook file keeps its existing groups.

    *existing_owned* are the groups already in the file that TRW owns (a merge would drop them for *fresh*). One
    that runs a kept hook file stays, in place of any fresh group running the same file; with nothing kept this
    returns *fresh* unchanged. A group that differs from what would replace it is named in *result*.
    """
    held = [g for g in existing_owned if kept_hook_of(root, [g])]
    if not held:
        return fresh
    kept_names = {n for g in held for n in entry_hook_names(g) if hook_file_kept(root, n)}
    merged: list[dict[str, object]] = []
    for group in fresh:  # a held group takes the place of the fresh one that runs the same kept file
        names = kept_names & set(entry_hook_names(group))
        if names:
            merged.extend(g for g in held if g not in merged and names & set(entry_hook_names(g)))
        else:
            merged.append(group)
    merged.extend(g for g in held if g not in merged)  # a held group with no fresh counterpart still stays
    for name in sorted(kept_names):
        if any(g not in fresh for g in held if name in entry_hook_names(g)):
            note_registration_left(result, name, settings_rel)
    return merged

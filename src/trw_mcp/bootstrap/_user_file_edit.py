"""One guarded path for editing a user-visible file (PRD-INFRA-192 FR09/FR10).

Every uninstall/update code path that edits (rather than deletes) a shared
client config or instruction file — hook-registration JSON, merged MCP-server
config, managed-block markdown — funnels through this module. It provides:

1. :func:`guard_refusal` / :func:`safe_read_text` — a symlink-safe read that
   refuses when the file itself, or any parent path component between the
   project root and the file, is a symlink, or when the resolved path escapes
   the root. Wraps :func:`trw_mcp.bootstrap._safe_remove.path_refusal`, the
   single implementation of that rule (deletion and edit share it).
2. :func:`drop_matching_hook_entries` / :func:`drop_matching_hook_commands` —
   JSON hook-registration editing across every hooks-json shape TRW writes,
   removing only entries/commands a caller-supplied predicate verifies as
   TRW's own.
3. :func:`matching_sort_keys` / :func:`atomic_write_text` — a byte-preserving
   write: rewrite only when the original bytes already equal TRW's own
   canonical serialization of the parsed original, and write atomically
   (temp file + ``os.replace``) once the guard has passed.
4. :func:`strip_managed_block` — marker-delimited managed-block removal that
   never touches an orphan marker and never collapses a blank line outside
   the removed span.
5. :func:`strip_toml_table` — text-level TOML table removal (no parse+redump)
   so a hand-edited ``.codex/config.toml`` / ``.grok/config.toml`` keeps its
   comments and formatting outside TRW's own ``[mcp_servers.trw]`` table.

Newlines and file mode are preserved exactly: :func:`safe_read_text` /
:func:`atomic_write_text` read and write raw bytes rather than going through
text-mode newline translation, and the write preserves the original file's
permission bits. TRW never removes or rewrites a byte it cannot prove it
wrote and that is unchanged, and never reads or writes through a symlink.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path

from ._safe_remove import path_refusal

# ---------------------------------------------------------------------------
# Guard + read + atomic write
# ---------------------------------------------------------------------------


def guard_refusal(path: Path, root: Path) -> str | None:
    """Return why editing *path* under *root* is unsafe, or ``None`` if safe.

    A thin, semantically-named wrapper over :func:`path_refusal` — deletion
    and edit share the exact same symlink/escape rule; there is exactly one
    implementation of it.
    """
    return path_refusal(path, root)


def safe_read_text(path: Path, root: Path) -> tuple[str | None, str | None]:
    """Guard, then read *path* as UTF-8 text with NO newline translation.

    Returns ``(text, None)`` on success, or ``(None, reason)`` when the guard
    refused or the file could not be read/decoded. The file is never opened
    before the guard has passed.

    Reads raw bytes and decodes them directly rather than ``Path.read_text``,
    which opens in universal-newline text mode and silently rewrites every
    ``\\r\\n`` to ``\\n`` on the way in. A CRLF file would then read back as
    all-LF, so a byte-identity/canonical-form comparison against the ORIGINAL
    bytes would be comparing against content that was already changed by the
    act of reading it.
    """
    refusal = guard_refusal(path, root)
    if refusal:
        return None, refusal
    try:
        return path.read_bytes().decode("utf-8"), None
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"could not read {path}: {exc}"


def atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically (temp file in the same dir, then replace).

    Writes raw bytes (never ``Path.write_text``, which can translate newlines)
    so a caller that read CRLF content and rewrote only the pieces it verified
    changed keeps every other ``\\r\\n`` intact. Preserves the original file's
    permission bits on the replacement temp file when the target already
    exists — ``os.replace`` swaps inodes, so without this the edited file
    would silently pick up the temp file's default (umask) mode instead of
    keeping whatever mode (e.g. ``0600``, an exec bit) the original had.
    """
    tmp_path = path.with_name(f".{path.name}.trw-tmp-{uuid.uuid4().hex}")
    try:
        tmp_path.write_bytes(text.encode("utf-8"))
        try:
            original_mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            original_mode = None  # file did not exist yet -- default mode is fine
        if original_mode is not None:
            os.chmod(tmp_path, original_mode)
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Byte-preserving JSON rewrite
# ---------------------------------------------------------------------------

# ``json.dumps(..., indent=2, sort_keys=...)`` conventions actually observed
# across this repo's own registration/config writers. Checked in order and the
# first exact match is reused for the rewrite, so re-writing the file never
# produces a spurious formatting-only diff against whichever TRW writer last
# touched it.
KNOWN_SORT_KEYS_CONVENTIONS: tuple[bool, ...] = (True, False)


def matching_sort_keys(data: object, raw_text: str) -> bool | None:
    """The ``sort_keys`` convention whose serialization of *data* equals *raw_text*, if any.

    Compares against the parsed-and-unmodified document, so this answers "was
    this file, as it stands right now, produced by a TRW writer we know
    about" — independent of whatever is about to be removed from it. ``None``
    means the file does not match any known TRW writer convention (hand
    edited or reformatted); the caller must leave it untouched and warn.
    """
    for sort_keys in KNOWN_SORT_KEYS_CONVENTIONS:
        if raw_text == json.dumps(data, indent=2, sort_keys=sort_keys) + "\n":
            return sort_keys
    return None


def rewrite_json_if_canonical(
    path: Path,
    root: Path,
    *,
    raw_text: str,
    original_data: object,
    new_data: object,
    file_label: str,
    subject: str,
    result: dict[str, list[str]],
) -> bool:
    """Rewrite *path* with *new_data* only when *raw_text* is TRW's canonical form.

    Guards first. If the edited result equals the original, nothing is
    written. If the original isn't canonical, nothing is written and a
    warning is recorded instead (rule: never rewrite bytes TRW cannot prove
    it wrote). Returns ``True`` when the file was rewritten.
    """
    refusal = guard_refusal(path, root)
    if refusal:
        result.setdefault("warnings", []).append(f"{file_label}: left untouched ({refusal})")
        return False
    if new_data == original_data:
        return False
    sort_keys = matching_sort_keys(original_data, raw_text)
    if sort_keys is None:
        result.setdefault("warnings", []).append(
            f"{file_label}: TRW registration(s) for {subject} remain; the file has custom "
            "formatting so TRW left it untouched — remove them by hand"
        )
        return False
    atomic_write_text(path, json.dumps(new_data, indent=2, sort_keys=sort_keys) + "\n")
    return True


# ---------------------------------------------------------------------------
# The one removal predicate (PRD-INFRA-192 FR09/FR10)
# ---------------------------------------------------------------------------


def is_generated_entry(entry: object, generated: Iterable[object]) -> bool:
    """True only when *entry* equals, in full, one entry TRW generates.

    Every structured removal -- a hook dict, a hook group's own fields, an
    MCP-server entry, a TOML server table -- asks this and nothing else. A
    matching key, command, or table name says where TRW's entry WOULD be, never
    that the bytes there are still TRW's: a user who added ``env``, a
    ``timeout`` or a nested table has changed the entry, so it stays and the
    caller warns. Compared as canonical JSON, not with ``==``: Python equates
    ``True``, ``1`` and ``1.0``, so a user's ``"version": true`` would otherwise
    pass for TRW's ``1``.
    """
    ours = {json.dumps(candidate, sort_keys=True) for candidate in generated}
    return json.dumps(entry, sort_keys=True) in ours


# ---------------------------------------------------------------------------
# Hook-registration JSON editing
# ---------------------------------------------------------------------------

# (event, whole matcher-group/entry, verified-TRW predicate context)
TrwEntryPredicate = Callable[[str, object], bool]
# (event, one hook COMMAND dict inside an entry's "hooks" list)
TrwHookCommandPredicate = Callable[[str, dict[str, object]], bool]


def _entry_hook_commands(entry: object) -> list[dict[str, object]]:
    if not isinstance(entry, dict):
        return []
    hooks = entry.get("hooks")
    if not isinstance(hooks, list):
        return []
    return [h for h in hooks if isinstance(h, dict)]


def prune_empty_event_keys(
    hooks_by_event: dict[str, object],
    new_lists: dict[str, list[object]],
) -> dict[str, object]:
    """Merge pruned per-event lists back in, honoring the empty-key rule.

    An event key is dropped only when its list was non-empty before this pass
    and became empty because of a removal in this pass. A key that was
    already empty, or that still holds entries, is always preserved.
    """
    merged: dict[str, object] = {}
    for event, original in hooks_by_event.items():
        if not isinstance(original, list):
            merged[event] = original
            continue
        pruned = new_lists.get(event, [])
        if pruned or not original:
            merged[event] = pruned
        # else: originally non-empty, emptied by our own removal -> drop key
    return merged


def drop_matching_hook_entries(
    hooks_by_event: dict[str, object],
    is_trw_entry: TrwEntryPredicate,
    file_label: str,
    result: dict[str, list[str]],
    *,
    warn_subject: Callable[[str, object], str] | None = None,
) -> tuple[dict[str, object], bool]:
    """Remove every TRW-owned whole ENTRY for which *is_trw_entry* is True.

    Used for ``.claude/settings.json``, where one entry carries exactly one
    script's identity, so a whole-entry drop is safe. An entry with hook
    commands that is not verified TRW is kept and (optionally) warned about.
    """
    changed = False
    new_lists: dict[str, list[object]] = {}
    for event, entries in hooks_by_event.items():
        if not isinstance(entries, list):
            continue
        kept: list[object] = []
        for entry in entries:
            if _entry_hook_commands(entry) and is_trw_entry(event, entry):
                changed = True
                continue
            kept.append(entry)
        new_lists[event] = kept
    return prune_empty_event_keys(hooks_by_event, new_lists), changed


def drop_matching_hook_commands(
    hooks_by_event: dict[str, object],
    is_trw_hook_command: TrwHookCommandPredicate,
    file_label: str,
    result: dict[str, list[str]],
    *,
    warn_others: bool = False,
    is_trw_group: TrwEntryPredicate | None = None,
) -> tuple[dict[str, object], bool]:
    """Remove only the TRW-verified hook COMMAND within each group/entry.

    Used for ``.codex/hooks.json`` and the Copilot hooks file, where a single
    matcher group can hold several hook commands: a user's own hook appended
    into a TRW-managed group survives even when the TRW hook in the same
    group is removed. A group is dropped only once none of its hook commands
    remain.

    With *is_trw_group*, a group whose own fields (``matcher``, ``description``,
    everything but ``hooks``) are not TRW's is left whole: its hooks are the
    user's to remove, even one that equals a TRW hook.
    """
    changed = False
    new_lists: dict[str, list[object]] = {}
    for event, groups in hooks_by_event.items():
        if not isinstance(groups, list):
            continue
        kept_groups: list[object] = []
        for group in groups:
            hooks = group.get("hooks") if isinstance(group, dict) else None
            if (
                not isinstance(group, dict)
                or not isinstance(hooks, list)
                or (is_trw_group is not None and not is_trw_group(event, group))
            ):
                kept_groups.append(group)
                continue
            kept_hooks: list[object] = []
            group_changed = False
            for hook in hooks:
                if isinstance(hook, dict) and is_trw_hook_command(event, hook):
                    group_changed = True
                    continue
                kept_hooks.append(hook)
            if not group_changed:
                kept_groups.append(group)
                continue
            changed = True
            if kept_hooks:
                new_group = dict(group)
                new_group["hooks"] = kept_hooks
                kept_groups.append(new_group)
            # else: no hooks left in this group -> the whole group is dropped
        new_lists[event] = kept_groups
    return prune_empty_event_keys(hooks_by_event, new_lists), changed


def drop_matching_flat_hook_entries(
    hooks_by_event: dict[str, object],
    is_trw_entry: TrwEntryPredicate,
) -> tuple[dict[str, object], bool]:
    """Remove ``{event: [{command: ...}]}`` entries *is_trw_entry* verifies as TRW's.

    Used for the flat per-event shapes (``.cursor/hooks.json``,
    ``.antigravitycli/hooks.json``) that have no group wrapper. The predicate
    sees the whole entry, so a TRW command the user gave a ``timeout`` stays.
    Same empty-key rule as the grouped shapes (:func:`prune_empty_event_keys`):
    only a key emptied by THIS removal goes, and non-list values are untouched.
    """
    changed = False
    new_lists: dict[str, list[object]] = {}
    for event, entries in hooks_by_event.items():
        if not isinstance(entries, list):
            continue
        new_lists[event] = [e for e in entries if not (isinstance(e, dict) and is_trw_entry(event, e))]
        changed = changed or len(new_lists[event]) != len(entries)
    return prune_empty_event_keys(hooks_by_event, new_lists), changed


# ---------------------------------------------------------------------------
# Managed-block (marker-delimited) text editing
# ---------------------------------------------------------------------------


def strip_managed_block(text: str, marker_pairs: tuple[tuple[str, str], ...]) -> tuple[str, bool, list[str]]:
    """Remove verified marker-delimited spans from *text*.

    A span is removed only when its start marker line has a matching end
    marker line reachable below it (the NEXT matching end marker) — matched
    line-anchored (the stripped line must equal the marker exactly), never as
    a substring inside prose. An orphan marker (a start with no reachable
    end, or an end with no preceding start) is left in place and reported in
    the returned warnings list rather than deleted or used to delete to EOF.

    No blank-line collapsing: the real writer (``state/claude_md``) inserts
    its own separator BEFORE a generated-header comment line that sits ABOVE
    the start marker, not adjacent to the marker line itself — so there is no
    writer-inserted blank line directly touching ``trw:start``/``trw:end`` for
    this function to remove. Removing exactly the marker span, and nothing
    else, is what keeps every byte outside it — including the user's own
    blank lines, wherever they fall — byte-identical.
    """
    starts = {start for start, _end in marker_pairs}
    ends = {end for _start, end in marker_pairs}
    end_for_start = dict(marker_pairs)

    lines = text.splitlines(keepends=True)
    warnings: list[str] = []
    out: list[str] = []
    changed = False

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped in starts:
            expected_end = end_for_start[stripped]
            # Look ahead for the matching end marker line.
            end_index = None
            for j in range(i + 1, n):
                if lines[j].strip() == expected_end:
                    end_index = j
                    break
            if end_index is None:
                warnings.append(f"orphan begin marker with no matching end: {stripped!r} — left in place")
                out.append(line)
                i += 1
                continue
            changed = True
            i = end_index + 1
            continue
        if stripped in ends:
            # Orphan end marker (no preceding start reached it above) — left
            # in place, never deleted.
            warnings.append(f"orphan end marker with no matching begin: {stripped!r} — left in place")
            out.append(line)
            i += 1
            continue
        out.append(line)
        i += 1

    result = "".join(out)
    return result, changed, warnings


# ---------------------------------------------------------------------------
# TOML table editing (text-level, byte-preserving)
# ---------------------------------------------------------------------------

_TOML_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(#.*)?$")


def _toml_table(raw: str, table: str) -> tuple[bool, object]:
    """``(present, value)`` of the dotted *table* in the parsed *raw* document."""
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover - Python <3.11 fallback
        import tomli as tomllib

    node: object = tomllib.loads(raw)
    for part in table.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _table_ends_at(rest: str, table: str) -> bool:
    """True when nothing after a table's text still belongs to it.

    A comment or blank line does not close a TOML table, so the first line after
    them must be a header not nested under *table* (or the end of the file);
    a bare key there would still be part of the table.
    """
    for line in rest.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = _TOML_HEADER_RE.match(line)
        return match is not None and not match.group(1).strip().startswith(table + ".")
    return True


def strip_toml_table(raw: str, table: str, generated: Iterable[str]) -> tuple[str, bool, str | None]:
    """Remove ``[table]`` only when its text is byte-for-byte a table TRW generates.

    Returns ``(text, removed, refusal)``. *generated* is the exact text TRW's
    writer renders for the table, nested sub-tables included. Anything else in
    that span -- a user's key, blank line, comment or reformatting -- makes it
    no longer TRW's, so it stays and *refusal* says why. Removing it takes the
    one blank separator line TRW's writer puts after a table, and nothing else;
    every other byte of the file is left as it was. Raises ``ValueError`` on
    unparseable TOML, like every other strip strategy.
    """
    if not _toml_table(raw, table)[0]:
        return raw, False, None
    for text in generated:
        start = raw.find(text)
        while start != -1:
            end = start + len(text)
            if (start == 0 or raw[start - 1] == "\n") and _table_ends_at(raw[end:], table):
                rendered = raw[:start] + raw[end:].removeprefix("\n")
                if _toml_table(rendered, table)[0]:
                    return raw, False, f"[{table}] has parts outside its own table block"
                return rendered, True, None
            start = raw.find(text, start + 1)
    return raw, False, f"[{table}] differs from the text TRW generates"

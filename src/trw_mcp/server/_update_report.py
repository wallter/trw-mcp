"""What ``update-project`` left alone, named one file per line (FB-INSTALL-03).

An 8.1.2 upgrade over an existing project printed nothing about an edited ``lib-trw.sh`` it kept (while replacing the
hooks that source it), a git-dirty ``AGENTS.md`` it did not refresh, or a retired skill it could not prove it wrote:
the first was recorded in ``result["modified"]``, which nothing read, and the others only as a count in
``preserved``. The lines here use the ``WARNING: <text>`` contract ``install-trw.py``'s progress reader keys on, so
they also survive its spinner.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

from trw_mcp.bootstrap._utils import printable

logger = structlog.get_logger(__name__)

#: ``preserved`` entries that carry a reason in parentheses, and what to tell the user about each.
#: Everything else in ``preserved`` (``.trw/config.yaml`` and the like) is kept on every run by design.
_KEPT_REASONS = {
    "(uncommitted_changes)": "it has uncommitted changes in git, so this update did not refresh it",
    "(not_installer_owned)": "TRW cannot show it wrote it (edited, or never recorded), so this update left it in place",
    "(uncommitted_changed_after_keep)": (
        "it has uncommitted changes in git, so this update kept your version, but later steps then changed TRW's "
        "own entries in it; run git diff on it to see exactly what changed"
    ),
}
#: What a dry run says of the same file: the preview is in the conditional, and names what stays the user's.
_WOULD_CHANGE_AFTER_KEEP = (
    "it has uncommitted changes in git, so this update would keep your version, then update TRW's own entries in it "
    "(hook registrations, statusLine); your other entries are kept"
)
#: The way out for a kept hook, skill or agent file (since 9.2.2). ``--rerender`` publishes TRW's current render
#: of exactly that file and records it; the bytes it replaces are moved into a ``.trw/trash`` capture, never
#: deleted, and the run prints that capture's path.
_RERENDER = (
    "to replace it with TRW's current version run: trw-mcp update-project --rerender <path> "
    "(the bytes it replaces are moved into .trw/trash, and that run prints where; it refuses and changes nothing "
    "if the file changes while it runs or TRW has no current version of it for this project)"
)
_KEPT_EDITED = f"you edited it since TRW last wrote it, so this update did not replace it; {_RERENDER}"
#: No record at all: nothing shows the user changed it, so the line must not say they did (sub_GOYE8ssHqS).
_KEPT_UNRECORDED = (
    "TRW has no record of writing it and it differs from TRW's current version, so this update left it alone "
    "(it is your own edit, or a copy TRW wrote without keeping a record of it; TRW cannot tell which); "
    f"{_RERENDER}"
)

__all__ = [
    "Record",
    "adopted_lines",
    "attention_count",
    "build_records",
    "kept_files",
    "print_claude_md",
    "print_kept",
    "print_retired",
    "removed_files",
    "report_kept",
    "report_removed",
]


@dataclass(frozen=True)
class Record:
    """One path the update touched or left, with the action actually taken: the one source the report renders from.

    ``removed`` was deleted; ``kept`` is a file the update did not replace; ``left_in_place`` is a retired TRW
    artifact the update reports but never deletes (its own warning carries the remedy).
    """

    path: str
    action: Literal["removed", "kept", "left_in_place"]
    reason: str = ""


def _left_in_place(result: dict[str, list[str]], target: Path) -> set[str]:
    """Display paths of retired artifacts present or kept, already named by a retired notice or its removal command."""
    return {
        _display_path(str(p), target) for p in (*result.get("retired_present", []), *result.get("retired_kept", []))
    }


def _display_path(path: str, target: Path) -> str:
    """*path* relative to the project when it is inside it, else as given.

    A file at the project root is shown as ``./NAME``: a bare ``kept FRAMEWORK.md`` read as "some FRAMEWORK.md",
    when it is the root reference copy, not ``.trw/frameworks/FRAMEWORK.md`` (9.0.1 upgrade report).
    """
    candidate = Path(path)
    if candidate.is_absolute():
        base = next((b for b in (target, target.resolve()) if candidate.is_relative_to(b)), None)
        if base is None:
            return path
        candidate = candidate.relative_to(base)
    rel = candidate.as_posix()
    return rel if "/" in rel else f"./{rel}"


def kept_files(result: dict[str, list[str]], target: Path) -> list[tuple[str, str]]:
    """``(path, why)`` for every file this update left alone, once each, in report order.

    The two producers: ``modified`` (a hook, skill or agent the user edited, recorded by the raw-copy guard and
    read by nothing until now) and ``preserved`` entries that carry a reason, which the summary only counts.
    """
    # A path a retired notice already names (with its removal command) is not named again as "kept": that read as
    # two contradictory statements (feedback #158).
    named_retired = _left_in_place(result, target)
    reasons = dict(_KEPT_REASONS)
    if "would_run" in result:  # a dry run proposes; it does not claim the later steps ran
        reasons["(uncommitted_changed_after_keep)"] = _WOULD_CHANGE_AFTER_KEEP
    kept: dict[str, str] = {}
    unrecorded = {str(p) for p in result.get("unrecorded", [])}
    for path in result.get("modified", []):
        why = _KEPT_UNRECORDED if str(path) in unrecorded else _KEPT_EDITED
        kept.setdefault(_display_path(str(path), target), why)
    for entry in result.get("preserved", []):
        for suffix, why in reasons.items():
            if str(entry).endswith(suffix):
                kept.setdefault(_display_path(str(entry).removesuffix(suffix).rstrip(), target), why)
    return [(path, why) for path, why in kept.items() if path not in named_retired]


def build_records(result: dict[str, list[str]], target: Path) -> list[Record]:
    """Every path that needs a report line, once, from the structured facts in *result* (no text matching).

    Removals the producer already described with a restore command (``retired_described``) are not repeated here.
    """
    from trw_mcp.bootstrap._retired_artifacts import curated_out_reason

    in_git = {str(p) for p in result.get("retired_described", [])}
    records = [
        Record(path, "removed", curated_out_reason(path)) for path in removed_files(result) if path not in in_git
    ]
    records.extend(Record(path, "left_in_place") for path in sorted(_left_in_place(result, target)))
    records.extend(Record(path, "kept", why) for path, why in kept_files(result, target))
    return records


def attention_count(result: dict[str, list[str]], target: Path) -> int:
    """How many paths the operator must look at: the ``kept`` and ``left_in_place`` records."""
    return sum(1 for record in build_records(result, target) if record.action != "removed")


def adopted_lines(result: dict[str, list[str]], target: Path) -> list[str]:
    """One line per client this bare update newly added to the install record: which, why, and how to remove it.

    A blocked client was always named ("<client> not adopted into the install record: ..."); an adopted one left
    no trace but ``config.yaml`` among the updated files (sub_GOYE8ssHqS). A client is named only when the record
    in *target* holds it now: the config write reports a failure as a warning, and a rollback puts the record
    back. A dry run changes no record, so it only proposes.
    """
    proposed = list(dict.fromkeys(result.get("adopted_clients", [])))
    if result.get("errors") or not proposed:
        return []
    dry = "would_run" in result
    if not dry:
        from trw_mcp.bootstrap._template_claude_md import _recorded_targets

        recorded = set(_recorded_targets(target))
        proposed = [client for client in proposed if client in recorded]
    verb = "would be adopted" if dry else "adopted"
    return [
        f"{client} {verb} into the install record: every TRW-named file of it in this project is byte-identical "
        f"to what TRW writes or recorded writing, so update-project keeps it up to date from now on; to remove it "
        f"instead run: trw-mcp uninstall --ide {client} (it lists what it would remove and asks first; --dry-run "
        "only lists; files you edited stay)"
        for client in proposed
    ]


def print_kept(result: dict[str, list[str]], target: Path) -> None:
    """One ``WARNING: kept <path>: <why>`` line per file left alone, so the installer re-surfaces it.

    Printed before the summary, like warnings: a run that kept an edited hook library while replacing the hooks
    that source it is exactly the run whose summary is skipped on error.
    """
    by_reason: dict[str, list[str]] = {}
    for path, why in kept_files(result, target):
        by_reason.setdefault(why, []).append(printable(path))
    for why, paths in by_reason.items():  # one line per reason, with the count (feedback #142)
        if len(paths) == 1:  # one file: the command is printed ready to run
            print(f"WARNING: kept {paths[0]}: {why.replace('<path>', shlex.quote(paths[0].removeprefix('./')))}")
        else:
            print(f"WARNING: kept {len(paths)} files ({why}): {', '.join(paths)}")


def print_retired(records: list[Record], *, dry_run: bool = False) -> None:
    """One line per retired TRW file deleted in place, with the client-curation reason when there is one.

    A dry run only proposes the removal, so it says "Would remove", never the past tense.
    """
    verb = "Would remove" if dry_run else "Removed"
    for record in records:
        if record.action == "removed":
            reason = f" ({record.reason})" if record.reason else ""
            print(f"{verb} retired TRW file: {printable(record.path)}{reason}")


def print_refreshed(result: dict[str, list[str]]) -> None:
    """``AGENTS.md: <what TRW did to its block>``: the file is the user's, so a write to it is never silent (#138).

    A dry run only proposes the write ("would refresh").
    """
    dry = "would_run" in result
    for key, verb in (
        ("created", "would create" if dry else "created"),
        ("updated", "would refresh" if dry else "refreshed"),
    ):
        if "AGENTS.md" in result.get(key, []):
            print(f"AGENTS.md: {verb} the TRW block")


def print_claude_md(edits: list[str], *, detailed: bool = False, quiet: bool = False) -> None:
    """One ``CLAUDE.md: <what TRW did>`` line per edit to the user's CLAUDE.md (``link_claude_md``).

    The file is the user's, so no edit to it is silent; ``install-trw.py`` re-surfaces these lines past its spinner.
    Under ``-v`` each is a structured log line instead; ``--quiet`` prints nothing.
    """
    for edit in edits if not quiet else []:
        if detailed:
            logger.info("claude_md_edited", path="CLAUDE.md", detail=edit)
        else:
            print(f"CLAUDE.md: {printable(edit)}")


def removed_files(result: dict[str, list[str]]) -> list[str]:
    """Every file this update deleted, once each, sorted: the retirements plus the transaction diff's deletions.

    ``retired`` is filled only by the retirement sweeps; ``cleaned`` is the snapshot-vs-final diff of the whole
    managed surface, so a deletion by any other writer is still named (the trw-prd-new removal of a 9.0.1 upgrade,
    deleted from three skill trees with no output line).
    """
    return sorted({str(p) for p in (*result.get("retired", []), *result.get("cleaned", []))})


def report_removed(result: dict[str, list[str]], *, detailed: bool, quiet: bool) -> None:
    """Name every deleted file: structured log lines under ``-v``, plain lines otherwise, nothing if quiet."""
    removed = removed_files(result)
    if detailed:
        for path in removed:
            logger.warning("update_project_removed", op="update_project", path=path)
    elif not quiet:
        print_retired(build_records(result, Path()), dry_run="would_run" in result)


def report_kept(result: dict[str, list[str]], target: Path, *, detailed: bool, quiet: bool) -> None:
    """Name each edit to the user's CLAUDE.md and what the update left alone: log lines under ``-v``, plain lines
    otherwise, nothing if quiet."""
    print_claude_md(result.get("claude_md", []), detailed=detailed, quiet=quiet)
    for line in adopted_lines(result, target):
        if detailed:
            logger.info("update_project_client_adopted", op="update_project", detail=line)
        elif not quiet:
            print(f"Note: {printable(line)}")  # information, not a problem: the prefix change notes carry
    if not detailed and not quiet:
        print_refreshed(result)
        for note in result.get("notes", []):
            print(f"Would apply: {printable(note)}" if "would_run" in result else f"Note: {printable(note)}")
    if detailed:
        for path, why in kept_files(result, target):
            logger.warning("update_project_kept", op="update_project", path=path, detail=why)
    elif not quiet:
        print_kept(result, target)

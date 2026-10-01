"""Lifecycle CLI subcommand handlers — extracted from _subcommands.py for module-size compliance.

Belongs to the ``_subcommands.py`` facade. Re-exported there for back-compat
with test imports (``test_uninstall.py``, ``test_cli_auth_subcommand.py``).

Two handlers:
- ``_run_uninstall`` — remove TRW files from a project (uninstall subcommand)
- ``_run_auth`` — login/logout/status auth subcommand dispatch
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trw_mcp.bootstrap._git_hooks import _resolve_hooks_dir
from trw_mcp.bootstrap._safe_remove import path_refusal, safe_remove
from trw_mcp.bootstrap._utils import printable
from trw_mcp.server._subcommands_uninstall_config import (
    _remove_managed_block_file as _remove_managed_block_file,
)
from trw_mcp.server._uninstall_corpus import (
    keep_memory_in_dir as _keep_memory_in_dir,
)
from trw_mcp.server._uninstall_corpus import (
    print_corpus_warning as _print_corpus_warning,
)
from trw_mcp.server._uninstall_corpus import (
    print_untracked_trw_entries as _print_untracked_trw_entries,
)
from trw_mcp.server._uninstall_corpus import (
    remove_trw_dir as _remove_trw_dir,
)
from trw_mcp.server._uninstall_corpus import (
    trw_corpus_blast_radius as _trw_corpus_blast_radius,
)
from trw_mcp.server._uninstall_global import GlobalConfigs
from trw_mcp.server._uninstall_report import (
    display,
    print_done,
    print_symlink_guidance,
    refusal_text,
    report_custom_format_kept,
    report_kept_trw,
    report_stripped,
)
from trw_mcp.server._uninstall_trash_report import _move_matched_captures_to_os_trash


def _surfaces_declared_by_others(recorded: list[str], removed: str) -> set[str]:
    """Relpaths the project's other recorded clients declare, per their client specs."""
    from trw_mcp.client_profiles.catalog import client_surfaces
    from trw_mcp.models.config._profiles import builtin_client_ids

    known = set(builtin_client_ids())
    return {s.relpath for client in recorded if client != removed and client in known for s in client_surfaces(client)}


def _run_uninstall(args: argparse.Namespace) -> None:
    """Handle the ``uninstall`` subcommand -- remove TRW files from a project.

    Registry-driven (PRD-SEC-006 FR07): the set of surfaces is derived from the
    client-profile registry manifest (every built-in profile plus framework
    core), so
    every profile is cleaned, not just claude-code. Shared files
    (AGENTS.md, ANTIGRAVITY.md, settings.json, copilot-instructions) have only their
    TRW-managed marker block removed; only artifacts TRW created are touched.
    It never removes ``~/.trw``: that is the memory daemon's one store, holding
    every checkout's namespace. ``--delete-memory`` forgets only this checkout's
    own namespace there, through its grant (PRD-CORE-280 FR06).

    CLIENT-REMOVE (installer refinement 5.1.0): ``--ide <client>``
    narrows the surface set to :func:`client_surfaces` for just that client
    (its own config/agents/skills/hooks — never the shared core surfaces
    every client relies on) and, on a real run, drops the client from
    ``target_platforms`` so a later bare ``update-project`` does not
    resurrect it. The corpus blast-radius warning, ``--delete-memory``, and
    ``--keep-memory`` are whole-project concerns and do not apply.

    PRD-INFRA-192 FR09: a plain client surface is manifest-driven, for both a
    scoped ``--ide`` removal and a whole-project uninstall alike, when
    ``content_hashes`` records at least one key under it -- a recorded file is
    deleted only when no remaining recorded client still owns it and its bytes
    are unedited (see ``bootstrap/_uninstall_manifest.py``), so a user's own
    custom file dropped into a TRW-owned directory, or a TRW file the user
    hand-edited, both survive. Without a readable current-schema manifest, a
    scoped OR whole-project removal refuses outright (NFR02): guessing
    ownership is exactly the failure mode this module exists to close. A
    project with no manifest FILE at all is not "corrupt" and proceeds.

    C3 rule 3: a plain surface the manifest covers with NO key at all (no
    recorder has ever covered it, or every file under it was user-edited) is
    never guessed at either -- a directory is kept whole, and a single file is
    removed only when it is still byte-identical to what TRW would write today
    (``bootstrap._uninstall_manifest.plan_uncovered_surface``). This applies to
    the framework-core root files too (``REVIEW.md``, ``FRAMEWORK.md``,
    ``AARE-F-FRAMEWORK.md``): a user's pre-existing or hand-edited copy of one
    of these survives exactly like a per-client file would. Only ``.trw``
    itself is exempt (it holds the manifest and cannot describe its own
    ownership; its own blast-radius/``--keep-memory``/``--user-tier`` logic
    governs it instead).
    """
    from trw_mcp.bootstrap._uninstall_manifest import (
        KeyDisposition,
        SurfaceDisposition,
        apply_removal,
        manifest_covers_surface,
        plan_manifest_removal,
        plan_uncovered_surface,
        prune_empty_dirs,
        remove_judged_surface,
        rewrite_manifest_after_removal,
    )
    from trw_mcp.bootstrap._version_manifest import _MANIFEST_FILE, _read_manifest, manifest_refusal
    from trw_mcp.client_profiles.catalog import client_surfaces, uninstall_surfaces

    target = Path(getattr(args, "target_dir", ".")).resolve()
    dry_run: bool = getattr(args, "dry_run", False)
    yes: bool = getattr(args, "yes", False)
    keep_memory: bool = getattr(args, "keep_memory", False)
    remove_ide: str | None = getattr(args, "ide", None)
    delete_memory: bool = getattr(args, "delete_memory", False) and not remove_ide

    # PRD-INFRA-192 FR09 C3: a whole-project uninstall is manifest-driven for
    # plain CLIENT surfaces too, exactly like a scoped ``--ide`` removal -- it
    # refuses on an unreadable/malformed manifest the same way. A project with
    # NO manifest FILE at all (never installed through this mechanism, or the
    # manifest was removed by hand) is not "corrupt": it proceeds, with every
    # plain surface falling under rule 3 (kept unless proven TRW's own,
    # byte-identical). Checked by file existence, never by parsing the refusal
    # string.
    manifest_path = target / ".trw" / _MANIFEST_FILE
    manifest_content_hashes: dict[str, str] = {}
    manifest_owners: dict[str, list[str]] = {}
    if remove_ide or manifest_path.is_file():
        refusal = manifest_refusal(target)
        if refusal:
            print(f"  {refusal}")
            raise SystemExit(1)
        manifest_data = _read_manifest(target) or {}
        manifest_content_hashes = manifest_data.get("content_hashes", {})  # type: ignore[assignment]
        manifest_owners = manifest_data.get("owners", {})  # type: ignore[assignment]

    # Blast-radius detection for the project .trw learning corpus. Removing
    # .trw wholesale permanently destroys memory.db + all learnings, so we warn
    # explicitly + nudge an export-first when a corpus is present. Not
    # applicable to a single-client --ide (client_surfaces never
    # includes .trw itself).
    # Deliberately UNRESOLVED (PRD-INFRA-192 FR09 P0): if ``.trw`` is itself a
    # symlink, resolving it here before the safety checks below run would
    # already have picked the symlink TARGET as the thing to warn about and
    # (further down) rmtree -- exactly the bug being fixed. Safety is decided
    # once, in `_safe_remove.path_refusal`, at the point of use.
    project_trw = target / ".trw"
    has_corpus, learning_count = (
        _trw_corpus_blast_radius(project_trw) if project_trw.is_dir() and not remove_ide else (False, 0)
    )

    plain_paths: list[Path] = []
    managed_paths: list[Path] = []
    merged_config_paths: list[tuple[Path, Path, str]] = []
    global_configs = GlobalConfigs(getattr(args, "global_config", False))
    covered_dispositions: list[KeyDisposition] = []
    covered_surface_roots: list[Path] = []
    uncovered_dispositions: list[SurfaceDisposition] = []
    from trw_mcp.bootstrap._template_claude_md import _recorded_targets

    surfaces = client_surfaces(remove_ide) if remove_ide else uninstall_surfaces()
    recorded_clients = _recorded_targets(target)
    # A surface several clients declare -- a managed block (AGENTS.md: cursor-cli
    # and grok) or a shared plain directory (``.claude/hooks``: claude-code, codex,
    # and copilot all run scripts from it, PRD-INFRA-192 FR09) -- belongs to all of
    # them: a scoped removal strips/deletes it only when no remaining recorded
    # client declares that surface too.
    still_declared = _surfaces_declared_by_others(recorded_clients, remove_ide) if remove_ide else set()
    remaining_targets = [c for c in recorded_clients if c != remove_ide] if remove_ide else []
    # ``.trw`` is the ONE surface exempt from the manifest-driven/rule-3
    # machinery below: it holds the manifest itself plus the learning corpus,
    # so it cannot describe its own ownership, and its removal is already
    # gated by its own blast-radius warning / --keep-memory
    # logic. Every OTHER framework-core plain file -- REVIEW.md, FRAMEWORK.md,
    # AARE-F-FRAMEWORK.md -- is a TRW-owned single file exactly like a
    # per-client one and goes through the SAME manifest-covered /
    # ``plan_uncovered_surface`` path (PRD-INFRA-192 FR09 C3 review
    # follow-up): a pre-existing user REVIEW.md, or one the user edited, must
    # survive uninstall the same as a hand-edited client file.
    core_relpaths = {".trw"}
    for surface in surfaces:
        base = Path.home() if surface.home_scoped else target
        # Deliberately UNRESOLVED (PRD-INFRA-192 FR09 P0): resolving here,
        # before deciding what to do with the surface, is exactly how a
        # symlinked ``.trw`` (or client dir) used to get its OUTSIDE target
        # queued for rmtree. `.exists()` still follows the link to detect
        # presence; deletion safety is decided once, at the point of use, by
        # `_safe_remove.path_refusal`.
        path = base / surface.relpath
        if not path.exists() or surface.relpath in still_declared:
            continue
        if global_configs.defers(surface, path):
            continue
        if surface.merged_config:
            # PRD-INFRA-192 FR09 P0: the guard's root must be the same anchor
            # *path* was built from (``base``) -- passing the always-resolved
            # project ``target`` here for a home-scoped surface breaks
            # ``path_refusal``'s containment check whenever HOME sits under a
            # path a symlink-resolves differently than the resolved project
            # root (e.g. macOS ``/var`` -> ``/private/var``), which reads as a
            # false "path is not under root" refusal for a perfectly legitimate
            # in-project HOME used by tests.
            merged_config_paths.append((path, base, surface.config_shape))
        elif surface.managed_block:
            managed_paths.append(path)
        elif surface.relpath in core_relpaths:
            plain_paths.append(path)
        elif manifest_covers_surface(manifest_content_hashes, surface.relpath):
            covered_dispositions.extend(
                plan_manifest_removal(
                    target,
                    surface.relpath,
                    remove_ide or "",
                    manifest_content_hashes,
                    manifest_owners,
                    remaining_targets,
                )
            )
            covered_surface_roots.append(path)
        else:
            uncovered_dispositions.append(plan_uncovered_surface(path, surface.relpath, target))

    # The `.git/hooks/post-commit` surface above names the DEFAULT hooks directory,
    # and the catalog entry says so. But git honours `core.hooksPath`, and install
    # follows it (`bootstrap/_git_hooks.py` resolves the same setting before
    # writing), so a project that redirects its hooks — common in monorepos and in
    # teams that share a checked-in `.githooks/` — received a TRW dispatch block at
    # a path no project-relative manifest can name. Uninstall then reported success
    # while leaving an ACTIVE hook that runs on every commit.
    #
    # A static registry entry cannot fix this because the destination is decided at
    # runtime by git config. So uninstall resolves it exactly the way install did,
    # through the same function, and strips the managed block wherever that lands.
    # `_resolve_hooks_dir` already fails open to the default directory, so a repo
    # without the setting, or without git on PATH, is unchanged. The git post-commit
    # hook is shared framework-core infrastructure, not client-owned — skip it for
    # a scoped --ide.
    if not remove_ide:
        configured_hook = (_resolve_hooks_dir(target, target / ".git", probe=True) / "post-commit").resolve()
        if configured_hook.is_file() and configured_hook not in managed_paths:
            managed_paths.append(configured_hook)

    global_configs.report_left(target, dry_run=dry_run)

    from trw_mcp.server import _uninstall_memory

    # Resolved before anything is shown or removed: the grant lives in .trw/runtime.
    try:
        memory = _uninstall_memory.checkout_memory(target) if delete_memory else None
    except _uninstall_memory.MemoryDeleteRefusedError as exc:
        print(f"  --delete-memory: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    nothing_on_disk = (
        not plain_paths
        and not managed_paths
        and not merged_config_paths
        and not delete_memory
        and not covered_dispositions
        and not uncovered_dispositions
    )
    if nothing_on_disk and remove_ide:
        print(f"  No {remove_ide} files found in this project.")
        # A client whose files are already gone may still be listed; dropping it is
        # the part of the removal that keeps update-project from reinstalling it.
        if remove_ide not in _recorded_targets(target):
            return
    elif nothing_on_disk:
        print("  No TRW files found in this project.")
        return

    print(f"\n  TRW files found in {target}:\n")
    for p in plain_paths:
        kind = "dir " if p.is_dir() else "file"
        size = ""
        if p.is_dir():
            count = sum(1 for _ in p.rglob("*") if _.is_file())
            size = f" ({count} files)"
        note = ""
        if p == project_trw and keep_memory and has_corpus:
            note = " — memory/ + learnings/ PRESERVED (--keep-memory)"
        print(f"    {kind}  {display(p, target)}{size}{note}")
    for p in managed_paths:
        print(f"    block {display(p, target)} (TRW-managed section)")
    for p, root, shape in merged_config_paths:
        print(global_configs.entry_line(p, root, shape, target, dry_run=dry_run))
    if memory:
        print(f"    rows  {memory.namespace} in the shared store {_uninstall_memory.shared_store()}")
    for d in covered_dispositions:
        detail = f" ({d.detail})" if d.detail else ""
        print(f"    {d.action:<16} {display(d.path, target)}{detail}")
    for u in uncovered_dispositions:
        detail = f" ({u.detail})" if u.detail else ""
        print(f"    {u.action:<16} {display(u.path, target)}{detail}")
    if remove_ide:
        print(f"    entry target_platforms: drop {remove_ide!r} in .trw/config.yaml")

    # Destructive blast-radius warning: removing project .trw without
    # --keep-memory permanently deletes memory.db + every learning. TRW's whole
    # value is durable learnings, so name the blast radius + nudge export-first.
    if project_trw in plain_paths and not remove_ide:
        _print_untracked_trw_entries(project_trw)
    corpus_at_risk = has_corpus and not keep_memory
    if corpus_at_risk:
        _print_corpus_warning(project_trw, learning_count, target, display)

    if dry_run:
        refusals = global_configs.refusals(
            managed_paths, merged_config_paths, covered_dispositions, uncovered_dispositions, target
        )
        if refusals:
            print("\n  --dry-run: .trw and the manifest would be kept, because an item would be refused.")
            print_symlink_guidance(refusals, target)
        print("\n  --dry-run: no files removed.")
        return

    if not yes:
        print()
        prompt = (
            "  Permanently delete the learning corpus? [y/N] " if corpus_at_risk else "  Remove these files? [y/N] "
        )
        confirm = input(prompt).strip().lower()
        if confirm not in ("y", "yes"):
            print("  Aborted.")
            return

    removed = 0
    errors = 0
    refused: list[Path] = []
    if memory:
        try:
            deleted = _uninstall_memory.delete_checkout_memory(memory, target / ".trw")
        except (_uninstall_memory.MemoryDeleteRefusedError, OSError) as exc:
            # Before any file goes: the grant that --delete-memory needs lives in .trw.
            print(f"  --delete-memory: {exc}; no files were removed", file=sys.stderr)
            raise SystemExit(1) from exc
        removed += 1
        print(f"  Deleted: {deleted} row(s) of {memory.namespace} from the shared store")
    removed_manifest_keys: set[str] = set()
    trw_left: list[str] = []
    captures: dict[str, list[str]] = {}  # both removal paths' .trw/trash captures, moved on in ONE step
    if covered_dispositions:
        apply_result: dict[str, list[str]] = {"preserved": [], "errors": []}
        removed_manifest_keys, covered_errors = apply_removal(covered_dispositions, apply_result, target)
        for message in apply_result["errors"]:
            print(f"  Error: {printable(message)}")
        for message in apply_result["preserved"]:
            print(f"  Preserved: {printable(message)}")
        captures.update({k: apply_result.get(k, []) for k in ("trashed", "trashed_at")})
        for d in covered_dispositions:
            if d.action == "remove" and d.key in removed_manifest_keys and d.detail != "already gone":
                removed += 1
                print(f"  Removed: {display(d.path, target)}")
            elif d.action == "preserved-edited":
                print(f"  Preserved (edited): {display(d.path, target)}")
            elif d.action == "kept-shared-owner":
                print(f"  Kept: {display(d.path, target)} ({d.detail})")
            elif d.action == "rejected-unsafe":
                print(f"  Error: {display(d.path, target)} ({d.detail})")
                errors += 1
                refused.append(d.path)
        errors += covered_errors
        trw_left = list(apply_result.get("trw_left", []))
        for root in covered_surface_roots:
            prune_empty_dirs(root, up_to=target)

    for u in uncovered_dispositions:
        if u.action == "refused":
            errors += 1
            refused.append(u.path)
            print(f"  Error: {display(u.path, target)} ({u.detail})")
            continue
        if u.action != "remove":
            print(f"  Kept: {display(u.path, target)} ({u.detail})")
            continue
        # Re-check safety immediately before deleting (TOCTOU defense, same as
        # `apply_removal`) even though `plan_uncovered_surface` already refused
        # a symlinked path at plan time.
        judged, detail = remove_judged_surface(u, target, captures)  # re-proves the judged bytes (codex r2 P0)
        if judged == "error":
            errors += 1
            refused.append(u.path)
            print(f"  Error removing {display(u.path, target)}: {detail}")
        elif judged == "kept":
            print(f"  Kept: {display(u.path, target)} ({detail})")
        else:
            removed += 1
            print(f"  Removed: {display(u.path, target)}")

    for p in managed_paths:
        try:
            status = _remove_managed_block_file(p, target, dry_run=False)
        except OSError as exc:
            errors += 1
            print(f"  Error updating {display(p, target)}: {exc}")
            continue
        if status == "removed":
            removed += 1
            print(f"  Removed: {display(p, target)} (TRW-only file)")
        elif status == "stripped":
            removed += 1
            print(f"  Cleaned: {display(p, target)} (removed TRW section)")
        elif status == "refused":
            errors += 1
            refused.append(p)
            print(f"  Error updating {display(p, target)}: {refusal_text(p, target)}")

    for p, root, shape in merged_config_paths:
        try:
            status = global_configs.strip(p, root, shape, captures)
        except OSError as exc:
            errors += 1
            print(f"  Error updating {display(p, target)}: {exc}")
            continue
        if status == "removed":
            removed += 1
            print(f"  Removed: {display(p, target)} (TRW-only file)")
        elif status == "stripped":
            removed += 1
            errors += report_stripped(p, target)
        elif status == "skipped":
            print(f"  Preserved: {display(p, target)} (unparseable; left untouched)")
        elif status in (None, "changed") and p in global_configs.paths:
            errors += global_configs.report_kept(p, status, target)
        elif status is None:  # a custom-formatted file is reported; any other untouched file is not
            errors += report_custom_format_kept(p, target)
        elif status == "refused":
            errors += 1
            refused.append(p)
            print(f"  Error updating {display(p, target)}: {refusal_text(p, root)}")

    _move_matched_captures_to_os_trash(captures, target, display)  # the plain surfaces' and the emptied configs'
    # ``.trw`` holds the install manifest, the only thing that can later prove which files under a refused
    # (symlinked) parent are TRW's. It goes last, and stays whenever anything above was refused or failed, so
    # a re-run after the user fixes the link can still classify the residue.
    if errors or refused or trw_left:
        errors += report_kept_trw(plain_paths, refused, trw_left, target)
    else:
        for p in plain_paths:
            refusal = path_refusal(p, target)
            if refusal:
                errors += 1
                print(f"  Error removing {display(p, target)}: {refusal}")
                continue
            # --keep-memory: preserve the learning corpus inside project .trw while
            # removing all other session/config state under it.
            if p == project_trw and keep_memory and has_corpus:
                kept_removed, kept_errors = _keep_memory_in_dir(p, target, display)
                removed += kept_removed
                errors += kept_errors
                print(f"  Kept: {display(p, target)}/memory + learnings (--keep-memory)")
                continue
            if p == project_trw and p.is_dir() and not p.is_symlink():
                trw_removed, trw_errors = _remove_trw_dir(p, target, display)
                removed += trw_removed
                errors += trw_errors
                continue
            failure = safe_remove(p, target)
            if failure:
                errors += 1
                print(f"  Error removing {display(p, target)}: {failure}")
            else:
                removed += 1
                print(f"  Removed: {display(p, target)}")

    if remove_ide:
        from trw_mcp.bootstrap._ide_targets_finalize import _remove_config_target_platform

        # FR09 §2.e: rewrite the manifest even when nothing was deleted this
        # run (every covered key was already gone, or kept-shared-owner) --
        # `remove_ide` can no longer own anything once it leaves target_platforms,
        # so any surviving owners record naming it is stale from this moment on.
        rewrite_manifest_after_removal(target, removed_manifest_keys, remove_ide)

        scratch: dict[str, list[str]] = {"updated": [], "warnings": []}
        _remove_config_target_platform(target, remove_ide, scratch)
        for warning in scratch.get("warnings", []):
            print(f"  Warning: {printable(warning)}")
        if scratch.get("updated"):
            print(f"  Updated: {display(Path(scratch['updated'][0]), target)} (dropped {remove_ide!r})")

    print_done(target, removed, remove_ide, delete_memory)
    if errors:
        # Truthful exit status: a partial uninstall must not report success to
        # scripted callers (`trw-mcp uninstall --yes && ...`).
        print(f"  {errors} item(s) could not be removed — see errors above.", file=sys.stderr)
        raise SystemExit(1)


def _run_auth(args: argparse.Namespace) -> None:
    """Handle the ``auth`` subcommand (login/logout/status)."""
    from trw_mcp.cli.auth import run_auth_login, run_auth_logout, run_auth_status

    config_path = Path.cwd() / ".trw" / "config.yaml"
    api_url = getattr(args, "api_url", None) or "https://api.trwframework.com"

    auth_cmd = getattr(args, "auth_command", None)
    if auth_cmd == "login":
        sys.exit(run_auth_login(api_url, config_path))
    elif auth_cmd == "logout":
        sys.exit(run_auth_logout(config_path))
    elif auth_cmd == "status":
        sys.exit(run_auth_status(config_path, api_url))
    else:
        # No auth subcommand: show help
        print("Usage: trw-mcp auth {login|logout|status}")
        print()
        print("Commands:")
        print("  login   Authenticate via device authorization flow")
        print("  logout  Remove stored API key")
        print("  status  Show current authentication status")
        sys.exit(0)

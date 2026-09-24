"""One ownership predicate for what a client-scoped write should touch.

PRD-INFRA-192 FR09 (C7): several install/update call sites each hand-decided
whether ``.claude/**`` and ``.mcp.json`` belonged to the resolved client set —
the old ``_wants_claude_scaffold`` (a codex-only special case), ``_run_core_
update_phases`` (unconditional), ``_merge_mcp_json`` callers (unconditional),
and ``_update_mcp_config`` (unconditional) — and all of them disagreed with
the uninstall side once ``client_profiles.catalog`` classified those surfaces
as Claude Code's own rather than core. This module derives ownership from the
catalog directly, so the install/update side and the uninstall side can never
drift apart again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path


def writes_surface(relpath: str, clients: Sequence[str], *, explicit: bool) -> bool:
    """True when *relpath* should be written for the resolved *clients*.

    A non-explicit resolution (bare ``init-project``, ``ide=None``) always
    returns True (CORE262-13): a bare install still gets every Claude Code
    surface, because ``ide=None`` means "no one asked for anything narrower".

    An EXPLICIT selection is gated on the client-profile catalog: *relpath*
    is written only when some client in *clients* declares it via
    :func:`trw_mcp.client_profiles.catalog.client_scaffold_relpaths`. This is
    what makes ``init-project --ide opencode`` create no ``.claude/`` or
    ``.mcp.json`` (neither is in opencode's catalog entry), while
    ``init-project --ide codex`` still creates ``.claude/hooks`` (codex's
    catalog entry declares it, since codex's own hook commands run scripts
    from there — see ``bootstrap/_codex_hooks.py``).
    """
    if not explicit:
        return True
    from trw_mcp.client_profiles.catalog import client_scaffold_relpaths

    return any(relpath in client_scaffold_relpaths(client) for client in clients)


def update_write_targets(target_dir: Path, ide: str | None) -> list[str]:
    """The client set ``update-project`` gates its writers against.

    The recorded ``.trw/config.yaml`` ``target_platforms`` plus *ide*, if
    given (PRD-INFRA-192 FR09 §3) — never raw ``detect_ide``, which reports
    claude-code for any project containing ``.claude/`` (TRW's own output).
    """
    from ._template_claude_md import _recorded_targets

    targets = list(_recorded_targets(target_dir))
    if ide and ide not in targets:
        targets.append(ide)
    return targets


def update_owns_surface(relpath: str, target_dir: Path, ide: str | None) -> bool:
    """True when *relpath* should be refreshed/deleted by ``update-project``.

    A project that recorded no clients at all (pre-record install, or one
    whose config could not be read) has nothing to gate against — refreshing
    is the pre-FR09 behavior for that case, so ownership defaults True rather
    than silently freezing an old install's surfaces.
    """
    targets = update_write_targets(target_dir, ide)
    if not targets:
        return True
    return writes_surface(relpath, targets, explicit=True)


def owners_for_content_hashes(content_hashes: Mapping[str, str], clients: Sequence[str]) -> dict[str, list[str]]:
    """Derive ``owners`` for every ``content_hashes`` key (PRD-INFRA-192 FR12).

    A key's owners are the clients in *clients* whose PLAIN catalog surface
    (never a managed block or merged config -- those are shared/user files by
    design, not something a single client "owns" for deletion purposes) covers
    the key's repo-relative path: equal, or a prefix at a ``/`` path boundary.
    A key no client in *clients* declares gets ``[]`` -- an unowned leftover
    (e.g. an older install's ``.claude/*`` file on a project now recorded as
    ``[opencode]``), recorded rather than dropped so a later per-client
    uninstall can see it was never anyone's to begin with and leave it alone.
    """
    from trw_mcp.client_profiles.catalog import client_surfaces

    from ._version_manifest import _manifest_key_path

    plain_surfaces: dict[str, list[str]] = {}
    for client in clients:
        for surface in client_surfaces(client):
            if surface.managed_block or surface.merged_config or surface.home_scoped:
                continue
            plain_surfaces.setdefault(surface.relpath, []).append(client)

    owners: dict[str, list[str]] = {}
    for key in content_hashes:
        path = _manifest_key_path(key)
        matched: set[str] = set()
        for relpath, owning_clients in plain_surfaces.items():
            if path == relpath or path.startswith(f"{relpath}/"):
                matched.update(owning_clients)
        owners[key] = sorted(matched)
    return owners


def update_scaffold_dirs(target_dir: Path, ide: str | None) -> list[str]:
    """The dirs ``update-project`` should ensure: client-neutral, plus owned Claude Code ones.

    Replaces unconditionally ensuring ``_CLAUDE_SCAFFOLD_DIRS`` on every
    ``update-project`` run (PRD-INFRA-192 FR09 §3): a project recorded as
    ``[opencode]`` must not have ``.claude/skills``/``.claude/agents``
    conjured back into existence by an update it never asked for.
    """
    from . import _CLAUDE_SCAFFOLD_DIRS, _TRW_DIRS

    owned = [d for d in _CLAUDE_SCAFFOLD_DIRS if update_owns_surface(d, target_dir, ide)]
    return [*_TRW_DIRS, *owned]

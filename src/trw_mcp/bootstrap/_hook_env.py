# Parent facade: bootstrap/_utils.py (re-exported there via _file_ops.py)
"""Per-client ``.trw/runtime/hook-env.d/<key>.sh`` writer.

Extracted from ``_file_ops.py`` (module-size compliance) so the R8 hook-env
work -- multi-client fan-out, legacy-lib compatibility, and the
resolved-``hooks_enabled`` warning -- has room to live without pushing that
already-large file over the 350 effective-LOC ceiling. Public names are
re-exported from ``_file_ops.py`` so existing import paths are preserved.

PRD-FIX-118/R8: ONE file PER CLIENT, never a single shared file. Before this
split, every profile synced in a repo rewrote the same
``.trw/runtime/hook-env.sh``, so whichever client synced LAST silently decided
every other client's ``NUDGE_ENABLED`` and ``TRW_SESSION_ID`` -- syncing a
hookless/no-session client (opencode, grok) after claude-code turned Claude
Code's own hooks unpinned. ``lib-trw.sh`` now derives a key from where the
CALLING hook physically lives (its own ``<config_dir>/hooks`` install
location, never a client-id table) and sources only that client's file.

Sol round 1 findings closed here:
- codex/copilot execute the SHARED ``.claude/hooks/*.sh`` scripts via an
  adapter, so a path-derived key alone always resolves to ``claude`` for them
  too. The adapters now export ``TRW_HOOK_CLIENT`` (validated
  ``[A-Za-z0-9_-]+``) ahead of the shared script, and ``lib-trw.sh`` prefers it
  over the path-derived key -- see ``_codex_hooks.py`` / ``_copilot.py``.
- init/update wrote only ``ide_targets[0]``'s file even though every resolved
  client gets its integrations installed; :func:`write_hook_env_for_clients`
  fans out to every one of them.
- a not-yet-upgraded install's ``.claude/hooks/lib-trw.sh`` still sources the
  single legacy ``hook-env.sh`` directly (it predates the per-client split and
  is refreshed only by init/update's own hook-copy step, never by a sync);
  deleting that file out from under it would silently drop its nudge/session
  settings. :func:`_installed_lib_predates_hook_env_split` detects that case
  from the installed library's own content and keeps the legacy file alive
  (best-effort, last-writer-wins -- exactly the pre-split behavior, no worse
  than status quo) until the library itself is upgraded.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.config._client_profile import ClientProfile

logger = structlog.get_logger(__name__)

_HOOK_ENV_KEY_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")

# ClientProfile.config_dir's own fallback sentinel: "no instruction_path parent
# directory of its own" (an instruction_path at the project root, e.g.
# AGENTS.md / ANTIGRAVITY.md). See the docstring on _hook_env_key below.
_HOOK_ENV_NO_CONFIG_DIR = ".trw"

# Present only in the per-client-split lib-trw.sh (the block that derives a
# hook-env.d key from $_hook_dir) -- absent from every pre-split copy. Content
# detection, not a version file, because the physical hook scripts are
# refreshed by init/update's own copy step, never by a hook-env write alone.
_LIB_TRW_SPLIT_MARKER = "hook-env.d"


def _sanitize_hook_env_key(raw: str) -> str:
    """Collapse *raw* to a safe ``hook-env.d/<key>.sh`` filename component."""
    cleaned = _HOOK_ENV_KEY_UNSAFE_RE.sub("-", raw.strip().strip(".")).strip("-")
    return cleaned or "default"


def hook_env_key_for_hooks_dir(hooks_dir: Path) -> str:
    """The key a hook physically installed under *hooks_dir* derives for itself.

    Mirrors ``lib-trw.sh``'s own shell-side derivation byte-for-byte: the name
    of *hooks_dir*'s PARENT directory (its ``<config_dir>/hooks`` layout),
    leading dot stripped. Exposed publicly so tests that run a bundled hook
    script straight from the package source tree (rather than an installed
    ``.claude/hooks/``) can write the per-client file under the SAME key that
    script will actually look for, instead of guessing.
    """
    return _sanitize_hook_env_key(hooks_dir.parent.name)


def _hook_env_key(profile: ClientProfile) -> str:
    """The ``.trw/runtime/hook-env.d/<key>.sh`` namespace for *profile*.

    Must match what ``lib-trw.sh`` independently derives from a hook's own
    install location (see :func:`hook_env_key_for_hooks_dir` and the shell-side
    twin in ``data/hooks/lib-trw.sh``) -- the two sides never share a
    client-id table, so keeping them in lockstep is what makes a hook read
    its OWN profile's settings regardless of sync order or which other client
    was synced most recently.

    ``ClientProfile.config_dir`` falls back to the ``.trw`` sentinel for any
    client whose ``instruction_path`` lives at the project root (``cursor-cli``,
    ``antigravity-cli``, ``grok`` all write ``AGENTS.md``/``ANTIGRAVITY.md``).
    That fallback means "this client owns no directory of its own", not "these
    three unrelated clients share one" -- keying all three to a literal
    ``.trw`` file would recreate the exact defect this split fixes, just for a
    different trio of clients. They key off ``client_id`` instead.
    """
    config_dir = profile.config_dir
    if config_dir == _HOOK_ENV_NO_CONFIG_DIR:
        return _sanitize_hook_env_key(profile.client_id)
    return _sanitize_hook_env_key(config_dir.replace("/", "-"))


def hook_env_dir(trw_dir: Path) -> Path:
    return trw_dir / "runtime" / "hook-env.d"


def _installed_lib_predates_hook_env_split(trw_dir: Path) -> bool:
    """True when the installed ``.claude/hooks/lib-trw.sh`` still sources the legacy shared file.

    Read from the FILESYSTEM, not the bundled package version this process
    ships: the physical script in a target project is only refreshed by
    init/update's own hook-copy step, so ``instructions sync`` alone (which
    only rewrites hook-env, never the hook scripts) must not assume the
    installed library already knows to read the new per-client path. No
    installed library at all (a fresh project, or one that never chose
    claude-code/codex/copilot's shared hook surface) means there is nothing
    old to keep working for, so this returns ``False``.
    """
    installed_lib = trw_dir.parent / ".claude" / "hooks" / "lib-trw.sh"
    try:
        content = installed_lib.read_text(encoding="utf-8")
    # trw-fail-silent-allow: absent/unreadable lib means nothing installed yet -- no legacy file to preserve for
    except OSError:
        return False
    return _LIB_TRW_SPLIT_MARKER not in content


def _write_hook_env_file(
    trw_dir: Path,
    profile: ClientProfile,
    *,
    key: str | None = None,
    warnings: list[str] | None = None,
) -> Path:
    """Write ``.trw/runtime/hook-env.d/<key>.sh`` for hook scripts (PRD-CORE-149 FR04).

    The generated file is sourced by that hook at startup to decide whether to
    run the nudge-pool init (``NUDGE_ENABLED``) and which client-identity tokens
    to expose (``TRW_CLIENT_DISPLAY_NAME`` / ``TRW_CLIENT_CONFIG_DIR``), plus a
    ``TRW_SESSION_ID`` stanza (PRD-FIX-118 FR01) sourced from the client's own
    session variable (:mod:`trw_mcp.client_profiles.session_identity`).
    Whether hooks run at all is ``hooks_enabled`` in ``TRWConfig``, published
    separately to ``.trw/runtime/hook-flags``.

    Idempotent: safe to rewrite on every sync. Permissions are 0600
    (the file is sourced as shell by the user's own hooks, so nobody else needs read access, and
    ``lib-trw.sh`` refuses one that is group- or world-writable). Creates ``runtime/hook-env.d``
    if missing.

    ``key`` overrides the profile-derived namespace -- used only by tests that
    run a bundled hook script from a location other than its production
    ``<config_dir>/hooks`` install path (see :func:`hook_env_key_for_hooks_dir`).

    ``warnings``, when given, receives one operator-facing message (appended,
    never replaced) when *profile* expects TRW hooks to run
    (``profile.hooks_enabled``) but the project's resolved ``hooks_enabled``
    is ``False`` -- this client's hooks will therefore never fire despite
    being installed. A structlog warning is always emitted regardless of
    whether a caller passed ``warnings``.

    The pre-split shared ``hook-env.sh`` is retained (kept byte-identical to
    this write) while the installed ``lib-trw.sh`` still predates the split --
    see :func:`_installed_lib_predates_hook_env_split` -- and deleted once the
    library is upgraded and no longer reads it.
    """
    from trw_mcp.client_profiles.session_identity import render_hook_env_session_block
    from trw_mcp.state._hook_flags import publish_hook_flags, resolve_hooks_enabled, resolve_hooks_enabled_source
    from trw_mcp.state.persistence import write_text_atomic

    env_dir = hook_env_dir(trw_dir)
    env_dir.mkdir(parents=True, exist_ok=True)
    resolved_key = key if key is not None else _hook_env_key(profile)
    path = env_dir / f"{resolved_key}.sh"
    nudge_flag = "true" if profile.nudge_enabled else "false"
    # SECURITY: shell-quote every embedded value -- see the equivalent note that
    # used to live here, now on this same content block: display_name /
    # config_dir are profile-derived and a value containing shell metacharacters
    # would otherwise inject into a script every hook `source`s at startup.
    content = (
        "# TRW hook environment (generated by instructions sync / init-project)\n"
        "# PRD-CORE-149 FR04: surfaces per-profile hook flags + client identity.\n"
        "# One file PER CLIENT -- see _write_hook_env_file / lib-trw.sh for why.\n"
        f"export NUDGE_ENABLED={shlex.quote(nudge_flag)}\n"
        f"export TRW_CLIENT_DISPLAY_NAME={shlex.quote(profile.display_name)}\n"
        f"export TRW_CLIENT_CONFIG_DIR={shlex.quote(profile.config_dir)}\n"
        + render_hook_env_session_block(profile.client_id)
    )
    # Every hook sources this file; an atomic replace never shows one a
    # truncated script.
    write_text_atomic(path, content, mode=0o600)
    legacy_shared_path = trw_dir / "runtime" / "hook-env.sh"
    if _installed_lib_predates_hook_env_split(trw_dir):
        # An old installed library still sources this file directly. Keep it
        # alive with the SAME content a pre-split install would have written --
        # last-writer-wins across multiple clients, exactly the pre-split
        # behavior, so this is no worse than status quo for a project that has
        # not yet refreshed its hook scripts.
        write_text_atomic(legacy_shared_path, content, mode=0o600)
    else:
        # No installed library, or an already-upgraded one: TRW-generated,
        # never user content, and no hook reads it any more -- deleted
        # opportunistically so a project never carries a stale artifact an
        # operator could mistake for still-authoritative. Best-effort: a
        # failed unlink must not block the real per-client write above.
        try:
            legacy_shared_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("hook_env_legacy_cleanup_failed", path=str(legacy_shared_path))
    publish_hook_flags(trw_dir)
    if profile.hooks_enabled and not resolve_hooks_enabled(trw_dir):
        source = resolve_hooks_enabled_source(trw_dir)
        message = (
            f"{profile.display_name} ({profile.client_id}) installs TRW hooks, but hooks_enabled resolves to "
            f"false ({source}), so they will not run. Remove or flip that setting to re-enable "
            f"{profile.display_name}'s hooks."
        )
        logger.warning(
            "hook_env_client_hooks_disabled_by_resolved_config",
            client_id=profile.client_id,
            display_name=profile.display_name,
            source=source,
        )
        if warnings is not None:
            warnings.append(message)
    logger.debug(
        "hook_env_written",
        path=str(path),
        client_id=profile.client_id,
        key=resolved_key,
        hooks_enabled=profile.hooks_enabled,
        nudge_enabled=profile.nudge_enabled,
    )
    return path


def write_hook_env_for_clients(
    trw_dir: Path,
    client_ids: list[str],
    *,
    warnings: list[str] | None = None,
) -> list[Path]:
    """Write ``hook-env.d/<key>.sh`` for every resolved client in *client_ids*.

    init/update install integrations for EVERY resolved client
    (``run_install_integrations`` / ``run_update_integrations``), so the
    hook-env write must cover the same set -- writing only ``client_ids[0]``
    left every other installed client's file stale (or, for a client sharing
    the ``.claude/hooks`` surface, silently governed by whichever profile
    happened to be first). Falls back to ``claude-code`` when *client_ids* is
    empty. Duplicate ids are written once. Fail-open per client: one profile's
    write failure is logged and does not stop the rest.
    """
    from trw_mcp.models.config._profiles import resolve_client_profile

    written: list[Path] = []
    seen: set[str] = set()
    for client_id in client_ids or ["claude-code"]:
        if client_id in seen:
            continue
        seen.add(client_id)
        try:
            profile = resolve_client_profile(client_id)
            written.append(_write_hook_env_file(trw_dir, profile, warnings=warnings))
        except Exception as exc:  # justified: fail-open, hook-env is best-effort, one client must not block another
            logger.warning("hook_env_write_failed", error=str(exc), client_id=client_id)
    return written

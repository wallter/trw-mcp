"""Client-aware scaffold sinks for ``init-project`` (PRD-CORE-262-FR05).

Both functions here used to write unconditionally: ``_create_directory_structure``
created ``.claude/hooks``, ``.claude/skills`` and ``.claude/agents`` for every
install, and ``_copy_bundled_data_files`` dropped Claude Code's
``settings.json`` next to them. A codex-only ``init-project`` therefore produced
47 files under a directory none of its selected clients read.

They now take the resolved target list, the same way ``_install_agents`` already
does. Extracted into their own module so ``_init_project`` stays under the
350-effective-LOC gate; the two names are re-exported from there for the
existing importers.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from trw_mcp.canons.registry import install_view, load_registry

from ._utils import _DATA_DIR, ProgressCallback, _copy_file, _ensure_dir


def _create_directory_structure(
    target_dir: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
    *,
    clients: Sequence[str] = ("claude-code",),
    explicit: bool = False,
) -> None:
    """Create the TRW directory scaffold inside *target_dir*.

    Client-owned directories (``.claude/**``) are created only for a client that
    reads them; the shared ``.trw`` tree and ``docs`` are unconditional.

    *explicit* (CORE262-13) says whether *clients* came from a user ``--ide``
    choice rather than ``detect_ide``. A pre-existing ``.codex/`` marker makes
    a bare ``init-project`` resolve to ``["codex"]`` too, and that auto-detected
    case must keep HEAD's default scaffold -- only an EXPLICIT codex-only
    request may drop it.
    """
    from . import _TRW_DIRS, _client_scaffold_dirs

    for rel_dir in [*_TRW_DIRS, *_client_scaffold_dirs(clients, explicit=explicit)]:
        _ensure_dir(target_dir / rel_dir, result, on_progress)


def _copy_bundled_data_files(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
    *,
    clients: Sequence[str] = ("claude-code",),
    explicit: bool = False,
) -> None:
    """Copy the bundled data files for *clients* to *target_dir*.

    ``_DATA_FILE_MAP`` is the client-neutral set; ``_client_data_files`` adds
    the rows a single client owns (``.claude/settings.json``). *explicit* has
    the same CORE262-13 meaning as in :func:`_create_directory_structure`.
    """
    from . import _DATA_FILE_MAP, _client_data_files

    # Canon bodies are promoted together with VERSION.yaml and DEPLOYMENT.json
    # by _write_version_yaml after config creation. Writing them here would
    # expose a mixed generation if init were interrupted.
    canon_destinations = {
        destination for _, destination in install_view(load_registry()) if destination.startswith(".trw/frameworks/")
    }
    for data_name, dest_rel in [*_DATA_FILE_MAP, *_client_data_files(clients, explicit=explicit)]:
        if dest_rel in canon_destinations:
            continue
        _copy_file(_DATA_DIR / data_name, target_dir / dest_rel, force, result, on_progress)

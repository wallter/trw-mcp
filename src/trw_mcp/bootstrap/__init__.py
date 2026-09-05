"""Project bootstrap — sets up and updates TRW framework in a target directory.

PRD-INFRA-006: ``trw-mcp init-project`` CLI command that copies all
required framework files into a target git repository.

``trw-mcp update-project`` selectively updates framework files (hooks,
skills, agents, FRAMEWORK.md) while preserving user-customized files
(config.yaml, learnings, CLAUDE.md user sections).
"""

from __future__ import annotations

# Re-export ``shutil`` so that existing test patches targeting
# ``trw_mcp.bootstrap.shutil.rmtree`` continue to resolve correctly.
import shutil as shutil  # explicit re-export for test compat
from collections.abc import Sequence

from trw_mcp.canons.registry import install_view, load_registry

from ._codex import (
    generate_codex_config as generate_codex_config,
)
from ._codex import (
    generate_codex_hooks as generate_codex_hooks,
)
from ._codex import (
    install_codex_skills as install_codex_skills,
)
from ._copilot import (
    generate_copilot_hooks as generate_copilot_hooks,
)
from ._copilot import (
    generate_copilot_instructions as generate_copilot_instructions,
)
from ._copilot import (
    generate_copilot_path_instructions as generate_copilot_path_instructions,
)
from ._copilot import (
    install_copilot_skills as install_copilot_skills,
)
from ._cursor import (
    _get_trw_mcp_entry_cursor as _get_trw_mcp_entry_cursor,
)
from ._cursor import (
    _write_fresh_mcp as _write_fresh_mcp,
)
from ._cursor import (
    generate_cursor_hooks as generate_cursor_hooks,
)
from ._cursor import (
    generate_cursor_mcp_config as generate_cursor_mcp_config,
)
from ._cursor import (
    generate_cursor_rules as generate_cursor_rules,
)
from ._init_project import (
    _copy_bundled_data_files as _copy_bundled_data_files,
)
from ._init_project import (
    _create_directory_structure as _create_directory_structure,
)
from ._init_project import (
    _generate_root_files as _generate_root_files,
)
from ._init_project import (
    _install_agents as _install_agents,
)
from ._init_project import (
    _install_hooks as _install_hooks,
)
from ._init_project import (
    _install_skills as _install_skills,
)
from ._init_project import (
    _validate_skill as _validate_skill,
)
from ._init_project import (
    _write_initial_config as _write_initial_config,
)
from ._init_project import (
    init_project as init_project,
)
from ._opencode import (
    _TRW_END_MARKER as _OPENCODE_TRW_END_MARKER,
)
from ._opencode import (
    _TRW_HEADER as _OPENCODE_TRW_HEADER,
)
from ._opencode import (
    _TRW_START_MARKER as _OPENCODE_TRW_START_MARKER,
)
from ._opencode import (
    _get_trw_mcp_entry as _get_trw_mcp_entry,
)
from ._opencode import (
    _parse_jsonc as _parse_jsonc,
)
from ._opencode import (
    generate_agents_md as generate_agents_md,
)
from ._opencode import (
    generate_opencode_config as generate_opencode_config,
)
from ._opencode import (
    merge_opencode_json as merge_opencode_json,
)
from ._update_project import (
    _CONTEXT_ALLOWLIST as _CONTEXT_ALLOWLIST,
)
from ._update_project import (
    _MANIFEST_FILE as _MANIFEST_FILE,
)
from ._update_project import (
    _TRW_END_MARKER as _TRW_END_MARKER,
)
from ._update_project import (
    _TRW_HEADER_MARKER as _TRW_HEADER_MARKER,
)
from ._update_project import (
    _TRW_START_MARKER as _TRW_START_MARKER,
)
from ._update_project import (
    PREDECESSOR_MAP as PREDECESSOR_MAP,
)
from ._update_project import (
    _cleanup_context_transients as _cleanup_context_transients,
)
from ._update_project import (
    _cleanup_stale_artifacts as _cleanup_stale_artifacts,
)
from ._update_project import (
    _extract_trw_section_content as _extract_trw_section_content,
)
from ._update_project import (
    _get_bundled_names as _get_bundled_names,
)
from ._update_project import (
    _get_custom_names as _get_custom_names,
)
from ._update_project import (
    _migrate_prefix_predecessors as _migrate_prefix_predecessors,
)
from ._update_project import (
    _minimal_claude_md_trw_block as _minimal_claude_md_trw_block,
)
from ._update_project import (
    _read_manifest as _read_manifest,
)
from ._update_project import (
    _remove_stale_artifacts as _remove_stale_artifacts,
)
from ._update_project import (
    _report_preserved_files as _report_preserved_files,
)
from ._update_project import (
    _run_claude_md_sync as _run_claude_md_sync,
)
from ._update_project import (
    _update_agents as _update_agents,
)
from ._update_project import (
    _update_always_overwrite_files as _update_always_overwrite_files,
)
from ._update_project import (
    _update_claude_md_trw_section as _update_claude_md_trw_section,
)
from ._update_project import (
    _update_codex_artifacts as _update_codex_artifacts,
)
from ._update_project import (
    _update_copilot_artifacts as _update_copilot_artifacts,
)
from ._update_project import (
    _update_cursor_artifacts as _update_cursor_artifacts,
)
from ._update_project import (
    _update_framework_files as _update_framework_files,
)
from ._update_project import (
    _update_hooks as _update_hooks,
)
from ._update_project import (
    _update_mcp_config as _update_mcp_config,
)
from ._update_project import (
    _update_opencode_artifacts as _update_opencode_artifacts,
)
from ._update_project import (
    _update_or_report as _update_or_report,
)
from ._update_project import (
    _update_skills as _update_skills,
)
from ._update_project import (
    _write_manifest as _write_manifest,
)
from ._update_project import (
    update_project as update_project,
)
from ._utils import (
    _DATA_DIR as _DATA_DIR,
)
from ._utils import (
    SUPPORTED_IDES as SUPPORTED_IDES,
)
from ._utils import (
    ProgressCallback as ProgressCallback,
)
from ._utils import (
    _check_package_version as _check_package_version,
)
from ._utils import (
    _copy_file as _copy_file,
)
from ._utils import (
    _default_config as _default_config,
)
from ._utils import (
    _ensure_dir as _ensure_dir,
)
from ._utils import (
    _files_identical as _files_identical,
)
from ._utils import (
    _generate_mcp_json as _generate_mcp_json,
)
from ._utils import (
    _merge_mcp_json as _merge_mcp_json,
)
from ._utils import (
    _minimal_claude_md as _minimal_claude_md,
)
from ._utils import (
    _pip_install_package as _pip_install_package,
)
from ._utils import (
    _result_action_key as _result_action_key,
)
from ._utils import (
    _trw_mcp_server_entry as _trw_mcp_server_entry,
)
from ._utils import (
    _verify_installation as _verify_installation,
)
from ._utils import (
    _write_if_missing as _write_if_missing,
)
from ._utils import (
    _write_installer_metadata as _write_installer_metadata,
)
from ._utils import (
    _write_version_yaml as _write_version_yaml,
)
from ._utils import (
    detect_ide as detect_ide,
)
from ._utils import (
    detect_installed_clis as detect_installed_clis,
)
from ._utils import (
    resolve_ide_targets as resolve_ide_targets,
)

# Client-neutral directories to scaffold inside the target repo. Every install
# gets these regardless of which client was selected (PRD-CORE-262-FR05).
_TRW_DIRS = [
    ".trw/frameworks",
    ".trw/context",
    ".trw/templates",
    ".trw/learnings/entries",
    ".trw/scripts",
    ".trw/runs",
    "docs",
]

# Claude Code's own scaffold directories (PRD-CORE-262-FR05). These used to
# sit unconditionally in ``_TRW_DIRS``, so a codex-only ``init-project``
# created a ``.claude`` tree and then filled it with 47 files no selected
# client loads.
#
# The exclusion is narrow and specific to ONE selection, not to "any client
# that isn't claude-code": measured evidence (release rehearsal, 2026-09-04)
# is a codex-ONLY install; RISK-008's own mitigation is "a project selecting
# several clients keeps every one". A bare ``init-project`` and every other
# single-client selection (cursor-ide, opencode, copilot, cursor-cli,
# antigravity-cli) keep scaffolding ``.claude/**`` exactly as HEAD did before
# this PRD -- only ``codex`` used alone drops it.
_CLAUDE_SCAFFOLD_DIRS: tuple[str, ...] = (".claude/hooks", ".claude/skills", ".claude/agents")

# The one resolved target set that excludes the Claude Code scaffold.
_CODEX_ONLY = frozenset({"codex"})


def _wants_claude_scaffold(clients: Sequence[str], *, explicit: bool = False) -> bool:
    """False only when *clients* is codex-only AND the user asked for it explicitly.

    CORE262-13: a bare/default ``init-project`` (``ide=None``) that resolves
    through ``detect_ide`` to ``["codex"]`` -- because the target directory
    already has a ``.codex/`` marker on disk -- is NOT a user request for
    codex-only. Before *explicit* existed here, that auto-detected case took
    the same suppression path as ``ide="codex"`` and silently lost HEAD's
    default scaffold. Only an EXPLICIT ``--ide codex`` (or ``ide="codex"``)
    selection may drop the Claude Code surfaces; every other path -- bare,
    detected, or any set containing another client -- keeps them.
    """
    return not (explicit and set(clients) == _CODEX_ONLY)


def _client_scaffold_dirs(clients: Sequence[str], *, explicit: bool = False) -> list[str]:
    """Return Claude Code's scaffold directories, unless *clients* is EXPLICITLY codex-only."""
    return list(_CLAUDE_SCAFFOLD_DIRS) if _wants_claude_scaffold(clients, explicit=explicit) else []


def _client_data_files(clients: Sequence[str], *, explicit: bool = False) -> list[tuple[str, str]]:
    """Return Claude Code's bundled data files, unless *clients* is EXPLICITLY codex-only."""
    return [("settings.json", ".claude/settings.json")] if _wants_claude_scaffold(clients, explicit=explicit) else []


# Mapping of bundled data files to their destination paths (relative to target).
_DATA_FILE_MAP: list[tuple[str, str]] = [
    *install_view(load_registry()),
    ("behavioral_protocol.yaml", ".trw/context/behavioral_protocol.yaml"),
    ("messages/messages.yaml", ".trw/context/messages.yaml"),
    ("templates/claude_md.md", ".trw/templates/claude_md.md"),
    ("gitignore.txt", ".trw/.gitignore"),
]

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
import shutil as shutil  # noqa: PLC0414  # explicit re-export for test compat

from ._init_project import (
    _copy_bundled_data_files as _copy_bundled_data_files,
    _create_directory_structure as _create_directory_structure,
    _generate_root_files as _generate_root_files,
    _install_agents as _install_agents,
    _install_hooks as _install_hooks,
    _install_skills as _install_skills,
    _write_initial_config as _write_initial_config,
    init_project as init_project,
)
from ._update_project import (
    _CONTEXT_ALLOWLIST as _CONTEXT_ALLOWLIST,
    _MANIFEST_FILE as _MANIFEST_FILE,
    _TRW_END_MARKER as _TRW_END_MARKER,
    _TRW_HEADER_MARKER as _TRW_HEADER_MARKER,
    _TRW_START_MARKER as _TRW_START_MARKER,
    PREDECESSOR_MAP as PREDECESSOR_MAP,
    _cleanup_context_transients as _cleanup_context_transients,
    _cleanup_stale_artifacts as _cleanup_stale_artifacts,
    _get_bundled_names as _get_bundled_names,
    _get_custom_names as _get_custom_names,
    _migrate_prefix_predecessors as _migrate_prefix_predecessors,
    _minimal_claude_md_trw_block as _minimal_claude_md_trw_block,
    _read_manifest as _read_manifest,
    _remove_stale_artifacts as _remove_stale_artifacts,
    _report_preserved_files as _report_preserved_files,
    _run_claude_md_sync as _run_claude_md_sync,
    _update_agents as _update_agents,
    _update_always_overwrite_files as _update_always_overwrite_files,
    _update_claude_md_trw_section as _update_claude_md_trw_section,
    _update_framework_files as _update_framework_files,
    _update_hooks as _update_hooks,
    _update_mcp_config as _update_mcp_config,
    _update_or_report as _update_or_report,
    _update_skills as _update_skills,
    _write_manifest as _write_manifest,
    update_project as update_project,
)
from ._utils import (
    _DATA_DIR as _DATA_DIR,
    _check_package_version as _check_package_version,
    _copy_file as _copy_file,
    _default_config as _default_config,
    _ensure_dir as _ensure_dir,
    _files_identical as _files_identical,
    _generate_mcp_json as _generate_mcp_json,
    _merge_mcp_json as _merge_mcp_json,
    _minimal_claude_md as _minimal_claude_md,
    _pip_install_package as _pip_install_package,
    _trw_mcp_server_entry as _trw_mcp_server_entry,
    _verify_installation as _verify_installation,
    _write_if_missing as _write_if_missing,
    _write_installer_metadata as _write_installer_metadata,
)

# Directories to scaffold inside the target repo.
_TRW_DIRS = [
    ".trw/frameworks",
    ".trw/context",
    ".trw/templates",
    ".trw/learnings/entries",
    ".trw/scripts",
    ".claude/hooks",
    ".claude/skills",
    ".claude/agents",
]

# Mapping of bundled data files to their destination paths (relative to target).
_DATA_FILE_MAP: list[tuple[str, str]] = [
    ("framework.md", ".trw/frameworks/FRAMEWORK.md"),
    ("framework.md", "FRAMEWORK.md"),
    ("behavioral_protocol.yaml", ".trw/context/behavioral_protocol.yaml"),
    ("messages/messages.yaml", ".trw/context/messages.yaml"),
    ("templates/claude_md.md", ".trw/templates/claude_md.md"),
    ("gitignore.txt", ".trw/.gitignore"),
    ("settings.json", ".claude/settings.json"),
]

__all__ = [
    # Constants
    "_DATA_DIR",
    "_DATA_FILE_MAP",
    "_TRW_DIRS",
    "_CONTEXT_ALLOWLIST",
    "_MANIFEST_FILE",
    "_TRW_END_MARKER",
    "_TRW_HEADER_MARKER",
    "_TRW_START_MARKER",
    "PREDECESSOR_MAP",
    # Utils
    "_check_package_version",
    "_copy_file",
    "_default_config",
    "_ensure_dir",
    "_files_identical",
    "_generate_mcp_json",
    "_merge_mcp_json",
    "_minimal_claude_md",
    "_pip_install_package",
    "_trw_mcp_server_entry",
    "_verify_installation",
    "_write_if_missing",
    "_write_installer_metadata",
    # Init project
    "_copy_bundled_data_files",
    "_create_directory_structure",
    "_generate_root_files",
    "_install_agents",
    "_install_hooks",
    "_install_skills",
    "_write_initial_config",
    "init_project",
    # Update project
    "_cleanup_context_transients",
    "_cleanup_stale_artifacts",
    "_get_bundled_names",
    "_get_custom_names",
    "_migrate_prefix_predecessors",
    "_minimal_claude_md_trw_block",
    "_read_manifest",
    "_remove_stale_artifacts",
    "_report_preserved_files",
    "_run_claude_md_sync",
    "_update_agents",
    "_update_always_overwrite_files",
    "_update_claude_md_trw_section",
    "_update_framework_files",
    "_update_hooks",
    "_update_mcp_config",
    "_update_or_report",
    "_update_skills",
    "_write_manifest",
    "update_project",
]

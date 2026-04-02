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

from ._codex import (
    generate_codex_agents as generate_codex_agents,
)
from ._codex import (
    generate_codex_config as generate_codex_config,
)
from ._codex import (
    generate_codex_hooks as generate_codex_hooks,
)
from ._codex import (
    install_codex_skills as install_codex_skills,
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

# Directories to scaffold inside the target repo.
_TRW_DIRS = [
    ".trw/frameworks",
    ".trw/context",
    ".trw/templates",
    ".trw/learnings/entries",
    ".trw/scripts",
    ".trw/runs",
    ".claude/hooks",
    ".claude/skills",
    ".claude/agents",
    "docs",
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
    ("trw_readme.md", "docs/TRW_README.md"),
    ("config_reference.md", "docs/CONFIG-REFERENCE.md"),
]

__all__ = [
    "PREDECESSOR_MAP",
    "SUPPORTED_IDES",
    "_CONTEXT_ALLOWLIST",
    "_DATA_DIR",
    "_DATA_FILE_MAP",
    "_MANIFEST_FILE",
    "_OPENCODE_TRW_END_MARKER",
    "_OPENCODE_TRW_HEADER",
    "_OPENCODE_TRW_START_MARKER",
    "_TRW_DIRS",
    "_TRW_END_MARKER",
    "_TRW_HEADER_MARKER",
    "_TRW_START_MARKER",
    "ProgressCallback",
    "_check_package_version",
    "_cleanup_context_transients",
    "_cleanup_stale_artifacts",
    "_copy_bundled_data_files",
    "_copy_file",
    "_create_directory_structure",
    "_default_config",
    "_ensure_dir",
    "_extract_trw_section_content",
    "_files_identical",
    "_generate_mcp_json",
    "_generate_root_files",
    "_get_bundled_names",
    "_get_custom_names",
    "_get_trw_mcp_entry",
    "_get_trw_mcp_entry_cursor",
    "_install_agents",
    "_install_hooks",
    "_install_skills",
    "_merge_mcp_json",
    "_migrate_prefix_predecessors",
    "_minimal_claude_md",
    "_minimal_claude_md_trw_block",
    "_parse_jsonc",
    "_pip_install_package",
    "_read_manifest",
    "_remove_stale_artifacts",
    "_report_preserved_files",
    "_result_action_key",
    "_run_claude_md_sync",
    "_trw_mcp_server_entry",
    "_update_agents",
    "_update_always_overwrite_files",
    "_update_claude_md_trw_section",
    "_update_cursor_artifacts",
    "_update_framework_files",
    "_update_hooks",
    "_update_mcp_config",
    "_update_opencode_artifacts",
    "_update_or_report",
    "_update_skills",
    "_validate_skill",
    "_verify_installation",
    "_write_fresh_mcp",
    "_write_if_missing",
    "_write_initial_config",
    "_write_installer_metadata",
    "_write_manifest",
    "_write_version_yaml",
    "detect_ide",
    "detect_installed_clis",
    "generate_agents_md",
    "generate_cursor_hooks",
    "generate_cursor_mcp_config",
    "generate_cursor_rules",
    "generate_opencode_config",
    "init_project",
    "merge_opencode_json",
    "resolve_ide_targets",
    "update_project",
]

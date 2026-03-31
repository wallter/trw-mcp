"""Directory structure, project source paths, platform, and MCP transport fields.

Covers sections 13, 27, 37, 38, 40 of the original _main_fields.py:
  - Directory structure & paths
  - Project source paths
  - Platform & update channel
  - Knowledge topology (CORE-021)
  - MCP transport
"""

from __future__ import annotations

from pydantic import Field, SecretStr


class _PathsFields:
    """Paths domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Directory structure & paths --

    task_root: str = "docs"
    runs_root: str = ".trw/runs"
    trw_dir: str = ".trw"
    worktree_dir: str = ".trees"
    learnings_dir: str = "learnings"
    entries_dir: str = "entries"
    receipts_dir: str = "receipts"
    reflections_dir: str = "reflections"
    scripts_dir: str = "scripts"
    patterns_dir: str = "patterns"
    context_dir: str = "context"
    scratch_dir: str = "scratch"
    events_file: str = "events.jsonl"
    checkpoints_file: str = "checkpoints.jsonl"
    frameworks_dir: str = "frameworks"
    templates_dir: str = "templates"

    # -- Project source paths --

    source_package_path: str = "trw-mcp/src"
    source_package_name: str = "trw_mcp"
    tests_relative_path: str = "trw-mcp/tests"
    test_map_filename: str = "test-map.yaml"

    # -- Platform & update channel --

    platform_telemetry_enabled: bool = False
    update_channel: str = "latest"
    platform_url: str = ""
    platform_urls: list[str] = Field(default_factory=list)
    platform_api_key: SecretStr = SecretStr("")
    installation_id: str = ""
    auto_upgrade: bool = False

    # -- Knowledge topology (CORE-021) --

    knowledge_sync_threshold: int = 50
    knowledge_jaccard_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    knowledge_min_cluster_size: int = Field(default=3, ge=1)
    knowledge_output_dir: str = "knowledge"

    # -- MCP transport --

    mcp_transport: str = "stdio"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8100

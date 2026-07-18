"""Shared bootstrap utilities — config generators, IDE detection, verification.

File operations live in ``_file_ops.py``; MCP JSON helpers live in
``_mcp_json.py``.  All public names are re-exported here so existing
import paths (``from trw_mcp.bootstrap._utils import X``) are preserved.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python <3.11 fallback
    import tomli as tomllib

# ---------------------------------------------------------------------------
# Re-exports from extracted sub-modules — REQUIRED for backward compatibility.
#
# Tests and external consumers import ``from trw_mcp.bootstrap._utils import X``
# directly.  These re-exports ensure those import paths still resolve.
# ---------------------------------------------------------------------------
from ._config_templates import _default_config as _default_config
from ._config_templates import _minimal_claude_md as _minimal_claude_md
from ._config_templates import _minimal_review_md as _minimal_review_md
from ._file_ops import ProgressCallback as ProgressCallback
from ._file_ops import _copy_file as _copy_file
from ._file_ops import _ensure_dir as _ensure_dir
from ._file_ops import _files_identical as _files_identical
from ._file_ops import _new_result as _new_result
from ._file_ops import _record_write as _record_write
from ._file_ops import _result_action_key as _result_action_key
from ._file_ops import _write_if_missing as _write_if_missing
from ._mcp_json import _generate_mcp_json as _generate_mcp_json
from ._mcp_json import _merge_mcp_json as _merge_mcp_json
from ._mcp_json import _pip_install_package as _pip_install_package

logger = structlog.get_logger(__name__)

# Resolve to ``src/trw_mcp/data/``.
# When this file lived at ``src/trw_mcp/bootstrap.py``, the path was
# ``Path(__file__).parent / "data"``.  Now that it lives at
# ``src/trw_mcp/bootstrap/_utils.py``, we need one extra ``.parent``.
_DATA_DIR = Path(__file__).parent.parent / "data"


# ---------------------------------------------------------------------------
# MCP server entry (kept here because tests patch trw_mcp.bootstrap._utils.shutil)
# ---------------------------------------------------------------------------


def _trw_mcp_server_entry() -> dict[str, object]:
    """Build the ``trw`` MCP server entry for .mcp.json.

    Prefers the ``trw-mcp`` console script when it is on PATH (the normal
    install). Falls back to a PORTABLE ``python3 -m trw_mcp.server`` invocation
    rather than the machine-absolute ``sys.executable`` (PRD-SEC-006, audit
    installer-client-12): committing a build-machine interpreter path into a
    project's ``.mcp.json`` breaks the config on every other machine and leaks
    a host-specific path. The default ``--debug`` flag is dropped — verbose
    logging is opt-in, not a baked-in default.
    """
    if shutil.which("trw-mcp"):
        return {"command": "trw-mcp", "args": []}
    # Portable fallback: a bare ``python3`` resolves per-machine via PATH and is
    # not flagged as a user-customized entry (which would block refresh).
    return {"command": "python3", "args": ["-m", "trw_mcp.server"]}


# ---------------------------------------------------------------------------
# Config generators
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# VERSION.yaml generation (DRY — derived from package metadata)
# ---------------------------------------------------------------------------


def _write_version_yaml(
    target_dir: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Generate ``.trw/frameworks/VERSION.yaml`` from package metadata.

    Derived values (no static file to maintain):
    - ``framework_version``: from TRWConfig default
    - ``aaref_version``: from TRWConfig default
    - ``trw_mcp_version``: from installed package metadata
    - ``deployed_at``: current UTC timestamp
    """
    from trw_mcp import __version__ as pkg_version
    from trw_mcp.canons.registry import bundled_manifest_bytes, load_registry
    from trw_mcp.framework_integrity import repair_framework_runtime
    from trw_mcp.models.config import get_config

    config = get_config()
    version_path = target_dir / ".trw" / "frameworks" / "VERSION.yaml"
    managed_paths = (
        target_dir / ".trw/frameworks/FRAMEWORK.md",
        target_dir / ".trw/frameworks/AARE-F-FRAMEWORK.md",
        version_path,
    )
    try:
        # Snapshot pre-existence BEFORE the repair writes, so a file that was
        # missing (e.g. user-deleted FRAMEWORK.md) is truthfully reported as
        # "created" on recreation, not blanket-classified by flow type.
        preexisting = {path: path.exists() for path in managed_paths}
        registry = load_registry(bundled_manifest_bytes())
        compiled_artifacts: dict[Path, bytes] = {}
        for canon in registry.compiled_canons:
            compiled_artifacts[Path(canon.runtime_compact_core)] = (
                _DATA_DIR / Path(canon.compact_core).name
            ).read_bytes()
            compiled_artifacts[Path(canon.runtime_reference)] = (_DATA_DIR / Path(canon.reference).name).read_bytes()
        repair_framework_runtime(
            target_dir,
            framework_source=(_DATA_DIR / "framework.md").read_text(encoding="utf-8"),
            aaref_source=(_DATA_DIR / "aaref.md").read_text(encoding="utf-8"),
            framework_version=config.framework_version,
            aaref_version=config.aaref_version,
            trw_mcp_version=pkg_version,
            registry_digest=registry.digest,
            additional_artifacts=compiled_artifacts,
        )
        logger.debug(
            "version_yaml_generated",
            path=str(version_path),
            framework=config.framework_version,
            trw_mcp=pkg_version,
        )
        fallback_key = _result_action_key(result)
        for path in managed_paths:
            key = fallback_key if preexisting[path] else "created"
            result[key].append(str(path))
            if on_progress:
                on_progress("Created" if key == "created" else "Updated", str(path))
    except OSError as exc:  # justified: boundary, file write may fail
        logger.warning("version_yaml_write_failed", path=str(version_path), error=str(exc))
        result["errors"].append(f"Failed to write {version_path}: {exc}")
        if on_progress:
            on_progress("Error", str(version_path))


# ---------------------------------------------------------------------------
# Installer metadata & verification
# ---------------------------------------------------------------------------


def _write_installer_metadata(
    target_dir: Path,
    action: str,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Write ``.trw/installer-meta.yaml`` with historical install-time metadata.

    PRD-INFRA-164 FR11 / D-26: this record is an explicit HISTORICAL install
    snapshot, never a current runtime authority. It uses the unambiguous v2
    install-time field names (``framework_version_at_install``,
    ``aaref_version_at_install``, ``trw_mcp_version_at_install``, ``recorded_at``)
    and carries ``record_kind=historical_install_snapshot``. Legacy scalar fields
    (``framework_version``/``package_version``) are retained for one release as
    read-only compatibility (NFR06); status/audit read them only as history and
    never as current equality. VERSION.yaml and the live-process fingerprint
    remain the current installed/process authorities.
    """
    from trw_mcp import __version__ as pkg_version
    from trw_mcp.models.config import get_config

    config = get_config()

    # Count deployed artifacts
    hooks_dir = target_dir / ".claude" / "hooks"
    skills_dir = target_dir / ".claude" / "skills"
    agents_dir = target_dir / ".claude" / "agents"
    hooks_count = len(list(hooks_dir.glob("*.sh"))) if hooks_dir.is_dir() else 0
    skills_count = len([d for d in skills_dir.iterdir() if d.is_dir()]) if skills_dir.is_dir() else 0
    agents_count = len(list(agents_dir.glob("*.md"))) if agents_dir.is_dir() else 0

    recorded_at = datetime.now(timezone.utc).isoformat()
    meta = {
        # v2 historical install-time schema (D-26): unambiguous *_at_install names.
        "record_kind": "historical_install_snapshot",
        "installer_meta_schema_version": 2,
        "framework_version_at_install": config.framework_version,
        "aaref_version_at_install": config.aaref_version,
        "trw_mcp_version_at_install": pkg_version,
        "recorded_at": recorded_at,
        # Legacy fields retained one release for compatibility (NFR06) — history only.
        "framework_version": config.framework_version,
        "package_version": pkg_version,
        "last_updated": recorded_at,
        "installed_by": f"trw-mcp {action}",
        "hooks_count": hooks_count,
        "skills_count": skills_count,
        "agents_count": agents_count,
    }
    meta_path = target_dir / ".trw" / "installer-meta.yaml"
    try:
        from trw_mcp.state.persistence import FileStateWriter

        writer = FileStateWriter()
        writer.write_yaml(meta_path, meta)
        # init_project uses "created", update_project uses "updated"
        key = _result_action_key(result)
        result[key].append(str(meta_path))
        if on_progress:
            on_progress("Created" if key == "created" else "Updated", str(meta_path))
    except OSError as exc:
        result["errors"].append(f"Failed to write {meta_path}: {exc}")
        if on_progress:
            on_progress("Error", str(meta_path))


def _verify_installation(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Run lightweight post-update health checks.

    Verifies hooks are executable, .mcp.json has trw entry, and
    client instruction file has TRW markers.  Adds warnings for any failures.
    """
    # Check hooks are executable
    hooks_dir = target_dir / ".claude" / "hooks"
    if hooks_dir.is_dir():
        for hook in hooks_dir.glob("*.sh"):
            if not os.access(hook, os.X_OK):
                result["warnings"].append(f"Hook not executable: {hook.name}")

    # Check .mcp.json has trw entry
    mcp_path = target_dir / ".mcp.json"
    if mcp_path.exists():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
            if "trw" not in data.get("mcpServers", {}):
                result["warnings"].append(".mcp.json missing 'trw' server entry")
        except (json.JSONDecodeError, OSError):
            result["warnings"].append(".mcp.json is not valid JSON")
    else:
        result["warnings"].append(".mcp.json not found")

    codex_config = target_dir / ".codex" / "config.toml"
    if codex_config.exists():
        try:
            data = tomllib.loads(codex_config.read_text(encoding="utf-8"))
            mcp_servers = data.get("mcp_servers", {})
            if not isinstance(mcp_servers, dict) or "trw" not in mcp_servers:
                result["warnings"].append(".codex/config.toml missing TRW MCP entry")
            else:
                trw_server = mcp_servers.get("trw")
                if isinstance(trw_server, dict) and "url" in trw_server and "command" not in trw_server:
                    result["warnings"].append(
                        ".codex/config.toml uses a direct TRW MCP HTTP URL; "
                        "run update-project to restore the stdio entry"
                    )
            features = data.get("features", {})
            if isinstance(features, dict) and features.get("codex_hooks") is True:
                result["warnings"].append(
                    ".codex/config.toml uses deprecated features.codex_hooks; "
                    "run update-project to migrate to features.hooks"
                )

            agents_dir = target_dir / ".codex" / "agents"
            for agent_name in (
                "trw-explorer.toml",
                "trw-implementer.toml",
                "trw-reviewer.toml",
                "trw-docs-researcher.toml",
            ):
                agent_path = agents_dir / agent_name
                if not agent_path.exists():
                    continue
                try:
                    agent_config = tomllib.loads(agent_path.read_text(encoding="utf-8"))
                except (tomllib.TOMLDecodeError, OSError):
                    continue
                pinned_model = agent_config.get("model")
                if pinned_model in {"gpt-5.4", "gpt-5.4-mini"}:
                    result["warnings"].append(
                        f".codex/agents/{agent_name} pins legacy generated model {pinned_model}; "
                        "the file was preserved because agent files are user-editable. Remove the "
                        "model key to inherit the active Codex model, or explicitly regenerate it."
                    )
        except (tomllib.TOMLDecodeError, OSError):
            result["warnings"].append(".codex/config.toml is not valid TOML")

    # Check client instruction file has TRW markers
    from trw_mcp.bootstrap._update_project import _TRW_END_MARKER, _TRW_START_MARKER

    claude_md = target_dir / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if _TRW_START_MARKER not in content or _TRW_END_MARKER not in content:
            result["warnings"].append("client instruction file missing TRW auto-generated markers")


def _check_package_version(result: dict[str, list[str]]) -> None:
    """Compare installed trw-mcp version against source version.

    Warns if the installed package is outdated, which means server-side
    fixes (log filtering, LLM client, tool logic) won't be active.
    """
    from trw_mcp import __version__ as source_version

    try:
        installed_version = importlib.metadata.version("trw-mcp")
    except importlib.metadata.PackageNotFoundError:
        result["warnings"].append(
            "trw-mcp package not found in Python environment. Install with: pip install -e trw-mcp[dev]"
        )
        return

    if installed_version != source_version:
        result["warnings"].append(
            f"Installed trw-mcp ({installed_version}) differs from source "
            f"({source_version}). Server-side fixes require reinstall: "
            f"pip install -e trw-mcp[dev]"
        )
    else:
        result["preserved"].append(f"trw-mcp package v{installed_version} (up to date)")


# ---------------------------------------------------------------------------
# IDE Detection and Adaptive Bootstrap (FR08 -- PRD-CORE-074)
# ---------------------------------------------------------------------------

# Supported IDEs - DRY constant for all IDE target operations
# cursor-ide: interactive Cursor IDE; cursor-cli: headless cursor-agent CI surface
SUPPORTED_IDES = [
    "claude-code",
    "cursor-ide",
    "cursor-cli",
    "opencode",
    "codex",
    "copilot",
    "antigravity-cli",
]

# Retired client identifiers (2026-07-11). ``gemini`` — Google deprecated the
# Gemini CLI in favor of Antigravity CLI; ``aider`` — never had a TRW client
# adapter. Retired IDs are no longer installable (absent from SUPPORTED_IDES)
# but are still RECOGNIZED (not "unknown"): ``--ide`` selection and target
# resolution surface a 'retired' message with a migration hint instead of a
# generic rejection, and existing ``.gemini/`` installs remain uninstallable
# via ``trw-mcp uninstall`` forever (see client_profiles/catalog.py).
_RETIRED_IDES: dict[str, str] = {
    "gemini": (
        "Gemini CLI was deprecated by Google; configure antigravity-cli instead "
        "(trw-mcp update-project . --ide antigravity-cli). Existing .gemini/ files are "
        "left untouched — run trw-mcp uninstall to remove them on demand."
    ),
    "aider": "aider never had a TRW client adapter.",
}


def detect_ide(target_dir: Path) -> list[str]:
    """Detect which AI coding CLIs have configuration in the target directory.

    Returns a list of IDE identifiers from SUPPORTED_IDES.  Both cursor-ide
    and cursor-cli can be detected simultaneously on developer machines with
    both surfaces configured.

    Detection strategy (PRD-CORE-136-FR07, PRD-CORE-137-FR06):
    - cursor-cli if: .cursor/cli.json exists, OR cursor-agent on PATH AND
      CURSOR_TRACE_ID not set, OR CURSOR_API_KEY env set.
    - cursor-ide if: .cursor/ dir exists, OR CURSOR_TRACE_ID env set, OR
      cursor (IDE launcher) on PATH.
    - Both can return simultaneously on dual-surface machines.
    """
    detected: list[str] = []
    if (target_dir / ".claude").is_dir():
        detected.append("claude-code")

    # cursor-cli: detected by cli.json file, cursor-agent binary (without IDE trace),
    # or CURSOR_API_KEY env var (headless auth)
    has_cli_json = (target_dir / ".cursor" / "cli.json").is_file()
    has_cursor_agent = bool(shutil.which("cursor-agent"))
    has_cursor_trace = bool(os.environ.get("CURSOR_TRACE_ID"))
    has_cursor_api_key = bool(os.environ.get("CURSOR_API_KEY"))
    cursor_cli_detected = has_cli_json or (has_cursor_agent and not has_cursor_trace) or has_cursor_api_key
    if cursor_cli_detected:
        detected.append("cursor-cli")

    # cursor-ide: detected by .cursor/ dir, CURSOR_TRACE_ID env var (IDE auto-injects),
    # or cursor IDE launcher on PATH
    has_cursor_dir = (target_dir / ".cursor").is_dir()
    has_cursor_bin = bool(shutil.which("cursor"))
    cursor_ide_detected = has_cursor_dir or has_cursor_trace or has_cursor_bin
    if cursor_ide_detected:
        detected.append("cursor-ide")

    if (target_dir / ".opencode").is_dir() or (target_dir / "opencode.json").is_file():
        detected.append("opencode")
    if (target_dir / ".codex").is_dir() or (target_dir / ".codex" / "config.toml").is_file():
        detected.append("codex")
    agents_dir = target_dir / ".github" / "agents"
    has_copilot_agents = agents_dir.is_dir() and any(f.name.endswith(".agent.md") for f in agents_dir.iterdir())
    if (target_dir / ".github" / "copilot-instructions.md").is_file() or has_copilot_agents:
        detected.append("copilot")
    if (target_dir / ".antigravitycli").is_dir() or (target_dir / "ANTIGRAVITY.md").is_file():
        detected.append("antigravity-cli")
    return detected


def detect_installed_clis() -> list[str]:
    """Detect which AI coding CLI binaries are installed on PATH.

    Returns a list of IDE identifiers for CLIs found via shutil.which().
    """
    detected: list[str] = []
    if shutil.which("claude"):
        detected.append("claude-code")
    if shutil.which("cursor-agent"):
        detected.append("cursor-cli")
    if shutil.which("cursor"):
        detected.append("cursor-ide")
    if shutil.which("opencode"):
        detected.append("opencode")
    if shutil.which("codex"):
        detected.append("codex")
    if shutil.which("github-copilot") or shutil.which("copilot"):
        detected.append("copilot")
    if shutil.which("antigravity-cli"):
        detected.append("antigravity-cli")
    return detected


def is_git_repo(target_dir: Path) -> bool:
    """Return True if *target_dir* looks like a git repository root.

    Symlink-safe: ``Path.exists()`` follows symlinks, so a symlinked ``.git``
    pointing at an attacker-chosen (or unrelated) location could fool a naive
    guard into scaffolding TRW into the wrong tree. We require ``.git`` to be a
    real directory (standard repo) or a regular file (git worktree / submodule
    gitfile), and explicitly reject a symlink at that path.

    Args:
        target_dir: Candidate repository root.

    Returns:
        True if ``target_dir/.git`` is a non-symlink directory or regular file.
    """
    git_path = target_dir / ".git"
    if git_path.is_symlink():
        return False
    # is_dir()/is_file() do not follow into a non-existent target and return
    # False for a dangling path, so this also rejects a broken .git symlink
    # (already excluded above) and a missing .git.
    return git_path.is_dir() or git_path.is_file()


def resolve_ide_targets(
    target_dir: Path,
    ide_override: str | None = None,
) -> list[str]:
    """Resolve which IDEs to configure.

    Args:
        target_dir: Project directory to check for existing IDE configs.
        ide_override: Explicit IDE selection ("claude-code", "cursor-ide", "cursor-cli", "opencode", "codex", "all").
            If provided, overrides auto-detection. An unrecognized value is
            rejected (the override is ignored and auto-detection is used) so a
            caller cannot inject an arbitrary string as a scaffolding target.

    Returns:
        List of IDE identifiers to configure.
    """
    if ide_override == "all":
        return SUPPORTED_IDES.copy()
    if ide_override:
        if ide_override in SUPPORTED_IDES:
            return [ide_override]
        if ide_override in _RETIRED_IDES:
            # Retired (not unknown): recognized but no longer installable. Surface
            # a 'retired' message with the migration hint and fall back to
            # detection so the retired id is dropped rather than scaffolded.
            logger.warning(
                "ide_override_retired",
                ide_override=ide_override,
                migration_hint=_RETIRED_IDES[ide_override],
            )
        else:
            # Unknown override — do NOT use it as a target. Fall back to detection.
            logger.warning(
                "ide_override_rejected",
                ide_override=ide_override,
                supported=SUPPORTED_IDES,
            )
    detected = detect_ide(target_dir)
    return detected or ["claude-code"]  # default to Claude Code


# ---------------------------------------------------------------------------
# client instruction file content generators
# ---------------------------------------------------------------------------

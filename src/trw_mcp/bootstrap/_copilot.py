"""Copilot CLI-specific bootstrap configuration.

Generates and smart-merges repo-scoped Copilot artifacts:
- .github/copilot-instructions.md  (repo-wide instructions)
- .github/instructions/*.instructions.md  (path-scoped instructions)
- .github/hooks/hooks.json  (hook event handlers with stdin JSON I/O)
- .github/agents/*.agent.md  (agent definitions)
- .github/skills/*/SKILL.md  (skill definitions)

PRD-CORE-127: Copilot CLI integration as first-class TRW client profile.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TypedDict

import structlog

from ._file_ops import (
    _new_result,
    _record_write,
    smart_merge_marker_section,
    write_instruction_file_with_merge,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_GITHUB_DIR = ".github"
_COPILOT_INSTRUCTIONS_PATH = ".github/copilot-instructions.md"
_COPILOT_HOOKS_PATH = ".github/hooks/hooks.json"
_COPILOT_AGENTS_DIR = ".github/agents"
_COPILOT_SKILLS_DIR = ".github/skills"
_COPILOT_INSTRUCTIONS_DIR = ".github/instructions"

# ---------------------------------------------------------------------------
# Marker constants (prefixed to avoid confusion with _opencode.py markers)
# ---------------------------------------------------------------------------

_COPILOT_TRW_START_MARKER = "<!-- trw:copilot:start -->"
_COPILOT_TRW_END_MARKER = "<!-- trw:copilot:end -->"
_TRW_HOOK_DESCRIPTION_PREFIX = "TRW managed:"


# ---------------------------------------------------------------------------
# TypedDicts for structured data
# ---------------------------------------------------------------------------


class PathScopedTemplate(TypedDict):
    """Template for a path-scoped Copilot instruction file."""

    applyTo: str
    content: str


class CopilotHookCommand(TypedDict):
    """A single command entry inside a Copilot hook group."""

    type: str
    command: str


class CopilotHookGroup(TypedDict):
    """A hook group entry in Copilot hooks.json."""

    description: str
    hooks: list[CopilotHookCommand]


class CopilotHooksPayload(TypedDict):
    """Top-level hooks.json structure for Copilot."""

    version: int
    hooks: dict[str, list[CopilotHookGroup]]


class CopilotHookConfig(TypedDict):
    """Mapping entry for a TRW hook → Copilot event."""

    script: str
    description: str


# ---------------------------------------------------------------------------
# Data directory helpers
# ---------------------------------------------------------------------------


def _copilot_data_dir() -> Path:
    """Return the bundled Copilot-specific data root."""
    from ._utils import _DATA_DIR

    return _DATA_DIR / "copilot"


def _copilot_skills_source_dir() -> Path:
    """Return the bundled Copilot-specific skills root."""
    return _copilot_data_dir() / "skills"


# ---------------------------------------------------------------------------
# Instructions generation
# ---------------------------------------------------------------------------


def _copilot_instructions_content() -> str:
    """Generate repo-wide Copilot instruction content with TRW ceremony guidance."""
    return f"""{_COPILOT_TRW_START_MARKER}
<!-- TRW AUTO-GENERATED — do not edit between markers -->

# TRW Framework Integration

This project uses the TRW (The Real Work) framework for structured AI-assisted development.

## Session Protocol

| Tool | When | Why |
|------|------|-----|
| `trw_session_start()` | First action | Loads prior learnings |
| `trw_learn(summary, detail)` | On discoveries | Saves findings for future sessions |
| `trw_checkpoint(message)` | After milestones | Resume point if context compacts |
| `trw_deliver()` | Last action | Persists session work |

## Available MCP Tools

TRW tools are available via MCP server. Key tools: `trw_session_start`, `trw_learn`,
`trw_checkpoint`, `trw_deliver`, `trw_init`, `trw_status`, `trw_recall`,
`trw_build_check`, `trw_review`, `trw_prd_create`, `trw_prd_validate`.

## Conventions

- Run tests after each change — fix failures before moving on
- Use `trw_learn()` to record discoveries, patterns, and gotchas
- Use `trw_checkpoint()` after working milestones
- Commit messages: `feat(scope): msg` (Conventional Commits)

{_COPILOT_TRW_END_MARKER}
"""


def _smart_merge_instructions(existing: str, trw_content: str) -> str:
    """Backward-compatible wrapper around the shared marker-merge helper.

    Kept so external callers / tests targeting this private symbol still work.
    New code should call :func:`smart_merge_marker_section` directly.
    """
    return smart_merge_marker_section(
        existing,
        trw_content,
        start_marker=_COPILOT_TRW_START_MARKER,
        end_marker=_COPILOT_TRW_END_MARKER,
    )


def generate_copilot_instructions(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate or smart-merge ``.github/copilot-instructions.md``.

    Delegates to the shared ``write_instruction_file_with_merge`` helper.
    """
    result = _new_result()
    write_instruction_file_with_merge(
        target_path=target_dir / _COPILOT_INSTRUCTIONS_PATH,
        rel_path=_COPILOT_INSTRUCTIONS_PATH,
        trw_section=_copilot_instructions_content(),
        start_marker=_COPILOT_TRW_START_MARKER,
        end_marker=_COPILOT_TRW_END_MARKER,
        force=force,
        result=result,
    )
    return result


# ---------------------------------------------------------------------------
# Path-scoped instructions
# ---------------------------------------------------------------------------

_PATH_SCOPED_TEMPLATES: dict[str, PathScopedTemplate] = {
    "python-testing.instructions.md": {
        "applyTo": "**/*test*.py,**/tests/**/*.py",
        "content": """# Python Testing Guidelines

- Use pytest as the test framework
- Follow `test_*.py` naming convention
- Add type annotations to test functions
- Use fixtures for shared setup
- Target 90%+ coverage for new code
""",
    },
    "typescript-react.instructions.md": {
        "applyTo": "**/*.tsx,**/*.ts",
        "content": """# TypeScript/React Guidelines

- Use PascalCase for React components
- Use camelCase for functions and hooks
- Colocate tests as `*.test.ts` or `*.test.tsx`
- Use ESLint + Prettier formatting
""",
    },
}


def generate_copilot_path_instructions(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate ``.github/instructions/*.instructions.md`` path-scoped files."""
    result = _new_result()
    instructions_dir = target_dir / _COPILOT_INSTRUCTIONS_DIR
    instructions_dir.mkdir(parents=True, exist_ok=True)

    for filename, template in _PATH_SCOPED_TEMPLATES.items():
        path = instructions_dir / filename
        existed = path.exists()
        if existed and not force:
            result["preserved"].append(f"{_COPILOT_INSTRUCTIONS_DIR}/{filename}")
            continue

        content = f"""---
applyTo: "{template["applyTo"]}"
---
{template["content"]}"""

        try:
            path.write_text(content, encoding="utf-8")
            _record_write(result, f"{_COPILOT_INSTRUCTIONS_DIR}/{filename}", existed=existed)
        except OSError as exc:
            result["errors"].append(f"Failed to write {path}: {exc}")

    return result


# ---------------------------------------------------------------------------
# Hooks generation — stdin JSON I/O adapter
# ---------------------------------------------------------------------------

# Copilot hook event names (camelCase) mapped to TRW hook scripts
_COPILOT_HOOK_MAP: dict[str, CopilotHookConfig] = {
    "sessionStart": {
        "script": "session-start.sh",
        "description": "Loading TRW session context",
    },
    "userPromptSubmitted": {
        "script": "user-prompt-submit.sh",
        "description": "Checking TRW phase guidance",
    },
    "preToolUse": {
        "script": "pre-tool-deliver-gate.sh",
        "description": "Checking TRW delivery gate",
    },
    "postToolUse": {
        "script": "post-tool-event.sh",
        "description": "Logging TRW tool effects",
    },
    "sessionEnd": {
        "script": "stop-ceremony.sh",
        "description": "Running TRW session cleanup",
    },
}


def _build_hook_adapter_command(event_name: str, hook_path: str) -> str:
    """Build a shell command that adapts Copilot stdin JSON to TRW hook scripts.

    Uses double-quoted ``sh -c`` wrapper to avoid single-quote nesting issues.
    The adapter:
    1. Reads JSON from stdin and saves to ``$_input``
    2. Extracts ``toolName`` field using grep/sed
    3. Pipes stdin to the shared TRW hook script
    4. For ``preToolUse``: translates exit code → JSON permission decision on stdout
    """
    # Use double-quote wrapper with escaped inner quotes to avoid
    # the single-quote nesting bug (all grep/sed patterns work correctly)
    json_extract = (
        "_input=$(cat); "
        'export TOOL_NAME=$(printf "%s" "$_input" | '
        'grep -o \'"toolName"[[:space:]]*:[[:space:]]*"[^"]*"\' 2>/dev/null | '
        "head -1 | "
        'sed \'s/.*"toolName"[[:space:]]*:[[:space:]]*"//;s/"$//\' || true)'
    )

    if event_name == "preToolUse":
        # preToolUse must return JSON stdout for permission decisions
        return (
            f"/bin/sh -c '"
            f"{json_extract}; "
            f'printf "%s" "$_input" | /bin/sh "{hook_path}" 2>/dev/null; '
            f"_rc=$?; "
            f'if [ "$_rc" -eq 2 ]; then '
            f'printf \'{{"permissionDecision":"deny"}}\'; '
            f"else "
            f'printf \'{{"permissionDecision":"allow"}}\'; '
            f"fi"
            f"'"
        )

    # Non-permission hooks: pipe stdin to hook, fail-open
    return f'/bin/sh -c \'{json_extract}; printf "%s" "$_input" | /bin/sh "{hook_path}" 2>/dev/null || true\''


def _copilot_hooks_payload() -> CopilotHooksPayload:
    """Return a Copilot hooks.json payload with TRW adapter scripts.

    Copilot hooks use stdin JSON I/O. Each hook entry runs a shell adapter
    that reads JSON from stdin, extracts fields, sources the shared TRW
    hook script, and (for preToolUse) returns JSON on stdout.
    """
    hooks: dict[str, list[CopilotHookGroup]] = {}
    git_root = "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

    for event_name, config in _COPILOT_HOOK_MAP.items():
        hook_path = f"{git_root}/.claude/hooks/{config['script']}"
        command = _build_hook_adapter_command(event_name, hook_path)

        hook_entry: CopilotHookCommand = {
            "type": "command",
            "command": command,
        }

        hooks[event_name] = [
            {
                "description": f"{_TRW_HOOK_DESCRIPTION_PREFIX} {config['description']}",
                "hooks": [hook_entry],
            }
        ]

    return {"version": 1, "hooks": hooks}


def _is_trw_hook_group(group: dict[str, object]) -> bool:
    """Identify a TRW-managed hook group in an existing hooks config."""
    description = group.get("description")
    return isinstance(description, str) and description.startswith(_TRW_HOOK_DESCRIPTION_PREFIX)


def _merge_copilot_hooks(
    existing: dict[str, object],
) -> CopilotHooksPayload:
    """Merge TRW-managed hooks into existing Copilot hooks.json."""
    existing_hooks = existing.get("hooks", {})
    if not isinstance(existing_hooks, dict):
        existing_hooks = {}

    trw_payload = _copilot_hooks_payload()
    trw_hooks = trw_payload["hooks"]

    merged_hooks: dict[str, list[CopilotHookGroup]] = {}

    for event_name in sorted(set(existing_hooks) | set(trw_hooks)):
        existing_groups = existing_hooks.get(event_name, [])
        if not isinstance(existing_groups, list):
            existing_groups = []

        # Keep user-managed groups, replace TRW-managed ones
        user_groups: list[CopilotHookGroup] = [
            g
            for g in existing_groups
            if isinstance(g, dict) and not _is_trw_hook_group(g)  # type: ignore[misc]
        ]
        trw_groups = trw_hooks.get(event_name, [])

        if trw_groups:
            merged_hooks[event_name] = user_groups + trw_groups
        elif user_groups:
            merged_hooks[event_name] = user_groups

    return {"version": 1, "hooks": merged_hooks}


def generate_copilot_hooks(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate ``.github/hooks/hooks.json``."""
    result = _new_result()
    hooks_dir = target_dir / _GITHUB_DIR / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks_path = target_dir / _COPILOT_HOOKS_PATH
    existed = hooks_path.exists()

    try:
        if existed and not force:
            raw_existing = json.loads(hooks_path.read_text(encoding="utf-8"))
            payload = _merge_copilot_hooks(raw_existing)
        else:
            payload = _copilot_hooks_payload()
        hooks_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        _record_write(result, _COPILOT_HOOKS_PATH, existed=existed)
    except (OSError, json.JSONDecodeError) as exc:
        result["errors"].append(f"Failed to write {hooks_path}: {exc}")

    return result


# ---------------------------------------------------------------------------
# Agents generation — .agent.md format
# ---------------------------------------------------------------------------

_COPILOT_AGENT_TEMPLATES: dict[str, str] = {
    "trw-explorer.agent.md": """---
name: trw-explorer
description: "Read-only codebase explorer for gathering evidence before edits."
tools:
  - read
  - glob
  - grep
  - web
mcp-servers:
  - trw
---

Stay in exploration mode.
Trace the real execution path, cite files and symbols, and avoid proposing fixes unless asked.
Prefer fast search and targeted reads over broad scans.

Use `trw_recall(query)` to check if the topic has been investigated before.
""",
    "trw-implementer.agent.md": """---
name: trw-implementer
description: "Implementation-focused agent for bounded code changes."
tools:
  - read
  - edit
  - execute
  - glob
  - grep
mcp-servers:
  - trw
---

Own the requested fix or feature slice.
Make the smallest defensible change, keep unrelated files untouched, and validate the behavior you changed.

Use `trw_checkpoint(message)` after each working milestone.
Run tests after each change — fix failures before moving on.
""",
    "trw-reviewer.agent.md": """---
name: trw-reviewer
description: "Read-only reviewer focused on correctness, regressions, security, and missing tests."
tools:
  - read
  - glob
  - grep
  - web
mcp-servers:
  - trw
---

Review like an owner.
Lead with concrete findings, prioritize correctness and missing tests, and avoid style-only feedback unless it hides a real defect.

Use `trw_learn(summary, detail)` to record any patterns or gotchas discovered.
""",
    "trw-docs-researcher.agent.md": """---
name: trw-docs-researcher
description: "Documentation specialist that researches APIs and runtime behavior."
tools:
  - read
  - glob
  - grep
  - web
mcp-servers:
  - trw
---

Use web search and configured MCP servers to confirm APIs, options, and version-specific behavior.
Return concise answers with links or exact references when available.
Do not make code changes.
""",
}


def generate_copilot_agents(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Generate ``.github/agents/*.agent.md``."""
    result = _new_result()
    agents_dir = target_dir / _COPILOT_AGENTS_DIR
    agents_dir.mkdir(parents=True, exist_ok=True)

    for filename, content in _COPILOT_AGENT_TEMPLATES.items():
        path = agents_dir / filename
        existed = path.exists()

        if existed and not force:
            result["preserved"].append(f"{_COPILOT_AGENTS_DIR}/{filename}")
            continue

        try:
            path.write_text(content, encoding="utf-8")
            _record_write(result, f"{_COPILOT_AGENTS_DIR}/{filename}", existed=existed)
        except OSError as exc:
            result["errors"].append(f"Failed to write {path}: {exc}")

    return result


# ---------------------------------------------------------------------------
# Skills installation
# ---------------------------------------------------------------------------


def install_copilot_skills(
    target_dir: Path,
    *,
    force: bool = False,
) -> dict[str, list[str]]:
    """Install TRW bundled skills into ``.github/skills/`` for Copilot.

    Copilot discovers skills at ``.github/skills/*/SKILL.md`` (and also
    ``.claude/skills/`` for cross-compatibility). Bundled skills are
    validated before installation.
    """
    from ._init_project import _validate_skill

    result = _new_result()
    skills_source = _copilot_skills_source_dir()
    if not skills_source.is_dir():
        # Fall back to shared Claude Code skills if no Copilot-specific ones
        from ._utils import _DATA_DIR

        skills_source = _DATA_DIR / "skills"

    if not skills_source.is_dir():
        return result

    dest_root = target_dir / _COPILOT_SKILLS_DIR
    dest_root.mkdir(parents=True, exist_ok=True)

    for skill_dir in sorted(skills_source.iterdir()):
        if not skill_dir.is_dir():
            continue
        is_valid, reason = _validate_skill(skill_dir)
        if not is_valid:
            logger.warning("copilot_skill_validation_failed", skill=skill_dir.name, reason=reason)
            continue

        dest_skill = dest_root / skill_dir.name
        dest_skill.mkdir(parents=True, exist_ok=True)
        for skill_file in sorted(skill_dir.iterdir()):
            if not skill_file.is_file():
                continue
            dest = dest_skill / skill_file.name
            rel_path = f"{_COPILOT_SKILLS_DIR}/{skill_dir.name}/{skill_file.name}"
            existed = dest.exists()

            if existed and not force:
                # Update content but track as updated (not overwrite)
                try:
                    shutil.copy2(skill_file, dest)
                    result["updated"].append(rel_path)
                except OSError as exc:
                    result["errors"].append(f"Failed to copy {skill_file} -> {dest}: {exc}")
            else:
                try:
                    shutil.copy2(skill_file, dest)
                    result["created"].append(rel_path)
                except OSError as exc:
                    result["errors"].append(f"Failed to copy {skill_file} -> {dest}: {exc}")

    return result

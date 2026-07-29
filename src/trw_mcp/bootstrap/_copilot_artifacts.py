# Parent facade: bootstrap/_copilot.py
"""Copilot path-scoped instructions, agents, and skills — the mirrored artifacts.

Belongs to the ``_copilot.py`` facade. Re-exported there for back-compat with
callers and tests that import via the parent.

Split out of ``_copilot.py`` for the 350 effective-LOC module gate when these
three generators gained the content-aware user-edit guard. They share one
property that ``_copilot.py``'s remaining generators (``copilot-instructions.md``
and ``hooks.json``) do not: each writes a *whole file* that TRW owns outright, so
"refresh it" and "the user edited it" are mutually exclusive and must be decided
before the write. The other two merge into a marker-guarded region or a JSON
document and are governed by their own merge rules.

Each generator is paired with a ``*_contents()`` builder returning
``{repo-relative path: bundled bytes}``. That map is the single source of truth
consumed by BOTH the generator and
``_managed_client_artifacts.MANAGED_CLIENT_ARTIFACT_SOURCES`` (which records the
manifest baseline), so the guard and the baseline recorder can never key on
different paths.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from ._copilot_models import PathScopedTemplate
from ._file_ops import _new_result, _record_write

logger = structlog.get_logger(__name__)

_COPILOT_AGENTS_DIR = ".github/agents"
_COPILOT_SKILLS_DIR = ".github/skills"
_COPILOT_INSTRUCTIONS_DIR = ".github/instructions"


def _copilot_data_dir() -> Path:
    """Return the bundled Copilot-specific data root."""
    from ._utils import _DATA_DIR

    return _DATA_DIR / "copilot"


def _copilot_skills_source_dir() -> Path:
    """Return the bundled Copilot-specific skills root."""
    return _copilot_data_dir() / "skills"


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
- Meet the project-configured coverage gate; if none exists, report measured coverage without inventing a percentage
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


def _render_path_instruction(template: PathScopedTemplate) -> str:
    """Render one path-scoped instruction file's full on-disk text."""
    return f"""---
applyTo: "{template["applyTo"]}"
---
{template["content"]}"""


def copilot_path_instruction_contents() -> dict[str, bytes]:
    """Bundled ``.github/instructions/*`` content, keyed by repo-relative path.

    Single source of truth shared by :func:`generate_copilot_path_instructions`
    and the managed-artifact manifest sweep
    (``_managed_client_artifacts.MANAGED_CLIENT_ARTIFACT_SOURCES``).
    """
    return {
        f"{_COPILOT_INSTRUCTIONS_DIR}/{filename}": _render_path_instruction(template).encode("utf-8")
        for filename, template in _PATH_SCOPED_TEMPLATES.items()
    }


def generate_copilot_path_instructions(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Generate ``.github/instructions/*.instructions.md`` path-scoped files.

    Content-aware: a file matching the bundled content or TRW's recorded last
    write is refreshed; a user-edited one is preserved. The previous
    ``existed and not force`` short-circuit preserved edits but also froze
    TRW-owned files at their first-installed content forever.
    """
    from ._managed_client_artifacts import artifact_user_edited

    result = _new_result()
    instructions_dir = target_dir / _COPILOT_INSTRUCTIONS_DIR
    instructions_dir.mkdir(parents=True, exist_ok=True)

    for rel_path, incoming in copilot_path_instruction_contents().items():
        path = target_dir / rel_path
        existed = path.exists()
        if existed and not force and artifact_user_edited(path, rel_path, incoming, manifest_hashes):
            logger.info("copilot_path_instruction_user_modified", path=rel_path)
            result["preserved"].append(rel_path)
            continue

        try:
            path.write_bytes(incoming)
            _record_write(result, rel_path, existed=existed)
        except OSError as exc:
            result["errors"].append(f"Failed to write {path}: {exc}")

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


def copilot_agent_contents() -> dict[str, bytes]:
    """Bundled ``.github/agents/*`` content, keyed by repo-relative path."""
    return {
        f"{_COPILOT_AGENTS_DIR}/{filename}": content.encode("utf-8")
        for filename, content in _COPILOT_AGENT_TEMPLATES.items()
    }


def generate_copilot_agents(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Generate ``.github/agents/*.agent.md``.

    Content-aware: unmodified agents are refreshed when the bundled template
    changes; user-edited ones are preserved.
    """
    from ._managed_client_artifacts import artifact_user_edited

    result = _new_result()
    agents_dir = target_dir / _COPILOT_AGENTS_DIR
    agents_dir.mkdir(parents=True, exist_ok=True)

    for rel_path, incoming in copilot_agent_contents().items():
        path = target_dir / rel_path
        existed = path.exists()

        if existed and not force and artifact_user_edited(path, rel_path, incoming, manifest_hashes):
            logger.info("copilot_agent_user_modified", path=rel_path)
            result["preserved"].append(rel_path)
            continue

        try:
            path.write_bytes(incoming)
            _record_write(result, rel_path, existed=existed)
        except OSError as exc:
            result["errors"].append(f"Failed to write {path}: {exc}")

    return result


# ---------------------------------------------------------------------------
# Skills installation
# ---------------------------------------------------------------------------


def _copilot_effective_skills_source() -> Path | None:
    """Resolve the skills source dir Copilot installs from, or ``None``.

    Prefers the curated ``data/copilot/skills`` set and falls back to the shared
    ``data/skills`` set when no Copilot-specific one ships.
    """
    skills_source = _copilot_skills_source_dir()
    if skills_source.is_dir():
        return skills_source
    from ._utils import _DATA_DIR

    fallback = _DATA_DIR / "skills"
    return fallback if fallback.is_dir() else None


def copilot_skill_contents() -> dict[str, bytes]:
    """Bundled ``.github/skills/**`` content, keyed by repo-relative path.

    Applies the same ``_validate_skill`` gate the installer does, so an invalid
    bundled skill is absent from both the install set and the manifest baseline.
    """
    from ._init_project import _validate_skill

    skills_source = _copilot_effective_skills_source()
    if skills_source is None:
        return {}

    contents: dict[str, bytes] = {}
    for skill_dir in sorted(skills_source.iterdir()):
        if not skill_dir.is_dir():
            continue
        is_valid, reason = _validate_skill(skill_dir)
        if not is_valid:
            logger.warning("copilot_skill_validation_failed", skill=skill_dir.name, reason=reason)
            continue
        for skill_file in sorted(skill_dir.iterdir()):
            if not skill_file.is_file():
                continue
            rel_path = f"{_COPILOT_SKILLS_DIR}/{skill_dir.name}/{skill_file.name}"
            try:
                contents[rel_path] = skill_file.read_bytes()
            except OSError:
                logger.warning("copilot_skill_source_unreadable", path=str(skill_file))
    return contents


def install_copilot_skills(
    target_dir: Path,
    *,
    force: bool = False,
    manifest_hashes: dict[str, str] | None = None,
) -> dict[str, list[str]]:
    """Install TRW bundled skills into ``.github/skills/`` for Copilot.

    Copilot discovers skills at ``.github/skills/*/SKILL.md`` (and also
    ``.claude/skills/`` for cross-compatibility). Bundled skills are
    validated before installation.

    Content-aware (CONSTITUTION HB-2): a skill file matching the bundled
    content or TRW's recorded last write is refreshed; a user-edited one is
    preserved. Both branches of the previous ``if existed and not force``
    issued the same ``copy2``, so the ``force`` flag only picked a result
    bucket and every hand edit was destroyed on every update.
    """
    from ._managed_client_artifacts import artifact_user_edited

    result = _new_result()
    contents = copilot_skill_contents()
    if not contents:
        return result

    dest_root = target_dir / _COPILOT_SKILLS_DIR
    dest_root.mkdir(parents=True, exist_ok=True)

    for rel_path, incoming in contents.items():
        dest = target_dir / rel_path
        existed = dest.exists()

        if existed and not force and artifact_user_edited(dest, rel_path, incoming, manifest_hashes):
            logger.info("copilot_skill_user_modified", path=rel_path)
            result["preserved"].append(rel_path)
            continue

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(incoming)
            _record_write(result, rel_path, existed=existed)
        except OSError as exc:
            result["errors"].append(f"Failed to write {dest}: {exc}")

    return result

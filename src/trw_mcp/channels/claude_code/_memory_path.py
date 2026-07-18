"""Claude Code project-ID derivation and memory directory resolution.

Belongs to the ``channels/claude_code`` package (PRD-DIST-2405 FR10).

The project-ID encoding matches the Anthropic-documented convention:
absolute POSIX path with each ``/`` replaced by ``-`` and a leading
``-`` prepended.

    /home/dev/projects/my-app → -home-dev-projects-my-app
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "CLAUDE_PROJECTS_DIR",
    "derive_claude_project_id",
    "resolve_memory_dir",
    "resolve_memory_index_path",
]

# Default base directory for Anthropic auto-memory files.
CLAUDE_PROJECTS_DIR: Path = Path.home() / ".claude" / "projects"


def derive_claude_project_id(project_root: Path) -> str:
    """Return the Anthropic project-ID string for *project_root*.

    Encoding: absolute POSIX path, each ``/`` replaced by ``-``,
    leading ``-`` prepended.

    Args:
        project_root: Absolute path to the repository root.

    Returns:
        Project-ID string, e.g. ``"-home-dev-projects-my-app"``.

    Examples::

        >>> from pathlib import Path
        >>> derive_claude_project_id(Path("/home/dev/projects/my-app"))
        '-home-dev-projects-my-app'
    """
    abs_path = project_root.resolve()
    return "-" + str(abs_path).replace("/", "-").lstrip("-")


def resolve_memory_dir(
    project_root: Path,
    claude_projects_dir: Path | None = None,
) -> Path:
    """Return the Claude Code memory directory for *project_root*.

    Args:
        project_root: Absolute path to the repository root.
        claude_projects_dir: Override for ``~/.claude/projects/``.
            Used in tests to redirect to a temp directory.

    Returns:
        Path to the memory directory (not yet created).
    """
    base = claude_projects_dir if claude_projects_dir is not None else CLAUDE_PROJECTS_DIR
    project_id = derive_claude_project_id(project_root)
    return base / project_id / "memory"


def resolve_memory_index_path(
    project_root: Path,
    claude_projects_dir: Path | None = None,
) -> Path:
    """Return the path to MEMORY.md inside the memory directory.

    Args:
        project_root: Absolute path to the repository root.
        claude_projects_dir: Override for ``~/.claude/projects/``.

    Returns:
        Path to ``MEMORY.md`` inside the memory directory.
    """
    return resolve_memory_dir(project_root, claude_projects_dir) / "MEMORY.md"

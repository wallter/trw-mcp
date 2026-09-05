"""Runnable-command lint for PRD ``verification_commands`` (PRD-INFRA-179-FR02).

Single source of truth for two things that used to be able to drift:

1. ``_VERIFICATION_COMMAND_RE`` — the "does this PRD mention a verification
   command at all" scoring regex (re-exported by ``_prd_scoring_counts``), and
2. :func:`malformed_verification_command_reason` — the structural lint that
   asks the stronger question the scoring regex never asked: read
   left-to-right, is this string a *runnable* invocation?

The failure this closes: ``"FR01: cd trw-mcp && ../.venv/bin/python -m pytest ..."``
contains ``pytest``, so it scored as a verification command, and
``scripts/prd_verify_check.py`` then handed it to ``bash -c`` — where it dies as
``bash: line 1: FR01:: command not found`` (exit 127), indistinguishable from a
genuinely failing test.

**Stdlib-only and free of intra-package imports on purpose**:
``scripts/prd_verify_check.py`` loads this file directly by path
(``importlib.util.spec_from_file_location``) so the repo-root script and the MCP
validator share one rule without the script depending on ``trw_mcp`` being
importable. ``scripts/tests/test_prd_verify_check_command_lint.py`` asserts that
the file the script loads is this one.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

#: Command name -> the argument pattern that makes it a *verification* command
#: in prose. The scoring regex is built from this mapping so the names the lint
#: accepts and the names the scorer recognises can never diverge (RISK-002).
_COMMAND_ARGUMENT_PATTERNS: dict[str, str] = {
    "pytest": "",
    "python": r" -m pytest",
    "npx": r" vitest run",
    "npm": r"(?: run)? test",
    "make": r" test",
    "go": r" test",
    "cargo": r" test",
}

#: Bare command names the scoring regex recognises (``pytest``, ``python``, ...).
VERIFICATION_COMMAND_NAMES: frozenset[str] = frozenset(_COMMAND_ARGUMENT_PATTERNS)

# Recognizable verification commands in PRD text.
_VERIFICATION_COMMAND_RE = re.compile(
    r"\b(?:" + "|".join(name + pattern for name, pattern in _COMMAND_ARGUMENT_PATTERNS.items()) + r")\b",
    re.IGNORECASE,
)

#: POSIX shell builtins/keywords that legitimately lead a verification command
#: (``cd trw-mcp && pytest ...``, ``test -f x``, ``[ -f x ]``, ``! grep ...``).
_SHELL_KEYWORDS: frozenset[str] = frozenset(
    {
        "!",
        "(",
        ".",
        ":",
        "[",
        "cd",
        "echo",
        "exec",
        "export",
        "false",
        "for",
        "if",
        "printf",
        "set",
        "source",
        "test",
        "true",
        "until",
        "while",
    }
)

#: Standard utilities PRDs use to express a verification step. Kept explicit so
#: the verdict is identical on every machine for the shapes the corpus actually
#: uses; anything outside it still passes when ``shutil.which`` resolves it.
_STANDARD_UTILITIES: frozenset[str] = frozenset(
    {
        "awk",
        "bash",
        "cat",
        "cmp",
        "cp",
        "curl",
        "cut",
        "diff",
        "env",
        "find",
        "git",
        "grep",
        "head",
        "jq",
        "ls",
        "mkdir",
        "mv",
        "mypy",
        "node",
        "python3",
        "rg",
        "rm",
        "ruff",
        "sed",
        "sh",
        "shellcheck",
        "sort",
        "sqlite3",
        "tail",
        "tee",
        "timeout",
        "tr",
        "uniq",
        "uv",
        "wc",
        "xargs",
    }
)

#: Name shapes that can never be an executable: an environment-variable
#: assignment is handled separately, everything else with a ``:``/``,`` in it
#: (``FR01:``, ``NFR03,``) is prose, not a command.
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _leading_token(command: str) -> str | None:
    """Return the first token that must resolve to an executable, or None."""
    for token in command.strip().split():
        if _ENV_ASSIGNMENT_RE.match(token):
            continue  # `FOO=bar cmd ...` — the assignment is not the command
        return token
    return None


def malformed_verification_command_reason(command: str, *, repo_root: Path | None = None) -> str | None:
    """Return why ``command`` is not runnable, or ``None`` when it is.

    A command is runnable when its leading token (after any ``VAR=value``
    prefixes) is a shell keyword, a known command name, a standard utility, a
    path that exists under ``repo_root``, or a name ``shutil.which`` resolves.

    Args:
        command: One ``verification_commands`` frontmatter entry.
        repo_root: Repository root used to resolve relative path tokens. When
            ``None``, path-shaped tokens are accepted without a filesystem check.

    Returns:
        A one-line human reason string, or ``None`` when the entry is runnable.
    """
    stripped = command.strip()
    if not stripped:
        return "empty verification_commands entry"
    if stripped.startswith("#"):
        return "verification_commands entry is a shell comment — it exits 0 without verifying anything"
    token = _leading_token(stripped)
    if token is None:
        return "verification_commands entry is only environment assignments, with no command to run"
    if "/" in token:
        if repo_root is None:
            return None
        candidate = Path(token) if Path(token).is_absolute() else repo_root / token
        if candidate.exists():
            return None
        return f"leading path `{token}` does not exist (resolved against the repository root)"
    if token in _SHELL_KEYWORDS or token in VERIFICATION_COMMAND_NAMES or token in _STANDARD_UTILITIES:
        return None
    if shutil.which(token) is not None:
        return None
    return f"leading token `{token}` is not a runnable command or path"


def malformed_verification_commands(
    commands: list[object],
    *,
    repo_root: Path | None = None,
) -> list[tuple[str, str]]:
    """Return ``(entry, reason)`` for every non-runnable entry in ``commands``."""
    findings: list[tuple[str, str]] = []
    for entry in commands:
        if not isinstance(entry, str):
            findings.append((repr(entry), "verification_commands entry is not a string"))
            continue
        reason = malformed_verification_command_reason(entry, repo_root=repo_root)
        if reason is not None:
            findings.append((entry, reason))
    return findings

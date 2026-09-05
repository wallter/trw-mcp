"""Merge-ensure the SEC-005 credentials.yaml ignore rule in ``.trw/.gitignore``.

Belongs to the ``_template_updater.py`` facade. Re-exported there for
back-compat with callers/tests.

The bundled ``gitignore.txt`` is only deployed to ``.trw/.gitignore`` on INIT
(``_DATA_FILE_MAP``); ``update-project`` never refreshed it. Existing installs
predating PRD-SEC-005 therefore keep a CUSTOM ``.trw/.gitignore`` that lacks a
``credentials.yaml`` rule, and the SEC-005 credential migration then writes the
bearer credential into an unignored file. Rather than blind-overwriting the
user's custom ignores (the ``_ALWAYS_UPDATE`` semantics), we merge-ensure the
single credentials rule, preserving all user customizations.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from ._utils import ProgressCallback

logger = structlog.get_logger(__name__)

# Whole-line ignore rules that MUST be present in ``.trw/.gitignore``:
#  - ``credentials.yaml`` — the SEC-005 credential store (mode 0600), never
#    tracked (PRD-SEC-005-FR02).
#  - ``backups/`` — the PRD-FIX-123-FR04 pre-write copies of CLAUDE.md /
#    AGENTS.md, which mirror files that may hold private project rules.
_CREDENTIALS_IGNORE_RULE = "credentials.yaml"
_CREDENTIALS_IGNORE_COMMENT = (
    "# Secret: credentials.yaml holds the platform_api_key (mode 0600) — never track it (PRD-SEC-005)."
)
_BACKUPS_IGNORE_RULE = "backups/"
_BACKUPS_IGNORE_COMMENT = "# Pre-write copies of your instruction files — never track them (PRD-FIX-123)."

#: ``(rule, comment)`` pairs merge-ensured into an existing custom ignore file,
#: in the order they are appended.
_REQUIRED_RULES: tuple[tuple[str, str], ...] = (
    (_CREDENTIALS_IGNORE_RULE, _CREDENTIALS_IGNORE_COMMENT),
    (_BACKUPS_IGNORE_RULE, _BACKUPS_IGNORE_COMMENT),
)


def _accepted_forms(rule: str) -> set[str]:
    """Return the whole-line spellings that count as *rule* already being set.

    Accepts the bare form plus the leading-slash and ``.trw/``-prefixed path
    forms some users write. A directory rule is also accepted without its
    trailing slash.
    """
    bare = rule.rstrip("/")
    forms = {rule, bare}
    for base in (rule, bare):
        forms |= {f"/{base}", f".trw/{base}", f"/.trw/{base}"}
    return forms


def _rule_already_present(text: str, rule: str) -> bool:
    """Return True if *rule* is covered by a whole-line entry in *text*.

    Uses line-anchored whole-line matching (strip + exact compare), never a
    substring scan — a prose mention of "credentials.yaml" in a comment must
    not be mistaken for an active ignore rule.
    """
    accepted = _accepted_forms(rule)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped in accepted:
            return True
    return False


def _missing_rules(text: str) -> tuple[tuple[str, str], ...]:
    """Return the required ``(rule, comment)`` pairs absent from *text*."""
    return tuple((rule, comment) for rule, comment in _REQUIRED_RULES if not _rule_already_present(text, rule))


def _render_rules(pairs: tuple[tuple[str, str], ...]) -> str:
    """Render ``(rule, comment)`` pairs as appended gitignore lines."""
    return "".join(f"{comment}\n{rule}\n" for rule, comment in pairs)


def _ensure_credentials_gitignored(
    target_dir: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback = None,
) -> None:
    """Merge-ensure ``.trw/.gitignore`` carries every required rule.

    Covers the SEC-005 ``credentials.yaml`` rule (FR02) and the PRD-FIX-123-FR04
    ``backups/`` rule. Appends only the MISSING rules to an existing custom
    ``.trw/.gitignore`` WITHOUT discarding any user customizations (the safe
    alternative to a blind ``_ALWAYS_UPDATE`` overwrite), and is idempotent.
    Creates a minimal ``.gitignore`` when one is absent.

    Fail-open: no ``.trw/`` directory, or an OS/decoding error, is a no-op.
    """
    trw_dir = target_dir / ".trw"
    if not trw_dir.is_dir():
        return
    gitignore = trw_dir / ".gitignore"

    if gitignore.is_file():
        try:
            existing = gitignore.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            result.setdefault("errors", []).append(f"Failed to read {gitignore}: {exc}")
            return
        missing = _missing_rules(existing)
        if not missing:
            return
        names = ", ".join(rule for rule, _ in missing)
        if dry_run:
            result["updated"].append(f"would update: {gitignore} (add {names} ignore rule)")
            return
        sep = "" if existing.endswith("\n") or existing == "" else "\n"
        appended = f"{existing}{sep}{_render_rules(missing)}"
        try:
            gitignore.write_text(appended, encoding="utf-8")
        except OSError as exc:
            result.setdefault("errors", []).append(f"Failed to update {gitignore}: {exc}")
            return
        result["updated"].append(str(gitignore))
        if on_progress:
            on_progress("Updated", str(gitignore))
        return

    # No .gitignore at all: create a minimal one carrying every required rule.
    if dry_run:
        names = ", ".join(rule for rule, _ in _REQUIRED_RULES)
        result["created"].append(f"would create: {gitignore} ({names} ignore rule)")
        return
    try:
        gitignore.write_text(_render_rules(_REQUIRED_RULES), encoding="utf-8")
    except OSError as exc:
        result.setdefault("errors", []).append(f"Failed to create {gitignore}: {exc}")
        return
    result["created"].append(str(gitignore))
    if on_progress:
        on_progress("Created", str(gitignore))

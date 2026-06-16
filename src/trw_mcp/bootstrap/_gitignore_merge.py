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

# Whole-line ignore rule that MUST be present in ``.trw/.gitignore`` so the
# SEC-005 credential store (``.trw/credentials.yaml``, mode 0600) is never
# tracked (PRD-SEC-005-FR02).
_CREDENTIALS_IGNORE_RULE = "credentials.yaml"
_CREDENTIALS_IGNORE_COMMENT = (
    "# Secret: credentials.yaml holds the platform_api_key (mode 0600) — never track it (PRD-SEC-005)."
)


def _credentials_already_ignored(text: str) -> bool:
    """Return True if ``credentials.yaml`` is covered by a whole-line rule.

    Uses line-anchored whole-line matching (strip + exact compare), never a
    substring scan — a prose mention of "credentials.yaml" in a comment must
    not be mistaken for an active ignore rule. Accepts the bare filename and
    the equivalent ``.trw/credentials.yaml`` path form some users may write.
    """
    accepted = {
        _CREDENTIALS_IGNORE_RULE,
        f"/{_CREDENTIALS_IGNORE_RULE}",
        f".trw/{_CREDENTIALS_IGNORE_RULE}",
        f"/.trw/{_CREDENTIALS_IGNORE_RULE}",
    }
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped in accepted:
            return True
    return False


def _ensure_credentials_gitignored(
    target_dir: Path,
    result: dict[str, list[str]],
    dry_run: bool,
    on_progress: ProgressCallback = None,
) -> None:
    """Merge-ensure ``.trw/.gitignore`` ignores ``credentials.yaml`` (FR02).

    Appends the credentials rule to an existing custom ``.trw/.gitignore``
    WITHOUT discarding any user customizations (the safe alternative to a blind
    ``_ALWAYS_UPDATE`` overwrite), and is idempotent. Creates a minimal
    ``.gitignore`` when one is absent.

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
        if _credentials_already_ignored(existing):
            return
        if dry_run:
            result["updated"].append(f"would update: {gitignore} (add credentials.yaml ignore rule)")
            return
        sep = "" if existing.endswith("\n") or existing == "" else "\n"
        appended = f"{existing}{sep}{_CREDENTIALS_IGNORE_COMMENT}\n{_CREDENTIALS_IGNORE_RULE}\n"
        try:
            gitignore.write_text(appended, encoding="utf-8")
        except OSError as exc:
            result.setdefault("errors", []).append(f"Failed to update {gitignore}: {exc}")
            return
        result["updated"].append(str(gitignore))
        if on_progress:
            on_progress("Updated", str(gitignore))
        return

    # No .gitignore at all: create a minimal one carrying the rule.
    if dry_run:
        result["created"].append(f"would create: {gitignore} (credentials.yaml ignore rule)")
        return
    try:
        gitignore.write_text(
            f"{_CREDENTIALS_IGNORE_COMMENT}\n{_CREDENTIALS_IGNORE_RULE}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        result.setdefault("errors", []).append(f"Failed to create {gitignore}: {exc}")
        return
    result["created"].append(str(gitignore))
    if on_progress:
        on_progress("Created", str(gitignore))

"""Platform-credential storage out of git-tracked config (PRD-SEC-005).

Single source of truth for reading, writing, and migrating the
``platform_api_key`` bearer credential. The credential now lives in an
ignored ``.trw/credentials.yaml`` written mode ``0600`` rather than in the
git-tracked ``.trw/config.yaml``.

Belongs to the ``trw_mcp.models.config`` facade. Pure stdlib (no PyYAML) so
the installer template and the config loader can share the same precedence
logic without importing heavy dependencies.

Resolution precedence (highest wins) -- PRD-SEC-005-FR03:

1. ``TRW_PLATFORM_API_KEY`` environment variable (enterprise path).
2. ``.trw/credentials.yaml`` (``platform_api_key`` field).
3. ``.trw/config.yaml`` (``platform_api_key`` field) -- DEPRECATED
   backward-compat fallback; emits exactly one deprecation warning per
   process (FR04).
"""

from __future__ import annotations

import os
import re
import sys
import threading
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Environment variable that takes top precedence (FR03).
ENV_VAR = "TRW_PLATFORM_API_KEY"

# Filename of the ignored credential store, sibling to ``config.yaml``.
CREDENTIALS_FILENAME = "credentials.yaml"

_KEY_FIELD = "platform_api_key"

# Matches a top-level ``platform_api_key:`` line in a flat YAML file. The
# credential file is intentionally a tiny flat mapping, so a regex line scan
# is sufficient and avoids a PyYAML dependency in the installer template.
_KEY_RE = re.compile(r"^(\s*)platform_api_key\s*:\s*(.*)$")

# One-shot guard so the deprecation warning is emitted at most once per
# process (FR04: "exactly one deprecation warning ... per process").
_deprecation_lock = threading.Lock()
_deprecation_emitted = False


def credentials_path_for(config_path: Path) -> Path:
    """Return the ``credentials.yaml`` path sibling to *config_path*."""
    return config_path.parent / CREDENTIALS_FILENAME


def _strip_yaml_scalar(raw: str) -> str:
    """Strip quotes/whitespace from a flat YAML scalar value."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return value


def read_key_from_file(path: Path) -> str:
    """Return the ``platform_api_key`` value in *path*, or ``""`` if absent.

    Never raises: a missing/unreadable file or absent field yields ``""``.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    for line in text.splitlines():
        m = _KEY_RE.match(line)
        if m:
            value = _strip_yaml_scalar(m.group(2))
            if value:
                return value
    return ""


def write_credentials_key(credentials_path: Path, api_key: str) -> None:
    """Write *api_key* to *credentials_path* (``platform_api_key``) mode 0600.

    Creates the parent directory if needed. The file is (re)written with a
    single ``platform_api_key`` field. The chmod is best-effort: on platforms
    that do not honor POSIX mode bits (e.g. Windows) a WARNING is logged and
    the write still proceeds, mirroring ``_pin_store.py`` (NFR03).
    """
    credentials_path.parent.mkdir(parents=True, exist_ok=True)
    credentials_path.write_text(
        f'# TRW platform credential — ignored by git, mode 0600 (PRD-SEC-005).\nplatform_api_key: "{api_key}"\n',
        encoding="utf-8",
    )
    try:
        os.chmod(credentials_path, 0o600)
    except OSError as exc:
        # Windows does not honor POSIX mode bits; warn and proceed (NFR03).
        logger.warning(
            "credentials_chmod_failed",
            path=str(credentials_path),
            error=type(exc).__name__,
        )


def remove_credentials_key(credentials_path: Path) -> bool:
    """Remove the ``platform_api_key`` credential from *credentials_path*.

    Used by ``auth logout`` (PRD-SEC-005 round-2): the bearer credential lives
    in ``credentials.yaml`` post-SEC-005, so logout MUST clear it there — not
    only in the deprecated ``config.yaml`` fallback. The whole file is deleted
    (it exists solely to hold the credential), so the resolver finds nothing.

    Returns True iff a non-empty key was present and is now removed. Idempotent
    and fail-open: a missing/unreadable file is a no-op returning False.
    """
    if not read_key_from_file(credentials_path):
        return False
    try:
        credentials_path.unlink()
    except OSError as exc:
        logger.warning(
            "credentials_remove_failed",
            path=str(credentials_path),
            error=type(exc).__name__,
        )
        return False
    return True


def _emit_deprecation_warning(config_path: Path) -> None:
    """Emit exactly one deprecation warning per process (FR04)."""
    global _deprecation_emitted
    with _deprecation_lock:
        if _deprecation_emitted:
            return
        _deprecation_emitted = True
    logger.warning(
        "platform_api_key_from_deprecated_config",
        config_path=str(config_path),
        guidance="run 'trw-mcp update-project .' to migrate the key to .trw/credentials.yaml",
    )
    print(
        "TRW: WARNING — platform_api_key was read from the deprecated, "
        f"git-tracked {config_path}. Run 'trw-mcp update-project .' to move it "
        "into .trw/credentials.yaml (mode 0600, gitignored), then rotate any "
        "key already committed to git history.",
        file=sys.stderr,
    )


def reset_deprecation_state() -> None:
    """Reset the one-shot deprecation guard (test isolation only)."""
    global _deprecation_emitted
    with _deprecation_lock:
        _deprecation_emitted = False


def resolve_platform_api_key(
    config_path: Path,
    *,
    config_key: str | None = None,
) -> str:
    """Resolve the platform API key by precedence (FR03/FR04).

    Precedence: ``TRW_PLATFORM_API_KEY`` env > ``credentials.yaml`` >
    *config_key* (the value already parsed from ``config.yaml``). If the key
    is resolved from the deprecated *config_key* path, a one-shot deprecation
    warning is emitted.

    Args:
        config_path: Path to ``.trw/config.yaml`` (used to locate the sibling
            ``credentials.yaml`` and for the deprecation message).
        config_key: The ``platform_api_key`` already read from ``config.yaml``
            by the caller (the loader already parsed the YAML). ``None``/``""``
            means config.yaml carries no key.

    Returns:
        The resolved key, or ``""`` if no source supplies one.
    """
    env_key = os.environ.get(ENV_VAR, "").strip()
    if env_key:
        return env_key

    creds_key = read_key_from_file(credentials_path_for(config_path))
    if creds_key:
        return creds_key

    if config_key:
        _emit_deprecation_warning(config_path)
        return config_key

    return ""


def _blank_config_key(config_path: Path) -> bool:
    """Blank the ``platform_api_key`` field in *config_path* in place.

    Returns True if a non-empty key was found and blanked. Idempotent: an
    already-empty/absent field is a no-op (returns False).
    """
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return False

    new_lines: list[str] = []
    blanked = False
    for line in lines:
        m = _KEY_RE.match(line.rstrip("\n"))
        if m and _strip_yaml_scalar(m.group(2)):
            indent = m.group(1)
            new_lines.append(f'{indent}platform_api_key: ""\n')
            blanked = True
        else:
            new_lines.append(line if line.endswith("\n") else line + "\n")

    if blanked:
        config_path.write_text("".join(new_lines), encoding="utf-8")
    return blanked


def migrate_config_key(config_path: Path) -> bool:
    """Move a tracked ``config.yaml`` key into ``credentials.yaml`` (FR05).

    Idempotent: if ``config.yaml`` has no non-empty ``platform_api_key``, this
    is a no-op and returns False. Otherwise the key is written to
    ``credentials.yaml`` (mode 0600) and blanked in ``config.yaml``.

    Returns True iff a migration was performed.
    """
    config_key = read_key_from_file(config_path)
    if not config_key:
        return False

    credentials_path = credentials_path_for(config_path)
    existing_cred = read_key_from_file(credentials_path)
    # If credentials.yaml already holds a key, prefer it but still blank the
    # tracked config so the credential stops being committed.
    write_credentials_key(credentials_path, existing_cred or config_key)
    _blank_config_key(config_path)

    logger.warning(
        "platform_api_key_migrated",
        config_path=str(config_path),
        credentials_path=str(credentials_path),
        guidance="rotate the key if it was already committed to git history",
    )
    return True


def migrate_for_update_project(config_path: Path, result: dict[str, list[str]]) -> None:
    """Run the FR05 credential migration for ``update-project``, recording notes.

    Idempotent and fail-open: a missing config, an absent/empty key, or an OS
    error never raises — the update continues. On a successful migration the
    ``result`` dict's ``updated``/``warnings`` lists gain operator-facing notes
    (including the rotate-if-committed advisory).
    """
    if not config_path.is_file():
        return
    try:
        if migrate_config_key(config_path):
            result["updated"].append("Migrated platform_api_key to .trw/credentials.yaml (mode 0600)")
            result["warnings"].append(
                "platform_api_key moved out of git-tracked config.yaml — "
                "ROTATE the key if it was already committed to git history."
            )
    except OSError as exc:
        result["warnings"].append(f"Credential migration skipped: {exc}")

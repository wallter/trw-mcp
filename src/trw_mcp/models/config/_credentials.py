"""Platform-credential storage out of git-tracked config (PRD-SEC-005).

Single source of truth for reading, writing, and migrating the
``platform_api_key`` bearer credential. The credential now lives in an
ignored ``.trw/credentials.yaml`` written mode ``0600`` rather than in the
git-tracked ``.trw/config.yaml``.

Belongs to the ``trw_mcp.models.config`` facade. Pure stdlib (no PyYAML) so
the installer template and the config loader can share the same precedence
logic without importing heavy dependencies.

Resolution precedence (highest wins) -- PRD-SEC-005-FR03:

1. ``TRW_PLATFORM_API_KEY`` / ``TRW_API_KEY`` environment variable
   (enterprise path; inject from a secret manager, no on-disk key required).
2. ``.trw/credentials.yaml`` (``platform_api_key`` field).
3. The machine store ``~/.trw/credentials.yaml``: the same one-line format,
   outside every repository, so one sign-in serves every project on the
   computer. It follows ``Path.home()`` like the ``~/.trw/config.yaml`` machine
   layer (and the machine jev store ``~/.trw/jev.env``), not ``TRW_USER_DIR``.
   It is read only when it is a regular file (not a symlink) owned by the
   current user with no group/other permission bits; anything looser is
   refused with a warning and never read.

The git-tracked ``.trw/config.yaml`` is NEVER a resolution source: the
credential is a secret and must not live in a tracked file. A legacy tracked
key is migrated into ``credentials.yaml`` (and blanked in config.yaml) by
``migrate_config_key`` on ``trw-mcp update-project``; the resolver itself has
no config.yaml fallback.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path, PurePath

import structlog
from trw_memory.exceptions import UnsafeWriteError
from trw_memory.machine_secrets import PrivateRead, read_private_file
from trw_memory.safe_fs import write_beneath

logger = structlog.get_logger(__name__)

# Environment variables that take top precedence (FR03). ``TRW_PLATFORM_API_KEY``
# is the canonical enterprise variable; ``TRW_API_KEY`` is accepted as an alias
# because the installer (``scripts/install.sh``) and ``publish-release.sh`` export
# the key under that name.
ENV_VAR = "TRW_PLATFORM_API_KEY"
ALT_ENV_VAR = "TRW_API_KEY"

# Filename of the ignored credential store, sibling to ``config.yaml``.
CREDENTIALS_FILENAME = "credentials.yaml"

# Matches a top-level ``platform_api_key:`` line in a flat YAML file. The
# credential file is intentionally a tiny flat mapping, so a regex line scan
# is sufficient and avoids a PyYAML dependency in the installer template.
_KEY_RE = re.compile(r"^(\s*)platform_api_key\s*:\s*(.*)$")

#: Called with each file the migration wrote and the exact bytes it wrote, so an update's rollback can prove
#: those writes were its own (``trw_mcp._checkout_write.record_run_write``).
WriteRecorder = Callable[[Path, bytes], None]


#: How the machine store is named in messages (never with the home path expanded).
MACHINE_CREDENTIALS_LABEL = "~/.trw/credentials.yaml"
_MACHINE_REL_PATH = PurePath(".trw", CREDENTIALS_FILENAME)

#: The layer a resolved key came from, for ``auth status`` (``""`` = none).
SOURCE_ENV = "environment"
SOURCE_PROJECT = ".trw/credentials.yaml"
SOURCE_MACHINE = MACHINE_CREDENTIALS_LABEL


def machine_credentials_path() -> Path:
    """``~/.trw/credentials.yaml``, resolved at call time so a changed ``HOME`` is honoured."""
    return Path.home() / _MACHINE_REL_PATH


def _read_machine(path: Path | None) -> PrivateRead[str]:
    """The machine store through the shared owner-only reader (``trw_memory.machine_secrets``), parsed for the key."""
    target = machine_credentials_path() if path is None else path
    return read_private_file(target, _key_in_text, shown=MACHINE_CREDENTIALS_LABEL)


def machine_store_problem(path: Path | None = None) -> str:
    """Why the machine store must not be read, ``""`` when it may be (or is absent).

    The same owner-only rule as the machine jev store: a symlink, a non-regular
    file, another owner, or any group/other permission bit is refused.
    """
    return _read_machine(path).problem


def read_machine_key(path: Path | None = None) -> str:
    """Return the machine store's key, or ``""`` when absent or refused (refusal is logged)."""
    return _read_machine(path).value or ""


def write_machine_key(api_key: str, *, home: Path | None = None) -> Path:
    """Write *api_key* to ``~/.trw/credentials.yaml`` at exactly 0600; return its path.

    A symlinked ``~/.trw`` or store is refused with ``UnsafeWriteError``.
    """
    if not api_key or "\n" in api_key or "\r" in api_key or '"' in api_key:
        raise ValueError("not a storable API key")
    root = Path.home() if home is None else home
    content = f'# TRW platform credential for every project on this computer — owner-only, keep it 0600.\nplatform_api_key: "{api_key}"\n'
    write_beneath(root, _MACHINE_REL_PATH, content.encode("utf-8"), mode=0o600, exact_mode=True)
    return root / _MACHINE_REL_PATH


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
    return _key_in_text(text)


def _key_in_text(text: str) -> str:
    """The first non-empty ``platform_api_key`` value in *text*, or ``""``."""
    for line in text.splitlines():
        m = _KEY_RE.match(line)
        if m:
            value = _strip_yaml_scalar(m.group(2))
            if value:
                return value
    return ""


def write_credentials_key(credentials_path: Path, api_key: str) -> bytes:
    """Write *api_key* to *credentials_path* (``platform_api_key``) mode 0600.

    Delegates the whole write to ``trw_memory.safe_fs.write_beneath``, anchored
    one level above *credentials_path* (its grandparent as the trusted root,
    its parent-dir name plus leaf as the safe-fs relative path): the file is
    created at mode 0600 directly -- there is no separate ``os.chmod`` call
    after the write, closing the create-then-chmod window R12/V03/V04 all
    confirmed (PRD-CORE-337-FR09). A symlinked leaf or a symlinked parent
    component (e.g. a hostile checkout replacing the credentials directory
    with a link) is refused with ``trw_memory.exceptions.UnsafeWriteError``
    rather than written through; the parent directory is still created when
    missing, matching the previous ``mkdir(parents=True, exist_ok=True)``
    behavior for the ordinary (non-symlinked) case.
    """
    root = credentials_path.parent.parent
    rel_path = PurePath(credentials_path.parent.name, credentials_path.name)
    content = f'# TRW platform credential — ignored by git, mode 0600 (PRD-SEC-005).\nplatform_api_key: "{api_key}"\n'
    data = content.encode("utf-8")
    write_beneath(root, rel_path, data, mode=0o600)
    return data


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


def resolve_platform_api_key_with_source(config_path: Path) -> tuple[str, str]:
    """Resolve the key and name the layer that supplied it (``("", "")`` when none).

    Precedence: env > project ``.trw/credentials.yaml`` > machine store.
    """
    env_key = os.environ.get(ENV_VAR, "").strip() or os.environ.get(ALT_ENV_VAR, "").strip()
    if env_key:
        return env_key, SOURCE_ENV
    project_key = read_key_from_file(credentials_path_for(config_path))
    if project_key:
        return project_key, SOURCE_PROJECT
    machine_key = read_machine_key()
    if machine_key:
        return machine_key, SOURCE_MACHINE
    return "", ""


def resolve_platform_api_key(config_path: Path) -> str:
    """Resolve the platform API key by precedence (FR03).

    This is the SINGLE source of truth for the package's ``platform_api_key``
    resolution — every package consumer reads the key that this function feeds
    into ``TRWConfig.platform_api_key`` (via ``_loader.py``). There is NO
    ``config.yaml`` fallback: the credential is a secret and must never be read
    from the git-tracked config.

    Precedence (highest wins): ``TRW_PLATFORM_API_KEY`` env > ``TRW_API_KEY``
    env > ``.trw/credentials.yaml`` > ``~/.trw/credentials.yaml``.

    Args:
        config_path: Path to ``.trw/config.yaml`` (used only to locate the
            sibling ``credentials.yaml``).

    Returns:
        The resolved key, or ``""`` if no source supplies one.
    """
    return resolve_platform_api_key_with_source(config_path)[0]


def _blank_config_key_text(text: str) -> str:
    """Return *text* with every non-empty ``platform_api_key`` value replaced by ``""`` (lines otherwise kept)."""
    new_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        m = _KEY_RE.match(line.rstrip("\n"))
        if m and _strip_yaml_scalar(m.group(2)):
            new_lines.append(f'{m.group(1)}platform_api_key: ""\n')
        else:
            new_lines.append(line if line.endswith("\n") else line + "\n")
    return "".join(new_lines)


def _blank_config_key(config_path: Path, record: WriteRecorder | None = None) -> bool:
    """Blank the ``platform_api_key`` field in *config_path* in place.

    Returns True if a non-empty key was found and blanked. Idempotent: an
    already-empty/absent field is a no-op (returns False).
    """
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # trw-fail-silent-allow: an unreadable config has no key to move; the update continues
        return False
    if not read_key_from_file(config_path):
        return False
    data = _blank_config_key_text(text).encode("utf-8")
    config_path.write_bytes(data)
    if record is not None:
        record(config_path, data)
    return True


def migrate_config_key(config_path: Path, record: WriteRecorder | None = None) -> bool:
    """Move a tracked ``config.yaml`` key into ``credentials.yaml`` (FR05).

    Idempotent: if ``config.yaml`` has no non-empty ``platform_api_key``, this
    is a no-op and returns False. Otherwise the key is written to
    ``credentials.yaml`` (mode 0600) and blanked in ``config.yaml``.

    *record*, when given, is called with each file written and its exact bytes.

    Returns True iff a migration was performed.
    """
    config_key = read_key_from_file(config_path)
    if not config_key:
        return False

    credentials_path = credentials_path_for(config_path)
    existing_cred = read_key_from_file(credentials_path)
    # If credentials.yaml already holds a key, prefer it but still blank the
    # tracked config so the credential stops being committed.
    written = write_credentials_key(credentials_path, existing_cred or config_key)
    if record is not None:
        record(credentials_path, written)
    _blank_config_key(config_path, record)

    logger.warning(
        "platform_api_key_migrated",
        config_path=str(config_path),
        credentials_path=str(credentials_path),
        guidance="rotate the key if it was already committed to git history",
    )
    return True


def migrate_for_update_project(config_path: Path, result: dict[str, list[str]]) -> None:
    """Run the FR05 credential migration for ``update-project``, recording notes.

    Idempotent and fail-open: a missing config, an absent/empty key, an OS
    error, or a refused symlinked write target never raises — the update
    continues. On a successful migration the ``result`` dict's
    ``updated``/``warnings`` lists gain operator-facing notes (including the
    rotate-if-committed advisory).
    """
    if not config_path.is_file():
        return
    from trw_mcp._checkout_write import record_run_write  # update-project only: keep the loader's import light

    try:
        if migrate_config_key(config_path, record_run_write):
            # update-project runs this again after it puts an uncommitted config.yaml back whole, so each note
            # is added once per run.
            for key, note in (
                ("updated", "Migrated platform_api_key to .trw/credentials.yaml (mode 0600)"),
                (
                    "warnings",
                    "platform_api_key moved out of git-tracked config.yaml — "
                    "ROTATE the key if it was already committed to git history.",
                ),
            ):
                if note not in result[key]:
                    result[key].append(note)
    except (OSError, UnsafeWriteError) as exc:
        result["warnings"].append(f"Credential migration skipped: {exc}")

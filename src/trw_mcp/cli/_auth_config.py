"""Config-file write/clear helpers for the TRW auth CLI.

Belongs to the ``auth.py`` facade. Re-exported there for back-compat.

Extracted (PRD-SEC-005) to keep ``auth.py`` under the 350-effective-LOC gate
once the credential write moved to ``credentials.yaml``. These helpers operate
on the git-tracked ``config.yaml`` for non-secret metadata (org/email) and the
``logout`` field-clear path. The bearer credential itself is written via
``models/config/_credentials.py`` and never lands here.
"""

from __future__ import annotations

import re
from pathlib import Path

from trw_mcp.models.config._credentials import (
    _blank_config_key,
    credentials_path_for,
    remove_credentials_key,
)

_YAML_KEY_RE = re.compile(r"^(\s*)(platform_api_key)\s*:\s*(.*)$")


def _read_config_lines(config_path: Path) -> list[str] | None:
    """Read config file lines, or None if file doesn't exist."""
    try:
        return config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return None


def device_auth_logout(config_path: Path) -> bool:
    """Remove the platform credential from BOTH credential stores.

    PRD-SEC-005 round-2: the bearer credential now lives in the ignored
    ``.trw/credentials.yaml`` (the SEC-005 store), with ``config.yaml`` only a
    deprecated fallback. Logout MUST clear the credentials.yaml key — clearing
    only config.yaml left the credential live, so ``auth status`` and the
    runtime resolver still reported authenticated.

    Clears the credentials.yaml store (deletes the file) AND blanks any legacy
    ``platform_api_key`` left in config.yaml. Idempotent and fail-open.

    Returns True iff a non-empty key was found in either store and removed.
    """
    removed_cred = remove_credentials_key(credentials_path_for(config_path))
    removed_config = _blank_config_key(config_path)
    return removed_cred or removed_config


def _save_config_field(config_path: Path, key: str, value: str) -> None:
    """Write or update a single field in config YAML."""
    field_re = re.compile(rf"^(\s*)({re.escape(key)})\s*:\s*(.*)$")
    lines = _read_config_lines(config_path)
    if lines is None:
        config_path.write_text(f'{key}: "{value}"\n', encoding="utf-8")
        return

    new_lines: list[str] = []
    replaced = False
    for line in lines:
        m = field_re.match(line.rstrip("\n"))
        if m:
            indent = m.group(1)
            new_lines.append(f'{indent}{key}: "{value}"\n')
            replaced = True
        else:
            new_lines.append(line if line.endswith("\n") else line + "\n")

    if not replaced:
        new_lines.append(f'{key}: "{value}"\n')

    config_path.write_text("".join(new_lines), encoding="utf-8")


def _save_api_key(config_path: Path, api_key: str) -> None:
    """Write or update ``platform_api_key`` in config YAML.

    DEPRECATED (PRD-SEC-005): the credential no longer belongs in the tracked
    ``config.yaml``. Retained only for backward compatibility; production login
    writes to ``credentials.yaml`` via ``write_credentials_key``.
    """
    lines = _read_config_lines(config_path)
    if lines is None:
        # Create minimal config
        config_path.write_text(
            f'platform_api_key: "{api_key}"\n',
            encoding="utf-8",
        )
        return

    new_lines: list[str] = []
    replaced = False
    for line in lines:
        m = _YAML_KEY_RE.match(line.rstrip("\n"))
        if m:
            indent = m.group(1)
            new_lines.append(f'{indent}platform_api_key: "{api_key}"\n')
            replaced = True
        else:
            new_lines.append(line if line.endswith("\n") else line + "\n")

    if not replaced:
        new_lines.append(f'platform_api_key: "{api_key}"\n')

    config_path.write_text("".join(new_lines), encoding="utf-8")

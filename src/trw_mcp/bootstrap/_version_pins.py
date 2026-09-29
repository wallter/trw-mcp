"""Retire canon-version pins an older init wrote as defaults (M1 dry run release blocker).

Belongs to the ``update-project`` post-update phase (``_update_project._run_post_update_phases``).
Until 8.0 ``init-project`` wrote ``framework_version: <the then-current default>`` into ``.trw/config.yaml``.
``update-project`` preserves that file as user config, so the pin froze every project at the canon it was
initialised with, and the redeployed canon failed ``framework_integrity`` against it. A pin is retired only on
ownership evidence: its value equals the ``*_at_install`` version init recorded in ``.trw/installer-meta.yaml``
(written alongside ``config.yaml``) and is not newer than the installed default. The removal is reported. A pin
without that record -- a user's choice, a newer version, a non-TRW string -- is left alone.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

#: field -> the shape of a TRW-shipped value for it (numeric parts captured for the comparison).
_SHIPPED_SHAPES: dict[str, re.Pattern[str]] = {
    "framework_version": re.compile(r"^v(\d+)\.(\d+)_TRW$"),
    "aaref_version": re.compile(r"^v(\d+)\.(\d+)\.(\d+)$"),
}


def _parts(field: str, value: str) -> tuple[int, ...] | None:
    match = _SHIPPED_SHAPES[field].match(value)
    return tuple(int(part) for part in match.groups()) if match else None


#: The installer-meta fields that record what an init wrote as each field's default.
_INSTALL_EVIDENCE: dict[str, tuple[str, ...]] = {
    "framework_version": ("framework_version_at_install", "framework_version"),
    "aaref_version": ("aaref_version_at_install",),
}


def _install_record(target_dir: Path) -> dict[str, str]:
    """``.trw/installer-meta.yaml`` as flat strings; empty when it is absent or unreadable (then nothing retires)."""
    path = target_dir / ".trw" / "installer-meta.yaml"
    if not path.is_file() or path.is_symlink():
        return {}
    record: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, raw = line.partition(":")
        if sep and not line.startswith((" ", "\t")):
            record[key.strip()] = raw.strip().strip("'\"")
    return record


def _written_by_init(field: str, value: str, record: dict[str, str]) -> bool:
    """The pin's value is what an init recorded writing, and no newer than the installed default."""
    from trw_mcp.models.config import TRWConfig

    pinned = _parts(field, value)
    default = _parts(field, str(TRWConfig.model_fields[field].default))
    # update-project rewrites installer-meta too, copying config.yaml's pin into the *_at_install fields, so only
    # a record init itself last wrote proves init chose the value (canon-redeploy review r2).
    by_init = record.get("installed_by") == "trw-mcp init-project"
    recorded = by_init and any(record.get(key) == value for key in _INSTALL_EVIDENCE[field])
    return recorded and pinned is not None and default is not None and pinned <= default


def _older_than_default(field: str, value: str) -> bool:
    from trw_mcp.models.config import TRWConfig

    pinned = _parts(field, value)
    default = _parts(field, str(TRWConfig.model_fields[field].default))
    return pinned is not None and default is not None and pinned < default


def retire_default_version_pins(
    target_dir: Path, result: dict[str, list[str]], proven: frozenset[str] | None = None
) -> frozenset[str]:
    """Remove a canon-version pin that an earlier init provably wrote as its default; return the pins retired.

    Ownership evidence is ``.trw/installer-meta.yaml``, which init writes alongside ``config.yaml``: a pin whose
    value equals the recorded ``*_at_install`` version (and is not newer than the installed default) is the
    init's default. A pin with no such record is the user's, and stays. Call it BEFORE the update rewrites
    installer-meta; a later call passes the returned *proven* set, since the evidence is then overwritten.
    """
    from trw_mcp._checkout_write import write_checkout_file
    from trw_mcp.models.config import _reset_config

    config_path = target_dir / ".trw" / "config.yaml"
    if not config_path.is_file() or config_path.is_symlink():
        return frozenset()
    record = _install_record(target_dir) if proven is None else {}
    kept: list[str] = []
    retired: list[str] = []
    for line in config_path.read_text(encoding="utf-8").splitlines(keepends=True):
        field, sep, raw = line.strip().partition(":")
        value = raw.strip().strip("'\"")
        pin = f"{field}: {value}"
        if sep and field in _SHIPPED_SHAPES and not line.startswith((" ", "\t")):
            if (pin in proven) if proven is not None else _written_by_init(field, value, record):
                retired.append(pin)
                continue
            if proven is None and _older_than_default(field, value):
                result.setdefault("warnings", []).append(
                    f".trw/config.yaml pins '{pin}', older than the installed canon, and nothing proves init wrote "
                    "it, so it was kept; it freezes the framework canon at that version (framework_integrity "
                    f"fails). Delete the '{field}:' line unless you pinned it on purpose."
                )
        kept.append(line)
    if not retired:
        return frozenset()
    write_checkout_file(target_dir, config_path, "".join(kept))
    _reset_config()  # the cached config still carries the pin; the redeploy must see the package default
    info = result.setdefault("info", [])
    for pin in retired:
        message = (
            f"retired the stale canon pin '{pin}' from .trw/config.yaml: init-project wrote it as its default "
            "(recorded in .trw/installer-meta.yaml), and the installed package now supplies the version"
        )
        if message not in info:  # the update applies it before the redeploy and again after preservation
            logger.info("canon_version_pin_retired", pin=pin, path=str(config_path))
            info.append(message)
    return frozenset(retired)

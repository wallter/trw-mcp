"""Which ``trw-distill`` a version venv gets: an explicit pin, else the highest compatible wheelhouse wheel."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import structlog
from packaging.tags import sys_tags
from packaging.utils import InvalidWheelFilename, parse_wheel_filename
from packaging.version import Version

from trw_mcp.shared_server._records import SharedServerError

logger = structlog.get_logger(__name__)


def _compatible_distill_versions(wheelhouse: Path) -> list[Version]:
    """Versions of the ``trw_distill`` wheels in *wheelhouse* that install on THIS interpreter.

    Compatibility is judged against the current interpreter's tags (the venv is created from the same ``uv``
    default python); unparseable wheel names are logged and ignored.
    """
    supported = set(sys_tags())
    found: list[Version] = []
    for wheel in wheelhouse.glob("trw_distill-*.whl"):
        try:
            name, version, _build, tags = parse_wheel_filename(wheel.name)
        except InvalidWheelFilename:
            logger.warning("shared_wheel_name_ignored", wheel=wheel.name)
            print(f"trw-mcp: ignoring wheelhouse file with an unparseable name: {wheel.name}", file=sys.stderr)
            continue  # trw-fail-silent-allow: logged and reported on stderr; the file is skipped, never an error
        if name == "trw-distill" and supported.intersection(tags):
            found.append(version)
    return found


def _distill_spec(wheelhouse: Path, with_distill: str | None) -> str | None:
    """``trw-distill==Y``: the explicit *with_distill*, else the highest compatible wheel; ``None`` if neither."""
    if with_distill is not None:
        if not re.fullmatch(r"trw-distill==[0-9A-Za-z][0-9A-Za-z.+!_-]*", with_distill):
            raise SharedServerError(f"--with takes one pinned spec, trw-distill==<version>; got {with_distill!r}")
        return with_distill
    found = _compatible_distill_versions(wheelhouse)
    return f"trw-distill=={max(found)}" if found else None

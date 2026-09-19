"""Live writers that predate the installed distribution (PRD-CORE-277-FR09).

Belongs to the ``_subcommands_doctor.py`` and ``_subcommands_release.py``
facades; both read the same layer so doctor and ``version-status`` cannot
disagree about it (they already did once: doctor said PASS while version-status
said ``compatible: false``).

After an in-place upgrade the previous session's servers keep running and keep
writing the same store — five of them on the reporting operator's box, holding
locks in ``.trw/memory/memory.db.writers/`` (sub_ThQtB4q1pMKoHi4i, 2026-09-16).
Nothing said so.

What can and cannot be established here, stated once so no caller overstates it:

* a writer lock records a PID and the wall-clock epoch at which it REGISTERED.
  It does not record the code that process loaded. So the finding is
  "registered before the installed distribution's metadata timestamp", never
  "running an old version".
* the timestamp is the ``.dist-info`` directory's mtime, which is an install
  RECEIPT and not a version: a same-version reinstall refreshes it (every live
  writer then looks older), and an editable checkout does not move it when the
  source changes (a genuinely older process then looks current). Both directions
  are reported in the payload rather than hidden behind a verdict.

This module reports. It never signals a process it did not spawn — the only
``os.kill`` on this path is the census's existing ``signal 0`` liveness probe.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Literal

import structlog

# TypedDict from typing_extensions, not typing: Pydantic cannot build a schema
# from a stdlib TypedDict on 3.10/3.11, and this repo pins that as a runtime rule
# (tests/test_supported_python_registration.py).
from typing_extensions import TypedDict

from trw_mcp.state._writer_census_identity import read_writer_lock

logger = structlog.get_logger(__name__)

__all__ = ["PredatingWriters", "predating_writers", "predating_writers_row"]

_DISTRIBUTION = "trw-mcp"


class PredatingWriters(TypedDict):
    """One measurement of the writer registry against the install receipt."""

    measured: bool
    reason: str
    install_epoch: float | None
    install_epoch_source: str
    pids: list[int]
    total_live_writers: int
    caveats: list[str]


def _install_epoch() -> tuple[float | None, str]:
    """Return the installed distribution's metadata mtime and where it came from."""
    try:
        dist = distribution(_DISTRIBUTION)
    except PackageNotFoundError:
        logger.info("predating_writers_distribution_missing", distribution=_DISTRIBUTION)
        return None, "unavailable"
    meta_path = getattr(dist, "_path", None)
    if isinstance(meta_path, Path) and meta_path.exists():
        return meta_path.stat().st_mtime, "dist_info_mtime"
    try:
        import trw_mcp

        module_file = trw_mcp.__file__
    except (ImportError, AttributeError):  # pragma: no cover - trw_mcp is importing this
        return None, "unavailable"
    if module_file is None:  # pragma: no cover - namespace package
        return None, "unavailable"
    return Path(module_file).stat().st_mtime, "module_mtime"


def predating_writers(trw_dir: Path) -> PredatingWriters:
    """Measure live writer registrations against the install receipt."""
    from trw_mcp.state.memory_pressure import live_memory_writer_pids

    caveats = [
        "a same-version reinstall refreshes the timestamp, so every live writer then appears to predate it",
        "an editable checkout does not refresh it when the source changes, so an older process can appear current",
    ]
    install_epoch, source = _install_epoch()
    live = live_memory_writer_pids(trw_dir)
    if install_epoch is None:
        return {
            "measured": False,
            "reason": "the installed distribution's metadata timestamp could not be read",
            "install_epoch": None,
            "install_epoch_source": source,
            "pids": [],
            "total_live_writers": len(live),
            "caveats": caveats,
        }

    writers_dir = trw_dir / "memory" / "memory.db.writers"
    older: list[int] = []
    unknown = 0
    for pid in live:
        try:
            record = read_writer_lock(writers_dir / f"{pid}.lock")
        except (OSError, UnicodeDecodeError):
            unknown += 1
            continue
        if record is None or record.registered_epoch is None:
            unknown += 1
            continue
        if record.registered_epoch < install_epoch:
            older.append(pid)
    if unknown:
        caveats.append(f"{unknown} live writer lock(s) carry no readable registration epoch and were not classified")
    return {
        "measured": True,
        "reason": "",
        "install_epoch": install_epoch,
        "install_epoch_source": source,
        "pids": sorted(older),
        "total_live_writers": len(live),
        "caveats": caveats,
    }


def predating_writers_row(trw_dir: Path) -> tuple[Literal["PASS", "WARN", "SKIP"], str]:
    """Render the doctor row. Reports; never terminates anything."""
    measurement = predating_writers(trw_dir)
    if not measurement["measured"]:
        return "SKIP", f"predating-writer check not run: {measurement['reason']}."
    pids = measurement["pids"]
    if not pids:
        return (
            "PASS",
            f"none of {measurement['total_live_writers']} live writer registration(s) predate the installed "
            f"distribution ({measurement['install_epoch_source']}).",
        )
    return (
        "WARN",
        f"{len(pids)} running server process(es) predate the installed version "
        f"(pids {', '.join(str(pid) for pid in pids)}): their writer registrations are older than the installed "
        f"distribution's metadata timestamp ({measurement['install_epoch_source']}), and their loaded versions are "
        "unknown. Remedy: restart those sessions, or `kill <pid>` for each one after confirming it is not the "
        "current connection (a client can keep its old server alive after /mcp). Nothing was signalled.",
    )

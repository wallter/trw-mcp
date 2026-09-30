"""One reading of "which trw-memory is this client, which is the daemon" for the doctor rows (CODEX-P0-C step 3).

Belongs to the ``_subcommands_doctor.py`` facade (helper for ``_doctor_memory_daemon``, ``_doctor_version_status`` and
``_doctor_launcher_divergence``). Before this, ``memory_backend`` FAILed a client/daemon mismatch while ``memory_daemon``
and ``version_status`` were green or gave advice that only fits an OLD daemon ("stop pid ...; the next call starts a
new one"), which for a NEWER daemon means evicting the newer service in favour of the older client. The direction decides
the remedy: the client is upgraded, the newer daemon is left alone.

No subprocess, daemon call or MCP connection (NFR01 of the launcher row): only the discovery record and dist-info files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_NUMERIC = re.compile(r"\d+")


def version_key(version: str) -> tuple[int, ...]:
    """Leading numeric components (``5.0.1.dev3`` -> ``(5, 0, 1)``); unparseable -> ``()``."""
    parts = []
    for piece in version.split("."):
        found = _NUMERIC.match(piece)
        if found is None:
            break
        parts.append(int(found.group()))
    return tuple(parts)


@dataclass(frozen=True)
class DaemonSkew:
    """A live daemon whose trw-memory version differs from the client's."""

    pid: int
    daemon: str
    client: str

    @property
    def daemon_is_newer(self) -> bool:
        return version_key(self.daemon) > version_key(self.client)

    @property
    def incompatible(self) -> bool:
        """The client's own refusal rule (``DaemonClient._compatible``): another MAJOR version. Minor/patch skew works."""
        from trw_memory.daemon._versions import majors_differ

        return majors_differ(self.daemon, self.client)

    def remedy(self) -> str:
        if self.daemon_is_newer:
            return (
                f"upgrade THIS client (trw-memory {self.client} -> {self.daemon} or newer: check `trw-mcp --version` and "
                f"the launcher your client config names); do not stop the newer daemon to make an older client work"
            )
        return "restart the older daemon so the next memory call starts one from this installation"


def daemon_skew(client_version: str, daemon_version: str, pid: int) -> DaemonSkew | None:
    if client_version == daemon_version:
        return None
    return DaemonSkew(pid=pid, daemon=daemon_version, client=client_version)


def read_daemon_skew() -> DaemonSkew | None:
    """The live daemon's skew against the installed trw-memory, or ``None`` (no daemon, unreadable, or in step)."""
    from trw_memory import __version__ as installed
    from trw_memory.daemon import DaemonPaths, DiscoveryAbsent, DiscoveryInvalid, read_discovery_result

    paths = DaemonPaths.resolve(create=False)
    result = read_discovery_result(paths)
    if isinstance(result, DiscoveryAbsent | DiscoveryInvalid) or not result.is_live(paths.lock):
        return None
    return daemon_skew(installed, str(result.version), int(result.pid))


def _interpreter_of(script: str, hops: int = 3) -> str | None:
    """The Python interpreter a console script runs: its shebang, or through an ``exec "<path>"`` shim (bounded)."""
    try:
        with open(script, "rb") as handle:
            head = handle.read(4096).decode("utf-8", errors="replace")
    except (
        OSError
    ):  # trw-fail-silent-allow: an unreadable launcher has no version to report; the caller says "not resolved"
        return None
    lines = head.splitlines()
    first = lines[0] if lines else ""
    if first.startswith("#!") and "python" in first.rsplit("/", 1)[-1]:
        return first[2:].split()[0]
    shim = re.search(r'^\s*exec\s+"?([^"\s]+)"?', head, re.MULTILINE)
    if shim is None or hops <= 0:
        return None
    return _interpreter_of(shim.group(1), hops - 1)


def launcher_versions(command: str, base: Path) -> tuple[str, str] | None:
    """``(trw-mcp, trw-memory)`` versions of the venv a console script belongs to, read from its dist-info.

    *command* is the name a client config launches: bare (searched on ``PATH``) or a path (relative to *base*). The
    interpreter its shebang (or an ``exec`` shim) names decides the venv whose ``lib/python*/site-packages`` holds the
    metadata. ``None`` when the launcher cannot be found or is not a venv console script.
    """
    import shutil

    found = shutil.which(command) if "/" not in command and "\\" not in command else str((base / command).resolve())
    if not found:
        return None
    interpreter = _interpreter_of(found)
    if interpreter is None:
        return None
    venv = Path(interpreter).parent.parent
    versions = []
    for dist in ("trw_mcp", "trw_memory"):
        metadata = next(iter(sorted(venv.glob(f"lib/python*/site-packages/{dist}-*.dist-info/METADATA"))), None)
        if metadata is None:
            return None
        match = re.search(r"^Version:\s*(\S+)", metadata.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
        if match is None:
            return None
        versions.append(match.group(1))
    return versions[0], versions[1]

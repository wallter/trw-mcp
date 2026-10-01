"""PRD-INFRA-200 FR01: doctor flags a managed client config whose resolved

launcher diverges from the project venv build (the N17 regression class: a
config left pointing at a stale global PATH install reports doctor PASS today
because ``version_status_row``/`stale_editable_metadata`` are package-level --
neither resolves what a specific client config actually names).

Scope: applies only in a dev checkout (``trw-mcp/src/trw_mcp`` under
target_dir); a user project is always green on this row. NFR01: one file read
plus one version-free string parse per managed config -- never a subprocess,
daemon, or MCP connection.

Belongs to the ``_subcommands_doctor.py`` facade (row imported the same way as
``_doctor_retired_artifacts.check_retired_artifacts``, to stay under its
350-eLOC gate).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import tomllib

if TYPE_CHECKING:
    from trw_mcp.server._subcommands_doctor import CheckResult

#: Mirrors ``bootstrap._utils._PROJECT_VENV_LAUNCHERS`` (not imported directly:
#: Class E -- this module reads config TEXT, it never imports the resolver's
#: platform-detection code path), plus the same venv's ``trw-mcp-proxy``, which
#: the resolver names instead once ``shared_mcp`` is on: still the project venv's build.
_PROJECT_VENV_LAUNCHERS: tuple[str, ...] = (
    ".venv/bin/trw-mcp",
    ".venv/Scripts/trw-mcp.exe",
    ".venv/bin/trw-mcp-proxy",
    ".venv/Scripts/trw-mcp-proxy.exe",
)
_WORKSPACE_PREFIX = "${workspaceFolder}/"
_Row = tuple[Literal["PASS", "WARN", "FAIL"], str]


class _Unparseable:
    """Sentinel: the config file exists but could not be read or parsed.

    codex sol fix-delta round 1 on lane-infra-200-d: a present-but-malformed
    config (e.g. a commented ``.vscode/mcp.json`` under strict JSON, or any
    config an OSError makes unreadable) must never collapse into the same
    "no command found" case a config that simply has no ``trw`` entry does --
    the former is an operator-visible problem this row exists to surface, the
    latter is an ordinary, unrelated config shape.
    """


_UNPARSEABLE = _Unparseable()
_Extracted = str | None | _Unparseable


def _expand_workspace(value: str) -> str:
    """Strip a leading ``${workspaceFolder}/`` (Cursor/VS Code); the remainder is project-relative either way."""
    return value.removeprefix(_WORKSPACE_PREFIX)


def _is_project_venv_launcher(command: str, target_dir: Path) -> bool:
    """Whether *command* resolves to the same file one of the project-venv launcher paths does.

    codex sol fix-delta round 1: a bare string comparison treated
    ``./.venv/bin/trw-mcp`` and an equivalent absolute path as a divergence
    from the canonical ``.venv/bin/trw-mcp`` form, though all three name the
    identical file. Resolving both sides removes that false WARN.
    """
    expanded = _expand_workspace(command)
    candidate = (target_dir / expanded).resolve()
    return any(candidate == (target_dir / rel).resolve() for rel in _PROJECT_VENV_LAUNCHERS)


_BARE_INTERPRETER = re.compile(r"python(\d+(\.\d+)*)?(\.exe)?")


def _is_bare_interpreter(command: str) -> bool:
    """``python3 -m trw_mcp.server`` and kin: a PATH-resolved interpreter (no directory part)."""
    return _BARE_INTERPRETER.fullmatch(command) is not None


def _has_project_venv_launcher(target_dir: Path) -> bool:
    return any((target_dir / rel).is_file() for rel in _PROJECT_VENV_LAUNCHERS)


def _is_unresolvable_but_safe(command: str, target_dir: Path) -> bool:
    """A bare interpreter is the resolver's own last fallback, so it is only safe while no project venv exists.

    Which ``trw_mcp`` it imports cannot be determined without executing it (NFR01). With no project venv
    there is nothing for it to diverge from; once ``.venv/bin/trw-mcp`` exists the resolver would name that
    launcher instead, so a bare ``python3`` then runs whatever build PATH finds -- the shadow this row targets
    (INFRA-200-FR01-PYTHON3-LAUNCHER).
    """
    return _is_bare_interpreter(command) and not _has_project_venv_launcher(target_dir)


def _read_config_text(path: Path) -> str | _Unparseable:
    """A config file's decoded text, or ``_UNPARSEABLE`` for an unreadable or non-UTF-8 file.

    codex sol fix-delta round 2: ``UnicodeDecodeError`` (invalid UTF-8 bytes) is not an
    ``OSError`` -- it escaped an ``except OSError``-only guard around this same call and
    reached the doctor's generic fail-open FAIL instead of this row's own distinct WARN.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (
        OSError,
        UnicodeDecodeError,
    ):  # trw-fail-silent-allow: an unreadable/non-UTF-8 config is reported unparseable, not crashed on (Class A)
        return _UNPARSEABLE


def _extract_command(data: object, *, servers_key: str) -> str | None:
    servers = data.get(servers_key) if isinstance(data, dict) else None
    trw = servers.get("trw") if isinstance(servers, dict) else None
    command = trw.get("command") if isinstance(trw, dict) else None
    return command if isinstance(command, str) else None


def _mcp_json_command(path: Path, *, key: str) -> _Extracted:
    """Parses as JSONC: VS Code's ``mcp.json`` permits ``//``/``/* */`` comments, and stripping them is a

    no-op for the other JSON configs here, so one parser covers every JSON-family config.
    """
    from trw_mcp.bootstrap._opencode_jsonc import _parse_jsonc

    raw = _read_config_text(path)
    if isinstance(raw, _Unparseable):
        return raw
    try:
        data = _parse_jsonc(raw)
    except ValueError:  # trw-fail-silent-allow: a malformed config is reported unparseable, not crashed on (Class A)
        return _UNPARSEABLE
    return _extract_command(data, servers_key=key)


def _opencode_command(path: Path) -> _Extracted:
    from trw_mcp.bootstrap._opencode_jsonc import _parse_jsonc

    raw = _read_config_text(path)
    if isinstance(raw, _Unparseable):
        return raw
    try:
        data = _parse_jsonc(raw)
    except ValueError:  # trw-fail-silent-allow: a malformed config is reported unparseable, not crashed on (Class A)
        return _UNPARSEABLE
    mcp = data.get("mcp") if isinstance(data, dict) else None
    trw = mcp.get("trw") if isinstance(mcp, dict) else None
    command = trw.get("command") if isinstance(trw, dict) else None
    if isinstance(command, list) and command and isinstance(command[0], str):
        return command[0]
    return None


def _codex_command(path: Path) -> _Extracted:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (
        OSError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
    ):  # trw-fail-silent-allow: a malformed/unreadable/non-UTF-8 config is reported unparseable, not crashed on (Class A)
        return _UNPARSEABLE
    return _extract_command(data, servers_key="mcp_servers")


#: (repo-relative config path, extractor). Order matches the PRD's Part-2 list
#: (grok's config shape is still unknown per the design doc and is out of scope).
_MANAGED_CONFIGS: tuple[tuple[str, Callable[[Path], _Extracted]], ...] = (
    (".mcp.json", lambda p: _mcp_json_command(p, key="mcpServers")),
    (".codex/config.toml", _codex_command),
    (".cursor/mcp.json", lambda p: _mcp_json_command(p, key="mcpServers")),
    (".vscode/mcp.json", lambda p: _mcp_json_command(p, key="servers")),
    (".github/mcp.json", lambda p: _mcp_json_command(p, key="mcpServers")),
    ("opencode.json", _opencode_command),
)


def present_managed_configs(target_dir: Path) -> list[str]:
    """The repo-relative managed client configs present in *target_dir*: the clients an upgrade must reconnect."""
    return [rel for rel, _extractor in _MANAGED_CONFIGS if (target_dir / rel).is_file()]


def _is_dev_checkout(target_dir: Path) -> bool:
    return (target_dir / "trw-mcp" / "src" / "trw_mcp").is_dir()


def _older_launchers(target_dir: Path) -> tuple[list[str], list[str]]:
    """``(incompatible, merely_older)`` managed configs, by the versions their launcher's venv carries.

    Incompatible = a MAJOR older than this build's trw-mcp, or a trw-memory major different from the live daemon's (the
    client's own refusal rule); merely older = a lower minor/patch. Applies to every project, not only a dev checkout: a
    bare ``trw-mcp`` resolving to an old install is what let a 7.0.1 client downgrade a deployed canon and read no memory
    from an 8.x daemon (CODEX-P0-C). Version-only, from dist-info; nothing is executed.
    """
    from importlib.metadata import PackageNotFoundError, version

    from trw_memory.daemon._versions import majors_differ

    from trw_mcp.server._doctor_version_skew import launcher_versions, read_daemon_skew, version_key

    try:  # dist-info, like the launchers' own: an editable install's source version can run ahead of its metadata
        running_mcp, running_memory = version("trw-mcp"), version("trw-memory")
    except (
        PackageNotFoundError
    ):  # trw-fail-silent-allow: no metadata to compare against, so no launcher can be called older
        return [], []
    daemon = read_daemon_skew()
    daemon_memory = daemon.daemon if daemon is not None else running_memory
    extractors = dict(_MANAGED_CONFIGS)
    incompatible: list[str] = []
    older: list[str] = []
    for rel in present_managed_configs(target_dir):
        command = extractors[rel](target_dir / rel)
        if command is None or isinstance(command, _Unparseable):
            continue
        versions = launcher_versions(_expand_workspace(command), target_dir)
        if versions is None:
            continue
        mcp, memory = versions
        stale_mcp = version_key(mcp) < version_key(running_mcp)
        stale_memory = version_key(memory) < version_key(daemon_memory)
        if not (stale_mcp or stale_memory):
            continue
        text = (
            f"{rel} launches {command!r}: trw-mcp {mcp} / trw-memory {memory}, older than this build "
            f"({running_mcp} / {running_memory}) or the live daemon ({daemon_memory})"
        )
        breaks = (stale_mcp and majors_differ(mcp, running_mcp)) or majors_differ(memory, daemon_memory)
        (incompatible if breaks else older).append(text)
    return incompatible, older


def launcher_divergence_row(target_dir: Path) -> _Row:
    """FAIL for a launcher on an older major, WARN for an older minor/patch or one off the project venv; else PASS."""
    incompatible, older = _older_launchers(target_dir)
    if incompatible:
        return (
            "FAIL",
            f"{len(incompatible)} managed config(s) start an OLDER trw-mcp: {'; '.join(sorted(incompatible))}. An older "
            "client cannot use a newer memory daemon (it reads no memory) and can overwrite the deployed framework "
            "canon. Fix: upgrade this client or point the config at the project's launcher (`trw-mcp update-project`); "
            "do not stop the newer daemon.",
        )
    if older:
        return (
            "WARN",
            f"{len(older)} managed config(s) start a slightly older trw-mcp (same major): {'; '.join(sorted(older))}. "
            "Upgrade this client when convenient.",
        )
    if not _is_dev_checkout(target_dir):
        return "PASS", "not a dev checkout; launcher-divergence check does not apply"

    extractors = dict(_MANAGED_CONFIGS)
    offenders: list[str] = []
    for rel in present_managed_configs(target_dir):
        command = extractors[rel](target_dir / rel)
        if isinstance(command, _Unparseable):
            offenders.append(f"{rel} (could not be parsed; verify its launcher manually)")
            continue
        if (
            command is None
            or _is_project_venv_launcher(command, target_dir)
            or _is_unresolvable_but_safe(command, target_dir)
        ):
            continue
        offenders.append(f"{rel} (resolves to {command!r}, not the project venv)")

    if not offenders:
        return "PASS", "every managed client config resolves to the project venv build"
    return (
        "WARN",
        f"{len(offenders)} managed config(s) resolve outside the project venv: "
        f"{'; '.join(sorted(offenders))} -- fix with `trw-mcp update-project`, "
        "or remove/upgrade the shadowing global install",
    )


def check_launcher_divergence(target: Path, _config: object) -> CheckResult:
    """Doctor-registry entry point (imported by name into _subcommands_doctor.py's globals)."""
    from trw_mcp.server._subcommands_doctor import CheckResult

    return CheckResult("launcher_divergence", *launcher_divergence_row(target))


__all__ = ["check_launcher_divergence", "launcher_divergence_row", "present_managed_configs"]

"""``trw-mcp assess``: configure the trw_assess (jev) backend once per computer, and see where it comes from.

* ``assess configure`` writes the OpenRouter key into the owner-only machine store
  (``~/.trw/jev.env``, 0600, :mod:`trw_memory.decisions._machine_store`) and sets
  ``assess_enabled: true`` in ``~/.trw/config.yaml``, so every TRW project on the machine can use
  ``trw_assess`` without a per-project ``.env``. The key is read from stdin or a hidden prompt, never
  from argv (shell history, ``ps``); ``--from-env-file`` promotes it from an existing ``.env``.
* ``assess status`` names the layer that enables the backend and the layer that supplies the key,
  endpoint and model. It never prints the key: presence and its last four characters at most.
* ``assess install-check`` is the installer's step. With a machine key it reports, in one line,
  that this project inherits it (switching the machine on when no layer has decided yet); with a key
  only in the project ``.env`` it offers once, on the terminal, to promote it (``--offer``), and
  otherwise reports the command. It never writes the key into a project file.

The precedence lives in one place each: enablement in ``trw_memory.decisions._enablement``, the key,
endpoint and model in ``trw_memory.decisions._machine_store.resolve_jev_settings``. This module only
writes the two machine files and reports what those resolvers decide.
"""

from __future__ import annotations

import argparse
import getpass
import os
import stat
import sys
from pathlib import Path
from types import ModuleType

__all__ = ["add_assess_subcommands", "enable_machine_switch", "install_check", "mask_key", "run_assess"]

_KEY = "OPENROUTER_API_KEY"
_USER_CONFIG_LABEL = "~/.trw/config.yaml"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
#: Same text as ``trw_memory.decisions._machine_store.MACHINE_STORE_LABEL`` (pinned by a test); kept literal
#: so registering the CLI tree does not import the decisions package.
MACHINE_STORE_LABEL = "~/.trw/jev.env"


def add_assess_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``assess configure|status|install-check``."""
    assess = subparsers.add_parser("assess", help="Configure trw_assess (jev) once per computer, and show its sources")
    verbs = assess.add_subparsers(dest="assess_command")
    configure = verbs.add_parser(
        "configure",
        help=f"Store the OpenRouter key in {MACHINE_STORE_LABEL} (0600) and enable trw_assess for every project",
    )
    configure.add_argument(
        "--from-env-file", type=Path, help="promote OPENROUTER_API_KEY (and TRW_JEV_*) from this .env"
    )
    configure.add_argument("--base-url", help="TRW_JEV_BASE_URL to store (must be https on openrouter.ai)")
    configure.add_argument("--model", help="TRW_JEV_MODEL to store")
    configure.add_argument(
        "--no-enable", action="store_true", help=f"store the key without touching {_USER_CONFIG_LABEL}"
    )
    status = verbs.add_parser("status", help="Show which layer enables trw_assess and supplies its key (never the key)")
    status.add_argument("target", nargs="?", default=".", type=Path)
    check = verbs.add_parser("install-check", help="Installer step: inherit the machine key, or offer to promote one")
    check.add_argument("target", nargs="?", default=".", type=Path)
    check.add_argument("--offer", action="store_true", help="ask on the terminal (never in headless or --json runs)")


def _store() -> ModuleType:
    """``trw_memory.decisions._machine_store``, imported only when a verb runs."""
    from trw_memory.decisions import _machine_store

    return _machine_store


def _dotenv_values(path: Path) -> dict[str, str]:
    from trw_memory.decisions._dotenv import parse_dotenv_subset

    return parse_dotenv_subset(path, allowed_keys=_store().MACHINE_STORE_KEYS)


def mask_key(key: str | None) -> str:
    """``absent``, or ``present`` plus the last four characters when the key is long enough to spare them."""
    if not key:
        return "absent"
    return f"present (...{key[-4:]})" if len(key) >= 16 else "present"


def enable_machine_switch(home: Path | None = None) -> str:
    """Set ``assess_enabled: true`` in ``~/.trw/config.yaml``, keeping every other key and comment.

    Returns ``""`` on success, else why it refused (a file that is not a YAML mapping is never
    overwritten). The file keeps its own permission bits.
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError
    from trw_memory.safe_fs import write_beneath

    root = Path.home() if home is None else home
    path = root / ".trw" / "config.yaml"
    yaml = YAML()  # round-trip: comments and key order survive
    mode = 0o644
    data: object = {}
    if path.is_file():
        mode = stat.S_IMODE(path.stat().st_mode)
        try:
            data = yaml.load(path.read_text(encoding="utf-8")) or {}
        except (YAMLError, ValueError, OSError) as exc:
            return f"{_USER_CONFIG_LABEL} could not be read ({type(exc).__name__}); add `assess_enabled: true` by hand"
    if not isinstance(data, dict):
        return f"{_USER_CONFIG_LABEL} is not a YAML mapping; add `assess_enabled: true` by hand"
    data["assess_enabled"] = True
    from io import StringIO

    buf = StringIO()
    yaml.dump(data, buf)
    write_beneath(root, ".trw/config.yaml", buf.getvalue().encode("utf-8"), mode=mode, exact_mode=True)
    return ""


def _read_key_interactively() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("OpenRouter API key (input hidden): ").strip()
    return sys.stdin.readline().strip()


def _configure(args: argparse.Namespace) -> int:
    values: dict[str, str] = {}
    if args.from_env_file is not None:
        values = _dotenv_values(args.from_env_file)
        if not values.get(_KEY):
            print(f"assess configure: no {_KEY} in {args.from_env_file}; nothing written", file=sys.stderr)
            return 1
    else:
        values[_KEY] = _read_key_interactively()
        if not values[_KEY]:
            print("assess configure: no key on stdin or at the prompt; nothing written", file=sys.stderr)
            return 1
    values.update({k: v for k, v in (("TRW_JEV_BASE_URL", args.base_url), ("TRW_JEV_MODEL", args.model)) if v})
    _store().write_machine_store(values)
    print(f"assess configure: {MACHINE_STORE_LABEL} written (0600); key {mask_key(values[_KEY])}")
    if args.no_enable:
        return 0
    refused = enable_machine_switch()
    if refused:
        print(f"assess configure: {refused}", file=sys.stderr)
        return 1
    print(
        f"assess configure: assess_enabled: true in {_USER_CONFIG_LABEL}; every TRW project on this machine inherits it"
    )
    return 0


def _status(target: Path) -> int:
    from trw_mcp.tools._assess_enablement import backend_enablement

    target = target.resolve()
    enabled, source = backend_enablement(target)
    settings = _store().resolve_jev_settings(dict(os.environ), target / ".env")
    store = _store().read_machine_store()
    rows = {
        "backend": f"{'on' if enabled else 'off'} ({source or 'no layer set it; default off'})",
        "key": f"{mask_key(settings.api_key)}" + (f" from {settings.key_source}" if settings.key_source else ""),
        "base_url": f"{settings.base_url_source or 'package default'}",
        "model": f"{settings.model_source or 'package default'}",
        "machine_store": (
            f"{MACHINE_STORE_LABEL} refused: it {store.problem}"
            if store.problem
            else f"{MACHINE_STORE_LABEL} {'present (0600)' if store.present else 'absent'}"
        ),
    }
    for name, value in rows.items():
        print(f"{name}: {value}")
    return 0


def _non_interactive() -> bool:
    return any(os.environ.get(flag, "").strip().lower() in _TRUTHY for flag in ("TRW_HEADLESS", "TRW_JSON"))


def _ask_on_tty(question: str) -> bool:
    """Ask once on the controlling terminal; ``False`` when there is none (curl|bash keeps /dev/tty)."""
    if _non_interactive():
        return False
    try:
        with open("/dev/tty", "r+", encoding="utf-8") as tty:
            tty.write(f"{question} [y/N] ")
            tty.flush()
            return tty.readline().strip().lower() in {"y", "yes"}
    except (
        OSError
    ):  # trw-fail-silent-allow: no controlling terminal means "do not prompt"; the caller reports the command instead
        return False


def install_check(target: Path, *, offer: bool) -> str:
    """The installer's jev line for ``target`` (``""`` when nothing is configured anywhere)."""
    from trw_mcp.tools._assess_enablement import backend_enablement

    target = target.resolve()
    machine_key = _store().read_machine_store().values.get(_KEY)
    project_values = _dotenv_values(target / ".env")
    enabled, source = backend_enablement(target)
    if machine_key:
        refused = ""
        if not enabled and not source:  # no layer decided: a machine key is the operator's opt-in
            refused = enable_machine_switch()
            enabled, source = backend_enablement(target)
        if enabled:
            return f"trw_assess: on for this project (enabled by {source}, key from {MACHINE_STORE_LABEL}); no project file written"
        reason = f"switched off by {source}" if source else f"not enabled ({refused})"
        return f"trw_assess: machine key found in {MACHINE_STORE_LABEL}, but the backend is {reason}"
    if not project_values.get(_KEY):
        return ""
    command = f"trw-mcp assess configure --from-env-file {target / '.env'}"
    if offer and _ask_on_tty(
        f"Use this project's OpenRouter key for every TRW project on this computer ({MACHINE_STORE_LABEL})?"
    ):
        _store().write_machine_store(project_values)
        refused = enable_machine_switch()
        return f"trw_assess: key promoted to {MACHINE_STORE_LABEL} (0600)" + (
            f"; {refused}" if refused else f" and enabled in {_USER_CONFIG_LABEL}"
        )
    return (
        f"trw_assess: the OpenRouter key is only in this project's .env; to share it with every project run: {command}"
    )


def run_assess(args: argparse.Namespace) -> None:
    """Dispatch ``assess configure|status|install-check``."""
    command = getattr(args, "assess_command", None)
    if command == "configure":
        sys.exit(_configure(args))
    if command == "status":
        sys.exit(_status(args.target))
    if command == "install-check":
        line = install_check(args.target, offer=args.offer)
        if line:
            print(line)
        sys.exit(0)
    print("usage: trw-mcp assess {configure,status,install-check}", file=sys.stderr)
    sys.exit(2)

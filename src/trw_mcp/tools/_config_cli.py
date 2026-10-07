"""``trw-mcp config``: the one supported writer for ``config.yaml`` keys (PRD-INFRA-210).

``config set KEY VALUE [--scope project|machine] [--target-dir DIR]`` changes one key in one layer.
The key is a public ``TRWConfig`` field, or ``FIELD.SUBKEY`` for a dict-typed field (one entry,
siblings kept). Credentials are refused: they live in the credentials file, not a ``config.yaml``
(PRD-SEC-005). The value is one YAML scalar or flow node; it is validated against the field before
anything is written. The write is a ruamel round-trip published through ``write_beneath``, so
comments, key order, indentation and the file mode survive, and a symlink is refused rather than
followed. Nothing here starts a process or contacts a network.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from trw_mcp.tools._config_dispatch_step import run_dispatch_step, run_dispatch_verb
from trw_mcp.tools._config_writer import ConfigSetRefusedError, SetResult, _field_for, set_config_value

__all__ = [
    "PICKUP_LINE",
    "ConfigSetRefusedError",
    "add_config_subcommands",
    "run_config",
    "run_dispatch_step",
    "set_config_value",
]

PICKUP_LINE = "Connected clients apply this on their next TRW tool call; reconnect (/mcp) only if trw_dispatch still does not appear"
_SCOPES = ("project", "machine")


def add_config_subcommands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``config set`` and ``config dispatch``."""
    config = subparsers.add_parser("config", help="Write a config.yaml key with validation (config set)")
    verbs = config.add_subparsers(dest="config_command")
    setter = verbs.add_parser("set", help="Set one public config key in the project or machine config.yaml")
    setter.add_argument("key", help="a config field, or FIELD.SUBKEY for one entry of a dict field")
    setter.add_argument("value", help="one YAML scalar or flow value, e.g. true, medium, '{codex: medium}'")
    setter.add_argument("--scope", choices=_SCOPES, default="project", help="project (default) or machine")
    setter.add_argument("--target-dir", type=Path, default=Path("."), help="project whose .trw/ is written")
    step = verbs.add_parser("dispatch", help="Installer step: offer trw_dispatch and pin per-client model and effort")
    step.add_argument("target", nargs="?", default=".", type=Path)
    step.add_argument("--offer", action="store_true", help="ask on the terminal (never in headless or --json runs)")
    switch = step.add_mutually_exclusive_group()
    switch.add_argument("--enable", action="store_true", help="write dispatch_tools_exposed: true (machine scope)")
    switch.add_argument("--disable", action="store_true", help="write dispatch_tools_exposed: false (machine scope)")
    step.add_argument(
        "--dispatch-model", action="append", default=[], metavar="CLIENT=MODEL", help="pin a model (repeatable)"
    )
    step.add_argument(
        "--dispatch-effort", action="append", default=[], metavar="CLIENT=LEVEL", help="pin an effort (repeatable)"
    )
    step.add_argument(
        "--json", action="store_true", help="print the structured setup result instead of the Dispatch line"
    )


def _effective(field: str, sub: str | None, target_dir: Path) -> object:
    from trw_mcp.models.config._loader import _build_config_unguarded

    value = getattr(_build_config_unguarded(target_dir / ".trw" / "config.yaml"), field, None)
    return value.get(sub) if sub is not None and isinstance(value, dict) else value


def _report(key: str, scope: str, target_dir: Path, result: SetResult) -> None:
    field, sub = _field_for(key)
    effective = _effective(field, sub, target_dir)
    print(f"{key}: {'written' if result.changed else 'unchanged'} in {result.path}")
    print(f"effective: {effective}")
    env_name = f"TRW_{field.upper()}"
    if env_name in os.environ:
        print(f"warning: {env_name} is set in the environment and wins over config.yaml")
    if scope == "machine":
        from trw_mcp.models.config._loader import _read_yaml_overrides

        try:
            project = _read_yaml_overrides(target_dir / ".trw" / "config.yaml")
        except Exception:  # trw-fail-silent-allow: an unreadable project layer only drops the shadow hint
            project = {}
        layer = project.get(field)
        if layer is not None and (sub is None or (isinstance(layer, dict) and sub in layer)):
            print(f"warning: the project .trw/config.yaml also sets {key} and wins over the machine layer here")
    print(PICKUP_LINE)


def _set(args: argparse.Namespace) -> int:
    try:
        result = set_config_value(args.key, args.value, scope=args.scope, target_dir=args.target_dir)
        _report(args.key, args.scope, args.target_dir, result)
    except ConfigSetRefusedError as exc:
        print(f"config set: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"config set: write failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    return 0


def run_config(args: argparse.Namespace) -> None:
    """Dispatch ``config set|dispatch``."""
    command = getattr(args, "config_command", None)
    if command == "set":
        sys.exit(_set(args))
    if command == "dispatch":
        sys.exit(run_dispatch_verb(args))
    print("usage: trw-mcp config {set,dispatch}", file=sys.stderr)
    sys.exit(2)

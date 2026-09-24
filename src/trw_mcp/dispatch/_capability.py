"""Pre-flight check that the installed client CLI advertises every flag dispatch passes it.

Belongs to the ``_runner.dispatch`` flow; one helper for every registered client,
so a new adapter gets the check by declaring its argv, not by writing code.

Two measured failures motivate it (2026-09-23): an installed ``cursor-agent`` that
predates ``--sandbox`` exits 1 with "unknown option", and the VS Code ``copilot``
shim, with no Copilot CLI behind it, answers every argv with an install prompt
and exit 0 -- which normalized into ``ok=True``. Both binaries say so in their own
``--help``, which costs one spawn per binary per process.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from trw_mcp.dispatch._client_spec_types import ClientSpec
from trw_mcp.dispatch._normalize import _strip_ansi

__all__ = ["unadvertised_flags"]

_HELP_TIMEOUT_S = 15
_help_cache: dict[tuple[str, int, tuple[str, ...]], str] = {}


def _help_text(binary: str, subcommands: tuple[str, ...], env: Mapping[str, str], cwd: Path | None) -> str:
    """``binary subcommands --help`` as plain text, cached per resolved file and mtime; "" if it cannot run."""
    try:
        stat = os.stat(binary)
    except OSError:  # trw-fail-silent-allow: no verdict; Popen reports the unresolvable binary
        return ""
    key = (os.path.realpath(binary), stat.st_mtime_ns, subcommands)
    if key not in _help_cache:
        try:
            proc = subprocess.run(  # noqa: S603 - argv list, no shell; binary is the one dispatch runs
                [binary, *subcommands, "--help"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=_HELP_TIMEOUT_S,
                env=dict(env),
                cwd=cwd,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):  # trw-fail-silent-allow: no verdict, not cached
            return ""  # a transient failure must not stick; Popen stays the authority
        # Styled help (``\x1b[1m--sandbox\x1b[0m``) would put an ``m`` before every flag.
        _help_cache[key] = _strip_ansi(f"{proc.stdout}\n{proc.stderr}")
    return _help_cache[key]


def unadvertised_flags(
    spec: ClientSpec,
    argv: Sequence[str],
    *,
    read_only: bool,
    extra_args: Sequence[str],
    env: Mapping[str, str],
    cwd: Path | None,
) -> tuple[str, str] | None:
    """``(silence_reason, message)`` when the installed binary's help omits a flag in *argv*.

    Only flags TRW's own registry put on the command line are judged; caller
    ``extra_args`` are theirs to vouch for, and an argv whose executable is not the
    registered binary (or an alias) is not judged at all. No verdict (``None``) when the binary
    does not resolve or its help cannot run -- ``Popen`` stays the authority for
    a launch failure. A missing read-only flag is ``sandbox_unsupported``: running
    the client without its sandbox is never the fallback.
    """
    if os.path.basename(argv[0]) not in (spec.binary, *spec.binary_aliases):
        return None  # a substituted executable is outside the registry's flag contract
    flags = {tok.split("=", 1)[0] for tok in argv[1:] if tok.startswith("-") and tok not in extra_args}
    binary = shutil.which(argv[0], path=env.get("PATH")) if flags else None
    if binary is None:
        return None
    subcommands = tuple(tok for tok in spec.base_argv[1:] if not tok.startswith("-"))
    help_text = _help_text(binary, subcommands, env, cwd)
    if not help_text.strip():
        return None
    missing = sorted(f for f in flags if not re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", help_text))
    if not missing:
        return None
    probe = " ".join([argv[0], *subcommands, "--help"])
    sandbox = [f for f in missing if read_only and f in {*spec.read_only_argv, *spec.confined_read_only_argv}]
    other = [f for f in missing if f not in sandbox]
    unsupported = (
        f"{binary} does not support {', '.join(other)} (not in `{probe}`); install or upgrade the {spec.binary} CLI"
    )
    if not sandbox:
        return "client_unsupported", unsupported
    refusal = (
        f"{binary} does not support {', '.join(sandbox)} (not in `{probe}`); refusing a read-only "
        f"dispatch without its sandbox -- upgrade the {spec.binary} CLI"
    )
    return "sandbox_unsupported", f"{refusal}\nalso client_unsupported: {unsupported}" if other else refusal

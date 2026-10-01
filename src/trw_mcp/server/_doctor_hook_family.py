"""Doctor row: the deployed Claude Code hook family can actually run (FB-INSTALL-01).

Belongs to the ``_subcommands_doctor.py`` facade (kept out of that file for the eLOC gate).

An update once kept an edited ``lib-trw.sh`` (588 lines against 2,057 bundled) while replacing the
hooks that source it: about 20 functions were undefined, every hook still exited 0, and this doctor
said PASS. The row FAILs when a TRW-managed hook (a name the bundle ships) has a syntax error
(``sh -n``: parse only), sources a ``lib-*.sh`` that is missing, or calls a function its deployed lib no
longer defines. Deployed hooks are parsed and compared, never sourced or run: they are
checkout-controlled (PRD-FIX-156 boundary).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._subcommands_doctor import CheckResult

__all__ = ["check_hook_family", "defined_functions", "hook_family_row"]

_BUNDLED = Path(__file__).resolve().parent.parent / "data" / "hooks"
_FUNC_DEF_RE = re.compile(r"^[ \t]*(?:function[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(\)", re.MULTILINE)
# Codex KI4: `. "$(dirname "$0")/lib-trw.sh"` nests quotes, so the source path runs to the lib name on its line.
_SOURCES_RE = re.compile(r"""(?:^|[\s;&|])(?:\.|source)[ \t]+[^\n;&|#]*?(lib-[A-Za-z0-9_-]+\.sh)""", re.MULTILINE)
# Codex KI3: a name in a comment or a single-quoted string is never called (sh expands nothing in '...').
_NOT_CODE_RE = re.compile(r"'[^'\n]*'|(?:^|(?<=\s))#[^\n]*", re.MULTILINE)
_SHOWN = 5


def defined_functions(text: str) -> set[str]:
    """Names of the shell functions *text* defines."""
    return set(_FUNC_DEF_RE.findall(text))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:  # trw-fail-silent-allow: an unreadable hook reads as empty, so the row reports it, not passes
        return ""


def _syntax_error(sh: str, path: Path) -> bool:
    try:
        # `sh -n` parses and never executes, so the checkout-controlled hook runs no code here.
        proc = subprocess.run([sh, "-n", str(path)], capture_output=True, timeout=10, check=False)  # noqa: S603
        return proc.returncode != 0
    except (OSError, subprocess.SubprocessError):
        return True


def _undefined(
    hook_text: str, libs: list[str], deployed: dict[str, set[str]], bundled: dict[str, set[str]]
) -> set[str]:
    """Functions *hook_text* calls that a bundled lib it sources defines but the deployed copy does not."""
    own = defined_functions(hook_text)
    code = _NOT_CODE_RE.sub(" ", hook_text)
    missing: set[str] = set()
    for lib in libs:
        lost = bundled.get(lib, set()) - deployed.get(lib, set()) - own
        missing |= {name for name in lost if re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", code)}
    return missing


def hook_family_row(target: Path) -> tuple[Literal["PASS", "FAIL", "SKIP"], str]:
    """FAIL when a managed hook cannot parse, sources a missing lib, or calls a function its lib lacks."""
    hooks = target / ".claude" / "hooks"
    sh = shutil.which("sh")
    managed = sorted(p.name for p in _BUNDLED.glob("*.sh")) if _BUNDLED.is_dir() else []
    deployed_names = [n for n in managed if (hooks / n).is_file()]
    if sh is None or not deployed_names:
        return "SKIP", "no TRW-managed hook in .claude/hooks (or no POSIX sh)."
    bundled_defs = {n: defined_functions(_read(_BUNDLED / n)) for n in managed if n.startswith("lib-")}
    deployed_defs = {n: defined_functions(_read(hooks / n)) for n in bundled_defs if (hooks / n).is_file()}
    problems: list[str] = []
    for name in deployed_names:
        text = _read(hooks / name)
        if _syntax_error(sh, hooks / name):
            problems.append(f"{name}: syntax error (sh -n)")
            continue
        libs = sorted({lib for lib in _SOURCES_RE.findall(text) if lib != name})
        absent = [lib for lib in libs if not (hooks / lib).is_file()]
        if absent:
            problems.append(f"{name}: sources missing {', '.join(absent)}")
            continue
        lost = _undefined(text, libs, deployed_defs, bundled_defs)
        if lost:
            shown = ", ".join(sorted(lost)[:_SHOWN]) + (f" (+{len(lost) - _SHOWN} more)" if len(lost) > _SHOWN else "")
            problems.append(f"{name}: calls {len(lost)} function(s) undefined in its {', '.join(libs)}: {shown}")
    if not problems:
        return "PASS", f"{len(deployed_names)} managed hook(s) parse and find every function they call."
    return "FAIL", (
        "; ".join(problems)
        + ". These hooks fail silently (they exit 0). Run `trw-mcp update-project` to refresh the hook family;"
        + " an edited lib is backed up to .trw/trash first."
    )


def check_hook_family(target: Path, _config: TRWConfig) -> CheckResult:
    """The ``hook_family`` doctor row (registered in ``_doctor_checks_registry``; kept here for the eLOC gate)."""
    from trw_mcp.server._subcommands_doctor import CheckResult

    return CheckResult("hook_family", *hook_family_row(target))

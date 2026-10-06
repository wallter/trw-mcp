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

__all__ = ["calls_missing", "check_hook_family", "defined_functions", "hook_family_row", "sourced_libs", "verify_calls"]

_BUNDLED = Path(__file__).resolve().parent.parent / "data" / "hooks"
# A command position: a line start, after ; & | ( { or $( or a backtick, or after a keyword that starts a
# command list. Codex KI2-r1: a name elsewhere (an echo argument, a string) is neither a call nor a definition.
_CMD_POS = r"(?:^|[;&|({`)]|\$\(|\b(?:then|do|else|elif|if|while|until)\b|!)[ \t]*"
_FUNC_DEF_RE = re.compile(_CMD_POS + r"(?:function[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(\)", re.MULTILINE)
# Codex KI4: `. "$(dirname "$0")/lib-trw.sh"` nests quotes, so the source path runs to the lib name on its line.
_SOURCES_RE = re.compile(r"""(?:^|[\s;&|])(?:\.|source)[ \t]+[^\n;&|#]*?(lib-[A-Za-z0-9_-]+\.sh)""", re.MULTILINE)
_SHOWN = 5


def defined_functions(text: str) -> set[str]:
    """Names of the shell functions *text* defines (outside comments and quoted text)."""
    return set(_FUNC_DEF_RE.findall(_code(text)))


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


def sourced_libs(text: str) -> set[str]:
    """The ``lib-*.sh`` names *text* sources."""
    return set(_SOURCES_RE.findall(text))


def calls_missing(text: str, libs: list[str], *, have: dict[str, set[str]], had: dict[str, set[str]]) -> set[str]:
    """Functions *text* calls that *had* (per lib) defines but *have* does not (best effort; see verify_calls)."""
    return verify_calls(text, libs, have=have, had=had)[0]


class _Uncertain(Exception):
    """The scanner met shell it does not model; the reason is shown to the user."""


_MAX_DEPTH = 32
_HEREDOC_RE = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
_CASE_RE = re.compile(r"(?<![A-Za-z0-9_])case(?![A-Za-z0-9_])")


def _scan(text: str) -> str:
    """*text* with comments and quoted text blanked to spaces (same length, so positions line up).

    Iterative, with a nesting cap: it can never recurse or crash (codex KI2-r3 KI6). Modelled: quotes,
    ``$'..'``, ``\\``, comments at a word start, ``$(..)``/backticks (code, also inside double quotes),
    ``${..}`` (data), heredoc bodies (data). Anything else it cannot place raises :class:`_Uncertain`.
    """
    out, n, i = list(text), len(text), 0
    stack: list[list[object]] = [["top"]]  # ["top"] | ["dq"] | ["sub", paren_depth] | ["bt"]
    bodies: list[tuple[int, str, bool]] = []  # pending heredocs: (unused, delimiter, strip_tabs)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    def push(frame: list[object]) -> None:
        if len(stack) > _MAX_DEPTH:
            raise _Uncertain(f"quotes or substitutions nested deeper than {_MAX_DEPTH}")
        stack.append(frame)

    while i < n:
        kind, c = stack[-1][0], text[i]
        if kind == "dq":
            if c == "\\":
                blank(i, i + 2)
                i += 2
            elif text.startswith("$(", i):
                push(["sub", 1])
                i += 2
            elif c == "`":
                push(["bt"])
                i += 1
            elif c == '"':
                blank(i, i + 1)
                stack.pop()
                i += 1
            else:
                blank(i, i + 1)
                i += 1
            continue
        if kind == "bt" and c == "`":
            stack.pop()
            i += 1
            continue
        # code: top level, inside $(..), or inside backticks
        if c == "\\":
            if text.startswith("\\\n", i):
                i += 2
                continue
            blank(i, i + 2)
            i += 2
        elif text.startswith("$'", i):
            j = i + 2
            while j < n and text[j] != "'":
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                raise _Uncertain("an unterminated $'..' string")
            blank(i, j + 1)
            i = j + 1
        elif c == "'":
            j = text.find("'", i + 1)
            if j < 0:
                raise _Uncertain("an unterminated '..' string")
            blank(i, j + 1)
            i = j + 1
        elif c == '"':
            blank(i, i + 1)
            push(["dq"])
            i += 1
        elif text.startswith("${", i):
            j = text.find("}", i + 2)
            if j < 0:
                raise _Uncertain("an unterminated ${..}")
            if "$(" in text[i:j] or "`" in text[i:j]:
                raise _Uncertain("a command substitution inside ${..}")
            blank(i, j + 1)  # parameter expansion is data, `#` and quotes inside it included
            i = j + 1
        elif text.startswith("$(", i):
            push(["sub", 1])
            i += 2
        elif c == "`":
            push(["bt"])
            i += 1
        elif c == "#" and (i == 0 or text[i - 1] in " \t\n;&|()"):
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif text.startswith("<<", i) and not text.startswith("<<<", i):
            m = _HEREDOC_RE.match(text, i)
            if m is None:
                raise _Uncertain("a heredoc whose delimiter it cannot read")
            bodies.append((0, m.group(3), m.group(1) == "-"))
            i = m.end()
        elif c == "\n" and bodies:
            k = i + 1
            for _unused, delim, strip_tabs in bodies:
                while True:
                    if k >= n:
                        raise _Uncertain(f"a heredoc never closed by {delim}")
                    end = text.find("\n", k)
                    end = n if end < 0 else end
                    line = text[k:end]
                    blank(k, end)
                    k = end + 1
                    if (line.lstrip("\t") if strip_tabs else line) == delim:
                        break
            bodies.clear()
            i = k
        else:
            if kind == "sub":
                if c == "(":
                    stack[-1][1] = int(str(stack[-1][1])) + 1
                elif c == ")":
                    stack[-1][1] = int(str(stack[-1][1])) - 1
                    if stack[-1][1] == 0:
                        stack.pop()
                elif _CASE_RE.match(text, i) and (i == 0 or not text[i - 1].isalnum()):
                    raise _Uncertain("a case statement inside a command substitution")
            i += 1
    if len(stack) > 1 or bodies:
        raise _Uncertain("an unterminated quote, substitution or heredoc")
    return "".join(out)


def _code(text: str) -> str:
    """Best-effort code view for definitions: on uncertainty, the raw text (more definitions, never fewer calls)."""
    try:
        return _scan(text)
    except _Uncertain:
        return text


# A call may follow NAME=value assignments and a prefix word (codex KI2-r2: `time f`, `X=1 f`).
_PREFIX = r"(?:[A-Za-z_][A-Za-z0-9_]*=[^ \t\n;&|]*[ \t]+)*(?:(?:time|command|exec|env|nohup|builtin|!)[ \t]+)*"


def verify_calls(
    text: str, libs: list[str], *, have: dict[str, set[str]], had: dict[str, set[str]]
) -> tuple[set[str], str | None]:
    """Functions *text* calls that a lib it sources used to define (*had*) but none sourced before the call now does.

    Returns ``(missing, reason)``. A non-None *reason* means the hook could not be fully modelled: *missing* is
    then only a guess, and callers must KEEP the hook and warn rather than retire it (lead ruling on KI2-r3).
    Definitions are the union of the libs sourced before the first call, plus the hook's own (KI2-r1/r2).
    """
    try:
        code = _scan(text)
    except _Uncertain as exc:
        return set(), str(exc)
    except Exception as exc:  # trw-fail-silent-allow: any parser failure becomes a shown "could not verify" reason
        return set(), f"could not be parsed ({type(exc).__name__})"
    own = set(_FUNC_DEF_RE.findall(code))
    order = [
        (m.group(1), m.start(1))
        for m in _SOURCES_RE.finditer(text)
        if m.group(1) in libs and code[m.start() : m.start(1)].lstrip(" \t\n;&|")[:1] in (".", "s")
    ]
    want = set().union(*(had.get(lib, set()) for lib in libs)) if libs else set()
    anywhere = set().union(*(have.get(lib, set()) for lib, _at in order)) if order else set()
    missing: set[str] = set()
    for name in want - own:
        call = re.search(_CMD_POS + _PREFIX + re.escape(name) + r"(?![A-Za-z0-9_])(?![ \t]*\(\))", code, re.MULTILINE)
        if call is None:
            continue
        ready = set().union(*(have.get(lib, set()) for lib, at in order if at < call.start())) if order else set()
        if name in ready:
            continue
        if name in anywhere and own:
            # Defined by a lib sourced later, and the hook has functions: the call may sit in a body that runs
            # after that source line (codex KI2-r3 KI4). Order cannot be decided statically here.
            return set(), "a call inside a function body may run after a later source line"
        missing.add(name)
    return missing, None


def hook_family_row(target: Path) -> tuple[Literal["PASS", "WARN", "FAIL", "SKIP"], str]:
    """FAIL when settings.json registers a missing TRW-managed hook, or one cannot parse, sources a missing lib, or
    calls a function its lib lacks.

    WARN when a hook's calls could not be verified (shell the checker does not model): never a guess as FAIL.
    """
    from trw_mcp.bootstrap._hook_closure import settings_hook_refs

    hooks = target / ".claude" / "hooks"
    sh = shutil.which("sh")
    managed = sorted(p.name for p in _BUNDLED.glob("*.sh")) if _BUNDLED.is_dir() else []
    deployed_names = [n for n in managed if (hooks / n).is_file()]
    # A hook settings.json registers but the folder lacks fails on every event; an empty folder must not read SKIP.
    absent_refs = {n for n in settings_hook_refs(target) if not (hooks / n).is_file()}
    unregistered = sorted(set(managed) & absent_refs)
    own_missing = sorted(absent_refs - set(managed))
    own_note = (
        f"settings.json registers {len(own_missing)} project-owned hook(s) missing from .claude/hooks: "
        + ", ".join(own_missing)
        + " (not TRW's; restore them or drop the registration)"
        if own_missing
        else ""
    )
    if unregistered:
        shown = ", ".join(unregistered[:_SHOWN]) + (
            f" (+{len(unregistered) - _SHOWN} more)" if len(unregistered) > _SHOWN else ""
        )
        return "FAIL", (
            f".claude/settings.json registers {len(unregistered)} TRW hook(s) missing from .claude/hooks: {shown}."
            " Claude Code cannot run them. Run `trw-mcp update-project` to restore them."
        )
    if own_note and (sh is None or not deployed_names):
        return "WARN", own_note + "."
    if sh is None or not deployed_names:
        return "SKIP", "no TRW-managed hook in .claude/hooks (or no POSIX sh)."
    bundled_defs = {n: defined_functions(_read(_BUNDLED / n)) for n in managed if n.startswith("lib-")}
    deployed_defs = {n: defined_functions(_read(hooks / n)) for n in bundled_defs if (hooks / n).is_file()}
    problems: list[str] = []
    unsure: list[str] = []
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
        lost, reason = verify_calls(text, libs, have=deployed_defs, had=bundled_defs)
        if reason:
            unsure.append(f"{name}: could not verify its calls ({reason})")
        elif lost:
            shown = ", ".join(sorted(lost)[:_SHOWN]) + (f" (+{len(lost) - _SHOWN} more)" if len(lost) > _SHOWN else "")
            problems.append(f"{name}: calls {len(lost)} function(s) undefined in its {', '.join(libs)}: {shown}")
    if not problems and (unsure or own_note):
        return "WARN", "; ".join(
            [*unsure, *([own_note] if own_note else [])]
        ) + ". Check those hooks by hand, or run `trw-mcp update-project`."
    if not problems:
        return "PASS", f"{len(deployed_names)} managed hook(s) parse and find every function they call."
    return "FAIL", (
        "; ".join(problems)
        + ". These hooks fail silently (they exit 0). Run `trw-mcp update-project` to refresh the hook family;"
        + " an edited lib with uncommitted changes is backed up to .trw/trash first; one committed in git is replaced in place."
    )


def check_hook_family(target: Path, _config: TRWConfig) -> CheckResult:
    """The ``hook_family`` doctor row (registered in ``_doctor_checks_registry``; kept here for the eLOC gate)."""
    from trw_mcp.server._subcommands_doctor import CheckResult

    return CheckResult("hook_family", *hook_family_row(target))

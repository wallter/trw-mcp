"""Every ``trw-distill ...`` / ``trw-mcp ...`` command an instruction source tells an agent to run is a real one.

E2E-DISTILL-RCA-TRACE-HINT (INC-053): the generated instructions said ``trw-distill rca trace <test>`` while the
verb takes a traceback file, so an agent following them got a 30-line stack trace. The check is on the SOURCE
text (the projection every carrier is generated from): the command path must exist, every flag must appear in
that command's help, and each ``<placeholder>`` must name the argument its position takes.
"""

from __future__ import annotations

import argparse
import itertools
import re
import shutil
import subprocess
import sys
from functools import cache
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
#: The instruction text agents are handed: the pre-edit / ceremony line and every explorer-agent template.
_SOURCES = sorted(
    [
        _SRC / "models" / "config" / "_pre_edit_channels.py",
        *(_SRC / "channels").glob("*/_explorer*.py"),
        *(_SRC / "data").glob("*/channels/manifest-*.yaml"),
    ]
)
#: A backticked command, or the command a ``regenerate`` / ``regenerate_cmd`` line tells the operator to run.
_SNIPPET = re.compile(
    r"`((?:trw-distill|trw-mcp)\s[^`]*)`|^\s*regenerate(?:_cmd)?:\s*((?:trw-distill|trw-mcp)\s.*)$", re.MULTILINE
)
_VERB = re.compile(r"^[a-z][a-z0-9-]*(?:\|[a-z][a-z0-9-]*)*$")


def _snippets() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in _SOURCES:
        for ticked, line in _SNIPPET.findall(path.read_text(encoding="utf-8")):
            found.append((path.name, " ".join((ticked or line).split())))
    return found


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if w}


@cache
def _mcp_parser() -> argparse.ArgumentParser:
    from trw_mcp.server._cli_argparse import _build_arg_parser

    return _build_arg_parser()


def _mcp_subparser(parser: argparse.ArgumentParser, verb: str) -> argparse.ArgumentParser | None:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction) and verb in action.choices:
            return action.choices[verb]
    return None


def _check_mcp(path: list[str], flags: list[str]) -> list[str]:
    parser: argparse.ArgumentParser | None = _mcp_parser()
    for verb in path:
        parser = _mcp_subparser(parser, verb) if parser is not None else None
        if parser is None:
            return [f"`trw-mcp {' '.join(path)}`: no such command {verb!r}"]
    return [
        f"`trw-mcp {' '.join(path)}` has no flag {flag}" for flag in flags if flag not in parser._option_string_actions
    ]


@cache
def _distill_help(path: tuple[str, ...]) -> tuple[int, str]:
    binary = shutil.which("trw-distill") or str(Path(sys.executable).with_name("trw-distill"))
    done = subprocess.run([binary, *path, "--help"], capture_output=True, text=True, timeout=60, check=False)
    return done.returncode, done.stdout


def _check_distill(path: list[str], flags: list[str], placeholders: list[str]) -> list[str]:
    code, help_text = _distill_help(tuple(path))
    label = f"`trw-distill {' '.join(path)}`"
    if code != 0:
        return [f"{label}: no such command"]
    problems = [f"{label} has no flag {flag}" for flag in flags if flag not in help_text]
    usage = re.search(r"^Usage: .*?\[OPTIONS\]\s*(.*)$", help_text, re.MULTILINE)
    args = re.findall(r"\[?([A-Z][A-Z_]+)\]?", usage.group(1)) if usage else []
    for index, placeholder in enumerate(placeholders):
        if index >= len(args):
            problems.append(f"{label} takes no argument {placeholder}")
        elif not _words(placeholder) & _words(args[index]):
            problems.append(f"{label} argument {index + 1} is {args[index]}, but the text says {placeholder}")
    return problems


def _problems(snippet: str) -> list[str]:
    binary, *tokens = snippet.split()
    verbs: list[list[str]] = []
    rest = list(tokens)
    while rest and _VERB.match(rest[0]):
        verbs.append(rest.pop(0).split("|"))
    flags = [t.split("=")[0] for t in rest if t.startswith("--")]
    placeholders = [t for t in rest if t.startswith("<") and t.endswith(">")]
    problems: list[str] = []
    for path in map(list, itertools.product(*verbs)):
        if binary == "trw-mcp":
            problems += _check_mcp(path, flags)
        else:
            problems += _check_distill(path, flags, placeholders)
    return problems


_CASES = _snippets()


def test_the_instruction_sources_contain_commands_to_check() -> None:
    assert any(s.startswith("trw-distill rca trace") for _, s in _CASES)
    assert any(s.startswith("trw-mcp ") for _, s in _CASES)


@pytest.mark.parametrize(("source", "snippet"), _CASES, ids=[f"{n}:{s}" for n, s in _CASES])
def test_an_instruction_command_matches_a_real_cli_signature(source: str, snippet: str) -> None:
    if snippet.startswith("trw-distill") and not (
        shutil.which("trw-distill") or Path(sys.executable).with_name("trw-distill").exists()
    ):
        pytest.skip("trw-distill is not installed here")

    assert _problems(snippet) == [], f"{source}: {snippet}"

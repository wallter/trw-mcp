"""PRD-CORE-305-NFR01: no shipped text tells an agent to call a tool that does not exist.

A remedy that names a retired tool is a dead end: the agent is blocked, told what to do, and cannot do
it (the REVIEW and DELIVER gates said "call trw_reflect" long after that tool was cut). The census reads
the text TRW ships (Python string literals that contain whitespace or a call, non-comment shell lines,
and every data file) and fails on

* any retired tool name (``surface_v2.RETIRED_TOOLS``), with or without parentheses; event names such as
  ``trw_reflect_complete`` are a different word and do not match; and
* any ``trw_<name>(`` call in that text that is not a registered tool or a helper the shipped hooks define.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

from tests import _source_index as source_index
from trw_mcp.models.surface_v2 import POST_CUT_SURFACE, RETIRED_TOOLS

_SRC = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
_DATA_SUFFIXES = {".md", ".yaml", ".yml", ".txt", ".mdc", ".toml", ".json"}
_RETIRED = re.compile(r"\b(" + "|".join(sorted(RETIRED_TOOLS, key=len, reverse=True)) + r")\b(?!_)")
_CALL = re.compile(r"\btrw_[a-z0-9_]+(?=\()")
_SHELL_DEF = re.compile(r"(?m)^\s*(trw_[a-z0-9_]+)\s*\(\)\s*\{")


def _is_guidance(text: str) -> bool:
    return "(" in text or any(c.isspace() for c in text)


def test_guidance_means_any_whitespace_or_a_call_not_a_bare_token() -> None:
    assert _is_guidance("\ntrw_reflect") and _is_guidance("trw_deliver()") and _is_guidance("run\ttrw_x")
    assert not _is_guidance("trw_reflect")


@functools.cache
def _prose() -> dict[str, str]:
    """``{shipped file: the prose it ships}``; shell prose also feeds the retired-name check."""
    texts: dict[str, str] = {}
    for path in _SRC.rglob("*"):
        if path.suffix == ".py":
            tree = source_index.tree(path)
            strings = (n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))
            # Prose or a call: anything with whitespace or "(". A bare registry token ("trw_reflect" in
            # an analytics tuple, an event name) is data, not guidance.
            text = "\n".join(s for s in strings if _is_guidance(s))
        elif path.suffix == ".sh":
            lines = path.read_text(encoding="utf-8").splitlines()
            text = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
        elif path.suffix in _DATA_SUFFIXES:
            text = path.read_text(encoding="utf-8")
        else:
            continue
        texts[str(path.relative_to(_SRC))] = text
    return texts


def test_no_shipped_text_names_a_retired_tool() -> None:
    hits = {f: sorted(set(_RETIRED.findall(t))) for f, t in _prose().items() if _RETIRED.search(t)}
    assert not hits, f"shipped text names retired tools (a dead-end remedy): {hits}"


def test_every_tool_call_in_shipped_text_names_a_registered_tool() -> None:
    """Shell helpers the hooks define (``trw_safe_write()``, ...) are real calls; nothing else is exempt."""
    helpers = {m for p in _SRC.rglob("*.sh") for m in _SHELL_DEF.findall(p.read_text(encoding="utf-8"))}
    known = POST_CUT_SURFACE | helpers
    unknown = {f: sorted(set(_CALL.findall(t)) - known) for f, t in _prose().items() if set(_CALL.findall(t)) - known}
    assert not unknown, f"shipped text calls tools that are not registered: {unknown}"


def test_the_census_sees_the_texts_it_guards() -> None:
    """Guards the guard: gate, hook and skill texts are in scope, so an empty census cannot pass."""
    prose = _prose()
    assert any("data/hooks/" in f for f in prose) and any("data/skills/" in f for f in prose)
    assert "trw_session_start()" in "\n".join(prose.values())

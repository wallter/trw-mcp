"""Lightweight JS-object-literal scanner for ``.claude/workflows/*.{js,mjs}``.

Built for ``test_workflow_agent_schema_contract.py`` (PRD-QUAL-073 follow-on,
2026-07-27 incident: commit 9700e9b709). That incident was a bundled TRW
``agentType`` paired with a JSON schema (``additionalProperties: false``) that
omitted a field the agent's own body mandates it record unconditionally —
``open_questions`` for ``trw-researcher``. The agent obeyed its own contract,
the schema forbade the field, and the mismatch burned five StructuredOutput
retries before the lens returned nothing.

This module resolves, from the workflow source TEXT alone (no execution, no
LLM calls, no dependency on the ``.claude/workflows`` runtime globals):

1. every ``const XXX = { ... }`` object literal that looks like a JSON
   schema (has a top-level ``properties`` key) — see ``extract_named_schemas``;
2. every ``agent(prompt, { ..., agentType: 'trw-x', ..., schema: XXX, ... })``
   call-options object — see ``find_agent_type_schema_pairings``.

It is deliberately NOT a general JS parser. It only needs to resolve flat
``key: value`` object literals (the shape every schema/call-options object in
these workflows is written in), so both entry points first blank out ``//``
and ``/* */`` comments (``_strip_comments``, keeping length and newlines so
positions and line numbers stay valid), then do brace/bracket/paren-depth
tracking with quoted-string spans skipped as opaque units. It does not
evaluate expressions, understand spread syntax, or handle multiple
assignments per line. Values are returned as raw text for the caller to
interpret (e.g. checking a value equals the literal ``false``, or recursing
into a nested ``{...}`` for its own top-level keys).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_CONST_OBJECT_RE = re.compile(r"\bconst\s+(\w+)\s*=\s*\{")
_AGENT_TYPE_RE = re.compile(r"agentType:\s*'([\w-]+)'")
_KEY_VALUE_RE = re.compile(r"^([\"'`]?)([A-Za-z_$][\w$]*)\1\s*:\s*(.*)$", re.DOTALL)


def _strip_comments(text: str) -> str:
    """Return same-length text with ``//`` / ``/* */`` comment spans blanked.

    Newlines inside a blanked span are preserved (so line numbers computed
    from the result still match the original), and quoted-string content is
    copied through untouched via its own string-aware sub-scan — so a ``//``
    or ``/*`` that appears inside a real string (a URL in a description, say)
    is never mistaken for a comment start. Downstream parsing only has to
    handle strings, not comments, once it runs on this output.
    """
    out = list(text)
    n = len(text)
    i = 0
    while i < n:
        c = text[i]
        if c in ("'", '"', "`"):
            quote = c
            j = i + 1
            while j < n:
                cj = text[j]
                if cj == "\\":
                    j += 2
                    continue
                j += 1
                if cj == quote:
                    break
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            end = text.find("\n", i)
            end = n if end == -1 else end
            for k in range(i, end):
                out[k] = " "
            i = end
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for k in range(i, end):
                if text[k] != "\n":
                    out[k] = " "
            i = end
            continue
        i += 1
    return "".join(out)


def _skip_string(text: str, i: int) -> int:
    """``text[i]`` is an opening quote char; return the index just past its close.

    Handles backslash escapes. An unterminated string (should not occur in
    well-formed, comment-stripped source) runs to end-of-text rather than
    raising, so a stray parse edge case degrades to "found nothing" rather
    than crashing the scan.
    """
    quote = text[i]
    i += 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == quote:
            return i + 1
        i += 1
    return n


def find_balanced_brace(text: str, open_idx: int) -> int:
    """``text[open_idx]`` must be ``'{'``. Return the index of its matching ``'}'``.

    ``text`` must already be comment-stripped (see ``_strip_comments``) —
    this function only skips quoted-string content, not comments.
    """
    if text[open_idx] != "{":
        raise ValueError(f"text[{open_idx}] is not '{{': {text[open_idx]!r}")
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if c in ("'", '"', "`"):
            i = _skip_string(text, i)
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError(f"unbalanced '{{' starting at offset {open_idx}")


def top_level_entries(obj_body: str) -> dict[str, str]:
    """Parse top-level ``key: value`` pairs of object-literal text (no outer braces).

    ``obj_body`` must already be comment-stripped. Splits on commas at depth 0
    (respecting ``{}``/``[]``/``()`` nesting and quoted strings), then matches
    each segment's leading identifier/quoted key. Returns
    ``{key: raw_value_text}`` — the value text is not further parsed.
    """
    entries: dict[str, str] = {}
    n = len(obj_body)
    i = 0
    depth = 0
    seg_start = 0

    def flush(seg: str) -> None:
        seg = seg.strip()
        if not seg:
            return
        m = _KEY_VALUE_RE.match(seg)
        if m:
            entries[m.group(2)] = m.group(3).strip()

    while i < n:
        c = obj_body[i]
        if c in ("'", '"', "`"):
            i = _skip_string(obj_body, i)
            continue
        if c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
        elif c == "," and depth == 0:
            flush(obj_body[seg_start:i])
            seg_start = i + 1
        i += 1
    flush(obj_body[seg_start:])
    return entries


@dataclass(frozen=True)
class SchemaInfo:
    name: str
    additional_properties_false: bool
    top_level_properties: frozenset[str]


def extract_named_schemas(source: str) -> dict[str, SchemaInfo]:
    """Find every ``const XXX = {...}`` in ``source`` that looks like a JSON schema.

    "Looks like a JSON schema" == has a top-level ``properties`` key whose
    value is itself an object literal. Anything else (plain config objects,
    ``MODELS``, etc.) is silently skipped. ``source`` is raw file text —
    comment-stripping happens internally.
    """
    code = _strip_comments(source)
    out: dict[str, SchemaInfo] = {}
    for m in _CONST_OBJECT_RE.finditer(code):
        name = m.group(1)
        open_idx = m.end() - 1
        try:
            close_idx = find_balanced_brace(code, open_idx)
        except ValueError:
            continue
        entries = top_level_entries(code[open_idx + 1 : close_idx])
        props_text = entries.get("properties", "")
        if not props_text.startswith("{"):
            continue
        try:
            props_close = find_balanced_brace(props_text, 0)
        except ValueError:
            continue
        props_entries = top_level_entries(props_text[1:props_close])
        out[name] = SchemaInfo(
            name=name,
            additional_properties_false=entries.get("additionalProperties", "").strip() == "false",
            top_level_properties=frozenset(props_entries.keys()),
        )
    return out


@dataclass(frozen=True)
class AgentSchemaPairing:
    file: Path
    line: int
    agent_type: str
    schema_var: str


def find_agent_type_schema_pairings(source: str, path: Path) -> list[AgentSchemaPairing]:
    """Find every call-options object literal carrying both ``agentType`` and ``schema``.

    ``source`` is raw file text — comment-stripping happens internally, so
    both the brace search and the reported line numbers are computed
    consistently (the stripped text is the same length and keeps every
    newline at its original offset).

    For each ``agentType: '...'`` occurrence, the enclosing ``{...}`` is
    located by scanning backward from the match for the nearest brace-balanced
    unmatched ``'{'``. The backward scan does not track quoted strings, but
    every observed source of extra braces between an object's open brace and
    its ``agentType`` key — ``${...}`` template interpolation in a ``label:``
    — is itself balanced, so raw brace counting resolves the correct
    enclosing object in practice; a stray unbalanced brace inside a string in
    that span is a known gap. Once the candidate open brace is found, the
    (string-aware) forward ``find_balanced_brace`` resolves its close.
    """
    code = _strip_comments(source)
    pairings: list[AgentSchemaPairing] = []
    for m in _AGENT_TYPE_RE.finditer(code):
        pos = m.start()
        depth = 0
        j = pos - 1
        open_idx: int | None = None
        while j >= 0:
            c = code[j]
            if c == "}":
                depth += 1
            elif c == "{":
                if depth == 0:
                    open_idx = j
                    break
                depth -= 1
            j -= 1
        if open_idx is None:
            continue
        try:
            close_idx = find_balanced_brace(code, open_idx)
        except ValueError:
            continue
        entries = top_level_entries(code[open_idx + 1 : close_idx])
        agent_type = entries.get("agentType", "").strip("'\"")
        schema_var = entries.get("schema", "").strip()
        if agent_type and schema_var and re.fullmatch(r"\w+", schema_var):
            line = code.count("\n", 0, pos) + 1
            pairings.append(AgentSchemaPairing(path, line, agent_type, schema_var))
    return pairings

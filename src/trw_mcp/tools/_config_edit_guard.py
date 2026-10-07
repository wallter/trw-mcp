"""What a ``config set|unset`` edit must preserve, checked on the text (PRD-INFRA-210).

Belongs to the ``_config_writer`` facade. Two concerns the YAML round-trip does not give for free:

* **Removing a key must not disturb anything else.** :func:`remove_key_lines` deletes a block-style key by line
  surgery on the original text: the span from the key's line to just before the next sibling, minus the trailing
  blank and comment lines that sit at or left of the key's column (they belong to the next key). Every other byte
  stays identical, which the YAML round-trip cannot promise for comments that follow a collection or a block
  scalar. Flow-style containers fall back to the round-trip removal.
* **An edit changes only its target.** YAML anchors, aliases and ``<<`` merge keys make several keys share one
  node, so a mutation can change a sibling or fail to remove an inherited key. :func:`check_only_target_changed`
  re-parses the pre-edit and the rendered text with the SAFE loader (plain values, no round-trip node types) and
  compares: the target must be as requested and nothing else may differ.
"""

from __future__ import annotations

import math
from typing import Any

__all__ = ["check_only_target_changed", "remove_key_lines"]

_REASON_SHARED = "the key is shared or inherited (a YAML alias or merge key), so the edit has no effect"


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _is_comment(line: str) -> bool:
    return line.lstrip(" ").startswith("#")


def _own_keys(mapping: Any) -> list[Any]:
    return [k for k, _ in mapping.non_merged_items()]


def _is_block(mapping: Any) -> bool:
    return not mapping.fa.flow_style()


def _key_line(mapping: Any, key: Any, lines: list[str]) -> int | None:
    """0-based line of a string *key* in a block mapping, or None when the position cannot be trusted.

    A non-string key (a number, a complex ``? [a, b]`` key) has no source span we can verify, so it is
    never located by line: the caller falls back to the round-trip.
    """
    if not isinstance(key, str):
        return None
    try:
        line, col = mapping.lc.key(key)
    except (
        KeyError,
        AttributeError,
        TypeError,
    ):  # trw-fail-silent-allow: no trustworthy line info means "fall back to the round-trip", which the guard still verifies
        return None
    if not _is_block(mapping) or line >= len(lines) or key not in lines[line][max(col - 1, 0) : col + len(key) + 3]:
        return None
    return int(line)


def _marker_limit(lines: list[str], start: int) -> int:
    """Index of the first YAML document marker (``---`` / ``...`` at column 0) after *start*, else the end."""
    for index in range(start + 1, len(lines)):
        if lines[index][:3] in {"---", "..."} and (len(lines[index]) == 3 or lines[index][3] in " \t\r\n"):
            return index
    return len(lines)


def remove_key_lines(text: str, layer: Any, field: str, sub: str | None, *, drop_field: bool = False) -> str | None:
    """*text* with ``field`` (or ``field.sub``) removed by line surgery, or None to fall back to the round-trip.

    *drop_field*: remove the whole ``field`` entry (``sub`` was its only key). Returns None for a flow-style
    container, a key inherited through a merge, or any position the line info cannot vouch for.
    """
    bom = "\ufeff" if text.startswith("\ufeff") else ""  # the stream BOM is not part of the first line's content
    lines = text[len(bom) :].splitlines(keepends=True)
    if not _is_block(layer) or field not in _own_keys(layer):
        return None
    top = _own_keys(layer)
    holder, key = layer, field
    if sub is not None and not drop_field:
        holder = layer[field]
        if not hasattr(holder, "lc") or sub not in _own_keys(holder):
            return None
        key = sub
    start = _key_line(holder, key, lines)
    if start is None:
        return None
    column = _indent(lines[start])
    siblings = _own_keys(holder)
    following = siblings[siblings.index(key) + 1 :]
    if following:
        end = _key_line(holder, following[0], lines)
    elif holder is layer:
        end = len(lines)
    else:  # the last key of a nested map ends where the next top-level key begins
        after = top[top.index(field) + 1 :]
        end = _key_line(layer, after[0], lines) if after else len(lines)
    if end is None or end <= start:
        return None
    end = min(end, _marker_limit(lines, start))  # a document marker (and what follows) is never part of the key
    while end - 1 > start and (
        not lines[end - 1].strip() or (_is_comment(lines[end - 1]) and _indent(lines[end - 1]) <= column)
    ):
        end -= 1
    return bom + "".join(lines[:start] + lines[end:])


def _norm(node: Any, _path: frozenset[int] = frozenset()) -> Any:
    """Plain, comparable form: dict/list recursed (cycle-aware), non-finite floats made equal to themselves."""
    if isinstance(node, (dict, list)):
        if id(node) in _path:
            return ("recursive-alias",)
        inner = _path | {id(node)}
        if isinstance(node, dict):
            return {k: _norm(v, inner) for k, v in node.items()}
        return [_norm(v, inner) for v in node]
    if isinstance(node, float) and not math.isfinite(node):
        return ("non-finite", "nan" if math.isnan(node) else ("+inf" if node > 0 else "-inf"))
    return node


def _wanted_plain(wanted: Any) -> Any:
    """The requested value as the reload will see it: dumped and safe-loaded, so a ``!!str 123`` tag compares as ``"123"``."""
    from io import StringIO

    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    buf = StringIO()
    try:
        YAML().dump({"v": wanted}, buf)
        return _safe_load(buf.getvalue())["v"]
    except (
        YAMLError,
        ValueError,
        TypeError,
        KeyError,
    ):  # trw-fail-silent-allow: an undumpable value compares as itself
        return _norm(wanted)


def _safe_load(text: str | None) -> Any:
    from ruamel.yaml import YAML

    loaded = YAML(typ="safe").load(text) if text and text.strip() else None
    return _norm(loaded) if loaded is not None else {}


def _members(table: Any, skip: str) -> dict[Any, Any]:
    return {k: v for k, v in table.items() if k != skip} if isinstance(table, dict) else {}


def check_only_target_changed(
    rendered: str, before_text: str | None, field: str, sub: str | None, wanted: Any, *, removing: bool
) -> str:
    """``""`` when *rendered* shows the target as requested and nothing else changed, else the reason.

    Both texts are loaded with the safe loader. For ``field`` (or ``field.sub``): absent when *removing*, else
    equal to *wanted*. Every other top-level key, and for a sub-key every sibling, must equal its pre-edit value.
    """
    from ruamel.yaml.error import YAMLError

    try:
        before, after = _safe_load(before_text), _safe_load(rendered)
    except (YAMLError, ValueError):
        return "the file cannot be parsed to verify the edit (an unusual tag or syntax)"
    if not isinstance(after, dict) or not isinstance(before, dict):
        return "the edited file is no longer a mapping"
    if _members(after, field) != _members(before, field):
        return "the edit would change other keys"
    old, new = before.get(field), after.get(field)
    want = _wanted_plain(wanted) if not removing else None
    if sub is None:
        reached = (field not in after) if removing else (field in after and new == want)
        return "" if reached else _REASON_SHARED
    if _members(old, sub) != _members(new, sub):
        return "the edit would change other keys"
    present = isinstance(new, dict) and sub in new
    reached = (not present) if removing else (isinstance(new, dict) and present and new[sub] == want)
    return "" if reached else _REASON_SHARED

"""Parsed YAML memoised for the length of one install, keyed by the text's content hash (P2a).

One ``update_project(ide="all")`` parsed the same 36 KB ``managed-artifacts.yaml`` six times, the target's
``channels/manifest.yaml`` once per client, and ``config.yaml`` about fifty times, all in pure-Python ruamel
(measured with a profiler, 2026-10-06).

* **Scope.** The memo lives in the install binding's shared state (``state._project_root_binding``): created
  when the outermost ``installing_into`` block opens and dropped when it closes, so it never outlives one
  ``init_project`` / ``update_project`` call and never crosses into another install's context. The install's
  own worker threads share the one dict (they run under ``copy_context().run``), which is safe: dict get and
  set are atomic, values are only ever deep-copied out, and two threads missing at once both parse (a wasted
  parse, never a wrong value). Outside an install every read parses, exactly as before.
* **Key.** The SHA-256 of the text plus the call site's kind and the parse callable's module and qualified
  name, never a path, mtime or size: a file rewritten within one mtime tick, or to the same size, has a
  different hash and is parsed again. Two call sites that reuse a kind still never share an entry, because
  their parse callables differ, so neither is handed the other's result shape. Equal text through one
  parser parses to an equal value whatever file it came from.
* **Isolation.** Every caller, the first included, gets a deep copy, so no caller can change what another
  reads. A parse that raises is not memoised: the next read raises again.

Only parsing is memoised. Reads still open and read the file, so a writer's change is always seen, and
writes are untouched.
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")
_SHARED_KEY = "yaml_parse_memo"


def _memo() -> dict[tuple[str, str, str, bytes], object] | None:
    from trw_mcp.state._project_root_binding import install_shared

    shared = install_shared()
    if shared is None:
        return None
    memo: dict[tuple[str, str, str, bytes], object] = shared.setdefault(_SHARED_KEY, {})
    return memo


def memo_parse(kind: str, text: str, parse: Callable[[str], _T]) -> _T:
    """``parse(text)``, memoised inside an install under (*kind*, *parse*'s name, sha256(*text*)); always a copy.

    *kind* names the call site and its loader mode. The parse callable's module and qualified name are part of
    the key as well, so a kind reused by another call site cannot hand it a value of a different shape.
    """
    memo = _memo()
    if memo is None:
        return parse(text)
    key = (kind, parse.__module__, parse.__qualname__, hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest())
    if key not in memo:
        memo[key] = parse(text)
    parsed: _T = copy.deepcopy(memo[key])  # type: ignore[assignment]
    return parsed

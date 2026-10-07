"""A per-process source index for the census and wiring scanners (test-performance program, P2c).

Why: about seventy census, wiring and boundary tests each walked and ``ast.parse``d the whole of
``trw-mcp/src`` (and some ``trw-memory/src``), and every planted-violation case copied the tree and parsed it
again. Parsing the two trees takes about one second, so a full run spent minutes re-deriving the same ASTs.

What it is: a content-addressed memo, one per process (so one per xdist worker, with nothing shared across
workers). Each source text is keyed by the SHA-256 of its bytes, so:

* a file edited after it was first indexed hashes differently and is re-read and re-parsed (never stale);
* a planted copy of an unchanged file (``copytree`` into ``tmp_path``) hits the same entry, and only the one
  planted file is parsed;
* the parse options that change the tree (``type_comments``, ``feature_version``, ``mode``) are part of the key.

Contract for callers:

* Trees returned by :func:`tree`, :func:`parse` and :func:`source` are SHARED. Treat them as read-only:
  ``ast.walk``/``ast.iter_child_nodes`` and the like are fine, but never assign to a node, set ``.parent``,
  run an ``ast.NodeTransformer`` over one, or call ``ast.fix_missing_locations`` on one. A caller that must
  mutate takes :func:`tree_copy` / :func:`parse_copy`, which hand back a private deep copy.
* ``SyntaxError`` is never cached; an unparseable file raises on every call, exactly as ``ast.parse`` would.
* Memory: a parsed tree costs roughly 30x its source size, so trw-mcp's and trw-memory's source together hold
  about 350 MB per worker that scans both. :func:`clear` drops everything.

Bypass: ``with source_index.bypass():`` (or ``TRW_SOURCE_INDEX=off`` in the environment) makes every lookup
read and parse fresh and store nothing. One representative census keeps running that way
(``test_direct_sqlite_connect_census.py::test_the_census_is_green_with_the_index_bypassed``) so the cached
path can never be the only path a census has ever been green on.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import os
import threading
import types
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_OFF_VALUES = frozenset({"off", "0", "false", "no"})

_Options = tuple[str, tuple[int, int] | None, bool]


@dataclass(frozen=True)
class IndexedSource:
    """One indexed file. ``tree`` is shared and read-only (see the module docstring)."""

    path: Path
    sha256: str
    text: str
    tree: ast.Module


class _State:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.texts: dict[str, str] = {}
        self.trees: dict[tuple[str, _Options], ast.AST] = {}
        self.hits = 0
        self.misses = 0
        self.bypass_depth = 0


_STATE = _State()


def _bypassed() -> bool:
    return _STATE.bypass_depth > 0 or os.environ.get("TRW_SOURCE_INDEX", "").lower() in _OFF_VALUES


@contextlib.contextmanager
def bypass() -> Iterator[None]:
    """Read and parse fresh, store nothing, for the duration of the block (re-entrant)."""
    with _STATE.lock:
        _STATE.bypass_depth += 1
    try:
        yield
    finally:
        with _STATE.lock:
            _STATE.bypass_depth -= 1


def clear() -> None:
    """Drop every cached text and tree and reset the counters."""
    with _STATE.lock:
        _STATE.texts.clear()
        _STATE.trees.clear()
        _STATE.hits = _STATE.misses = 0


def stats() -> dict[str, int]:
    """``hits``/``misses`` of tree lookups, and how many texts and trees are held."""
    with _STATE.lock:
        return {
            "hits": _STATE.hits,
            "misses": _STATE.misses,
            "texts": len(_STATE.texts),
            "trees": len(_STATE.trees),
        }


def _normalise(raw: bytes) -> str:
    """Decode as ``Path.read_text(encoding="utf-8")`` would: UTF-8 with universal newlines."""
    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _options(type_comments: bool, feature_version: tuple[int, int] | int | None, mode: str) -> _Options:
    if isinstance(feature_version, int):
        feature_version = (3, feature_version)
    return (mode, feature_version, type_comments)


def _parse_fresh(text: str, filename: str, options: _Options) -> ast.AST:
    mode, feature_version, type_comments = options
    return ast.parse(text, filename, mode, type_comments=type_comments, feature_version=feature_version)


def _tree_for(
    digest: str, text: str, filename: str, type_comments: bool, feature_version: tuple[int, int] | int | None, mode: str
) -> ast.AST:
    options = _options(type_comments, feature_version, mode)
    if _bypassed():
        return _parse_fresh(text, filename, options)
    key = (digest, options)
    with _STATE.lock:
        cached = _STATE.trees.get(key)
        if cached is not None:
            _STATE.hits += 1
            return cached
    tree = _parse_fresh(text, filename, options)  # outside the lock; a race only parses twice
    with _STATE.lock:
        _STATE.misses += 1
        _STATE.trees.setdefault(key, tree)
        return _STATE.trees[key]


def parse(
    source: str,
    filename: str = "<unknown>",
    *,
    type_comments: bool = False,
    feature_version: tuple[int, int] | int | None = None,
    mode: str = "exec",
) -> Any:
    """``ast.parse`` through the index, keyed by the SHA-256 of *source*. The result is shared and read-only."""
    digest = _digest(source.encode("utf-8", "surrogatepass"))
    return _tree_for(digest, source, filename, type_comments, feature_version, mode)


def parse_copy(source: str, filename: str = "<unknown>", **options: Any) -> Any:
    """Like :func:`parse`, but a private deep copy the caller may mutate."""
    return copy.deepcopy(parse(source, filename, **options))


def read(path: Path | str) -> str:
    """The text of *path* (UTF-8, universal newlines), re-read on every call and memoised by content hash."""
    raw = Path(path).read_bytes()
    if _bypassed():
        return _normalise(raw)
    digest = _digest(raw)
    with _STATE.lock:
        text = _STATE.texts.get(digest)
        if text is None:
            text = _STATE.texts[digest] = _normalise(raw)
        return text


def source(
    path: Path | str,
    *,
    type_comments: bool = False,
    feature_version: tuple[int, int] | int | None = None,
) -> IndexedSource:
    """Read *path* and return its hash, text and parsed tree. The file is re-read each call, so an edit is seen."""
    path = Path(path)
    raw = path.read_bytes()
    digest = _digest(raw)
    if _bypassed():
        text = _normalise(raw)
    else:
        with _STATE.lock:
            known = _STATE.texts.get(digest)
            text = known if known is not None else _STATE.texts.setdefault(digest, _normalise(raw))
    tree = _tree_for(digest, text, str(path), type_comments, feature_version, "exec")
    assert isinstance(tree, ast.Module)
    return IndexedSource(path=path, sha256=digest, text=text, tree=tree)


def tree(path: Path | str, **options: Any) -> ast.Module:
    """The parsed module of *path*. Shared and read-only; use :func:`tree_copy` to mutate."""
    return source(path, **options).tree


def tree_copy(path: Path | str, **options: Any) -> ast.Module:
    """The parsed module of *path* as a private deep copy the caller may mutate."""
    return copy.deepcopy(tree(path, **options))


def py_files(root: Path | str) -> list[Path]:
    """Every ``*.py`` under *root*, sorted. Listed fresh (a directory listing is cheap and must not go stale)."""
    return sorted(Path(root).rglob("*.py"))


def walk(root: Path | str) -> Iterator[IndexedSource]:
    """:func:`source` for every ``*.py`` under *root*, in sorted order."""
    for path in py_files(root):
        yield source(path)


class _CachedAst(types.SimpleNamespace):
    """The ``ast`` module with ``parse`` routed through the index; every other name is the real one."""

    def __getattr__(self, name: str) -> Any:
        return getattr(ast, name)

    @staticmethod
    def parse(source: str, filename: str = "<unknown>", mode: str = "exec", **options: Any) -> Any:
        return parse(source, filename, mode=mode, **options)


#: Drop-in for the ``ast`` global of a ``src`` scanner module that cannot import this test helper, e.g.
#: ``monkeypatch.setattr(trw_memory._write_census, "ast", source_index.cached_ast)``. Read-only trees only.
cached_ast = _CachedAst()

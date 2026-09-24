"""The learning-id index must stay a cache as the store grows.

Reported 2026-09-16 from a live thread dump: `trw_deliver` blocked a server for
tens of minutes inside `unsettled_contradiction_ids`, which looks up one entry
per correlated id. Each lookup rebuilt the whole index because the build took
32s against a 30s TTL — the index expired before it finished being built, so it
never cached anything and the cost became O(ids x store). A store of 6,805
entries crossed that threshold silently: no error, no log, just minutes.

Two defects, tested separately: the per-file read composed a whole YAML document
to obtain one top-level scalar, and the TTL was a fixed number that any large
enough store turns into a no-op.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests._layout import requires_local_timing
from tests._timing import assert_budget
from trw_mcp.scoring import _io_boundary
from trw_mcp.scoring._io_boundary import (
    _YAML_INDEX_TTL,
    _build_yaml_path_index,
    _effective_yaml_index_ttl,
    _get_yaml_path_index,
    _read_learning_id,
    _reset_yaml_path_index,
)
from trw_mcp.state.persistence import FileStateReader

pytestmark = pytest.mark.unit


#: Mean size of a real entry in this repo's store, measured 2026-09-16 over
#: 6,805 files. The fixture has to match this: the whole defect is parse cost
#: per entry, and a toy 80-byte entry composes fast enough to hide it. An early
#: version of this test used one, and the full-parse reader passed it.
_REAL_ENTRY_MEAN_BYTES = 7112


def _write_entry(directory: Path, lid: str, *, quoted: str = "") -> Path:
    path = directory / f"{lid}.yaml"
    rendered = f'"{lid}"' if quoted == '"' else f"'{lid}'" if quoted == "'" else lid
    # Shaped like a real learning: a quoted summary, a long folded detail block,
    # and nested sequences/mappings — the structures that cost a YAML composer.
    body_line = "  a line of recorded detail that a YAML composer must scan and fold. "
    repeats = max(1, _REAL_ENTRY_MEAN_BYTES // len(body_line))
    detail = "".join(f"{body_line}{n}\n" for n in range(repeats))
    path.write_text(
        f"id: {rendered}\n"
        f"summary: 'an entry whose summary is quoted, like every real one'\n"
        f"detail: |\n{detail}"
        f"tags:\n  - alpha\n  - beta\n  - gamma\n"
        f"metadata:\n  type: pattern\n  confidence: verified\n  impact: 0.5\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def _clean_index() -> None:
    _reset_yaml_path_index()
    yield
    _reset_yaml_path_index()


@pytest.mark.parametrize("quoted", ["", '"', "'"], ids=["bare", "double", "single"])
def test_the_id_is_read_under_every_quoting_style(tmp_path: Path, quoted: str) -> None:
    """The fast path must not silently drop entries it cannot pattern-match."""
    path = _write_entry(tmp_path, "L-abc123", quoted=quoted)
    assert _read_learning_id(FileStateReader(), path) == "L-abc123"


def test_an_id_the_pattern_cannot_match_still_resolves_through_the_parser(tmp_path: Path) -> None:
    """The control: the regex is an optimisation, never the only reader.

    Without this, a fast path that silently returned None for anything unusual
    would pass every other test here while quietly shrinking the index.
    """
    path = tmp_path / "folded.yaml"
    path.write_text("id: >-\n  L-folded\nsummary: s\n", encoding="utf-8")
    assert _read_learning_id(FileStateReader(), path) == "L-folded"


def test_a_nested_id_is_not_mistaken_for_the_entry_id(tmp_path: Path) -> None:
    """`id:` indented under a mapping is somebody else's field."""
    path = tmp_path / "nested.yaml"
    path.write_text("id: L-real\nmetadata:\n  id: L-nested-decoy\n", encoding="utf-8")
    assert _read_learning_id(FileStateReader(), path) == "L-real"


def _index_vs_parse(tmp_path: Path) -> tuple[dict[str, Path], dict[str, Path], float, float]:
    """Build the fast id index and the full-parse equivalent over the same 400-entry corpus."""
    for index in range(400):
        _write_entry(tmp_path, f"L-{index:06d}")

    started = time.monotonic()
    fast = _build_yaml_path_index(tmp_path)
    fast_seconds = time.monotonic() - started

    reader = FileStateReader()
    started = time.monotonic()
    parsed = {}
    for yaml_file in sorted(tmp_path.glob("*.yaml")):
        data = reader.read_yaml(yaml_file)
        lid = data.get("id")
        if isinstance(lid, str) and lid:
            parsed[lid] = yaml_file
    parse_seconds = time.monotonic() - started

    return fast, parsed, fast_seconds, parse_seconds


def test_reading_an_id_matches_the_full_parser(tmp_path: Path) -> None:
    """The fast reader must index exactly what the full-parse reader does."""
    fast, parsed, _fast_seconds, _parse_seconds = _index_vs_parse(tmp_path)
    assert fast == parsed, "the fast reader must index exactly what the parser does"


@requires_local_timing
def test_reading_an_id_is_much_cheaper_than_composing_the_document(tmp_path: Path) -> None:
    """The fast reader must be decisively cheaper than the parser it replaces.

    Stated as a RATIO between the two readers over the same corpus, not as a
    stopwatch against a fixed budget. An absolute budget is a measurement of
    machine load -- the same mistake this repo fixed in its daemon tests the same
    day -- and it also failed to discriminate: 1200 realistic entries compose in
    about 2s, comfortably inside any sane budget, while the real 6,805-entry
    store took 32s and blocked a server for tens of minutes.

    A ratio cannot be passed by a slow machine and cannot be passed at all if the
    two readers are the same code.
    """
    _fast, _parsed, fast_seconds, parse_seconds = _index_vs_parse(tmp_path)
    ratio = fast_seconds / parse_seconds
    assert_budget("fast_reader_vs_parser_ratio", ratio, 0.2, "ratio")


def test_repeated_lookups_build_the_index_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact shape from the thread dump: one lookup per correlated id."""
    for index in range(50):
        _write_entry(tmp_path, f"L-{index:06d}")

    builds = 0
    real_build = _io_boundary._build_yaml_path_index

    def _counting_build(directory: Path) -> dict[str, Path]:
        nonlocal builds
        builds += 1
        return real_build(directory)

    monkeypatch.setattr(_io_boundary, "_build_yaml_path_index", _counting_build)

    for _ in range(40):
        _get_yaml_path_index(tmp_path)

    assert builds == 1, f"40 lookups triggered {builds} full-store rebuilds"


def test_the_ttl_floor_rises_with_a_build_that_outruns_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """A store big enough to outrun the fixed TTL must still be cached."""
    monkeypatch.setattr(_io_boundary, "_yaml_path_index_build_seconds", 0.0)
    assert _effective_yaml_index_ttl() == _YAML_INDEX_TTL

    monkeypatch.setattr(_io_boundary, "_yaml_path_index_build_seconds", _YAML_INDEX_TTL * 2)
    assert _effective_yaml_index_ttl() > _YAML_INDEX_TTL * 2, (
        "a build costing more than the TTL must widen the floor past its own cost"
    )

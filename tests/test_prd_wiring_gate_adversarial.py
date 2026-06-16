"""ROUND-2 HARDENING — adversarial battery for the seam-registry parsers.

Surfaces:
  * ``trw_mcp.state.validation._prd_scoring_wiring.parse_seam_entries`` (Pydantic
    ``SeamEntry`` over ruamel-parsed frontmatter), and
  * ``scripts/check-seam-expiry.py`` (standalone PyYAML temporal gate).

Behavior contract: BOTH parsers are warn-not-crash. A malformed seam registry
(wrong shape, bad types, homoglyph kind, unparseable expiry) must NEVER crash
validation and must NEVER be silently accepted as covering the wiring gate.
``parse_seam_entries`` returns ``(valid_seams, warnings)`` skipping bad entries;
the script reports drift advisorily and drives its exit code purely off expired
seams. Large registries must stay fast (<1s for 10k). All tests assert the SAFE
behavior and are kept as regression.
"""

from __future__ import annotations

import datetime
import importlib.util
import time
from pathlib import Path

import pytest

# Public-mirror guard: this test asserts a MONOREPO invariant (repo-root
# scripts/ + .claude/ layout) absent from the standalone trw-mcp PyPI/GitHub
# mirror. Skip cleanly there; the monorepo CI still enforces it.
if not (Path(__file__).resolve().parents[2] / "scripts").is_dir():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )

from trw_mcp.state.validation._prd_scoring_wiring import (
    check_wiring_gate,
    parse_seam_entries,
)

_TODAY = datetime.date(2026, 6, 12)
_FUTURE = "2099-01-01"


def _seam(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "kind": "deferred",
        "target_prd": "PRD-X-001",
        "owner": "agent-7",
        "expiry_date": _FUTURE,
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------- #
# parse_seam_entries — shape confusion (must not crash, must not over-accept).
# --------------------------------------------------------------------------- #


def test_seams_as_dict_not_list_is_empty() -> None:
    # A mapping (not a list) is the absent-seams backward-compatible case.
    valid, warns = parse_seam_entries({"seams": {"kind": "deferred"}}, today=_TODAY)
    assert valid == [] and warns == []


def test_seams_none_is_empty() -> None:
    valid, warns = parse_seam_entries({"seams": None}, today=_TODAY)
    assert valid == [] and warns == []


def test_seams_missing_is_empty() -> None:
    valid, warns = parse_seam_entries({}, today=_TODAY)
    assert valid == [] and warns == []


@pytest.mark.parametrize("entry", ["a plain string", 42, 3.14, ["nested"], None, True])
def test_non_mapping_entry_is_skipped_with_warning(entry: object) -> None:
    valid, warns = parse_seam_entries({"seams": [entry]}, today=_TODAY)
    assert valid == []
    assert len(warns) == 1 and "not a mapping" in warns[0]


# --------------------------------------------------------------------------- #
# parse_seam_entries — kind homoglyphs + bad types must REJECT (not cover).
# --------------------------------------------------------------------------- #


def test_cyrillic_homoglyph_kind_is_rejected() -> None:
    # "dеferred" with a Cyrillic 'е' (U+0435) must NOT validate as "deferred".
    homoglyph = "dеferred"
    valid, warns = parse_seam_entries({"seams": [_seam(kind=homoglyph)]}, today=_TODAY)
    assert valid == [], "homoglyph kind must not be accepted as a valid seam"
    assert len(warns) == 1 and "invalid" in warns[0]


@pytest.mark.parametrize("bad_kind", ["", "DEFERRED", " deferred", "random", "deferred\n"])
def test_invalid_kind_is_rejected(bad_kind: str) -> None:
    valid, warns = parse_seam_entries({"seams": [_seam(kind=bad_kind)]}, today=_TODAY)
    assert valid == []
    assert len(warns) == 1


def test_expiry_as_yaml_date_object_is_normalized() -> None:
    # ruamel parses an unquoted ISO date into datetime.date; the parser must
    # normalize it to a string and accept the seam.
    valid, warns = parse_seam_entries({"seams": [_seam(expiry_date=datetime.date(2099, 1, 1))]}, today=_TODAY)
    assert len(valid) == 1 and warns == []
    assert valid[0].expiry_date == "2099-01-01"


@pytest.mark.parametrize("bad_expiry", [2099, "2099-13-45", "2099-02-30", "not-a-date", "", "∞"])
def test_malformed_expiry_is_rejected(bad_expiry: object) -> None:
    valid, warns = parse_seam_entries({"seams": [_seam(expiry_date=bad_expiry)]}, today=_TODAY)
    assert valid == []
    assert len(warns) == 1


def test_expired_seam_is_excluded_with_overdue_warning() -> None:
    valid, warns = parse_seam_entries({"seams": [_seam(expiry_date="2000-01-01")]}, today=_TODAY)
    assert valid == []
    assert len(warns) == 1 and "expired" in warns[0]


def test_missing_required_fields_rejected() -> None:
    valid, warns = parse_seam_entries({"seams": [{"kind": "deferred"}]}, today=_TODAY)
    assert valid == []
    assert len(warns) == 1


def test_extra_keys_do_not_crash() -> None:
    # SeamEntry ignores unknown keys; an entry with junk extras still validates.
    valid, warns = parse_seam_entries({"seams": [_seam(junk=[1, 2, 3])]}, today=_TODAY)
    assert len(valid) == 1 and warns == []


# --------------------------------------------------------------------------- #
# parse_seam_entries — performance bound (10k entries < 1s).
# --------------------------------------------------------------------------- #


def test_ten_thousand_entries_stays_fast() -> None:
    seams = [_seam(target_prd=f"PRD-{i}") for i in range(10_000)]
    start = time.monotonic()
    valid, warns = parse_seam_entries({"seams": seams}, today=_TODAY)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, f"10k-seam parse took {elapsed:.3f}s (>1s perf bound)"
    assert len(valid) == 10_000


def test_check_wiring_gate_tolerates_hostile_seams() -> None:
    # A public-surface FR with malformed seams must not crash; the malformed
    # seams provide no coverage so the unwired FR is still warned.
    content = "### FR01: thing\nsurface: public\n"
    fm = {"ip_tier": "public", "seams": {"not": "a list"}}
    warns, fails = check_wiring_gate(content, fm, mode="warn", today=_TODAY)
    assert any("wiring_gate_warning" in w for w in warns)
    assert fails == []


# --------------------------------------------------------------------------- #
# scripts/check-seam-expiry.py — standalone PyYAML parser.
# --------------------------------------------------------------------------- #


def _load_script() -> object:
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "scripts" / "check-seam-expiry.py"
    spec = importlib.util.spec_from_file_location("_check_seam_expiry_adv", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_SCRIPT = _load_script()


@pytest.mark.parametrize(
    "text",
    [
        "---\n- a\n- b\n---\n",  # non-dict root
        "---\nseams: [unclosed\n---\n",  # YAML syntax error
        "not frontmatter at all",  # no delimiter
        "",  # empty
    ],
)
def test_script_frontmatter_degrades_to_empty(text: str) -> None:
    out = _SCRIPT._frontmatter_dict(text)  # type: ignore[attr-defined]
    assert out == {}


def test_script_dup_yaml_keys_last_wins_no_crash() -> None:
    text = (
        "---\nid: P\nid: Q\nseams:\n  - kind: deferred\n    expiry_date: 2099-01-01\n"
        "    owner: o\n    target_prd: P\n---\n"
    )
    out = _SCRIPT._frontmatter_dict(text)  # type: ignore[attr-defined]
    assert out.get("id") == "Q"  # last-wins, no DuplicateKeyError escape
    assert isinstance(out.get("seams"), list)


@pytest.mark.parametrize(
    "raw,expected_kind",
    [
        ("2099-01-01", datetime.date),
        ("  2099-01-01  ", datetime.date),
        (datetime.date(2099, 1, 1), datetime.date),
        ("2099-13-45", type(None)),
        ("∞", type(None)),
        (2099, type(None)),
        (["2099-01-01"], type(None)),
        (None, type(None)),
    ],
)
def test_script_coerce_expiry(raw: object, expected_kind: type) -> None:
    assert isinstance(_SCRIPT._coerce_expiry(raw), expected_kind)  # type: ignore[attr-defined]


def test_script_homoglyph_kind_reported_but_expiry_drives_exit(tmp_path: Path) -> None:
    # A Cyrillic-homoglyph kind is reported as invalid (advisory) AND still
    # subjected to the expiry gate; an expired homoglyph seam still exits 1.
    (tmp_path / "PRD-homoglyph.md").write_text(
        "---\nid: PRD-H\nseams:\n  - kind: dеferred\n    owner: o\n"
        "    target_prd: P\n    expiry_date: 2000-01-01\n---\nbody\n",
        encoding="utf-8",
    )
    rc = _SCRIPT.check_corpus(tmp_path, today=_TODAY)  # type: ignore[attr-defined]
    assert rc == 1  # expired seam drives the non-zero exit


def test_script_homoglyph_future_expiry_is_advisory_only(tmp_path: Path) -> None:
    # A homoglyph kind with a FUTURE expiry: invalid-kind is advisory only and
    # must NOT flip the exit code (the temporal gate's job is expiry).
    (tmp_path / "PRD-homoglyph.md").write_text(
        "---\nid: PRD-H\nseams:\n  - kind: dеferred\n    owner: o\n"
        "    target_prd: P\n    expiry_date: 2099-01-01\n---\nbody\n",
        encoding="utf-8",
    )
    rc = _SCRIPT.check_corpus(tmp_path, today=_TODAY)  # type: ignore[attr-defined]
    assert rc == 0

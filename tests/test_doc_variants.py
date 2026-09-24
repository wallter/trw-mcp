"""Behavior tests for derived-artifact variant naming (PRD-CORE-299)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from trw_mcp.state.doc_variants import (
    VARIANT_KINDS,
    VariantLocationError,
    VariantName,
    list_variants,
    parse_variant_name,
    variant_dir,
    variant_name,
    variant_status,
    write_variant,
)


@pytest.mark.parametrize(
    ("base", "kind", "producer", "rnd", "expected"),
    [
        ("design.md", "review", "peer", 1, "design.review-peer-r1.md"),
        ("design.md", "draft", None, 2, "design.draft-r2.md"),
        ("design.md", "notes", None, None, "design.notes.md"),
        ("design.md", "audit", "cursor-cli", 12, "design.audit-cursor-cli-r12.md"),
        ("notes.v10.3.md", "audit", "frontier", 1, "notes.v10.3.audit-frontier-r1.md"),
        ("loader.py", "review", "peer", 1, "loader.py.review-peer-r1.md"),
        ("x.md", "review", "r2d2", None, "x.review-r2d2.md"),
    ],
)
def test_name_roundtrip_including_dotted_stems(
    base: str, kind: str, producer: str | None, rnd: int | None, expected: str
) -> None:
    name = variant_name(base, kind, producer, rnd)
    assert name == expected
    stem = base.removesuffix(".md")
    assert parse_variant_name(name) == VariantName(stem, kind, producer, rnd)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("v10.3.1.md", None),
        ("design.md", None),
        ("design.review-peer-r1.txt", None),
        (".review.md", None),
        ("design.reviewer-peer.md", None),
        ("design.review-r01.md", None),
        ("design.review-r1000.md", None),
        ("design.review-peer-r0.md", None),
        ("design.review-r1-r2.md", None),
        ("design.review-Peer.md", None),
        ("design.draft-r2.md", VariantName("design", "draft", None, 2)),
        ("x.review-r2d2.md", VariantName("x", "review", "r2d2", None)),
        ("th0rgal-sandboxed.sh-deep-dive-2026-05-02.md", None),
    ],
)
def test_parse_accepts_only_the_grammar(name: str, expected: VariantName | None) -> None:
    assert parse_variant_name(name) == expected


@pytest.mark.parametrize("name", ["", ".", ".md", "..md", "a..md", "\x00.review.md", "é.review.md", "a" * 5000])
def test_parse_is_total_and_deterministic(name: str) -> None:
    assert parse_variant_name(name) == parse_variant_name(name)


# Round-shaped parts ("r12") are drawn on purpose: a producer ending in one is the ambiguous case.
_SLUG_PART = st.from_regex(r"[a-z0-9]{1,6}", fullmatch=True) | st.from_regex(r"r[1-9][0-9]{0,2}", fullmatch=True)


@settings(max_examples=400, deadline=None)
@given(
    stem=st.from_regex(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,20}", fullmatch=True),
    kind=st.sampled_from(VARIANT_KINDS),
    producer=st.none() | st.lists(_SLUG_PART, min_size=1, max_size=4).map("-".join),
    rnd=st.none() | st.integers(min_value=1, max_value=999),
)
def test_every_accepted_name_roundtrips(stem: str, kind: str, producer: str | None, rnd: int | None) -> None:
    """A valid (kind, producer, round) formats and parses back unchanged; an ambiguous producer is refused."""
    assume(parse_variant_name(f"{stem}.md") is None)  # such a base is refused on its own (tested above)
    # The spec, stated independently of the implementation: at most 32 chars, no round-shaped last part.
    ambiguous = producer is not None and (len(producer) > 32 or re.search(r"(?:\A|-)r[0-9]+\Z", producer))
    if ambiguous:
        with pytest.raises(ValueError):
            variant_name(f"{stem}.md", kind, producer, rnd)
        return
    name = variant_name(f"{stem}.md", kind, producer, rnd)
    assert parse_variant_name(name) == VariantName(stem, kind, producer, rnd)


@pytest.mark.parametrize("producer", ["peer-r1", "a-b-r12", "x-r999"])
def test_producer_ending_in_a_round_is_rejected(producer: str) -> None:
    with pytest.raises(ValueError):
        variant_name("x.md", "review", producer)
    assert parse_variant_name(f"x.review-{producer}-r2.md") is None


@pytest.mark.parametrize("producer", ["../x", "A", "a_b", "a" * 33, "", "r12", "a--b", "-a"])
def test_hostile_producer_slugs_rejected(producer: str) -> None:
    with pytest.raises(ValueError):
        variant_name("design.md", "review", producer, 1)


@pytest.mark.parametrize(
    ("base", "kind", "rnd"),
    [
        ("design.md", "critique", 1),
        ("design.md", "review", 0),
        ("design.md", "review", 1000),
        ("sub/design.md", "review", 1),
        (".md", "review", 1),
        # A base that already reads as a variant of notes.md is ambiguous.
        ("notes.review.md", "audit", 1),
    ],
)
def test_bad_kind_round_or_ambiguous_base_rejected(base: str, kind: str, rnd: int) -> None:
    with pytest.raises(ValueError):
        variant_name(base, kind, "peer", rnd)


@pytest.mark.parametrize("kind", VARIANT_KINDS)
def test_every_kind_roundtrips(kind: str) -> None:
    assert parse_variant_name(variant_name("a.md", kind, "peer", 3)) == VariantName("a", kind, "peer", 3)


def _write(root: Path, base: Path, **kw: object) -> Path:
    args: dict[str, object] = {"kind": "review", "producer": "peer", "body": "findings", "ok": True}
    args.update(kw)
    return write_variant(base, root, **args)  # type: ignore[arg-type]


def test_status_is_decided_by_base_sha256_not_mtime(tmp_path: Path) -> None:
    base = tmp_path / "design.md"
    base.write_text("v1\n")
    variant = _write(tmp_path, base)
    assert variant_status(variant) == "fresh"
    os.utime(base, (1, 1))  # mtime moves, bytes do not
    assert variant_status(variant) == "fresh"
    base.write_text("v2\n")
    assert variant_status(variant) == "stale"
    base.unlink()
    assert variant_status(variant) == "orphaned"
    hand = tmp_path / "other.review.md"
    hand.write_text("no frontmatter\n")
    assert variant_status(hand) == "unrecorded"
    broken = tmp_path / "other.audit.md"
    broken.write_text("---\nvariant: [unclosed\n---\n")
    assert variant_status(broken) == "unrecorded"


def test_list_variants_orders_by_kind_producer_round(tmp_path: Path) -> None:
    base = tmp_path / "design.md"
    base.write_text("x")
    for name in (
        "design.review-a-r2.md",
        "design.review-a-r1.md",
        "design.audit-b-r1.md",
        "design-old.review-a-r1.md",
        "xv-2026-09-23-design.md",
        "design.review-a-r1.audit-b-r1.md",
    ):
        (tmp_path / name).write_text("y")
    got = [p.name for p, _ in list_variants(base)]
    assert got == ["design.audit-b-r1.md", "design.review-a-r1.md", "design.review-a-r2.md"]


def test_write_variant_takes_next_free_round_and_never_overwrites(tmp_path: Path) -> None:
    base = tmp_path / "design.md"
    base.write_text("x")
    first = _write(tmp_path, base)
    second = _write(tmp_path, base, body="second")
    assert (first.name, second.name) == ("design.review-peer-r1.md", "design.review-peer-r2.md")
    assert "findings" in first.read_text()
    other = _write(tmp_path, base, producer="frontier", kind="audit", ok=False, role="design-audit")
    assert other.name == "design.audit-frontier-r1.md"
    head = other.read_text()
    assert "ok: false" in head and "role: design-audit" in head and "base: design.md" in head


def test_write_variant_skips_a_round_created_after_the_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.state import doc_variants

    base = tmp_path / "design.md"
    base.write_text("x")
    racer = tmp_path / "design.review-peer-r1.md"
    real = doc_variants.list_variants

    def _scan_then_race(b: Path) -> list[tuple[Path, VariantName]]:
        found = real(b)
        racer.write_text("written by a concurrent run")
        return found

    monkeypatch.setattr(doc_variants, "list_variants", _scan_then_race)
    got = _write(tmp_path, base)
    assert got.name == "design.review-peer-r2.md"
    assert racer.read_text() == "written by a concurrent run"


def test_write_variant_exhausted_rounds_raise(tmp_path: Path) -> None:
    base = tmp_path / "design.md"
    base.write_text("x")
    (tmp_path / "design.review-peer-r999.md").write_text("last")
    with pytest.raises(VariantLocationError):
        _write(tmp_path, base)


@pytest.mark.parametrize(
    "rel",
    [
        "docs/requirements-aare-f/prds/PRD-CORE-001.md",
        "docs/requirements-aare-f/archive/prds/PRD-CORE-002.md",
        "docs/requirements-aare-f/sprints/active/sprint-1.md",
        ".claude/rules/testing.md",
        ".agents/skills/x/SKILL.md",
        ".opencode/agents/a.md",
        "trw-mcp/src/trw_mcp/data/agents/trw-lead.md",
    ],
)
def test_discovery_directory_refused(tmp_path: Path, rel: str) -> None:
    base = tmp_path / rel
    base.parent.mkdir(parents=True)
    base.write_text("x")
    with pytest.raises(VariantLocationError, match=str(Path(rel).parent)):
        _write(tmp_path, base)
    assert [p.name for p in base.parent.iterdir()] == [base.name]


def test_ordinary_directory_writes_next_to_base(tmp_path: Path) -> None:
    base = tmp_path / "docs" / "research" / "topic.md"
    base.parent.mkdir(parents=True)
    base.write_text("x")
    assert variant_dir(base, tmp_path) == base.parent.resolve()


def test_symlinked_base_escaping_root_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("x")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link.md").symlink_to(outside / "secret.md")
    with pytest.raises(VariantLocationError):
        _write(root, root / "link.md")
    assert list(outside.iterdir()) == [outside / "secret.md"]
    assert list(root.iterdir()) == [root / "link.md"]


def test_list_variants_reads_no_file_contents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "design.md"
    base.write_text("x")
    (tmp_path / "design.review-a-r1.md").write_text("y")
    (tmp_path / "unrelated.md").write_text("z")
    opened: list[str] = []
    real_open = Path.open

    def _counting_open(self: Path, *a: object, **k: object) -> object:
        opened.append(self.name)
        return real_open(self, *a, **k)  # type: ignore[call-overload]

    monkeypatch.setattr(Path, "open", _counting_open)
    assert len(list_variants(base)) == 1
    assert opened == []

"""Shared fixture helpers for the PRD-CORE-337 FR08 symlink-refusal tests: plant a link, prove its target held."""

from __future__ import annotations

from pathlib import Path

OUTSIDE_BYTES = b"outside the checkout -- must never change\n"


def plant_symlink(link: Path, outside_dir: Path, content: bytes = OUTSIDE_BYTES) -> Path:
    """Make *link* a symlink to a file outside the project holding *content*; return that file."""
    outside_dir.mkdir(parents=True, exist_ok=True)
    victim = outside_dir / link.name  # same name, so a name-based check cannot tell the two apart
    victim.write_bytes(content)
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(victim)
    return victim


def assert_untouched(link: Path, victim: Path, content: bytes = OUTSIDE_BYTES) -> None:
    """The link is still a link (nothing replaced it) and the file it points at kept its bytes."""
    now = victim.read_bytes()  # checked first: a write-through must fail on the victim's bytes
    assert now == content, f"the writer changed the file behind the link: {now[:60]!r}"
    assert link.is_symlink(), "the planted link must still be a link: nothing replaced it either"

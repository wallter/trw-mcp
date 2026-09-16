"""Canonical plan bodies and their digests (PRD-CORE-275-FR03).

Two harnesses must agree byte-for-byte on a body before either can agree on a
digest, so canonicalization is the whole contract here: sorted keys, no
insignificant whitespace, UTF-8, and path arrays **sorted and deduplicated**.

That last choice is deliberate and was a correction. An earlier draft required
arrays to be "ordered and duplicate-free" AND the digest to be independent of
input ordering, which is contradictory — either caller order is preserved and
therefore significant, or it is normalized away. Normalizing away is chosen so
that two agents who list the same paths in different orders still produce one
digest, which is what makes the digest usable as a shared identifier at all.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

#: Bumped only for an incompatible body change. A peer that does not recognize
#: the value treats the body as non-protocol data rather than guessing.
SCHEMA = "trw.plan-review.v1"

PROPOSAL_KEYS = ("schema", "type", "plan_id", "revision", "digest", "paths", "test_paths", "summary")
REVIEW_KEYS = ("schema", "type", "plan_id", "revision", "digest", "findings")


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    """One byte string for one logical body.

    ``sort_keys`` plus tight separators plus ``ensure_ascii=False`` means the
    encoding depends on the VALUES only, never on construction order or on the
    producing implementation's dict ordering.
    """

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def canonical_path(raw: str) -> str:
    """One spelling per file, so two harnesses agree on a digest.

    `sorted(set(...))` alone is NOT enough, and an independent review found the
    counterexamples: `src/a.py`, `./src/a.py` and `src//a.py` are the same file
    and digested three different ways. Unicode is worse — macOS emits decomposed
    (NFD) filenames where Linux emits composed (NFC), so the same accented
    filename digested differently per platform.

    Normalizing here rather than refusing the variants keeps the protocol usable
    from a shell, where `./x` is what tab-completion produces.
    """

    text = unicodedata.normalize("NFC", raw)
    while text.startswith("./"):
        text = text[2:]
    while "//" in text:
        text = text.replace("//", "/")
    return text


def normalize_paths(paths: list[str]) -> list[str]:
    """Canonical, sorted, deduplicated, order-insensitive.

    Deduplication happens AFTER canonicalization, so `src/a.py` and `./src/a.py`
    collapse to one entry instead of surviving as two spellings of one file.
    """

    return sorted({canonical_path(path) for path in paths})


def digest_of(fields: dict[str, Any]) -> str:
    """SHA-256 over the canonical form of every field EXCEPT ``digest``.

    Excluding the digest is what makes it verifiable: a receiver recomputes over
    the same fields and compares, which is impossible if the digest is part of
    its own input.
    """

    payload = {key: value for key, value in fields.items() if key != "digest"}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def build_proposal(
    *, plan_id: str, revision: int, paths: list[str], test_paths: list[str], summary: str
) -> dict[str, Any]:
    """A complete, digest-bound proposal body."""

    fields: dict[str, Any] = {
        "schema": SCHEMA,
        "type": "proposal",
        "plan_id": plan_id,
        "revision": revision,
        "paths": normalize_paths(paths),
        "test_paths": normalize_paths(test_paths),
        "summary": summary,
    }
    fields["digest"] = digest_of(fields)
    return fields


def build_review(*, plan_id: str, revision: int, digest: str, findings: list[str]) -> dict[str, Any]:
    """A review body. It carries findings and NOTHING that reads as approval.

    ``digest`` here is the PROPOSAL's digest, carried back so the proposer can
    tell which plan the findings describe. The review is not digest-bound to
    itself: it is advisory text, and a second digest would imply an authority it
    does not have.
    """

    return {
        "schema": SCHEMA,
        "type": "review",
        "plan_id": plan_id,
        "revision": revision,
        "digest": digest,
        "findings": list(findings),
    }


def encode(body: dict[str, Any]) -> str:
    """The exact string to hand to ``trw_send``."""

    return canonical_bytes(body).decode("utf-8")

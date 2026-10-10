"""Stamp the checkout commit on a learning's assertions at write time (PRD-CORE-362 FR01-FR03).

The verification pass holds an assertion as unknown when its checkout is strictly behind the commit the
assertion carries, so the learning must record that commit when it is written. One bounded HEAD lookup
per call that writes assertions; no stamp is a normal outcome and never refuses or fails a learning.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from trw_memory.lifecycle import claim_tree

__all__ = ["stamp_assertions"]

logger = structlog.get_logger(__name__)


def stamp_assertions(assertions: list[dict[str, str]] | None, root: Path) -> list[dict[str, str]] | None:
    """*assertions* with the HEAD of the checkout at *root* set as ``commit_hash`` where none was given.

    An assertion that already carries a non-empty ``commit_hash`` is kept as given. No assertions, or none
    lacking a stamp, starts no git process. When git cannot answer, the assertions come back unchanged.
    """
    if not assertions or all(not isinstance(a, dict) or a.get("commit_hash") for a in assertions):
        return assertions
    try:
        sha = claim_tree.checkout_head(root)
    except Exception as exc:  # trw-fail-silent-allow: a missing stamp never refuses or fails a learning
        logger.debug("assertion_stamp_unavailable", error=type(exc).__name__)
        return assertions
    if sha is None:
        return assertions
    return [{**a, "commit_hash": sha} if isinstance(a, dict) and not a.get("commit_hash") else a for a in assertions]

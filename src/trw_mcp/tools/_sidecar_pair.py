"""Is a before-edit-batch / risk-report pair from two builds? (SIDECAR-PAIR-ATOMIC)

Split out of ``_sidecar_substrate`` (a pure move of one helper). ``refresh-sidecars`` publishes the pair with
two renames, so a failure between them leaves the first file from the new build and the second from the
previous one; the producer stamps both with one ``pair_generation``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: ``refresh-sidecars`` publishes these two with two renames; each is the other's pair.
_PAIR_SIBLING: dict[str, str] = {"before-edit-batch": "risk-report", "risk-report": "before-edit-batch"}
_PAIRED_NAME = re.compile(r"^(before-edit-batch|risk-report)-([0-9a-f]{40})\.json$")


def pair_from_two_builds(
    sidecar_path: Path, envelope: dict[str, Any], read: Callable[[Path], dict[str, Any] | None]
) -> bool:
    """True when *envelope* and its sibling (read with *read*) carry different ``pair_generation`` stamps.

    Only ``refresh-sidecars`` stamps a pair. A sibling with no stamp (the post-commit
    ``risk-report --persist-sidecar`` refresh), no sibling, or one that cannot be read is
    not a mismatch, and neither is an envelope with no stamp of its own.
    """
    ours = envelope.get("pair_generation")
    named = _PAIRED_NAME.match(sidecar_path.name)
    if isinstance(ours, bool) or not isinstance(ours, (int, float)) or named is None:
        return False
    try:
        sibling = read(sidecar_path.with_name(f"{_PAIR_SIBLING[named.group(1)]}-{named.group(2)}.json"))
    except (OSError, ValueError, RecursionError):
        # trw-fail-silent-allow: a sibling that cannot be read, decoded or parsed is unreadable, which is no mismatch
        return False
    theirs = None if sibling is None else sibling.get("pair_generation")
    return isinstance(theirs, (int, float)) and not isinstance(theirs, bool) and theirs != ours


def pair_stale_action(sha: str, cli_remediation: str | None, no_producer_action: str) -> str:
    """What to tell the operator when the pair for *sha* is from two builds."""
    retry = f"re-run: {cli_remediation}" if cli_remediation else no_producer_action
    return f"Sidecar pair for {sha} is from two builds (an interrupted publish); {retry}"

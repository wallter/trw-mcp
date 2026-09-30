"""SIDECAR-PAIR-ATOMIC: a reader treats a before-edit-batch / risk-report pair from two builds as stale.

``refresh-sidecars`` publishes the pair with two renames, so a failure between them leaves the first
file from the new build and the second from the previous one. The producer stamps both with one
``pair_generation``; a reader that finds a sibling stamped with a different one refuses the pair.
A sibling with no stamp (a standalone ``risk-report --persist-sidecar`` refresh, which runs after
every commit) or no sibling at all is not a mismatch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.tools._sidecar_substrate import SCHEMA_VERSION_ACCEPTED, load_sidecar_with_sha_check

SHA = "a" * 40
NAMES = {"batch": f"before-edit-batch-{SHA}.json", "risk": f"risk-report-{SHA}.json"}


def _write(cache: Path, which: str, *, pair: float | None, at: float = 1714000000.0) -> Path:
    envelope: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_ACCEPTED,
        "sha": SHA,
        "generated_at_unix": at,
        "payload": {"which": which},
    }
    if pair is not None:
        envelope["pair_generation"] = pair
    path = cache / NAMES[which]
    path.write_text(json.dumps(envelope))
    return path


def _load(path: Path) -> Any:
    return load_sidecar_with_sha_check(path, expected_sha=SHA, cli_remediation="trw-distill refresh")


@pytest.mark.parametrize("read", ["batch", "risk"])
def test_a_pair_from_two_builds_is_stale_whichever_file_is_read(tmp_path: Path, read: str) -> None:
    paths = {"batch": _write(tmp_path, "batch", pair=2.0), "risk": _write(tmp_path, "risk", pair=1.0)}

    loaded = _load(paths[read])

    assert loaded.status == "stale_sha"
    assert loaded.payload is None
    assert "two builds" in str(loaded.action)


@pytest.mark.parametrize("read", ["batch", "risk"])
def test_a_matching_pair_loads(tmp_path: Path, read: str) -> None:
    paths = {"batch": _write(tmp_path, "batch", pair=2.0), "risk": _write(tmp_path, "risk", pair=2.0)}

    assert _load(paths[read]).status == "ok"


def test_a_sibling_refreshed_alone_is_not_a_mismatch(tmp_path: Path) -> None:
    """The post-commit hook re-persists risk-report alone, with a newer time and no pair stamp."""
    batch = _write(tmp_path, "batch", pair=2.0, at=100.0)
    _write(tmp_path, "risk", pair=None, at=200.0)

    assert _load(batch).status == "ok"


def test_a_missing_sibling_is_not_a_mismatch(tmp_path: Path) -> None:
    assert _load(_write(tmp_path, "batch", pair=2.0)).status == "ok"


def test_a_legacy_envelope_without_a_stamp_ignores_its_sibling(tmp_path: Path) -> None:
    batch = _write(tmp_path, "batch", pair=None)
    _write(tmp_path, "risk", pair=1.0)

    assert _load(batch).status == "ok"


def test_a_corrupt_sibling_does_not_hide_a_good_file(tmp_path: Path) -> None:
    batch = _write(tmp_path, "batch", pair=2.0)
    (tmp_path / NAMES["risk"]).write_text("{not json")

    assert _load(batch).status == "ok"


def test_a_sibling_of_undecodable_bytes_does_not_crash_a_good_read(tmp_path: Path) -> None:
    batch = _write(tmp_path, "batch", pair=2.0)
    (tmp_path / NAMES["risk"]).write_bytes(b"\xff\xfe\x00garbage")

    assert _load(batch).status == "ok"


def test_a_sibling_of_absurdly_nested_json_does_not_crash_a_good_read(tmp_path: Path) -> None:
    """``json.loads`` raises RecursionError, not ValueError, on deep nesting."""
    batch = _write(tmp_path, "batch", pair=2.0)
    (tmp_path / NAMES["risk"]).write_text("[" * 200_000 + "]" * 200_000)

    assert _load(batch).status == "ok"


def test_a_sibling_that_raises_a_permission_error_does_not_crash_a_good_read(tmp_path: Path) -> None:
    """``Path.exists`` re-raises EACCES before Python 3.13, ahead of the reader's own OSError handling."""
    from trw_mcp.tools._sidecar_pair import pair_from_two_builds

    batch = _write(tmp_path, "batch", pair=2.0)

    def deny(_path: Path) -> dict[str, Any] | None:
        raise PermissionError(13, "Permission denied")

    assert pair_from_two_builds(batch, {"pair_generation": 2.0}, deny) is False

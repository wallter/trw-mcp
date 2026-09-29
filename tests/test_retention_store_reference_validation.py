"""PRD-FIX-157-FR02: `retention_store.add_reference` validates its inputs.

Round 1 review found a real production caller this PRD's original caller
census missed: ``scripts/_runtime_retention.py``'s quarantine flow (outside
``trw-mcp/src``), so `add_reference` is kept rather than deleted (V03's
alternative). Its `digest`/`reference_id` parameters build path components
with no traversal check -- this module proves the fix closes that path and
that the function's existing, valid-input behavior (including the served
`scripts/_runtime_retention.py` quarantine path) is unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests._layout import requires_monorepo
from trw_mcp.telemetry.retention_store import REFS_RELATIVE_DIR, add_reference, store_payload

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def test_add_reference_rejects_path_traversal_reference_id(tmp_path: Path) -> None:
    """A '../../escape'-shaped reference_id must not write outside the refs dir."""
    receipt = store_payload(tmp_path, b"payload\n")

    with pytest.raises(ValueError, match="safe path segment"):
        add_reference(tmp_path, receipt.digest, "../../escape")

    escape_target = tmp_path.parent.parent / "escape.json"
    assert not escape_target.exists(), "a traversal reference_id must write nothing"
    refs_root = tmp_path / REFS_RELATIVE_DIR
    assert not refs_root.exists() or not any(refs_root.rglob("escape.json"))


def test_add_reference_rejects_malformed_digest(tmp_path: Path) -> None:
    """A digest outside the sha256:<64-hex> form store_payload always produces is refused."""
    with pytest.raises(ValueError, match="digest must match"):
        add_reference(tmp_path, "sha256:../x", "receipt-1")


@pytest.mark.parametrize(
    "digest",
    [
        "sha256:" + "g" * 64,  # non-hex char
        "sha256:" + "a" * 63,  # too short
        "md5:" + "a" * 64,  # wrong scheme
        "",
        "sha256:" + "a" * 64 + "\n",  # trailing newline: `$` in re.match matches before it
    ],
)
def test_add_reference_rejects_various_malformed_digests(tmp_path: Path, digest: str) -> None:
    with pytest.raises(ValueError, match="digest must match"):
        add_reference(tmp_path, digest, "receipt-1")


@pytest.mark.parametrize(
    "reference_id",
    [
        "receipt-1\n",  # trailing newline
        "receipt-1\r",  # trailing carriage return
        "recei\x1bpt-1",  # embedded control character (ESC)
        "receipt-1\x7f",  # DEL
    ],
)
def test_add_reference_rejects_control_characters_in_reference_id(tmp_path: Path, reference_id: str) -> None:
    """A reference_id carrying a trailing newline or any control byte is refused."""
    receipt = store_payload(tmp_path, b"payload\n")

    with pytest.raises(ValueError, match="safe path segment"):
        add_reference(tmp_path, receipt.digest, reference_id)


def test_add_reference_accepts_a_real_digest_and_ref_id_style_id(tmp_path: Path) -> None:
    """Valid input (a real store_payload digest, a flattened _ref_id-style id) still writes."""
    receipt = store_payload(tmp_path, b"payload\n")

    ref_file = add_reference(tmp_path, receipt.digest, ".trw__runtime__foo.db-wal")

    assert ref_file.is_file()
    assert ref_file.read_text(encoding="utf-8").strip().endswith("}")
    assert ref_file.is_relative_to(tmp_path / REFS_RELATIVE_DIR)


@requires_monorepo
def test_runtime_retention_quarantine_candidates_succeeds_with_one_candidate(tmp_path: Path) -> None:
    """The served scripts/_runtime_retention.py quarantine path still works end to end."""
    from scripts._runtime_retention import quarantine_candidates
    from scripts.trw_runtime_hygiene import RuntimeCandidate

    source = tmp_path / "some" / "sidecar.db-wal"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"wal-bytes\n")
    candidate = RuntimeCandidate(
        path="some/sidecar.db-wal", reason="stale_sidecar", size_bytes=source.stat().st_size, action="delete"
    )

    result = quarantine_candidates(tmp_path, [candidate], now_epoch_days=200)

    assert result["quarantined"] == ["some/sidecar.db-wal"]
    assert not source.exists(), "quarantine moves the original file into content-addressed storage"

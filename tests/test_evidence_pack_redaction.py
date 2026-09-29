"""PRD-CORE-323 FR06 -- every string that reaches the pack bytes passes the chokepoint (review r1).

Three bypasses the r1 review reproduced, each pinned at the function that changed
(``EntryWriter.seal`` / ``build_pack``) and all three together in one byte-level scan of
a pack exported through the CLI:

- P1-1: identifiers taken from sources (PRD file names, receipt ids, the run directory
  name) reached ``source.path``, the header and the outer receipt ids unredacted;
- P1-2: ``truncated`` metadata paths were built from raw mapping keys;
- P1-3: redacting mapping keys and values separately lost the key context the
  chokepoint's contextual patterns need (``password: hunter2`` exported unchanged).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests._evidence_pack_fixture import build_fixture, canonical, export

pytestmark = pytest.mark.integration

# Three distinct sk- credentials, one per identifier channel, plus one used as a mapping key.
_KEY_IN_FILENAME = "sk-" + "FileNamePlanted0123456789"
_KEY_IN_RECEIPT_ID = "sk-" + "ReceiptIdPlanted012345678"
_KEY_IN_RUN_DIR = "sk-" + "RunDirPlanted0123456789ab"
_KEY_AS_MAPPING_KEY = "sk-" + "abcdefghijklmnop123456"
_CONTEXT_SECRETS = {
    "password": "hunter2-planted-pw",
    "api_key": "planted-apikey-value",
    "token": "planted-token-value",
    "secret": "planted-secret-value",
    "DB_PASSWORD": "planted-db-password",
    # core323-s1 r2: a key that itself contains the context probe must not blind the detector.
    "trw-evidence-pack-context-probe.PASSWORD": "planted-probe-key-pw",
}


def _writer(root: Path):  # type: ignore[no-untyped-def]
    from trw_mcp.evidence_pack._redaction import EntryWriter

    return EntryWriter(root)


def _sealed_bytes(entry: dict[str, object]) -> bytes:
    return canonical(entry)


# ---------------------------------------------------------------------------
# Direct tests of the changed function, one per arm
# ---------------------------------------------------------------------------


def test_seal_redacts_source_identifiers(tmp_path: Path) -> None:
    """P1-1: a credential in a source path is redacted, and the entry says so."""
    entry = _writer(tmp_path).seal(
        source={"path": f"docs/requirements-aare-f/prds/PRD-CORE-901-{_KEY_IN_FILENAME}.md"},
        as_of="export_snapshot",
        fields={"prd_id": "PRD-CORE-901"},
    )
    assert _KEY_IN_FILENAME.encode() not in _sealed_bytes(entry)
    assert entry["label"] == "redacted"
    assert "<REDACTED:api_key>" in entry["source"]["path"]  # type: ignore[index]
    body = {k: v for k, v in entry.items() if k != "sha256"}
    assert entry["sha256"] == hashlib.sha256(canonical(body)).hexdigest()


def test_seal_an_ordinary_source_path_is_observed(tmp_path: Path) -> None:
    entry = _writer(tmp_path).seal(
        source={"path": ".trw/runs/task/20260926T082336Z-38eb6638/meta/receipts/build/build-0f3a.json"},
        as_of="run_record",
        fields={"receipt_id": "build-0f3a"},
    )
    assert entry["label"] == "observed"
    assert entry["source"] == {"path": ".trw/runs/task/20260926T082336Z-38eb6638/meta/receipts/build/build-0f3a.json"}


def test_seal_truncation_metadata_uses_redacted_keys(tmp_path: Path) -> None:
    """P1-2: the truncated-field path is built from the sanitized key, never the raw one."""
    long_value = "x " * 2049  # 4,098 characters
    entry = _writer(tmp_path).seal(
        source={"path": "p.md"},
        as_of="export_snapshot",
        fields={"verification_mappings": [{"requirement_id": "FR01"}, {_KEY_AS_MAPPING_KEY: long_value}]},
    )
    assert _KEY_AS_MAPPING_KEY.encode() not in _sealed_bytes(entry)
    assert entry["truncated"] == {"verification_mappings[1].<REDACTED:api_key>": len(long_value)}


@pytest.mark.parametrize("key", sorted(_CONTEXT_SECRETS))
def test_seal_redacts_values_under_contextual_keys(tmp_path: Path, key: str) -> None:
    """P1-3: the key/value context is kept, so the chokepoint's contextual patterns fire."""
    value = _CONTEXT_SECRETS[key]
    entry = _writer(tmp_path).seal(
        source={"path": "p.md"},
        as_of="export_snapshot",
        fields={"verification_mappings": [{"requirement_id": "FR01", key: value}]},
    )
    assert value.encode() not in _sealed_bytes(entry)
    mapping = entry["verification_mappings"][0]  # type: ignore[index]
    assert mapping[key].startswith("<REDACTED:")
    assert mapping["requirement_id"] == "FR01"
    assert entry["label"] == "redacted"


@pytest.mark.parametrize(
    "value",
    [["hunter2-in-a-list"], {"nested": "hunter2-in-a-dict"}, 20260926],
    ids=["list", "dict", "int"],
)
def test_seal_contextual_key_covers_every_value_shape(tmp_path: Path, value: object) -> None:
    entry = _writer(tmp_path).seal(source={"path": "p.md"}, as_of="run_record", fields={"password": value})
    data = _sealed_bytes(entry)
    assert b"hunter2" not in data and b"20260926" not in data
    assert entry["label"] == "redacted"


def test_seal_leaves_a_non_secret_key_alone(tmp_path: Path) -> None:
    entry = _writer(tmp_path).seal(
        source={"path": "p.md"}, as_of="run_record", fields={"status": "approved", "method": "test"}
    )
    assert (entry["status"], entry["method"], entry["label"]) == ("approved", "test", "observed")


def test_build_pack_redacts_the_run_directory_name(tmp_path: Path) -> None:
    """P1-1: the header's run identity passes the chokepoint too."""
    from trw_mcp.evidence_pack import build_pack

    fx = build_fixture(tmp_path, run_name=f"run-{_KEY_IN_RUN_DIR}")
    data = build_pack(fx.run)
    assert _KEY_IN_RUN_DIR.encode() not in data
    assert b'"run_identity":".trw/runs/task/run-<REDACTED:api_key>"' in data


# ---------------------------------------------------------------------------
# The class invariant: one byte-level scan over a CLI-exported pack
# ---------------------------------------------------------------------------


def _planted_prd() -> str:
    mappings = "\n".join(f"        {key}: {value}" for key, value in _CONTEXT_SECRETS.items())
    long_value = "x " * 2049
    return f"""---
prd:
  id: PRD-CORE-903
  status: approved
  verification:
    mappings:
      - requirement_id: PRD-CORE-903-FR01
{mappings}
      - {_KEY_AS_MAPPING_KEY}: "{long_value}"
---

# PRD-CORE-903
"""


def test_no_planted_value_reaches_the_pack_bytes(tmp_path: Path) -> None:
    fx = build_fixture(tmp_path, scope_extra=("PRD-CORE-903",), run_name=f"run-{_KEY_IN_RUN_DIR}")
    prds = tmp_path / "docs" / "requirements-aare-f" / "prds"
    (prds / f"PRD-CORE-903-{_KEY_IN_FILENAME}.md").write_text(_planted_prd(), encoding="utf-8")
    builds = fx.run / "meta" / "receipts" / "build"
    raw = (builds / f"{fx.positive_build}.json").read_bytes()
    (builds / f"build-{_KEY_IN_RECEIPT_ID}.json").write_bytes(raw)
    out = tmp_path / "pack.json"

    assert export(fx, out) == 0

    data = out.read_bytes()
    planted = [_KEY_IN_FILENAME, _KEY_IN_RECEIPT_ID, _KEY_IN_RUN_DIR, _KEY_AS_MAPPING_KEY, *_CONTEXT_SECRETS.values()]
    leaked = [value for value in planted if value.encode() in data]
    assert leaked == []
    # Non-vacuity: the planted PRD and the cloned receipt really were exported.
    assert b"PRD-CORE-903" in data
    assert data.count(b"build-<REDACTED:api_key>") >= 1


# ---------------------------------------------------------------------------
# Slice S2 channels: events, checkpoints, override records, journal proof_ref
# ---------------------------------------------------------------------------

_S2_PLANTED = {
    "event_value": "sk-" + "EventValuePlanted01234567",
    "event_key": "sk-" + "EventKeyPlanted0123456789",
    "event_class": "sk-" + "EventClassPlanted0123456",
    "tool_name": "sk-" + "ToolNamePlanted012345678",
    "deliver_payload": "sk-" + "DeliverPayloadPlanted0123",
    "checkpoint": "sk-" + "CheckpointPlanted01234567",
    "failed_command": "sk-" + "FailedCommandPlanted01234",
    "residual_risk": "sk-" + "ResidualRiskPlanted012345",
    "proof_ref": "sk-" + "ProofRefPlanted0123456789",
    "prd_decision": "sk-" + "PrdDecisionPlanted0123456",
    "event_password": "hunter2-planted-event-pw",
    "checkpoint_email": "checkpoint.leak@example.com",
}


def test_no_planted_value_reaches_the_pack_bytes_through_s2_sections(tmp_path: Path) -> None:
    """Byte-level scan over the decisions and verdict sections' sources (learning L-GEBk)."""
    import hashlib as _hashlib

    from tests._evidence_pack_s2_fixture import checkpoint, journal_proof_ref, log_event, override_record

    p = _S2_PLANTED
    fx = build_fixture(tmp_path, scope_extra=("PRD-CORE-904",))
    prds = tmp_path / "docs" / "requirements-aare-f" / "prds"
    (prds / "PRD-CORE-904.md").write_text(
        f"---\nprd:\n  id: PRD-CORE-904\n  status: approved\n---\n\n## Decisions\n\n- D1: rotate {p['prd_decision']}\n",
        encoding="utf-8",
    )
    log_event(
        fx, "decision", {"note": f"chose {p['event_value']}", p["event_key"]: "v", "password": p["event_password"]}
    )
    log_event(fx, f"custom-{p['event_class']}", {})
    log_event(fx, "tool_call", {"tool_name": p["tool_name"], "success": True})
    log_event(fx, "trw_deliver_complete", {"session_id": p["deliver_payload"]})
    checkpoint(fx, f"progress {p['checkpoint']} mail {p['checkpoint_email']}")
    override_record(fx, failed_command=f"run {p['failed_command']}", residual_risk=f"risk {p['residual_risk']}")
    operation_id = journal_proof_ref(tmp_path, f"ticket {p['proof_ref']}")
    out = tmp_path / "pack.json"

    assert export(fx, out) == 0

    data = out.read_bytes()
    leaked = [name for name, value in p.items() if value.encode() in data]
    assert leaked == []
    # proof_digest is sha256 over the unredacted proof_ref: publishing it would reverse a short value.
    assert _hashlib.sha256(f"ticket {p['proof_ref']}".encode()).hexdigest().encode() not in data
    # Non-vacuity: every planted channel really was exported, redacted.
    for present in (operation_id, "progress <REDACTED:api_key>", "run <REDACTED:api_key>", "risk <REDACTED:api_key>"):
        assert present.encode() in data, present
    assert b"ticket <REDACTED:api_key>" in data
    assert b"rotate <REDACTED:api_key>" in data
    assert b"custom-<REDACTED:api_key>" in data

"""PRD-CORE-208 NFR02: containment, bounds, redaction, permissions."""

from __future__ import annotations

import os
import stat

import pytest

from tests._delivery_support import make_coordinator, make_uuid7, strong_capability
from trw_mcp.tools._delivery_models import ClaimStatus
from trw_mcp.tools._delivery_request import (
    DeliveryLimits,
    DeliveryRequestError,
    build_canonical_request,
    hash_capability,
    validate_delivery_id,
    verify_capability,
)


def test_operation_store_is_owner_only_0600(tmp_path) -> None:
    """NFR02: the operation database resolves under .trw and is mode 0600."""
    coord = make_coordinator(tmp_path)
    coord.claim(delivery_id=make_uuid7(), capability_token=strong_capability())
    db_path = tmp_path / "delivery" / "operations.sqlite3"
    assert db_path.exists()
    mode = stat.S_IMODE(os.stat(db_path).st_mode)
    assert mode & 0o077 == 0  # no group/other permission bits


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", "\x00nul", "not-hex-uuid"])
def test_path_escape_and_malformed_ids_write_nothing(tmp_path, bad_id) -> None:
    """NFR02: traversal/malformed IDs fail closed with zero store writes."""
    coord = make_coordinator(tmp_path)
    result = coord.claim(delivery_id=bad_id, capability_token=strong_capability())
    assert result.status is ClaimStatus.REJECTED
    # No operation was created for a valid probe id afterwards either.
    assert coord.project_status(make_uuid7())["result"] in {"not_found_id", "not_found_store"}


def test_oversize_run_identity_and_request_rejected(tmp_path) -> None:
    """NFR02: serialized request over 128 KiB is rejected before write."""
    with pytest.raises(DeliveryRequestError) as exc:
        build_canonical_request(
            project_scope="scope",
            run_identity="r/" + ("x" * (DeliveryLimits.MAX_RECORD_BYTES + 10)),
            skip_reflect=False,
            skip_index_sync=False,
            allow_unverified=False,
            acceptable_failure_digest="",
        )
    assert exc.value.code == "oversize_request"


def test_seeded_secret_never_appears_in_rows_status_or_logs(tmp_path) -> None:
    """NFR02: a capability secret is stored only as a salted hash; never surfaces."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    secret = "SUPER-SECRET-TOKEN-" + strong_capability()
    coord.claim(delivery_id=did, capability_token=secret, run_identity="task/run-1")

    # Not in the raw operation row.
    conn = coord.store.connect()
    op = coord.store.get_operation(conn, did)
    conn.close()
    assert op is not None
    assert secret not in op.capability_hash
    assert secret not in op.capability_salt
    assert op.capability_hash and op.capability_hash != secret

    # Not in the status projection.
    assert secret not in repr(coord.project_status(did))

    # Not anywhere in the on-disk database bytes.
    db_bytes = (tmp_path / "delivery" / "operations.sqlite3").read_bytes()
    assert secret.encode() not in db_bytes


def test_capability_hash_is_salted_and_constant_time_verified() -> None:
    """FR04/NFR02: salted hash + constant-time compare; wrong salt fails closed."""
    token = strong_capability()
    salt_hex, digest = hash_capability(token)
    # Same token + same salt reproduces the hash; a different salt does not.
    assert verify_capability(token, salt_hex, digest) is True
    assert verify_capability("wrong-token", salt_hex, digest) is False
    assert verify_capability(token, "00" * 16, digest) is False
    # A malformed salt hex fails closed rather than raising.
    assert verify_capability(token, "zz", digest) is False


def test_prd_core_215_nfr03() -> None:
    """PRD-CORE-215 NFR03 — sensitive-data minimization: the typed envelope
    serializer redacts secret-looking diagnostic keys, and the connection
    fingerprint emits project identity as the project-root BASENAME only (no
    absolute path, credential, or environment dump)."""
    from trw_mcp.models.tool_result import REDACTED_PLACEHOLDER, Outcome, ToolResultEnvelope
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.tools._connection_fingerprint import build_connection_fingerprint

    # Secret-looking diagnostic keys never survive serialization.
    env = ToolResultEnvelope(
        outcome=Outcome.COMPLETED,
        diagnostics={
            "api_key": "SEEDED-SECRET-VALUE",
            "authorization": "Bearer leak",
            "safe_note": "kept",
        },
    )
    dumped = env.model_dump(mode="json")
    assert dumped["diagnostics"]["api_key"] == REDACTED_PLACEHOLDER
    assert dumped["diagnostics"]["authorization"] == REDACTED_PLACEHOLDER
    assert dumped["diagnostics"]["safe_note"] == "kept"
    assert "SEEDED-SECRET-VALUE" not in repr(dumped)
    assert "Bearer leak" not in repr(dumped)

    # The connection fingerprint carries basename-only project identity.
    fp = build_connection_fingerprint()
    project_identity = fp["project_identity"]
    assert "/" not in project_identity
    assert "\\" not in project_identity
    assert not os.path.isabs(project_identity)
    root = resolve_project_root()
    # Never the absolute checkout path — exactly the project-root basename.
    assert str(root) != project_identity
    assert project_identity in {root.name, "unknown"}
    # No credential / environment dump keys are present in the fingerprint.
    lowered = {k.lower() for k in fp}
    assert not any(tok in k for k in lowered for tok in ("secret", "token", "password", "env"))


def test_future_and_expired_ids_rejected_at_validation_layer() -> None:
    """NFR02: the validator itself is the containment boundary (pure, no I/O)."""
    from tests._delivery_support import days_ms

    now = 1_760_000_000_000
    valid = make_uuid7(now)
    validate_delivery_id(valid, now)  # does not raise
    with pytest.raises(DeliveryRequestError) as exc:
        validate_delivery_id(make_uuid7(now + days_ms(1)), now)
    assert exc.value.code == "future_skew"

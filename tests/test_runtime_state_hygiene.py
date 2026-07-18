from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_checker() -> object:
    script = Path(__file__).resolve().parents[2] / "scripts" / "check_trw_runtime_state.py"
    spec = importlib.util.spec_from_file_location("check_trw_runtime_state", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_trw_runtime_classifier_documents_path_tiers() -> None:
    checker = _load_checker()

    assert checker.classify_trw_path(".trw/frameworks/VERSION.yaml") == "canonical"
    assert checker.classify_trw_path(".trw/config.yaml") == "canonical"
    assert checker.classify_trw_path(".trw/compliance/reviews/2026/05/review.yaml") == "audit"
    assert checker.classify_trw_path(".trw/runtime/pins.json") == "ephemeral"
    assert checker.classify_trw_path(".trw/context/session-events.jsonl") == "ephemeral"
    assert checker.classify_trw_path(".trw/security/rate_limits.yaml") == "ephemeral"
    assert checker.classify_trw_path("trw-mcp/src/trw_mcp/state/_paths.py") == "outside_trw"


def test_precommit_check_rejects_ephemeral_trw_paths(capsys: object) -> None:
    checker = _load_checker()

    status = checker.main([".trw/runtime/pins.json", ".trw/frameworks/VERSION.yaml"])

    assert status == 1
    captured = capsys.readouterr()
    assert ".trw/runtime/pins.json" in captured.err
    assert ".trw/frameworks/VERSION.yaml" not in captured.err


def test_precommit_check_accepts_canonical_and_audit_paths() -> None:
    checker = _load_checker()

    assert checker.main([".trw/frameworks/VERSION.yaml", ".trw/compliance/reviews/2026/05/review.yaml"]) == 0


# ---------------------------------------------------------------------------
# PRD-CORE-181 FR01/FR03: fail-closed retention registry + content store
# ---------------------------------------------------------------------------


def test_prd_core_181_fr01(tmp_path) -> None:
    """FR01 acceptance: Given registered, unknown, unreadable, conflicting,
    referenced, sensitive, and authoritative fixtures, When retention
    classifies, Then only current valid unreferenced eligible artifacts enter
    cleanup and every uncertain fixture remains retained with reason."""
    from trw_mcp.telemetry.retention_registry import (
        REASON_AUTHORITATIVE,
        REASON_DIGEST_CONFLICT,
        REASON_REFERENCED,
        REASON_REGISTRY_UNREADABLE,
        REASON_SENSITIVE,
        REASON_UNREGISTERED,
        AuthorityClass,
        RetentionClass,
        RetentionDecision,
        RetentionEntry,
        SensitivityClass,
        classify_all,
        classify_artifact,
        digest_file,
        load_registry,
        save_registry,
    )

    root = tmp_path
    names = ["eligible.log", "conflict.log", "referenced.log", "sensitive.log", "authority.yaml"]
    for name in names:
        (root / name).write_text(f"{name}-v1\n", encoding="utf-8")

    def entry(name: str, **overrides: object) -> RetentionEntry:
        base: dict[str, object] = {
            "path": name,
            "authority_class": AuthorityClass.OBSERVATIONAL,
            "producer": "test",
            "owner": "test",
            "sensitivity": SensitivityClass.NONE,
            "retention_class": RetentionClass.BOUNDED_DAYS,
            "digest": digest_file(root / name),
            "retention_days": 7,
            "registered_epoch_days": 100,
        }
        base.update(overrides)
        return RetentionEntry.model_validate(base, strict=False)

    entries = [
        entry("eligible.log"),
        entry("conflict.log"),
        entry("referenced.log", references=("run/one",)),
        entry("sensitive.log", sensitivity=SensitivityClass.SENSITIVE),
        entry("authority.yaml", authority_class=AuthorityClass.AUTHORITATIVE),
    ]
    save_registry(root, entries)
    # Digest conflict: bytes change AFTER registration.
    (root / "conflict.log").write_text("changed\n", encoding="utf-8")

    paths = [*names, "unknown.log"]
    outcomes = {c.path: c for c in classify_all(paths, root, now_epoch_days=200)}

    assert outcomes["eligible.log"].decision is RetentionDecision.ELIGIBLE
    assert outcomes["unknown.log"].reason == REASON_UNREGISTERED
    assert outcomes["conflict.log"].reason == REASON_DIGEST_CONFLICT
    assert outcomes["referenced.log"].reason == REASON_REFERENCED
    assert outcomes["sensitive.log"].reason == REASON_SENSITIVE
    assert outcomes["authority.yaml"].reason == REASON_AUTHORITATIVE
    for path, c in outcomes.items():
        if path != "eligible.log":
            assert c.decision is RetentionDecision.RETAINED
            assert c.reason  # every uncertain fixture carries a typed reason

    # Window not yet expired -> retained (fail-closed on time too).
    fresh = classify_artifact("eligible.log", root, entries, now_epoch_days=101)
    assert fresh.decision is RetentionDecision.RETAINED

    # Unreadable registry retains EVERYTHING (fail-closed).
    registry_file = root / ".trw" / "retention" / "registry.json"
    registry_file.write_text("{not json", encoding="utf-8")
    loaded, readable = load_registry(root)
    assert loaded == [] and readable is False
    broken = classify_all(["eligible.log"], root, now_epoch_days=200)
    assert broken[0].decision is RetentionDecision.RETAINED
    assert broken[0].reason == REASON_REGISTRY_UNREADABLE

    # The fail-open SurfaceRegistry is never consulted as deletion authority:
    # no import of artifact_registry exists in the retention module namespace.
    import trw_mcp.telemetry.retention_registry as registry_module

    assert "artifact_registry" not in dir(registry_module)
    import_lines = [
        line
        for line in Path(registry_module.__file__ or "").read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert not any("artifact_registry" in line for line in import_lines)


def test_prd_core_181_fr03(tmp_path) -> None:
    """FR03 acceptance: Given duplicate payloads, unstable reads, collisions,
    and metadata references, When stored concurrently, Then one canonical blob
    exists for valid bytes, references resolve, and every uncertain case
    remains non-destructive and non-collectible."""
    import os

    from trw_mcp.telemetry.retention_store import (
        StoreOutcome,
        add_reference,
        is_collectible,
        resolve_reference,
        store_payload,
    )

    root = tmp_path
    payload = b"snapshot-bytes\n"

    first = store_payload(root, payload)
    assert first.outcome is StoreOutcome.STORED
    second = store_payload(root, payload)
    assert second.outcome is StoreOutcome.DEDUPLICATED
    assert second.blob_path == first.blob_path  # ONE canonical blob per digest
    blob = resolve_reference(root, first.digest)
    assert blob is not None and blob.read_bytes() == payload

    # Reference: a referenced blob is never collectible.
    add_reference(root, first.digest, "receipt-1")
    collectible, reason = is_collectible(root, first.digest)
    assert collectible is False and reason == "blob_referenced"

    # Collision (same digest path, different bytes = tampering): retain, block.
    os.chmod(blob, 0o644)
    blob.write_bytes(b"tampered\n")
    tampered = store_payload(root, payload)
    assert tampered.outcome is StoreOutcome.COLLISION_BLOCKED
    assert blob.read_bytes() == b"tampered\n"  # non-destructive: nothing overwritten
    assert resolve_reference(root, first.digest) is None  # typed absence
    collectible, reason = is_collectible(root, first.digest)
    assert collectible is False and reason == "blob_missing_or_corrupt"

    # Missing reference target resolves to typed absence, not a guess.
    assert resolve_reference(root, "sha256:" + "0" * 64) is None


def test_prd_core_181_fr07(tmp_path) -> None:
    """FR07 acceptance: Given fresh, stale, missing, locked, corrupt, and
    post-cleanup fixtures, When health runs, Then each has correct state and
    remediation without pass from absent evidence."""
    import json
    import os

    from trw_mcp.state._helpers import maintenance_health

    root = tmp_path
    now = 1_700_000_000.0

    # MISSING everything -> unknown (never a pass), each with remediation.
    empty = maintenance_health(root, now=now)
    by_name = {c["component"]: c for c in empty["components"]}
    assert empty["healthy"] is False
    assert by_name["inventory"]["state"] == "unknown" and by_name["inventory"]["remediation"]
    assert by_name["last_cleanup"]["state"] == "unknown"
    assert by_name["memory_wal"]["state"] == "unknown"
    assert by_name["quarantine_restore"]["state"] == "unknown"

    # FRESH inventory + post-cleanup receipt + registry + quarantine + db.
    retention = root / ".trw" / "retention"
    retention.mkdir(parents=True)
    inventory = retention / "inventory.json"
    inventory.write_text(json.dumps({"file_count": 1}), encoding="utf-8")
    os.utime(inventory, (now - 3600, now - 3600))
    cleanup = retention / "cleanup-receipt.json"
    cleanup.write_text(json.dumps({"collected": []}), encoding="utf-8")
    os.utime(cleanup, (now - 3600, now - 3600))
    # FR07 fix: quarantine_restore must count real pending restorables (meta
    # sidecars), not the fixed top-level dir entries. Two payloads pending.
    quarantine_meta = retention / "quarantine" / "meta"
    quarantine_meta.mkdir(parents=True)
    (quarantine_meta / "a.json").write_text("{}", encoding="utf-8")
    (quarantine_meta / "b.json").write_text("{}", encoding="utf-8")
    (root / ".trw" / "memory").mkdir()
    (root / ".trw" / "memory" / "memory.db").write_bytes(b"db")

    from trw_mcp.telemetry.retention_registry import (
        AuthorityClass,
        RetentionClass,
        RetentionEntry,
        SensitivityClass,
        save_registry,
    )

    save_registry(
        root,
        [
            RetentionEntry.model_validate(
                {
                    "path": "x.log",
                    "authority_class": AuthorityClass.OBSERVATIONAL,
                    "producer": "t",
                    "owner": "t",
                    "sensitivity": SensitivityClass.NONE,
                    "retention_class": RetentionClass.BOUNDED_DAYS,
                    "digest": "sha256:" + "0" * 64,
                },
                strict=False,
            )
        ],
    )
    healthy = maintenance_health(root, now=now)
    by_name = {c["component"]: c for c in healthy["components"]}
    assert by_name["inventory"]["state"] == "fresh"
    assert by_name["last_cleanup"]["state"] == "fresh"
    assert by_name["memory_wal"]["state"] == "ok"
    assert by_name["retention_registry"]["state"] == "ok"
    # FR07: 'ok' is honest only because cleanup actually consults the registry.
    assert "deletion_authority_wired=True" in by_name["retention_registry"]["detail"]
    # FR07: pending restorables reflect the two meta sidecars, not a constant 2-dirs.
    assert by_name["quarantine_restore"]["detail"] == "pending_restorables=2"
    assert healthy["healthy"] is True

    # STALE inventory (older than the freshness window).
    os.utime(inventory, (now - 30 * 86400, now - 30 * 86400))
    stale = maintenance_health(root, now=now)
    by_name = {c["component"]: c for c in stale["components"]}
    assert by_name["inventory"]["state"] == "stale" and by_name["inventory"]["remediation"]

    # LOCKED: explicit lock file on the memory db.
    (root / ".trw" / "memory" / "memory.db.lock").write_text("held")
    locked = maintenance_health(root, now=now)
    by_name = {c["component"]: c for c in locked["components"]}
    assert by_name["memory_wal"]["state"] == "locked"

    # CORRUPT: unreadable cleanup receipt is corrupt, not a pass.
    cleanup.write_text("{not json", encoding="utf-8")
    corrupt = maintenance_health(root, now=now)
    by_name = {c["component"]: c for c in corrupt["components"]}
    assert by_name["last_cleanup"]["state"] == "corrupt" and by_name["last_cleanup"]["remediation"]
    assert corrupt["healthy"] is False


def test_prd_core_181_nfr03(tmp_path, monkeypatch) -> None:
    """NFR03 local-first privacy: maintenance performs NO network transfer by
    default and never leaks sensitive payload bytes into its report."""
    import json as _json
    import socket

    from trw_mcp.state._helpers import maintenance_health

    root = tmp_path
    secret = "SENSITIVE-TOKEN-do-not-egress-0xDEADBEEF"
    (root / ".trw" / "memory").mkdir(parents=True)
    # A memory.db carrying secret bytes plus an active WAL sidecar.
    (root / ".trw" / "memory" / "memory.db").write_text(secret, encoding="utf-8")
    (root / ".trw" / "memory" / "memory.db-wal").write_text(secret + secret, encoding="utf-8")

    # Network fixture: any socket attempt is a hard failure. Maintenance must
    # still complete — proving zero off-machine transfer by default.
    def _no_network(*args: object, **kwargs: object) -> object:
        raise AssertionError("maintenance attempted a network transfer")

    monkeypatch.setattr(socket, "socket", _no_network)

    health = maintenance_health(root, now=1_700_000_000.0)

    # Secret fixture: the report describes STATE (counts/paths), never payloads.
    serialized = _json.dumps(health)
    assert secret not in serialized
    by_name = {c["component"]: c for c in health["components"]}
    # WAL health reports only a byte COUNT, not the WAL contents.
    assert by_name["memory_wal"]["state"] == "ok"
    assert by_name["memory_wal"]["detail"] == f"wal_bytes={len(secret + secret)}"

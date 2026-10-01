"""E2E-INC-118 (property form): a real store's export -> import into a fresh store -> export keeps every learning field.

``test_export_import_round_trip.py`` drives hand-written export rows. This drives the other end too: learnings written
through the real write path into a SOURCE store (a seeded generator, so every run is reproducible and a failure names
its seed), exported, imported into a second, empty store, and exported again. Then every field the export carries is
accounted for: it comes back equal, or it is named in ``PER_STORE`` with the reason it is deliberately not adopted. A
field the export gains later that appears in neither list fails here until someone decides which it is.

Each generated store holds every type, every status the import can give a learning, same-summary learnings that differ
in detail, and supersession links, and the destination must hold exactly as many learnings as the source did.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from tests._memory_fixtures import DaemonCheckout, MemoryDaemon, attach_checkout

pytestmark = pytest.mark.integration

#: Fields the round trip must keep: what a learning says about itself. Compared as exported, after mapping ids.
KEPT = (
    "summary",
    "detail",
    "type",
    "status",
    "impact",
    "confidence",
    "evidence_level",
    "nudge_line",
    "expires",
    "task_type",
    "domain",
    "phase_origin",
    "phase_affinity",
    "team_origin",
    "evidence",
    "client_profile",
    "model_id",
    "superseded",
    "invalidated_by",
    "assertions",
)
#: Exported fields the import deliberately does not carry over, each with its reason.
PER_STORE = {
    "id": "import re-keys: a forced id would overwrite another learning (E2E-INC-076); kept as an imported-id: tag",
    "source_type": "an unsigned file must not claim human provenance (E2E-INC-076); kept as an imported-source: tag",
    "source_identity": "names the file's project on the importing side",
    "protection_tier": "an unsigned file must not grant a learning protection (E2E-INC-076)",
    "tags": "compared separately: the imported-id:/imported-source: provenance tags are added, none may be lost",
    "created": "a per-store timestamp: the import creates the row now",
    "updated": "a per-store timestamp",
    "last_accessed_at": "runtime history of the source store",
    "access_count": "runtime history of the source store",
    "recall_count": "runtime history of the source store",
    "session_count": "runtime history of the source store",
    "recurrence": "runtime history of the source store",
    "outcome_history": "runtime history of the source store",
    "shard_id": "names the source session's shard",
    "namespace": "the store the row lives in",
    "origin_project": "the store the row lives in",
    "remote_id": "the store the row lives in",
    "anchors": "generated against the importing project's code, never imported",
    "anchor_validity": "generated against the importing project's code, never imported",
    "verification_status": "the importing project re-verifies",
    "verification_checked_at": "the importing project re-verifies",
    "verified_against": "the importing project re-verifies",
}

_SUBJECTS = (
    "postgres vacuum",
    "redis eviction",
    "kafka consumer groups",
    "terraform state",
    "docker layer cache",
    "github actions runners",
    "nginx upstream keepalive",
    "celery retries",
    "sqlite wal checkpoints",
    "kubernetes probes",
    "grpc deadlines",
    "s3 multipart uploads",
    "cron jitter",
    "tls session tickets",
    "oauth token refresh",
    "pytest fixtures",
    "mypy plugins",
    "rust lifetimes",
    "react suspense",
    "webpack chunking",
    "dns ttl",
    "ntp drift",
    "raid rebuilds",
    "zfs snapshots",
)
_EFFECTS = (
    "stalls under sustained write load",
    "hides the failure until the second deploy",
    "doubles the latency of the first request",
    "drops messages when the queue is full",
    "leaks file descriptors after a reload",
    "silently skips the health check",
    "needs an explicit timeout to fail fast",
    "behaves differently on a cold cache",
)
_TYPES = ("incident", "pattern", "convention", "hypothesis", "workaround", "decision")
_CONFIDENCE = ("unverified", "low", "medium", "high")
_EVIDENCE_LEVELS = ("observed", "verified", "inferred", "unknown")
_PHASES = ("research", "plan", "implement", "validate", "review", "deliver")
_STATUS_AFTER_WRITE = ("resolved", "obsolete", "obsolete_poisoned")


def _generate(seed: int, count: int = 14) -> list[dict[str, Any]]:
    """*count* distinct learnings for ``execute_learn``; the first two share a summary, the next two do too."""
    rng = random.Random(seed)
    subjects = rng.sample(_SUBJECTS, count)
    rows: list[dict[str, Any]] = []
    for index, subject in enumerate(subjects):
        summary = f"{subject} {rng.choice(_EFFECTS)}"
        if index in (1, 3):  # same headline as the row before, a different finding
            summary = rows[-1]["summary"]
        detail = (
            f"Case {seed}-{index}: {subject} was examined on the {rng.choice(_SUBJECTS)} path. "
            f"{rng.choice(_EFFECTS).capitalize()}; the fix was tried twice and held both times."
        )
        rows.append(
            {
                "summary": summary,
                "detail": detail,
                "type": _TYPES[index % len(_TYPES)],
                "impact": round(rng.choice([0.15, 0.3, 0.45, 0.6, 0.75, 0.9]), 2),
                "confidence": rng.choice(_CONFIDENCE),
                "evidence_level": rng.choice(_EVIDENCE_LEVELS),
                "tags": sorted(rng.sample(["infra", "db", "ci", "net", "perf", "ops", "build"], rng.randint(1, 3))),
                "evidence": [f"run log {seed}-{index}"] if rng.random() < 0.7 else [],
                "nudge_line": f"check {subject.split()[0]} first"[:80] if rng.random() < 0.7 else "",
                "expires": f"2027-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}" if rng.random() < 0.4 else "",
                "task_type": rng.choice(["", "debugging", "refactor", "deploy"]),
                "domain": rng.sample(["backend", "infra", "frontend", "data"], rng.randint(0, 2)),
                "phase_origin": rng.choice(["", *_PHASES]),
                "phase_affinity": rng.sample(_PHASES, rng.randint(0, 2)),
                "team_origin": rng.choice(["", "platform", "data"]),
                "client_profile": rng.choice(["", "claude-code", "codex"]),
                "model_id": rng.choice(["", "tier-a", "tier-b"]),
            }
        )
    rows[5].update(  # substantiated, so the store keeps confidence verified
        confidence="verified",
        evidence_level="observed",
        assertions=[{"type": "glob_exists", "pattern": "", "target": "**/*.md"}],
    )
    return rows


def _seed_source(root: Path, rows: list[dict[str, Any]], seed: int) -> None:
    """Write *rows* through the real write path, then give them every status and two supersession links."""
    from trw_mcp.state._helpers import load_project_config
    from trw_mcp.state._memory_update import update_learning
    from trw_mcp.state._project_root_binding import project_bound
    from trw_mcp.tools._learn_impl import execute_learn

    trw_dir = root / ".trw"
    config = load_project_config(trw_dir)
    rng = random.Random(seed + 1)
    ids: list[str] = []
    with project_bound(root):
        for row in rows:
            outcome = execute_learn(
                row["summary"],
                row["detail"],
                trw_dir,
                config,
                **{k: v for k, v in row.items() if k not in ("summary", "detail")},
            )
            assert outcome["status"] == "recorded", (outcome, row["summary"])
            ids.append(str(outcome["learning_id"]))
        # One row per status the import can restore, plus one superseded pair per seed's shuffle of the rest.
        order = rng.sample(range(len(ids)), len(ids))
        for status, at in zip(_STATUS_AFTER_WRITE, order[:3], strict=True):
            assert "error" not in update_learning(trw_dir, ids[at], status=status)
        for prior, closer in ((order[3], order[4]), (order[5], order[6])):
            assert "error" not in update_learning(trw_dir, ids[closer], supersedes=ids[prior])


def _export(root: Path) -> list[dict[str, Any]]:
    from trw_mcp.export import export_data

    return [dict(row) for row in export_data(root, "learnings")["learnings"]]


def _comparable(row: dict[str, Any], origin: dict[str, str]) -> dict[str, Any]:
    out = {key: row.get(key) for key in KEPT}
    # An assertion's claim travels; its recorded verification result is the importing project's own.
    out["assertions"] = sorted((a["type"], a.get("pattern") or "", a["target"]) for a in row.get("assertions") or [])
    out["superseded"] = bool(row.get("superseded"))
    closer = row.get("invalidated_by")
    out["invalidated_by"] = origin.get(closer, closer) if closer else None
    return out


@pytest.mark.parametrize("seed", [11, 23, 47])
def test_a_generated_store_survives_export_import_export_field_for_field(
    daemon_checkout: DaemonCheckout, memory_daemon: MemoryDaemon, tmp_path: Path, seed: int
) -> None:
    from trw_mcp.export import import_learnings

    source_root = daemon_checkout.trw_dir.parent
    rows = _generate(seed)
    _seed_source(source_root, rows, seed)
    exported = _export(source_root)
    assert len(exported) == len(rows), "the generator's learnings must all have been admitted by the source store"
    assert len({r["summary"] for r in exported}) < len(exported), "the store must hold same-summary learnings"

    source_file = tmp_path / "exp.json"
    source_file.write_text(json.dumps({"metadata": {"project": "source"}, "learnings": exported}), encoding="utf-8")
    target_root = tmp_path / "fresh"
    attach_checkout(target_root / ".trw", memory_daemon)
    result = import_learnings(source_file, target_root)
    assert (result["status"], result["imported"], result["skipped_duplicate"], result["refused"]) == (
        "ok",
        len(exported),
        0,
        0,
    ), result
    assert result["not_restored"] == [], result["not_restored"]

    back = _export(target_root)
    assert len(back) == len(exported), "counts: every learning comes back exactly once"
    origin_of = {
        str(r["id"]): tag.split(":", 1)[1] for r in back for tag in r.get("tags", []) if tag.startswith("imported-id:")
    }
    by_origin = {origin_of[str(r["id"])]: r for r in back}
    assert sorted(by_origin) == sorted(str(r["id"]) for r in exported)

    accounted = set(KEPT) | set(PER_STORE)
    for row in exported:
        assert set(row) <= accounted | {"superseded", "invalidated_by"}, (
            f"export carries {sorted(set(row) - accounted)}: decide whether the round trip keeps it (KEPT) or not (PER_STORE)"
        )
    for row in exported:
        came_back = by_origin[str(row["id"])]
        assert _comparable(came_back, origin_of) == _comparable(row, {}), (seed, row["id"], row["summary"])
        assert set(row.get("tags", [])) <= set(came_back.get("tags", [])), (seed, row["id"])
        assert came_back["source_type"] == "agent" and came_back["protection_tier"] == "normal"


@pytest.mark.parametrize(
    ("exported_status", "expected", "noted"),
    [
        ("resolved", "resolved", False),
        ("obsolete", "obsolete", False),
        ("obsolete_poisoned", "obsolete_poisoned", False),
        ("archived", "obsolete", True),  # a caller cannot archive; obsolete keeps it out of default recall
        ("purged-by-a-future-version", "obsolete", True),
        ("active", "active", False),
        ("", "active", False),
    ],
)
def test_an_exported_non_active_status_never_comes_back_active(
    daemon_checkout: DaemonCheckout, tmp_path: Path, exported_status: str, expected: str, noted: bool
) -> None:
    from trw_mcp.export import import_learnings

    root = daemon_checkout.trw_dir.parent
    row = {
        "id": "L-st001",
        "summary": "Status restore: the nightly compaction job is idempotent",
        "detail": "Re-running the compaction after a crash leaves the same rows, checked on two crashes.",
        "impact": 0.6,
        "status": exported_status,
    }
    source = tmp_path / "status.json"
    source.write_text(json.dumps({"metadata": {"project": "elsewhere"}, "learnings": [row]}), encoding="utf-8")

    result = import_learnings(source, root)

    assert (result["status"], result["imported"]) == ("ok", 1), result
    assert bool(result["not_restored"]) is noted, result["not_restored"]
    (back,) = [r for r in _export(root) if r["summary"] == row["summary"]]
    assert back["status"] == expected


def test_an_unknown_status_is_reported_escaped_and_bounded_never_raw(
    daemon_checkout: DaemonCheckout, tmp_path: Path
) -> None:
    """codex r1 known issue: the not_restored note is printed by the CLI, so a status holding terminal controls must not reach it."""
    from trw_mcp.export import format_import_summary, import_learnings

    root = daemon_checkout.trw_dir.parent
    hostile = "\x1b]0;owned\x07" + "x" * 200
    row = {
        "id": "L-st002",
        "summary": "Status echo: the retry budget resets after a successful call",
        "detail": "Observed on two services; the counter returns to its ceiling once a call succeeds.",
        "impact": 0.6,
        "status": hostile,
    }
    source = tmp_path / "hostile-status.json"
    source.write_text(json.dumps({"metadata": {"project": "elsewhere"}, "learnings": [row]}), encoding="utf-8")

    result = import_learnings(source, root)

    assert (result["status"], result["imported"]) == ("ok", 1), result
    shown = format_import_summary(result)
    assert result["not_restored"] and not any(ch in shown for ch in "\x1b\x07"), shown
    assert len(result["not_restored"][0]) < 120

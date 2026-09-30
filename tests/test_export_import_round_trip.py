"""E2E-INC-118: an export -> import-learnings -> export round trip keeps every learning and what it says about itself.

swarm-e2e S13 (dev31) found the round trip collapsing every non-pattern type to ``pattern``, resurrecting obsolete and
resolved learnings as ``active`` (back into default recall), dropping a distinct learning that shared another's
summary as a "duplicate", and losing which learning superseded which. This drives a real export shape through the
real import into a real memory daemon and re-exports it: each learning comes back once, with its type, status,
lifecycle fields and supersession.

By design (E2E-INC-076) import re-keys: it never forces the exported id and never adopts the exported source_type,
keeping both as ``imported-id:`` / ``imported-source:`` tags. So rows are matched through that tag, a supersession
link is compared after mapping ids, and the provenance tags, id, source and per-store counters are not compared.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._memory_fixtures import DaemonCheckout

pytestmark = pytest.mark.integration

#: Every field a learning states about itself that the round trip must keep (runtime counters, timestamps,
#: namespace/remote ids and the INC-076 provenance pair are per-store facts, not the learning's own).
_KEPT = (
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
)


def _row(ident: str, summary: str, *, type_: str, status: str = "active", **extra: Any) -> dict[str, Any]:
    return {
        "id": ident,
        "summary": summary,
        "detail": f"{summary}. Observed while rebuilding the staging pipeline; reproduced twice with the same inputs.",
        "type": type_,
        "status": status,
        "impact": 0.7,
        "tags": ["round-trip", type_],
        "evidence": [f"run log {ident}"],
        "confidence": "medium",
        "evidence_level": "observed",
        "nudge_line": f"check {type_} first",
        "expires": "2027-01-31",
        "task_type": "debugging",
        "domain": ["infra"],
        "phase_origin": "implement",
        "phase_affinity": ["validate"],
        "team_origin": "platform",
        "source_type": "agent",
        **extra,
    }


_SHARED = "Docker multi-stage builds shrink image size"

ROWS: list[dict[str, Any]] = [
    _row("L-aaa1", "React useEffect cleanup avoids stale subscriptions", type_="incident"),
    _row(
        "L-aaa2",
        "Postgres index bloat is fixed with REINDEX CONCURRENTLY",
        type_="workaround",
        superseded=True,
        invalidated_by="L-aaa3",
    ),
    _row("L-aaa3", "Postgres index bloat is prevented with a fillfactor below 90", type_="decision"),
    _row("L-aaa4", "Git rebase onto an older base silently drops merge commits", type_="convention"),
    _row("L-aaa5", "Terraform state locking needs a DynamoDB table per backend", type_="pattern"),
    _row("L-aaa6", "SQLite WAL mode lets readers proceed during a write", type_="pattern", status="obsolete"),
    _row(
        "L-aaa7", "Kubernetes readiness probes must not call downstream services", type_="convention", status="resolved"
    ),
    _row("L-aaa8", "Pinning the resolver version keeps lockfiles reproducible", type_="hypothesis"),
    _row("L-aaa9", _SHARED, type_="pattern"),
    {
        **_row("L-aab0", _SHARED, type_="pattern"),
        "detail": "A distinct finding under the same headline: the builder stage must not copy the test fixtures.",
        "tags": ["round-trip", "docker", "fixtures"],
    },
]


def _kept(row: dict[str, Any], origin_of: dict[str, str]) -> dict[str, Any]:
    out = {k: row.get(k) for k in _KEPT}
    out["tags"] = sorted(t for t in row.get("tags", []) if not t.startswith(("imported-id:", "imported-source:")))
    out["superseded"] = bool(row.get("superseded"))
    closer = row.get("invalidated_by")
    out["invalidated_by"] = origin_of.get(closer, closer) if closer else None
    return out


def test_every_learning_survives_the_round_trip_with_what_it_says_about_itself(
    daemon_checkout: DaemonCheckout, tmp_path: Path
) -> None:
    from trw_mcp.export import export_data, import_learnings

    source = tmp_path / "exp.json"
    source.write_text(json.dumps({"metadata": {"project": "elsewhere"}, "learnings": ROWS}), encoding="utf-8")
    root = daemon_checkout.trw_dir.parent

    result = import_learnings(source, root)
    assert (result["status"], result["imported"], result["skipped_duplicate"]) == ("ok", len(ROWS), 0), result

    exported = export_data(root, "learnings")["learnings"]
    origin_of = {
        str(row["id"]): tag.split(":", 1)[1]
        for row in exported
        for tag in row.get("tags", [])
        if tag.startswith("imported-id:")
    }
    by_origin = {origin_of[str(row["id"])]: row for row in exported if str(row["id"]) in origin_of}

    assert sorted(by_origin) == sorted(r["id"] for r in ROWS), "every exported learning comes back exactly once"
    for original in ROWS:
        back, sent = _kept(by_origin[original["id"]], origin_of), _kept(original, {})
        # The trw_learn write path may ADD tags it derives from the content (a real store's export already carries
        # them, so there the round trip is exact); none of the exported tags may be lost.
        assert set(sent.pop("tags")) <= set(back.pop("tags")), original["id"]
        assert back == sent, original["id"]


# -- codex r1 known issues (E2E-INC-118) ------------------------------------------------------------------------------


def _import(rows: list[dict[str, Any]], root: Path, source: Path) -> dict[str, Any]:
    from trw_mcp.export import import_learnings

    source.write_text(json.dumps({"metadata": {"project": "elsewhere"}, "learnings": rows}), encoding="utf-8")
    return dict(import_learnings(source, root))


def _copies(root: Path) -> dict[str, list[dict[str, Any]]]:
    """Every row in the project, grouped by the exported id it was imported from."""
    from trw_mcp.export import export_data

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in export_data(root, "learnings")["learnings"]:
        for tag in row.get("tags", []):
            if tag.startswith("imported-id:"):
                grouped.setdefault(tag.split(":", 1)[1], []).append(row)
    return grouped


def test_a_verified_learning_is_imported_never_refused_and_keeps_verified_only_when_the_file_substantiates_it(
    daemon_checkout: DaemonCheckout, tmp_path: Path
) -> None:
    root = daemon_checkout.trw_dir.parent
    claim = {"type": "glob_exists", "pattern": "", "target": "**/*.md", "last_result": True}
    rows = [
        _row(
            "L-ver1",
            "Asserted: the docs tree ships markdown",
            type_="pattern",
            confidence="verified",
            evidence_level="verified",
            evidence=[],
            assertions=[claim],
        ),
        _row(
            "L-ver2",
            "Claimed verified but nothing in the file backs it",
            type_="pattern",
            confidence="verified",
            evidence_level="verified",
            evidence=[],
        ),
    ]

    result = _import(rows, root, tmp_path / "verified.json")

    assert (result["imported"], result["refused"]) == (2, 0), result
    back = {origin: copies[0] for origin, copies in _copies(root).items()}
    assert back["L-ver1"]["confidence"] == "verified"
    assert [(a["type"], a["target"]) for a in back["L-ver1"]["assertions"]] == [("glob_exists", "**/*.md")]
    assert back["L-ver2"]["confidence"] == "high"
    assert any("L-ver2" in note and "high" in note for note in result["not_restored"]), result["not_restored"]


def test_running_an_interrupted_import_again_completes_its_statuses_and_links(
    daemon_checkout: DaemonCheckout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trw_mcp.export_import as export_import
    from trw_mcp.state._store_selection import StoreUnavailableError

    root = daemon_checkout.trw_dir.parent
    rows = [
        _row("L-int1", "Retired: the nightly job reads the replica", type_="pattern", status="obsolete"),
        _row("L-int2", "The cache key omits the locale", type_="incident", superseded=True, invalidated_by="L-int3"),
        _row("L-int3", "The cache key includes locale and currency", type_="decision"),
        _row("L-int4", "Batch writes above 500 rows time out", type_="pattern"),
    ]
    real_store = export_import._store_entry
    calls = {"n": 0}

    def _store_goes_away_on_the_fourth_write(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 4:
            raise StoreUnavailableError("the memory daemon went away")
        return real_store(*args, **kwargs)

    monkeypatch.setattr(export_import, "_store_entry", _store_goes_away_on_the_fourth_write)
    assert _import(rows, root, tmp_path / "first.json")["status"] == "failed"
    monkeypatch.setattr(export_import, "_store_entry", real_store)

    retry = _import(rows, root, tmp_path / "retry.json")

    assert (retry["imported"], retry["skipped_duplicate"], retry["not_restored"]) == (1, 3, []), retry
    back = {origin: copies[0] for origin, copies in _copies(root).items()}
    assert back["L-int1"]["status"] == "obsolete"
    assert back["L-int2"]["superseded"] is True
    assert back["L-int2"]["invalidated_by"] == back["L-int3"]["id"]


def test_a_superseded_learning_links_to_a_closer_that_an_earlier_import_brought(
    daemon_checkout: DaemonCheckout, tmp_path: Path
) -> None:
    root = daemon_checkout.trw_dir.parent
    closer = _row("L-cl1", "Queue consumers must ack after the write commits", type_="decision")
    prior = _row("L-pr1", "Queue consumers ack on receipt", type_="pattern", superseded=True, invalidated_by="L-cl1")
    assert _import([closer], root, tmp_path / "closer.json")["imported"] == 1

    result = _import([prior, closer], root, tmp_path / "both.json")

    assert (result["imported"], result["skipped_duplicate"], result["not_restored"]) == (1, 1, []), result
    back = {origin: copies[0] for origin, copies in _copies(root).items()}
    assert back["L-pr1"]["superseded"] is True
    assert back["L-pr1"]["invalidated_by"] == back["L-cl1"]["id"]


def test_re_importing_a_learning_whose_exported_id_a_native_row_holds_adds_no_second_copy(
    daemon_checkout: DaemonCheckout, tmp_path: Path
) -> None:
    from trw_mcp.export_import import _store_entry
    from trw_mcp.state._helpers import load_project_config
    from trw_mcp.state._project_root_binding import project_bound

    root = daemon_checkout.trw_dir.parent
    with project_bound(root):
        native = _store_entry(
            {"summary": "Native: gRPC deadlines propagate", "detail": "Seen on the payments path.", "impact": 0.7},
            daemon_checkout.trw_dir,
            load_project_config(daemon_checkout.trw_dir),
            "here",
        )
    native_id = str(native["learning_id"])
    elsewhere = [
        _row(
            native_id, "Elsewhere: a resolved learning that happens to share the id", type_="pattern", status="resolved"
        )
    ]

    first = _import(elsewhere, root, tmp_path / "first.json")
    again = _import(elsewhere, root, tmp_path / "again.json")

    assert (first["imported"], again["imported"], again["skipped_duplicate"]) == (1, 0, 1), (first, again)
    assert len(_copies(root)[native_id]) == 1


def test_the_content_digest_keeps_the_boundary_between_summary_and_detail() -> None:
    from trw_mcp.export_import import _content_digest

    assert _content_digest("a\nb", "c") != _content_digest("a", "b\nc")
    assert _content_digest(" a ", "b") == _content_digest("a", "b ")


# -- codex r2 known issues (E2E-INC-118) ------------------------------------------------------------------------------


def test_two_different_learnings_sharing_an_exported_id_keep_their_own_supersession(
    daemon_checkout: DaemonCheckout, tmp_path: Path
) -> None:
    root = daemon_checkout.trw_dir.parent
    rows = [
        _row(
            "L-dup", "Superseded: retries use a fixed delay", type_="pattern", superseded=True, invalidated_by="L-clo"
        ),
        _row("L-dup", "Unrelated: the health check needs a timeout", type_="pattern"),
        _row("L-clo", "Retries use jittered exponential backoff", type_="decision"),
    ]

    result = _import(rows, root, tmp_path / "shared-id.json")

    assert (result["imported"], result["not_restored"]) == (3, []), result
    by_summary = {row["summary"]: row for copies in _copies(root).values() for row in copies}
    closed = by_summary["Superseded: retries use a fixed delay"]
    untouched = by_summary["Unrelated: the health check needs a timeout"]
    assert closed["superseded"] is True
    assert closed["invalidated_by"] == by_summary["Retries use jittered exponential backoff"]["id"]
    assert not untouched.get("superseded")


def test_a_closer_id_that_names_two_learnings_is_reported_not_guessed(
    daemon_checkout: DaemonCheckout, tmp_path: Path
) -> None:
    root = daemon_checkout.trw_dir.parent
    rows = [
        _row(
            "L-pri",
            "Superseded: builds cache the lockfile hash",
            type_="pattern",
            superseded=True,
            invalidated_by="L-two",
        ),
        _row("L-two", "Builds cache the resolved dependency tree", type_="decision"),
        _row("L-two", "A different learning that reuses the closer's id", type_="pattern"),
    ]

    result = _import(rows, root, tmp_path / "ambiguous-closer.json")

    assert result["imported"] == 3, result
    assert any("L-two" in note and "2 learnings" in note for note in result["not_restored"]), result["not_restored"]
    prior = _copies(root)["L-pri"][0]
    assert not prior.get("superseded")


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_malformed_evidence_level_on_a_verified_row_is_handled_per_row(
    daemon_checkout: DaemonCheckout, tmp_path: Path, dry_run: bool
) -> None:
    from trw_mcp.export import import_learnings

    root = daemon_checkout.trw_dir.parent
    rows = [
        _row(
            "L-bad",
            "Verified with a malformed evidence level",
            type_="pattern",
            confidence="verified",
            evidence_level=[],
            evidence=["fact"],
        ),
        _row("L-ok", "An ordinary learning after it", type_="pattern"),
    ]
    source = tmp_path / "malformed.json"
    source.write_text(json.dumps({"metadata": {"project": "elsewhere"}, "learnings": rows}), encoding="utf-8")

    result = dict(import_learnings(source, root, dry_run=dry_run))

    assert (result["status"], result["imported"]) == ("ok", 2), result
    if not dry_run:
        assert _copies(root)["L-bad"][0]["confidence"] == "high"


# -- EVIDENCE-DELETION-POLICY r2 known issue (INC-118-SUPERSEDE-REPAIR) ------------------------------------------------


def test_an_import_never_writes_supersession_onto_the_projects_own_closer(
    daemon_checkout: DaemonCheckout, tmp_path: Path
) -> None:
    from trw_mcp.export import export_data
    from trw_mcp.export_import import _store_entry
    from trw_mcp.state._helpers import load_project_config
    from trw_mcp.state._project_root_binding import project_bound

    root = daemon_checkout.trw_dir.parent
    native_row = {
        "summary": "Native: queue consumers ack after commit",
        "detail": "Seen on the billing path.",
        "impact": 0.7,
    }
    with project_bound(root):
        native = _store_entry(native_row, daemon_checkout.trw_dir, load_project_config(daemon_checkout.trw_dir), "here")
    native_id = str(native["learning_id"])

    def native_as_exported() -> dict[str, Any]:
        return next(row for row in export_data(root, "learnings")["learnings"] if row["id"] == native_id)

    before = native_as_exported()
    rows = [
        _row("L-imp", "Imported: consumers ack on receipt", type_="pattern", superseded=True, invalidated_by=native_id),
        {**native_row, "id": native_id, "type": "pattern", "status": "active"},
    ]

    result = _import(rows, root, tmp_path / "native-closer.json")

    assert (result["imported"], result["skipped_duplicate"]) == (1, 1), result
    assert any(native_id in note and "own learning" in note for note in result["not_restored"]), result["not_restored"]
    assert native_as_exported() == before, "the project's own learning must not be changed by an import"
    assert not _copies(root)["L-imp"][0].get("superseded")

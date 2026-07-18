"""PRD-FIX-085 and PRD-CORE-177 JSONL rotation and retention parity tests."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from trw_mcp.telemetry.retention import rotate_telemetry_log


def _seed_jsonl_over_threshold(path: Path, target_mb: float = 11) -> int:
    """Write enough JSONL lines to exceed the rotation threshold."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = '{"x": "' + "y" * 1024 + '"}\n'  # ~1 KB per line
    needed = int(target_mb * 1024)  # number of 1 KB lines
    with path.open("w", encoding="utf-8") as f:
        for _ in range(needed):
            f.write(payload)
    return path.stat().st_size


def test_recall_tracking_rotates_when_oversized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """record_recall() rotates recall_tracking.jsonl when over 10 MB."""
    trw_dir = tmp_path / ".trw"
    log_path = trw_dir / "logs" / "recall_tracking.jsonl"
    pre_size = _seed_jsonl_over_threshold(log_path)
    assert pre_size > 10 * 1024 * 1024

    monkeypatch.setattr("trw_mcp.state.recall_tracking.resolve_trw_dir", lambda: trw_dir)

    from trw_mcp.state.recall_tracking import record_recall

    assert record_recall("L-rotate-probe", query="probe") is True

    # File rotated: original moved to .1, new file is small.
    rotated = log_path.with_suffix(log_path.suffix + ".1")
    assert rotated.exists(), "recall_tracking.jsonl.1 should exist after rotation"
    assert log_path.stat().st_size < pre_size, "fresh recall_tracking.jsonl is small"


def test_recall_tracking_no_rotation_when_under_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """record_recall() does NOT rotate when under 10 MB."""
    trw_dir = tmp_path / ".trw"
    log_path = trw_dir / "logs" / "recall_tracking.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text('{"existing": "row"}\n', encoding="utf-8")

    monkeypatch.setattr("trw_mcp.state.recall_tracking.resolve_trw_dir", lambda: trw_dir)

    from trw_mcp.state.recall_tracking import record_recall

    assert record_recall("L-no-rotate", query="probe") is True
    rotated = log_path.with_suffix(log_path.suffix + ".1")
    assert not rotated.exists()


def test_propensity_already_rotates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """propensity_log already had rotation pre-fix; verify it still works."""
    log_path = tmp_path / ".trw" / "logs" / "propensity.jsonl"
    pre_size = _seed_jsonl_over_threshold(log_path)
    assert pre_size > 10 * 1024 * 1024

    from trw_mcp.state.propensity_log import _rotate_jsonl

    _rotate_jsonl(log_path)
    rotated = log_path.with_suffix(log_path.suffix + ".1")
    assert rotated.exists()


def test_deferred_deliver_log_rotates_when_oversized(tmp_path: Path) -> None:
    """_log_deferred_result() rotates deferred-deliver.jsonl when over 10 MB."""
    trw_dir = tmp_path / ".trw"
    log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
    pre_size = _seed_jsonl_over_threshold(log_path)
    assert pre_size > 10 * 1024 * 1024

    from trw_mcp.tools._deferred_delivery import _log_deferred_result

    _log_deferred_result(trw_dir, {"foo": "bar"}, errors=[])

    rotated = log_path.with_suffix(log_path.suffix + ".1")
    assert rotated.exists(), "deferred-deliver.jsonl.1 should exist after rotation"
    assert log_path.stat().st_size < pre_size


# NOTE: BufferedTelemetryEmitter belongs to the PROPRIETARY trw-swarm package and is
# covered by trw-swarm's own tests/test_loop_runtime.py. It must NOT be imported here:
# trw-mcp/tests/ ships to the public GitHub mirror via `git subtree split`, so a
# `from trw_swarm...` import would leak proprietary API surface and hard-fail (ImportError)
# on the standalone mirror where no trw-swarm sibling exists.


def test_rotate_telemetry_log_compresses_dense_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"event":"one"}\n' * 10, encoding="utf-8")

    result = rotate_telemetry_log(path, max_bytes=10, compress=True)

    assert result["rotated"] is True
    archive = Path(str(result["archive_path"]))
    assert archive.suffix == ".gz"
    with gzip.open(archive, "rt", encoding="utf-8") as handle:
        assert '"event":"one"' in handle.read()
    assert path.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# PRD-CORE-181-FR04: one atomic rotation/compression policy
# ---------------------------------------------------------------------------


def test_prd_core_181_fr04(tmp_path: Path) -> None:
    """FR04 acceptance: Given size, age, writer, crash, and reference fixtures,
    When rotation runs, Then eligible segments compress atomically and active
    or referenced data remains ordered."""
    from trw_mcp.telemetry.retention import rotate_and_compress

    log = tmp_path / "events.jsonl"
    active_line = json.dumps({"seq": 3}) + "\n"
    log.write_text(json.dumps({"seq": 2}) * 200 + "\n", encoding="utf-8")

    # Closed segments: one old (eligible), one referenced, one too-young.
    old_segment = tmp_path / "events.jsonl.1"
    old_segment.write_text(json.dumps({"seq": 1}) + "\n", encoding="utf-8")
    referenced_segment = tmp_path / "events.jsonl.2"
    referenced_segment.write_text(json.dumps({"seq": 0}) + "\n", encoding="utf-8")
    # Crash leftover from an interrupted prior compression.
    (tmp_path / "events.jsonl.1.gz.tmp").write_bytes(b"partial-garbage")

    import os

    old_mtime = 1_000_000.0
    os.utime(old_segment, (old_mtime, old_mtime))
    now = old_mtime + 100 * 86400

    result = rotate_and_compress(
        log,
        max_bytes=100,  # active is oversized -> size rotation
        min_age_seconds=7 * 86400,
        referenced=("events.jsonl.2",),
        now=now,
    )

    # Size fixture: active rotated to a new closed segment; a NEW active file
    # exists and is never compressed in place.
    assert result["rotated"] is True
    assert log.exists() and log.stat().st_size == 0
    log.write_text(active_line, encoding="utf-8")  # writer continues appending

    # Age fixture: the old closed segment compressed atomically and reads back.
    assert "events.jsonl.1.gz" in result["compressed"]
    assert not old_segment.exists()
    with gzip.open(tmp_path / "events.jsonl.1.gz", "rb") as handle:
        assert json.loads(handle.read().splitlines()[0])["seq"] == 1

    # Reference fixture: referenced segment untouched, in order, uncompressed.
    assert {"segment": "events.jsonl.2", "reason": "referenced"} in result["skipped"]
    assert referenced_segment.read_text(encoding="utf-8").strip()

    # Crash fixture: the stale .gz.tmp was discarded without touching data.
    assert not (tmp_path / "events.jsonl.1.gz.tmp").exists()

    # Idempotent re-run: nothing eligible remains; active still uncompressed.
    again = rotate_and_compress(
        log, max_bytes=10_000, min_age_seconds=7 * 86400, referenced=("events.jsonl.2",), now=now
    )
    assert again["rotated"] is False and again["compressed"] == []
    assert log.read_text(encoding="utf-8") == active_line


# ---------------------------------------------------------------------------
# PRD-CORE-181-NFR02: crash and concurrency safety — atomic replacement and
# process-safe coordination preserve readable canonical state.
# ---------------------------------------------------------------------------


def test_prd_core_181_nfr02(tmp_path: Path) -> None:
    """Crash and writer fixtures preserve readable canonical state: an
    interrupted compression never loses a segment, the active writer file is
    never rewritten in place, and a concurrently-referenced segment is skipped."""
    from trw_mcp.telemetry.retention import rotate_and_compress

    log = tmp_path / "events.jsonl"
    active_bytes = b'{"active":true}\n'
    log.write_bytes(active_bytes)

    # A sealed closed segment plus a crash leftover from an interrupted prior
    # compression (the .gz.tmp exists alongside the still-present original).
    segment = tmp_path / "events.jsonl.1"
    segment_bytes = b'{"seq":1}\n'
    segment.write_bytes(segment_bytes)
    (tmp_path / "events.jsonl.1.gz.tmp").write_bytes(b"half-written-garbage")

    # A concurrently-referenced (active writer) segment must never be touched.
    referenced = tmp_path / "events.jsonl.2"
    referenced.write_bytes(b'{"seq":2}\n')

    import os

    old = 1_000_000.0
    os.utime(segment, (old, old))
    now = old + 30 * 86400

    result = rotate_and_compress(log, max_bytes=10_000, min_age_seconds=0.0, referenced=("events.jsonl.2",), now=now)

    # Atomic replacement: the segment ends as a single readable canonical form
    # (its .gz), never "neither" — no data-loss window.
    assert not segment.exists()
    assert not (tmp_path / "events.jsonl.1.gz.tmp").exists()  # crash leftover discarded
    compressed = tmp_path / "events.jsonl.1.gz"
    assert compressed.is_file()
    with gzip.open(compressed, "rb") as handle:
        assert handle.read() == segment_bytes  # bytes survive the atomic swap

    # The active writer file is never compressed in place — it stays plain text.
    assert log.read_bytes() == active_bytes
    assert not (tmp_path / "events.jsonl.gz").exists()

    # The referenced (concurrently-written) segment is skipped, uncompressed.
    assert {"segment": "events.jsonl.2", "reason": "referenced"} in result["skipped"]
    assert referenced.is_file() and not (tmp_path / "events.jsonl.2.gz").exists()

    # A segment whose read-back does not verify is reported corrupt and LEFT
    # untouched (report, never collect) — canonical bytes preserved.
    corrupt_seg = tmp_path / "events.jsonl.3"
    corrupt_bytes = b'{"seq":3}\n'
    corrupt_seg.write_bytes(corrupt_bytes)
    os.utime(corrupt_seg, (old, old))
    real_gzip_open = gzip.open

    def _bad_gzip_open(path: object, *args: object, **kwargs: object) -> object:
        handle = real_gzip_open(path, *args, **kwargs)  # type: ignore[arg-type]
        mode = args[0] if args else kwargs.get("mode", "rb")
        if str(mode).startswith("r"):  # corrupt only the verification read-back

            class _Corrupt:
                def __enter__(self_inner) -> object:
                    return self_inner

                def __exit__(self_inner, *exc: object) -> bool:
                    handle.close()
                    return False

                def read(self_inner) -> bytes:
                    return b"tampered"

            return _Corrupt()
        return handle

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("trw_mcp.telemetry.retention.gzip.open", _bad_gzip_open)
    try:
        corrupt_result = rotate_and_compress(log, max_bytes=10_000, min_age_seconds=0.0, now=now)
    finally:
        monkeypatch.undo()

    assert "events.jsonl.3" in corrupt_result["corrupt"]
    assert corrupt_seg.read_bytes() == corrupt_bytes  # untouched, still readable
    assert not (tmp_path / "events.jsonl.3.gz").exists()


# ---------------------------------------------------------------------------
# PRD-CORE-181-FR04 wiring (P1-1): rotate_and_compress now has a production
# caller — the telemetry publisher's deliver-step maintenance path.
# ---------------------------------------------------------------------------


def test_telemetry_log_rotates_through_publish_learnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A large real pipeline-events.jsonl rotates+compresses through the
    production ``publish_learnings`` maintenance path (deliver step D08)."""
    trw_dir = tmp_path / ".trw"
    log_path = trw_dir / "logs" / "pipeline-events.jsonl"
    pre_size = _seed_jsonl_over_threshold(log_path, target_mb=11)
    assert pre_size > 10 * 1024 * 1024

    # Point every trw_dir resolution in the publisher at the temp tree.
    monkeypatch.setattr("trw_mcp.telemetry.publisher.resolve_trw_dir", lambda: trw_dir)

    from trw_mcp.telemetry.publisher import publish_learnings

    # publish_learnings runs the FR04 rotation first, then returns (offline by
    # default) — the rotation is network-independent maintenance.
    result = publish_learnings()
    assert result["skipped_reason"] in {"offline_mode", "no_entries"}

    # Active writer file resealed small; the closed segment compressed atomically.
    assert log_path.exists() and log_path.stat().st_size < pre_size
    assert (trw_dir / "logs" / "pipeline-events.jsonl.1.gz").is_file()


def test_telemetry_log_rotation_fast_path_under_threshold(tmp_path: Path) -> None:
    """The size gate is a cheap no-op fast path when the log is under max_bytes."""
    from trw_mcp.telemetry.publisher import rotate_pipeline_telemetry_log

    trw_dir = tmp_path / ".trw"
    log_path = trw_dir / "logs" / "pipeline-events.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text('{"small": true}\n', encoding="utf-8")

    result = rotate_pipeline_telemetry_log(trw_dir, max_bytes=10 * 1024 * 1024)

    assert result == {"rotated": False, "reason": "under_threshold"}
    assert not (trw_dir / "logs" / "pipeline-events.jsonl.1").exists()
    assert not (trw_dir / "logs" / "pipeline-events.jsonl.1.gz").exists()


def test_telemetry_log_default_threshold_reads_config_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD-CORE-181-FR04 wiring: the default (``max_bytes=None``) rotation
    threshold is sourced from ``TRWConfig.telemetry_log_max_bytes`` — a tiny
    configured bound rotates a log the 10 MiB default would leave untouched."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.telemetry.publisher import rotate_pipeline_telemetry_log

    trw_dir = tmp_path / ".trw"
    log_path = trw_dir / "logs" / "pipeline-events.jsonl"
    log_path.parent.mkdir(parents=True)
    # ~1 KB — well under the 10 MiB default, but over a 64-byte configured cap.
    log_path.write_text('{"x": "' + "y" * 1000 + '"}\n', encoding="utf-8")
    assert log_path.stat().st_size > 64

    monkeypatch.setattr(
        "trw_mcp.telemetry.publisher.get_config",
        lambda: TRWConfig(telemetry_log_max_bytes=64),
    )

    result = rotate_pipeline_telemetry_log(trw_dir)  # default path — no max_bytes

    assert result.get("rotated") is True, "configured tiny cap must trigger rotation"
    assert (trw_dir / "logs" / "pipeline-events.jsonl.1.gz").is_file()


# ---------------------------------------------------------------------------
# PRD-CORE-181-NFR02 (P1 release-blocker): rotate_and_compress must hold the
# SAME exclusive flock append_jsonl takes on the active file, so a concurrent
# append can never interleave with the size-check -> rename -> touch that
# closes the active segment. trw-mcp runs one OS process per MCP client, so an
# in-process thread lock is insufficient.
# ---------------------------------------------------------------------------


def test_rotation_serializes_against_an_active_file_lock_holder(tmp_path: Path) -> None:
    """While another fd holds the exclusive advisory lock on the active file
    (as ``FileStateWriter.append_jsonl`` does mid-append), ``rotate_and_compress``
    must BLOCK — it cannot rename the file out from under the appender — and
    then complete once the lock is released."""
    import threading

    from trw_mcp._locking import _lock_ex, _lock_un
    from trw_mcp.telemetry.retention import rotate_and_compress

    log = tmp_path / "events.jsonl"
    log.write_bytes(b'{"x":1}\n' * 4000)  # comfortably over the 10_000-byte cap
    assert log.stat().st_size > 10_000

    # Stand in for a concurrent appender holding the active-file lock.
    holder = log.open("a", encoding="utf-8")
    _lock_ex(holder.fileno())

    done = threading.Event()
    box: dict[str, object] = {}

    def _rotate() -> None:
        box["result"] = rotate_and_compress(log, max_bytes=10_000, min_age_seconds=0.0)
        done.set()

    worker = threading.Thread(target=_rotate)
    worker.start()
    try:
        # Rotation is blocked on the held lock: it must NOT have rotated yet.
        assert not done.wait(timeout=0.5), "rotation ran despite a held active-file lock"
        assert not log.with_name("events.jsonl.1").exists()

        # Release the appender's lock; rotation now proceeds.
        _lock_un(holder.fileno())
        holder.close()
        assert done.wait(timeout=10), "rotation did not resume after lock release"
    finally:
        worker.join(timeout=10)

    result = box["result"]
    assert isinstance(result, dict) and result["rotated"] is True
    # Active segment was closed into .1 and (min_age=0) compressed in the same call.
    assert log.with_name("events.jsonl.1.gz").exists()
    assert log.exists() and log.stat().st_size == 0  # fresh active writer file

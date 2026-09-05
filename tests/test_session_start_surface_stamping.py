"""Integration tests for surface_snapshot_id stamping in trw_session_start.

Wave 2a — PRD-HPO-MEAS-001 FR-2 wiring check. These assertions verify
that ``stamp_session`` is actually invoked by ``trw_session_start`` and
its resolved ``surface_snapshot_id`` flows into the result dict +
``run_surface_snapshot.yaml`` is written when a run is pinned.

These are behavioral (not existence) tests: each one would fail if the
Wave 2a wiring were reverted to the Phase-1 empty-string default.

They call ``session_start(verbose=True)``. ``surface_snapshot_id`` is an
opaque digest with no caller action, so as of 2026-07-27 the compact
(default) payload drops it along with the other identity stamps — see
``_session_start_trim._COMPACT_DROP_KEYS``. ``verbose=True`` is the
diagnostic mode that preserves them, so the wiring is still observed
through the tool's own return value and the checks are unweakened; only
the observation mode changed.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
import yaml

from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.telemetry.artifact_registry import clear_snapshot_cache


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    clear_snapshot_cache()
    yield
    clear_snapshot_cache()


@pytest.fixture
def pinned_run(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., Path]:
    """Return a factory that scaffolds a run and forces session_start to pin it.

    Real pinning depends on ``ctx.session_id`` state the tool harness does not
    supply, so run discovery, the pin write, and the run-status read are stubbed
    — that is what makes the disk assertions in these tests fire unconditionally
    instead of only when the harness happens to pin (audit finding F-04).

    The patch targets are the BINDING sites, not the ceremony facade: PRD-DIST-243
    moved this step into ``_ceremony_session_start_steps``, which imports
    ``pin_active_run`` / ``_get_run_status`` from their source modules at call
    time, so a facade patch would no longer propagate.
    """

    def _make(run_id: str = "20260423T000000Z-deadbeef", *, run_yaml_extra: str = "", events: bool = True) -> Path:
        run_root = tmp_project / ".trw" / "runs" / "test-task" / run_id
        (run_root / "meta").mkdir(parents=True, exist_ok=True)
        (run_root / "meta" / "run.yaml").write_text(
            "task: test-task\nphase: research\nstatus: active\n" + run_yaml_extra
        )
        if events:
            (run_root / "meta" / "events.jsonl").touch()

        import trw_mcp.tools.ceremony as ceremony_mod

        monkeypatch.setattr(ceremony_mod, "_find_active_run_compat", lambda ctx: run_root)
        monkeypatch.setattr(
            "trw_mcp.state._paths.pin_active_run",
            lambda run_dir, *, context=None, session_id=None: None,
        )
        monkeypatch.setattr(
            "trw_mcp.tools._ceremony_runtime_helpers._get_run_status",
            lambda rd: {
                "active_run": rd.name,
                "status": "active",
                "task_name": "test-task",
                "phase": "research",
            },
        )
        return run_root

    return _make


def test_session_start_populates_surface_snapshot_id(tmp_project: Path) -> None:
    """``trw_session_start`` returns a non-empty ``surface_snapshot_id``."""
    server = make_test_server("ceremony")
    session_start = extract_tool_fn(server, "trw_session_start")

    result = session_start(verbose=True)
    # Phase 1: fail-open means empty-string is allowed ONLY when the
    # bundled data root is unavailable. In a dev install, resolve always
    # succeeds, so we expect a real 64-char sha256.
    assert "surface_snapshot_id" in result
    # Either a real id OR explicit empty-string (fail-open path); never missing.
    snapshot_id = result["surface_snapshot_id"]
    assert isinstance(snapshot_id, str)
    if snapshot_id:
        assert len(snapshot_id) == 64, f"expected sha256 hex or empty, got {snapshot_id!r}"


def test_session_start_writes_run_surface_snapshot_when_run_pinned(
    pinned_run: Callable[..., Path],
) -> None:
    """With an active run pinned, ``run_surface_snapshot.yaml`` is on disk."""
    run_root = pinned_run()

    server = make_test_server("ceremony", "checkpoint")
    session_start = extract_tool_fn(server, "trw_session_start")
    result = session_start(verbose=True)

    # Primary assertion — surface_snapshot_id present in result dict.
    assert "surface_snapshot_id" in result

    # Unconditional disk assertion — with the stubs above, Step 2c MUST
    # have called stamp_session(run_root / "meta").
    manifest = run_root / "meta" / "run_surface_snapshot.yaml"
    assert manifest.exists(), f"pinned run should have run_surface_snapshot.yaml at {manifest}"
    body = manifest.read_text()
    assert "snapshot_id:" in body
    assert "artifacts:" in body


def test_session_start_updates_run_yaml_with_surface_snapshot_pointer(
    pinned_run: Callable[..., Path],
) -> None:
    run_root = pinned_run()

    server = make_test_server("ceremony")
    session_start = extract_tool_fn(server, "trw_session_start")
    result = session_start(verbose=True)

    run_yaml = yaml.safe_load((run_root / "meta" / "run.yaml").read_text(encoding="utf-8"))
    assert run_yaml["surface_snapshot_id"] == result["surface_snapshot_id"]
    assert run_yaml["run_surface_snapshot_path"] == "meta/run_surface_snapshot.yaml"


def test_default_compact_path_still_stamps_the_run_on_disk(
    pinned_run: Callable[..., Path],
) -> None:
    """The stamping wiring must hold on the path production actually takes.

    Every real trw_session_start call is verbose=False. The other tests in this
    file observe through verbose=True because the digest is dropped from the
    compact response — which means that without this test, making the STAMPING
    itself conditional on verbose, or reordering trim_session_start_payload
    ahead of run_steps, would leave the whole file green.

    So this one asserts the disk artifacts only, on the default call.
    """
    run_root = pinned_run("20260727T000000Z-cafebabe", events=False)

    server = make_test_server("ceremony")
    session_start = extract_tool_fn(server, "trw_session_start")
    result = session_start()

    assert result.get("compact") is True, "default call was not the compact path"
    assert "surface_snapshot_id" not in result, "compact must not carry the digest"

    run_yaml = yaml.safe_load((run_root / "meta" / "run.yaml").read_text(encoding="utf-8"))
    assert run_yaml["surface_snapshot_id"], "compact path did not stamp run.yaml"
    assert run_yaml["run_surface_snapshot_path"] == "meta/run_surface_snapshot.yaml"
    assert (run_root / "meta" / "run_surface_snapshot.yaml").is_file()


def test_subsequent_checkpoint_event_inherits_surface_snapshot_id_from_run_context(
    pinned_run: Callable[..., Path],
) -> None:
    run_root = pinned_run(run_yaml_extra="owner_session_id: s-test\n")

    server = make_test_server("ceremony", "orchestration")
    session_start = extract_tool_fn(server, "trw_session_start")
    checkpoint = extract_tool_fn(server, "trw_checkpoint")
    result = session_start(verbose=True)
    checkpoint(run_path=str(run_root), message="milestone")

    events_files = sorted((run_root / "meta").glob("events-*.jsonl"))
    assert events_files, "unified events file should exist after session_start + checkpoint"
    records = [json.loads(line) for line in events_files[0].read_text(encoding="utf-8").splitlines()]
    assert all(record["surface_snapshot_id"] == result["surface_snapshot_id"] for record in records)


def test_session_start_stamp_failure_keeps_the_key_and_fails_the_verdict(
    pinned_run: Callable[..., Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FORCED stamping failure still yields the key, and now fails the verdict.

    Previously this called the happy path and asserted only that the key existed
    and was a str — which the successful case already proves, so the error branch
    it names was never executed. Raising inside the registry build makes the
    contract the thing under test.

    PRD-CORE-263-FR01 changed the second half. ``surface_stamp`` is declared
    critical, and the old ``success is True`` assertion is exactly the defect the
    PRD names: the flag was live and its branch unreachable. NFR04 keeps the key
    present as an empty string for a parser that asserts presence.
    """
    pinned_run()

    def _boom(**_kwargs: object) -> object:
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr("trw_mcp.telemetry.artifact_registry.SurfaceRegistry.build_and_emit", _boom)

    server = make_test_server("ceremony")
    session_start = extract_tool_fn(server, "trw_session_start")

    result = session_start(verbose=True)

    assert "surface_snapshot_id" in result, "the key is written even when the stamp fails"
    assert result["surface_snapshot_id"] == ""
    assert result["success"] is False, "surface_stamp is declared critical (PRD-CORE-263-FR01)"
    assert any("surface_stamp" in err for err in result["errors"])

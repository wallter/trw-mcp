"""PRD-CORE-231-FR01: durable ``hint_delivered`` telemetry for eligible edits.

The CC-03 PreToolUse hook wrote only to a 24h-TTL context directory, so the
>=90% T2 delivery gate had no durable source to be measured from. These tests
drive the real ``compute_before_edit_hint`` path and read the real
``.trw/telemetry/channel-events.jsonl``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.channels._distill_telemetry import hash_file_path
from trw_mcp.channels._telemetry import VALID_EVENT_TYPES, validate_event_type

_EVENT = "hint_delivered"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".trw" / "telemetry").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def _events(repo: Path) -> list[dict[str, object]]:
    log = repo / ".trw" / "telemetry" / "channel-events.jsonl"
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _hint_events(repo: Path) -> list[dict[str, object]]:
    return [e for e in _events(repo) if e.get("event_type") == _EVENT]


def _compute(monkeypatch: pytest.MonkeyPatch, repo: Path, *, eligible: bool, file_path: str = "src/app.py") -> object:
    """Run the real hint computation with entitlement forced on/off."""
    from trw_mcp.tools import before_edit_hint as beh

    # The channel-event writer resolves its log via TRW_REPO_ROOT.
    monkeypatch.setenv("TRW_REPO_ROOT", str(repo))
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: repo / ".trw")
    monkeypatch.setattr(beh._sidecar_substrate, "distill_installed", lambda: eligible)
    monkeypatch.setattr(beh, "_collect_learnings", lambda _fp: [])
    return beh.compute_before_edit_hint(file_path=file_path, repo_root=str(repo))


def test_event_type_is_registered() -> None:
    """The new event must be a canonical type, not a silently-dropped typo."""
    assert _EVENT in VALID_EVENT_TYPES
    validate_event_type(_EVENT)


def test_eligible_edit_emits_event(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    """An entitlement-allowed edit emits exactly one event with the FR01 fields."""
    result = _compute(monkeypatch, repo, eligible=True)

    events = _hint_events(repo)
    assert len(events) == 1
    extra = events[0]["extra"]
    assert isinstance(extra, dict)
    assert extra["eligible"] is True
    assert extra["distill_status"] == getattr(result, "distill_status")
    assert extra["file_path_hash"] == hash_file_path("src/app.py")
    assert "tier" in extra


def test_misses_are_recorded_not_just_hits(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    """A sidecar-less eligible edit still emits — otherwise the gate is survivorship-biased."""
    result = _compute(monkeypatch, repo, eligible=True)

    # No sidecar exists in this fixture, so this is a delivery MISS.
    assert getattr(result, "distill_status") != "hint_available"
    events = _hint_events(repo)
    assert len(events) == 1
    assert events[0]["extra"]["eligible"] is True  # type: ignore[index]


def test_ineligible_edit_emits_nothing(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    """``tier_required`` is NOT an eligible edit and must not enter the denominator."""
    result = _compute(monkeypatch, repo, eligible=False)

    assert getattr(result, "distill_status") == "tier_required"
    assert _hint_events(repo) == []


def test_one_event_per_eligible_invocation(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    """The delivery rate is a ratio of invocations, so emission must not batch or dedupe."""
    for _ in range(3):
        _compute(monkeypatch, repo, eligible=True)

    assert len(_hint_events(repo)) == 3


def test_file_path_is_hashed_not_recorded(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    """Durable telemetry carries a digest, never a readable repo path."""
    secret = "src/very_secret_internal_name.py"
    (repo / "src" / "very_secret_internal_name.py").write_text("y = 2\n", encoding="utf-8")

    _compute(monkeypatch, repo, eligible=True, file_path=secret)

    raw = (repo / ".trw" / "telemetry" / "channel-events.jsonl").read_text(encoding="utf-8")
    assert "very_secret_internal_name" not in raw
    assert hash_file_path(secret) in raw


def test_path_hash_is_stable_and_distinguishing() -> None:
    """The digest must be deterministic (same file) and separating (different files)."""
    assert hash_file_path("a/b.py") == hash_file_path("a/b.py")
    assert hash_file_path("a/b.py") != hash_file_path("a/c.py")


def test_telemetry_failure_never_breaks_the_hint(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    """NFR02: a broken telemetry sink must not fail the edit-time hint."""
    import trw_mcp.channels._distill_telemetry as dt

    def _boom(**_kwargs: object) -> None:
        raise OSError("telemetry sink is gone")

    monkeypatch.setattr(dt, "append_channel_event", _boom)

    result = _compute(monkeypatch, repo, eligible=True)

    assert getattr(result, "file_path") == "src/app.py"
    assert _hint_events(repo) == []

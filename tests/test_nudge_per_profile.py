"""PRD-CORE-146 Wave 2C FR10/FR11 — parameterized per-profile nudge suite.

Covers all 8 built-in client profiles (claude-code, opencode, cursor-ide,
cursor-cli, codex, copilot, gemini, aider). Exercises:

FR10: per-profile surface-flag wiring — nudge_enabled, effective_nudge_messenger,
effective_nudge_density, nudge_budget_chars. Also verifies nudge-eligible
profiles actually emit ``nudge_shown`` INFO events and that the dedup
invariant holds across two sequential calls.

FR11: negative tests — nudge-off profiles (opencode, codex, aider) must NOT
emit ``nudge_shown`` structlog INFO events nor nudge_shown rows in
``.trw/context/session-events.jsonl``.

NFR03 — pinned field names: nudge_shown, event, step, learning_ids,
surface_type, nudge_history, turn_first_shown, last_shown_turn,
phases_shown.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import structlog.testing

# Built-in profile catalogue and nudge_enabled truth table.
_ALL_PROFILES: tuple[str, ...] = (
    "claude-code",
    "opencode",
    "cursor-ide",
    "cursor-cli",
    "codex",
    "copilot",
    "gemini",
    "aider",
)

_EXPECTED_NUDGE_ENABLED: dict[str, bool] = {
    "claude-code": True,
    "opencode": False,
    "cursor-ide": True,
    "cursor-cli": True,
    "codex": False,
    "copilot": True,
    "gemini": True,
    "aider": False,
}

_FULL_MODE_PROFILES: tuple[str, ...] = tuple(
    p for p, enabled in _EXPECTED_NUDGE_ENABLED.items() if enabled
)
_NUDGE_OFF_PROFILES: tuple[str, ...] = tuple(
    p for p, enabled in _EXPECTED_NUDGE_ENABLED.items() if not enabled
)


def _write_profile_config(trw_dir: Path, profile_id: str, extra: str = "") -> None:
    """Write a minimal ``.trw/config.yaml`` pinning the active client profile."""
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    body = f"target_platforms:\n  - {profile_id}\n{extra}"
    (trw_dir / "config.yaml").write_text(body, encoding="utf-8")


# --- FR10.1: nudge_enabled table ----------------------------------------------


@pytest.mark.parametrize("profile_id", _ALL_PROFILES)
def test_profile_nudge_enabled_matches_expected(profile_id: str) -> None:
    """FR10: each profile's ``nudge_enabled`` default matches the CLIENT-PROFILES
    surface-flag table — full-mode clients on, light-mode clients off.
    """
    from trw_mcp.models.config._profiles import resolve_client_profile

    profile = resolve_client_profile(profile_id)
    assert profile.nudge_enabled is _EXPECTED_NUDGE_ENABLED[profile_id]
    # Sanity: client_id round-trips.
    assert profile.client_id == profile_id


# --- FR10.2: messenger + density resolution -----------------------------------


@pytest.mark.parametrize("profile_id", _ALL_PROFILES)
def test_profile_messenger_resolves_to_standard(profile_id: str, tmp_path: Path) -> None:
    """FR10: every profile with empty config override must resolve
    ``effective_nudge_messenger`` to ``"standard"`` — no built-in profile
    selects a non-standard messenger today. ``effective_nudge_density``
    likewise falls back to ``None`` for all profiles.
    """
    from trw_mcp.models.config import TRWConfig

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    config = TRWConfig(trw_dir=str(trw_dir), target_platforms=[profile_id])

    assert config.effective_nudge_messenger == "standard"

    # effective_nudge_density is the W2A field — present since Wave 2A.
    if not hasattr(config, "effective_nudge_density"):
        pytest.skip("W2A pending: effective_nudge_density not yet exposed")
    assert config.effective_nudge_density is None


# --- FR10.3: full-mode profiles emit nudge_shown events -----------------------


@pytest.mark.parametrize("profile_id", ["claude-code", "cursor-ide", "gemini"])
def test_append_ceremony_status_emits_when_enabled(profile_id: str, tmp_path: Path) -> None:
    """FR10: when a full-mode profile is active and a learning candidate is
    available, ``append_ceremony_status`` emits a ``nudge_shown`` INFO event.

    Drives the ``learning_injection`` messenger branch (line 504 in
    _ceremony_status.py) to guarantee a deterministic emission; real-world
    pool dispatch would be non-deterministic in a unit test.
    """
    from trw_mcp.tools._ceremony_status import append_ceremony_status

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    _write_profile_config(
        trw_dir,
        profile_id,
        extra="nudge_enabled: true\nnudge_messenger: learning_injection\n",
    )

    with (
        patch(
            "trw_mcp.state.ceremony_nudge.select_learning_injection_content",
            return_value=("Injected nudge content", "L-fr10", "foo.py"),
        ),
        structlog.testing.capture_logs() as captured,
    ):
        response = append_ceremony_status({"status": "ok"}, trw_dir)

    nudge_events = [e for e in captured if e.get("event") == "nudge_shown"]
    assert len(nudge_events) >= 1, f"expected nudge_shown for {profile_id}, got {captured!r}"
    assert response.get("nudge_content") == "Injected nudge content"
    # Pinned fields (NFR03): learning_id field carries the selected learning.
    assert nudge_events[0].get("learning_id") == "L-fr10"


# --- FR10.4: per-profile nudge budget enforcement -----------------------------


@pytest.mark.parametrize("profile_id", _FULL_MODE_PROFILES)
def test_profile_budget_respected(profile_id: str, tmp_path: Path) -> None:
    """FR10: ``compute_nudge`` must never return a string longer than
    ``nudge_budget_chars`` for the active profile. Mocks the pool-message
    loader so the raw candidate exceeds budget; the assembler must trim.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.ceremony_nudge import compute_nudge
    from trw_mcp.state._ceremony_progress_state import CeremonyState

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    config = TRWConfig(trw_dir=str(trw_dir), target_platforms=[profile_id])
    budget = config.nudge_budget_chars
    assert budget >= 100  # sanity: Field(ge=100)

    huge = "X" * (budget * 3)
    state = CeremonyState()

    with (
        patch("trw_mcp.models.config._loader.get_config", return_value=config),
        patch("trw_mcp.state._nudge_content.load_pool_message", return_value=huge),
    ):
        result = compute_nudge(state, available_learnings=0)

    # compute_nudge returns either a bounded string or "" (fail-open). Either
    # way the budget invariant holds.
    assert isinstance(result, str)
    assert len(result) <= budget, (
        f"{profile_id}: compute_nudge returned {len(result)} chars, budget={budget}"
    )


# --- FR10.5: idempotent / dedup invariant across two calls --------------------


@pytest.mark.parametrize("profile_id", _FULL_MODE_PROFILES)
def test_append_ceremony_status_idempotent_across_two_calls(
    profile_id: str, tmp_path: Path
) -> None:
    """FR10: two sequential ``append_ceremony_status`` calls with the same
    learning candidate must honour the phase-dedup invariant — the second
    call MUST NOT log a second ``nudge_shown`` INFO event for the same
    (learning_id, phase) pair. ``nudge_history`` grows monotonically (size
    never shrinks) and each entry's ``phases_shown`` list contains the
    phase exactly once.
    """
    from trw_mcp.state._ceremony_progress_state import read_ceremony_state
    from trw_mcp.tools._ceremony_status import append_ceremony_status

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    _write_profile_config(
        trw_dir,
        profile_id,
        extra="nudge_enabled: true\nnudge_messenger: learning_injection\n",
    )

    with patch(
        "trw_mcp.state.ceremony_nudge.select_learning_injection_content",
        return_value=("Injected nudge", "L-dedup", "foo.py"),
    ):
        with structlog.testing.capture_logs() as first_capture:
            first = append_ceremony_status({"status": "ok"}, trw_dir)
        state_after_first = read_ceremony_state(trw_dir)
        history_size_first = len(state_after_first.nudge_history)

        with structlog.testing.capture_logs() as second_capture:
            second = append_ceremony_status({"status": "ok"}, trw_dir)
        state_after_second = read_ceremony_state(trw_dir)
        history_size_second = len(state_after_second.nudge_history)

    # Monotonic growth.
    assert history_size_second >= history_size_first

    # First call emitted the learning-backed nudge; second must not re-emit the
    # same learning_id for the same phase, though it may fall back to a minimal
    # synthetic nudge.
    first_events = [e for e in first_capture if e.get("event") == "nudge_shown"]
    second_events = [e for e in second_capture if e.get("event") == "nudge_shown"]
    assert len(first_events) == 1, f"{profile_id}: first call should emit one nudge_shown"
    assert all(e.get("learning_id") != "L-dedup" for e in second_events), (
        f"{profile_id}: second call must not re-emit deduped learning_id; got {second_events!r}"
    )
    # Response contract: first call carries nudge_content, second does not
    # (matches test_learning_injection_messenger_dedups_and_records_impression).
    assert first.get("nudge_content") == "Injected nudge"
    assert second.get("nudge_content") != "Injected nudge"

    # NFR03: phase_dedup invariant — each (learning_id, phase) appears once.
    entry = state_after_second.nudge_history.get("L-dedup")
    assert entry is not None
    phases_shown = entry["phases_shown"]
    assert phases_shown.count(state_after_second.phase) == 1, (
        f"{profile_id}: phase {state_after_second.phase!r} duplicated in phases_shown={phases_shown!r}"
    )
    # Pinned fields (NFR03).
    assert "turn_first_shown" in entry
    assert "last_shown_turn" in entry


# --- FR11: nudge-off profiles emit nothing ------------------------------------


@pytest.mark.parametrize("profile_id", _NUDGE_OFF_PROFILES)
def test_nudge_off_profile_emits_no_nudge_shown_info(
    profile_id: str, tmp_path: Path
) -> None:
    """FR11: nudge-off profiles must NEVER log ``nudge_shown`` INFO events,
    even when a learning candidate is mocked to be available. The early
    return at ``effective_nudge_enabled=False`` guards this.
    """
    from trw_mcp.tools._ceremony_status import append_ceremony_status

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    # Only pin target_platforms — do NOT set nudge_enabled override. The
    # profile default (False for opencode/codex/aider) must win.
    _write_profile_config(trw_dir, profile_id)

    with (
        patch(
            "trw_mcp.state.ceremony_nudge.select_learning_injection_content",
            return_value=("Should-not-appear", "L-off", "foo.py"),
        ),
        structlog.testing.capture_logs() as captured,
    ):
        response = append_ceremony_status({"status": "ok"}, trw_dir)

    nudge_events = [e for e in captured if e.get("event") == "nudge_shown"]
    assert nudge_events == [], (
        f"{profile_id}: nudge-off profile emitted nudge_shown events: {nudge_events!r}"
    )
    # ceremony_status should still be present (fail-closed on nudge only).
    assert "ceremony_status" in response
    # nudge_content should NOT have been set.
    assert "nudge_content" not in response


@pytest.mark.parametrize("profile_id", _NUDGE_OFF_PROFILES)
def test_nudge_off_profile_emits_no_nudge_shown_jsonl(
    profile_id: str, tmp_path: Path
) -> None:
    """FR11: nudge-off profiles must not append ``nudge_shown`` rows to
    ``.trw/context/session-events.jsonl``.
    """
    from trw_mcp.tools._ceremony_status import append_ceremony_status

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    _write_profile_config(trw_dir, profile_id)

    with patch(
        "trw_mcp.state.ceremony_nudge.select_learning_injection_content",
        return_value=("Should-not-appear", "L-off", "foo.py"),
    ):
        append_ceremony_status({"status": "ok"}, trw_dir)

    events_path = trw_dir / "context" / "session-events.jsonl"
    if not events_path.exists():
        # No file = no events = test passes.
        return

    text = events_path.read_text(encoding="utf-8")
    nudge_rows = [line for line in text.splitlines() if '"event":"nudge_shown"' in line]
    assert nudge_rows == [], (
        f"{profile_id}: nudge-off profile wrote nudge_shown rows: {nudge_rows!r}"
    )

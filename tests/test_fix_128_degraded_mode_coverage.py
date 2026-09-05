"""PRD-FIX-128 acceptance/boundary coverage not carried by the host test files.

Every test here drives the REAL shipped hooks as subprocesses, exactly like
``test_core_247_degraded_mode_hooks.py`` -- no hook is mocked or stubbed. The
fixture helpers are imported from that module rather than duplicated, so a
change to the fixture shape (pin entry fields, marker layout, env-popping)
cannot silently drift between the two files.

Scope, five gaps identified by re-reading the shipped ``lib-trw.sh`` against
PRD-FIX-128 FR01/FR04/FR08/NFR03 that the existing host-file tests do not
exercise:

1. Key shapes containing an embedded space or a combined ``../`` + slash
   segment (FR04/NFR03) -- the existing identity test covers a bare ``a/b``
   and a bare ``..``, not these two shapes.
2. A malformed or unreadable ``pins.json`` reaching the DETECTOR's owned-run
   resolution (FR01/NFR02) -- the existing malformed-pin-store tests only
   exercise the FR08 reclamation sweep, not ``resolve_owned_run``.
3. A pin whose ``run_path`` escapes the project root (NFR03) -- not exercised
   anywhere in the host file.
4. The FR08 decision table's case 2 -- an EXPIRED pin record (dead pid, stale
   heartbeat) is age-bounded like "no pin record", while a LIVE pin (case 1)
   is kept regardless of age. The host file's sweep test covers case 1 (live)
   and case 3 (no record); case 2 is untested.
5. The legacy single-file latch path is removed as the mkdir precondition the
   same way the epoch path is (FR03) -- the host file only exercises this for
   the epoch marker.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_core_247_degraded_mode_hooks import (
    _HOOK_DIRS,
    _backdate,
    _env,
    _epoch_marker,
    _hook_log,
    _latch_marker,
    _make_project,
    _pin_run,
    _run,
    _seed_event_log,
    _tool_row,
    _write_pins,
)

_ROOT = Path(__file__).resolve().parent.parent

if not (_ROOT.parent / "scripts").is_dir():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )


@pytest.fixture(params=_HOOK_DIRS, ids=lambda p: p.parent.name)
def hook_dir(request: pytest.FixtureRequest) -> Path:
    return Path(request.param)


def _prompt_payload(
    root: Path, payload: dict[str, object], env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = _env(root)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["sh", str(root / ".claude" / "hooks" / "user-prompt-submit.sh")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        cwd=root,
        env=env,
        check=False,
    )


# ---------------------------------------------------------------------------
# 1. FR04/NFR03 -- additional rejected key shapes: embedded space, "../x"
# ---------------------------------------------------------------------------


def test_pin_key_with_embedded_space_or_dotdot_slash_is_rejected_without_traversal(
    tmp_path: Path, hook_dir: Path
) -> None:
    """FR04/NFR03: a space or a combined dot-dot-plus-slash key creates nothing.

    The existing identity test covers a bare ``a/b`` (one separator) and a
    bare ``..`` (the dot-dot-only shape) as two of its five cases. Neither
    exercises a key containing whitespace (rejected by the
    ``[!A-Za-z0-9._-]`` character class, never asserted directly) nor a key
    that combines a dot-dot segment with a path separator (``../x``, the
    shape an attacker would actually try against a filename built by string
    concatenation). Both must be rejected before any filesystem call, exactly
    like the already-tested shapes, and must leave the same operator-visible
    trail: one ``degraded_identity_unresolved`` record.
    """
    now = datetime.now(timezone.utc)

    def _armed(label: str) -> Path:
        root = _make_project(tmp_path, hook_dir, f"unsafe-key-{label}")
        _run(root, "session-start.sh", {"source": "startup", "session_id": "armed"})
        _seed_event_log(root, [_tool_row("Bash", now)])
        from tests.test_core_247_degraded_mode_hooks import _age_epoch

        _age_epoch(root, seconds=600, prompt_index=3, key="armed")
        (root / ".trw" / "context" / "hook-executions.log").unlink(missing_ok=True)
        return root

    cases = (
        ("dotdot-slash", "../x"),
        ("embedded-space", "a b"),
        ("leading-absolute", "/etc/passwd"),
    )
    for label, unsafe_key in cases:
        root = _armed(label)
        before = sorted(q.name for q in (root / ".trw" / "runtime").iterdir())
        result = _prompt_payload(root, {"prompt": "go", "session_id": unsafe_key})
        assert result.returncode == 0, (label, result.stderr)
        assert "DEGRADED" not in result.stdout, (label, result.stdout)
        records = [line for line in _hook_log(root) if "degraded_identity_unresolved" in line]
        assert len(records) == 1, (label, records)
        assert "reason=" in records[0], (label, records[0])
        after = sorted(q.name for q in (root / ".trw" / "runtime").iterdir())
        assert after == before, (
            f"{label}: a rejected key ({unsafe_key!r}) created something under the runtime "
            f"directory -- traversal or an unvalidated path component. before={before} after={after}"
        )
        # No file was created anywhere outside the project tree (the classic
        # ``../`` traversal target) or as a top-level sibling of the project.
        assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
            p.name for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith("unsafe-key-")
        ), f"{label}: an unsafe key ({unsafe_key!r}) created a file outside its own project directory"


# ---------------------------------------------------------------------------
# 2. FR01/NFR02 -- a malformed or unreadable pins.json reaches the DETECTOR,
#    not only the FR08 sweep, and the detector still falls back correctly.
# ---------------------------------------------------------------------------


def test_malformed_or_unreadable_pins_store_makes_the_detector_fall_back_to_pinless(
    tmp_path: Path, hook_dir: Path
) -> None:
    """FR01/NFR02: ``resolve_owned_run`` degrades to the pinless log, per file.

    The existing malformed/unreadable-pins-store tests exercise only the FR08
    reclamation sweep (``trw_degraded_sweep_markers``). Nothing in the host
    file drives a broken pin store through the DETECTOR's ownership lookup
    (``resolve_owned_run``, consumed by ``trw_observed_trw_tool_call``). This
    proves the fallback is a genuine read-then-degrade, not a coincidence of
    fixture shape: a run pinned behind the broken store holds a fresh
    ``trw_checkpoint`` row that the detector must NOT be able to see, so the
    verdict has to come from the pinless log alone in both directions.
    """
    now = datetime.now(timezone.utc)

    def _armed(label: str) -> Path:
        root = _make_project(tmp_path, hook_dir, f"broken-pins-{label}")
        _run(root, "session-start.sh", {"source": "startup", "session_id": "s"})
        from tests.test_core_247_degraded_mode_hooks import _age_epoch

        _age_epoch(root, seconds=600, prompt_index=1, key="s")
        # A real pin, with a real run holding a FRESH trw_ row, exists on disk --
        # so if the broken store were somehow still consulted, the session
        # would wrongly read as "surface present" for the wrong reason.
        _pin_run(root, "s", rows=[_tool_row("trw_checkpoint", now)])
        return root

    for label, corrupt in (
        ("malformed", lambda p: p.write_text("{not valid json[", encoding="utf-8")),
        ("unreadable", lambda p: p.chmod(0o000)),
    ):
        # (a) Pinless log is genuinely stale/absent => the fallback must see
        #     silence and the block must fire, proving the owned run's fresh
        #     row was NOT the source of the verdict.
        root = _armed(f"{label}-silent")
        pins_path = root / ".trw" / "runtime" / "pins.json"
        corrupt(pins_path)
        _seed_event_log(root, [_tool_row("Bash", now - timedelta(hours=6))])
        result = _prompt_payload(root, {"prompt": "go", "session_id": "s"})
        assert result.returncode == 0, (label, result.stderr)
        assert "TRW DEGRADED MODE" in result.stdout, (
            f"{label} pins.json: the detector must fall back to the pinless log and find silence "
            f"there, not silently treat the broken store as evidence of a present surface. "
            f"stdout={result.stdout!r}"
        )

        # (b) Pinless log DOES hold a fresh trw_ row => the fallback must see
        #     that and stay silent -- proving the pinless scan genuinely ran.
        present = _armed(f"{label}-present")
        pins_path2 = present / ".trw" / "runtime" / "pins.json"
        corrupt(pins_path2)
        _seed_event_log(present, [_tool_row("trw_session_start", now)])
        result2 = _prompt_payload(present, {"prompt": "go", "session_id": "s"})
        assert result2.returncode == 0, (label, result2.stderr)
        assert "DEGRADED" not in result2.stdout, (
            f"{label} pins.json: a fresh trw_ row in the pinless log was not honored once the "
            f"pin store could not be read. stdout={result2.stdout!r}"
        )
        if pins_path2.exists():
            pins_path2.chmod(0o644)  # restore so tmp_path cleanup can remove it

        if pins_path.exists():
            pins_path.chmod(0o644)


def test_malformed_pins_store_also_leaves_reclamation_untouched(tmp_path: Path, hook_dir: Path) -> None:
    """FR08 companion to the detector-side test above: same corrupt store,
    same session, the SessionStart sweep still prunes nothing.

    Written as one project so the two effects (detector fallback and
    reclamation) are asserted against the identical broken pins.json rather
    than two independently-built fixtures that could silently diverge.
    """
    root = _make_project(tmp_path, hook_dir, "broken-pins-sweep")
    _run(root, "session-start.sh", {"source": "startup", "session_id": "old"})
    (root / ".trw" / "config.yaml").write_text("degraded_marker_retention_hours: 1\n", encoding="utf-8")
    _backdate(_epoch_marker(root, "old"), hours=48.0)
    (root / ".trw" / "runtime" / "pins.json").write_text("{not valid json[", encoding="utf-8")

    _run(root, "session-start.sh", {"source": "startup", "session_id": "sweeper"})

    assert _epoch_marker(root, "old").exists(), (
        "a malformed pin store pruned a marker via the SessionStart sweep -- an unparseable store "
        "can never widen reclamation"
    )


# ---------------------------------------------------------------------------
# 3. NFR03 -- a pin whose run_path escapes the project root is not followed
# ---------------------------------------------------------------------------


def test_pinned_run_path_escaping_project_root_is_not_followed(tmp_path: Path, hook_dir: Path) -> None:
    """NFR03 acceptance: containment holds even when the run itself is valid.

    ``resolve_owned_run`` requires the resolved ``run_path`` to sit inside
    ``.trw/runs/`` (or the configured task-root runs layout) under the
    project root. A pin whose ``run_path`` points at a directory that
    otherwise looks exactly like a real run -- ``meta/run.yaml`` and a fresh
    ``trw_checkpoint`` row in ``meta/events.jsonl`` -- must still be rejected
    by the containment check and the detector must fall back to the pinless
    log, which stays genuinely stale here so the block fires.
    """
    now = datetime.now(timezone.utc)
    root = _make_project(tmp_path, hook_dir, "escaping-run")
    _run(root, "session-start.sh", {"source": "startup", "session_id": "escapee"})
    from tests.test_core_247_degraded_mode_hooks import _age_epoch

    _age_epoch(root, seconds=600, prompt_index=1, key="escapee")
    _seed_event_log(root, [_tool_row("Bash", now - timedelta(hours=6))])

    # A run directory that is a SIBLING of the project root -- outside every
    # layout resolve_owned_run is willing to walk.
    outside_run = tmp_path / "outside-run" / "meta"
    outside_run.mkdir(parents=True, exist_ok=True)
    (outside_run / "run.yaml").write_text("task: escapee-task\ncomplexity_class: MINIMAL\n", encoding="utf-8")
    (outside_run / "events.jsonl").write_text(json.dumps(_tool_row("trw_checkpoint", now)) + "\n", encoding="utf-8")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _write_pins(
        root,
        {
            "escapee": {
                "client_hint": None,
                "created_ts": stamp,
                "last_heartbeat_ts": stamp,
                "pid": os.getpid(),
                "run_path": str(outside_run.parent),
            }
        },
    )

    result = _prompt_payload(root, {"prompt": "go", "session_id": "escapee"})
    assert result.returncode == 0, result.stderr
    assert "TRW DEGRADED MODE" in result.stdout, (
        "a pin whose run_path escapes the project root was followed: the fresh trw_checkpoint row "
        "in the escaping run was treated as this session's evidence instead of being rejected by "
        "the containment check"
    )


# ---------------------------------------------------------------------------
# 4. FR08 decision table case 2 -- an EXPIRED pin record is age-bounded,
#    distinct from case 1 (live pin, kept regardless of age) and case 3 (no
#    pin record at all, already covered in the host file).
# ---------------------------------------------------------------------------


def test_expired_pin_record_is_age_bounded_while_a_live_pin_survives_the_same_sweep(
    tmp_path: Path, hook_dir: Path
) -> None:
    """FR08 acceptance: the full four-way decision table in one sweep.

    ``idle-but-live`` (case 1: pid alive, whatever the heartbeat) is kept
    however old its marker is -- the host file already asserts this in
    isolation. ``expired-old`` (case 2: pid dead AND heartbeat older than
    ``pin_ttl_hours`` -- an EXPIRED pin RECORD, not an ABSENT one) is
    age-bounded exactly like a session with no pin record at all, and its
    marker is old enough to cross the retention bound, so it must be
    reclaimed. ``expired-fresh`` shares the same expired pin record but its
    marker has NOT yet crossed the retention bound, so it must survive --
    proving the age bound is actually being applied to case 2, not simply
    "any pin record present keeps the marker forever".
    """
    root = _make_project(tmp_path, hook_dir, "expired-pin-sweep")
    (root / ".trw" / "config.yaml").write_text(
        "degraded_marker_retention_hours: 1\npin_ttl_hours: 1\n", encoding="utf-8"
    )
    for key in ("idle-but-live", "expired-old", "expired-fresh"):
        _run(root, "session-start.sh", {"source": "startup", "session_id": key})
        _latch_marker(root, key).parent.mkdir(parents=True, exist_ok=True)
        _latch_marker(root, key).write_text("", encoding="utf-8")

    # idle-but-live: PID alive, heartbeat irrelevant, marker aged well past
    # the 1-hour retention bound -- must be KEPT (case 1).
    _backdate(_epoch_marker(root, "idle-but-live"), hours=5.0)
    _backdate(_latch_marker(root, "idle-but-live"), hours=5.0)
    live_epoch = _epoch_marker(root, "idle-but-live").read_text(encoding="utf-8")

    # expired-old: PID dead, heartbeat older than pin_ttl_hours=1, marker aged
    # past the 1-hour retention bound -- must be RECLAIMED (case 2).
    _backdate(_epoch_marker(root, "expired-old"), hours=5.0)
    _backdate(_latch_marker(root, "expired-old"), hours=5.0)

    # expired-fresh: same expired pin record, marker NOT yet past the
    # retention bound -- must be KEPT (case 2, age bound not crossed yet).
    _backdate(_epoch_marker(root, "expired-fresh"), hours=0.1)
    _backdate(_latch_marker(root, "expired-fresh"), hours=0.1)

    now_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    stale_heartbeat = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _write_pins(
        root,
        {
            "idle-but-live": {
                "pid": os.getpid(),
                "last_heartbeat_ts": stale_heartbeat,
                "run_path": str(root),
            },
            "expired-old": {
                "pid": 999999999,
                "last_heartbeat_ts": stale_heartbeat,
                "run_path": str(root),
            },
            "expired-fresh": {
                "pid": 999999999,
                "last_heartbeat_ts": stale_heartbeat,
                "run_path": str(root),
            },
        },
    )
    assert now_stamp  # constructed for readability; the heartbeat above is what the ttl check reads

    _run(root, "session-start.sh", {"source": "startup", "session_id": "sweeper"})

    assert _epoch_marker(root, "idle-but-live").exists(), "case 1 (live pid): kept regardless of age"
    assert _epoch_marker(root, "idle-but-live").read_text(encoding="utf-8") == live_epoch
    assert _latch_marker(root, "idle-but-live").exists()

    assert not _epoch_marker(root, "expired-old").exists(), (
        "case 2 (expired pin RECORD, not absent): a marker past the retention bound must be "
        "reclaimed exactly as an unpinned identity's marker is -- an expired record is not a live one"
    )
    assert not _latch_marker(root, "expired-old").exists()

    assert _epoch_marker(root, "expired-fresh").exists(), (
        "case 2's age bound was applied even though the marker had not yet crossed the retention "
        "threshold -- an expired pin record must not become an immediate-eviction signal"
    )
    assert _latch_marker(root, "expired-fresh").exists()


# ---------------------------------------------------------------------------
# 5. FR03 -- the legacy single-file latch path is removed as the mkdir
#    precondition, exactly as the epoch path already is.
# ---------------------------------------------------------------------------


def test_legacy_latch_file_is_removed_when_a_session_emits(tmp_path: Path, hook_dir: Path) -> None:
    """FR03: a pre-existing single-file latch converts to the keyed directory.

    Before PRD-FIX-128, ``.trw/runtime/degraded-mode`` was one project-scoped
    FILE. The host file proves the equivalent conversion for the epoch path
    (``test_epoch_markers_are_keyed_per_session`` part c) but never exercises
    it for the latch path, whose write only happens on the EMIT path (the
    latch is written by the offline-block emitter, not by SessionStart). A
    project upgraded mid-life-cycle can carry that legacy file into a fresh
    degraded verdict, so the emit path itself must tolerate it.
    """
    now = datetime.now(timezone.utc)
    root = _make_project(tmp_path, hook_dir, "legacy-latch")
    legacy_latch_file = root / ".trw" / "runtime" / "degraded-mode"
    legacy_latch_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_latch_file.write_text("", encoding="utf-8")
    assert legacy_latch_file.is_file()

    _run(root, "session-start.sh", {"source": "startup", "session_id": "leg"})
    from tests.test_core_247_degraded_mode_hooks import _age_epoch

    _seed_event_log(root, [_tool_row("trw_deliver", now - timedelta(hours=6))])
    _age_epoch(root, seconds=600, prompt_index=1, key="leg")

    fired = _prompt_payload(root, {"prompt": "go", "session_id": "leg"})
    assert fired.returncode == 0, fired.stderr
    assert "TRW DEGRADED MODE" in fired.stdout, fired.stdout
    assert legacy_latch_file.is_dir(), (
        "the legacy single-file latch was not removed as the mkdir precondition -- the emitter "
        "would otherwise fail to create the keyed latch marker under a stale regular file"
    )
    assert _latch_marker(root, "leg").is_file(), "the keyed latch marker was not created after conversion"

"""PRD-FIX-128 external audit fixes (agy postimpl findings, 2026-09-04/05).

An independent cross-vendor audit of the landed PRD-FIX-128 produced 13
findings against ``lib-trw.sh``. Each row was re-verified here against the
CURRENT hooks by reading and by executing the real hook functions in a temp
repo, and confirmed rows were fixed. See the PRD's "External audit findings
addressed" table for the row-by-row verdict, including the rows fixed
elsewhere (row 12 in ``test_core_247_degraded_mode_hooks.py``, in place) or
resolved without a code change (row 4, row 6, row 11 — PRD/comment wording
only; row 10 — deferred, conflicts with a locked NFR03 test).

Deliberately a SEPARATE file from ``test_core_247_degraded_mode_hooks.py``
(untouched here except for a single in-place fix at row 12) and from
``test_fix_128_degraded_mode_coverage.py`` (a concurrent lane) — no shared
fixture names are defined here that either of those files also defines, only
imported by name from the module that already owns them.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.test_core_247_degraded_mode_hooks import (
    _HOOK_DIRS,
    _age_epoch,
    _backdate,
    _env,
    _epoch_marker,
    _iso,
    _latch_marker,
    _make_project,
    _run,
    _seed_event_log,
    _tool_row,
    _write_pins,
    hook_dir,
)

__all__ = ["hook_dir"]  # re-exported fixture; silence an unused-import lint

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The commit this session started from (see git log at session start): the
# unpatched shell library, used as the "before" half of two red-first
# comparisons below (rows 1 and 9) so the regression is demonstrated by
# actually running the old code, not asserted from memory.
_PRE_FIX_COMMIT = "e74389584d"


def _old_lib_text() -> str:
    return subprocess.run(
        ["git", "show", f"{_PRE_FIX_COMMIT}:trw-mcp/src/trw_mcp/data/hooks/lib-trw.sh"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _source_and_run(root: Path, lib: Path, script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "-c", f'. "{lib}" >/dev/null 2>&1; {script}'],
        text=True,
        capture_output=True,
        cwd=root,
        env=env,
        check=False,
    )


# ---------------------------------------------------------------------------
# Row 1 — `.`, `..`, and a leading `-` were admitted as session keys.
# ---------------------------------------------------------------------------


#: Keys the OLD character-class test actually admitted (every character in
#: each is individually in the allowed set, and none contains the two-dot
#: substring `*..*` already excluded pre-fix). `..` is deliberately NOT here:
#: the pre-fix pattern `*..*` already matched and rejected it (it IS two dots
#: in a row), so it is not part of the row 1 regression -- only `.` (which
#: contains no `..` substring) and a leading `-` are.
_ROW1_PREVIOUSLY_ADMITTED = [".", "-", "-rf", "--force"]


@pytest.mark.parametrize("bad_key", _ROW1_PREVIOUSLY_ADMITTED)
def test_row1_marker_path_rejects_dot_and_leading_dash(tmp_path: Path, hook_dir: Path, bad_key: str) -> None:
    """P1 CONFIRMED and fixed: `trw_degraded_marker_path` admitted `.` and any
    `-`-prefixed key.

    `.` resolves the marker path to the parent directory itself; a leading
    `-` risks being read as an option flag downstream. Both pass the old
    character-class test because every character in each is individually
    admitted (`-` and `.` are both in the allowed set).
    """
    root = _make_project(tmp_path, hook_dir, f"row1-{re.sub(r'[^a-z]', 'x', bad_key.lower()) or 'dash'}")
    lib = root / ".claude" / "hooks" / "lib-trw.sh"
    env = _env(root)
    env["TRW_SESSION_ID"] = bad_key
    probe = _source_and_run(root, lib, "trw_degraded_marker_path epoch", env)
    assert probe.returncode != 0, f"key {bad_key!r} was admitted as a marker path: {probe.stdout!r}"
    assert probe.stdout == "", f"key {bad_key!r} printed a path: {probe.stdout!r}"

    # Red half: the pre-fix library admitted this same key (proves the
    # assertion above is a real regression test, not a tautology of a
    # function that always rejects everything).
    old_lib = tmp_path / f"old-lib-trw-{bad_key!r}.sh"
    old_lib.write_text(_old_lib_text(), encoding="utf-8")
    old_probe = _source_and_run(root, old_lib, "trw_degraded_marker_path epoch", env)
    assert old_probe.returncode == 0, (
        f"key {bad_key!r} was ALREADY rejected before the fix — this case does not "
        "exercise the row 1 regression"
    )


def test_row1_double_dot_was_already_rejected_before_the_fix(tmp_path: Path, hook_dir: Path) -> None:
    """`..` is excluded by the audit's row 1 title but was NOT actually part of
    the regression: the pre-fix `*..*` glob already matches and rejects a
    literal `..` key. Asserted here so the fix's scope is stated precisely
    rather than implied by the finding's title.
    """
    root = _make_project(tmp_path, hook_dir, "row1-dotdot")
    lib = root / ".claude" / "hooks" / "lib-trw.sh"
    env = _env(root)
    env["TRW_SESSION_ID"] = ".."
    assert _source_and_run(root, lib, "trw_degraded_marker_path epoch", env).returncode != 0

    old_lib = tmp_path / "old-lib-trw-dotdot.sh"
    old_lib.write_text(_old_lib_text(), encoding="utf-8")
    assert _source_and_run(root, old_lib, "trw_degraded_marker_path epoch", env).returncode != 0, (
        "'..' was admitted by the pre-fix library -- if this ever starts failing, row 1's scope "
        "has changed and the docstring above needs updating"
    )


def test_row1_a_well_formed_key_is_still_admitted(tmp_path: Path, hook_dir: Path) -> None:
    """Sanity: the row 1 fix rejects by SHAPE, not by breaking the function."""
    root = _make_project(tmp_path, hook_dir, "row1-sanity")
    lib = root / ".claude" / "hooks" / "lib-trw.sh"
    env = _env(root)
    env["TRW_SESSION_ID"] = "normal-key123"
    ok = _source_and_run(root, lib, "trw_degraded_marker_path epoch", env)
    assert ok.returncode == 0
    assert ok.stdout.strip().endswith("/session-epoch/normal-key123")


# ---------------------------------------------------------------------------
# Rows 2/3 — the legacy `.trw/runtime/degraded-mode` regular file was
# migrated only via the epoch writer's dir_ready call, never via the latch
# clear path, and the sweep's `find` could read the unmigrated file as a
# stale marker at depth 0.
# ---------------------------------------------------------------------------


def test_row2_clear_latch_migrates_the_legacy_regular_file(tmp_path: Path, hook_dir: Path) -> None:
    """P1 CONFIRMED and fixed: `trw_clear_degraded_latch` alone must migrate
    a pre-FR02 `.trw/runtime/degraded-mode` regular file to a directory.
    """
    root = _make_project(tmp_path, hook_dir, "row2")
    legacy = root / ".trw" / "runtime" / "degraded-mode"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("legacy-shared-latch\n", encoding="utf-8")
    assert legacy.is_file()

    lib = root / ".claude" / "hooks" / "lib-trw.sh"
    env = _env(root)
    env["TRW_SESSION_ID"] = "mig"
    result = _source_and_run(root, lib, "trw_clear_degraded_latch", env)
    assert result.returncode == 0
    assert legacy.is_dir(), "the legacy regular file was never migrated by trw_clear_degraded_latch alone"


def test_row3_sweep_migrates_both_directories_before_scanning(tmp_path: Path, hook_dir: Path) -> None:
    """P1 CONFIRMED and fixed: `trw_degraded_sweep_markers` must migrate BOTH
    legacy regular files before its `find` call, or an unmigrated file is
    read as a stale marker at depth 0 and can be deleted.
    """
    root = _make_project(tmp_path, hook_dir, "row3")
    legacy_epoch = root / ".trw" / "runtime" / "session-epoch"
    legacy_latch = root / ".trw" / "runtime" / "degraded-mode"
    legacy_epoch.parent.mkdir(parents=True, exist_ok=True)
    legacy_epoch.write_text("legacy-shared-epoch\n0\n", encoding="utf-8")
    legacy_latch.write_text("legacy-shared-latch\n", encoding="utf-8")
    assert legacy_epoch.is_file() and legacy_latch.is_file()

    lib = root / ".claude" / "hooks" / "lib-trw.sh"
    env = _env(root)
    env["TRW_SESSION_ID"] = "sweeper"
    result = _source_and_run(root, lib, "trw_degraded_sweep_markers nobody-key", env)
    assert result.returncode == 0
    assert legacy_epoch.is_dir(), "the legacy epoch file was not migrated before the sweep ran"
    assert legacy_latch.is_dir(), "the legacy latch file was not migrated before the sweep ran"


# ---------------------------------------------------------------------------
# Row 5 — jq tolerates a non-dict pin VALUE; the python3 fallback aborted the
# entire scan on the same input, so one malformed pins.json entry disabled
# reclamation for every other identity on a python3-only host.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("force_no_jq", [False, True], ids=["jq", "no-jq-python3-fallback"])
def test_row5_malformed_pin_entry_does_not_block_pruning_of_others(
    tmp_path: Path, hook_dir: Path, force_no_jq: bool
) -> None:
    """P2 CONFIRMED and fixed: a non-dict pin VALUE (e.g. ``{"s1": null}``)
    must be skipped per-row, on both the jq and the python3 code path, rather
    than disabling the whole sweep.
    """
    label = "row5-nojq" if force_no_jq else "row5-jq"
    root = _make_project(tmp_path, hook_dir, label)
    (root / ".trw" / "config.yaml").write_text("degraded_marker_retention_hours: 1\n", encoding="utf-8")
    _run(root, "session-start.sh", {"source": "startup", "session_id": "gone"})
    _latch_marker(root, "gone").parent.mkdir(parents=True, exist_ok=True)
    _latch_marker(root, "gone").write_text("", encoding="utf-8")
    _backdate(_epoch_marker(root, "gone"), hours=2.0)
    _backdate(_latch_marker(root, "gone"), hours=2.0)
    # "gone" itself is deliberately UNPINNED (identity with no pin record);
    # the only pins.json entry is the malformed one from the audit's own
    # example.
    _write_pins(root, {"malformed-null": None})

    env = _env(root)
    if force_no_jq:
        bin_dir = tmp_path / f"bin-{label}"
        bin_dir.mkdir(exist_ok=True)
        for tool in (
            "sh",
            "python3",
            "grep",
            "head",
            "sed",
            "tr",
            "cat",
            "cut",
            "tail",
            "wc",
            "mkdir",
            "rm",
            "mv",
            "date",
            "find",
            "dirname",
            "kill",
        ):
            resolved = shutil.which(tool)
            assert resolved is not None, f"required tool missing from the test environment: {tool}"
            (bin_dir / tool).symlink_to(resolved)
        env["PATH"] = str(bin_dir)

    result = subprocess.run(
        ["sh", str(root / ".claude" / "hooks" / "session-start.sh")],
        input=json.dumps({"source": "startup", "session_id": "sweeper"}),
        text=True,
        capture_output=True,
        cwd=root,
        env=env,
        check=False,
    )
    assert result.returncode == 0
    assert not _epoch_marker(root, "gone").exists(), (
        f"[{label}] a malformed pins.json entry (a non-dict VALUE) blocked pruning of an "
        "unrelated, genuinely unpinned identity's expired marker"
    )


# ---------------------------------------------------------------------------
# Row 6 — REFUTED, not fixed. Documented here as a confirmatory test rather
# than a red-first fix: the audit proposed replacing the `&&` short circuit
# in trw_observed_trw_tool_call with an unconditional fall-through, on the
# theory that it "inverts the detector on fresh runs". Implementing that
# literally broke
# test_detector_reads_the_owned_run_event_log_before_the_pinless_fallback
# arm (d), which pins the OPPOSITE behavior by design. This test documents
# WHY: _trw_scan_log_for_trw_call's return code 0 covers both "found" and
# "could not be used as evidence", and the pinless call site shares that
# same fail-open direction, so the short circuit and an explicit
# fall-through are provably the same function.
# ---------------------------------------------------------------------------


def test_row6_short_circuit_is_equivalent_to_falling_through(tmp_path: Path, hook_dir: Path) -> None:
    """REFUTED: consulting the pinless log after a missing owned-run log
    changes no verdict, because both fail-open the same way.
    """
    now = datetime.now(timezone.utc)
    root = _make_project(tmp_path, hook_dir, "row6")
    lib = root / ".claude" / "hooks" / "lib-trw.sh"

    # The pinless log holds a real, confirmed-negative (non-trw_) row.
    _seed_event_log(root, [_tool_row("Bash", now)])
    missing_owned = root / "does" / "not" / "exist" / "events.jsonl"
    cut = _iso(now - timedelta(seconds=1))

    env = _env(root)
    # Directly exercise the two-call sequence trw_observed_trw_tool_call runs,
    # bypassing pin resolution entirely so this is a pure statement about the
    # scan primitive's fail-open composition.
    script = (
        f'_trw_scan_log_for_trw_call "{missing_owned}" "{cut}"; owned_rc=$?; '
        f'_trw_scan_log_for_trw_call "{root / ".trw" / "context" / "session-events.jsonl"}" "{cut}"; '
        "pinless_rc=$?; "
        'echo "owned=$owned_rc pinless=$pinless_rc"'
    )
    result = _source_and_run(root, lib, script, env)
    assert result.returncode == 0
    assert result.stdout.strip() == "owned=0 pinless=1", (
        "a missing owned-run log must report rc=0 (the fail-open code, shared with 'found'), and "
        "a confirmed-negative pinless log must report rc=1 -- proving the short circuit in "
        "trw_observed_trw_tool_call ('owned rc==0 -> return 0 immediately') and an unconditional "
        "fall-through that then discards a confirmed-negative pinless answer produce the SAME "
        "final verdict (present) either way"
    )


# ---------------------------------------------------------------------------
# Row 7 — the tail-line bound was an untyped, unbounded env var.
# ---------------------------------------------------------------------------


def test_row7_event_tail_lines_is_a_typed_bounded_tunable(tmp_path: Path, hook_dir: Path) -> None:
    """P2 CONFIRMED and fixed: `degraded_event_tail_lines` is now a Pydantic
    field with an admission record, read through `trw_config_int` via a
    dedicated accessor mirroring its three siblings.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._field_admission_registry import FIELD_ADMISSIONS

    typed_default = TRWConfig().degraded_event_tail_lines
    assert typed_default == 500
    assert "degraded_event_tail_lines" in FIELD_ADMISSIONS

    root = _make_project(tmp_path, hook_dir, "row7")
    lib = root / ".claude" / "hooks" / "lib-trw.sh"
    assert "trw_degraded_event_tail_lines" in lib.read_text(encoding="utf-8")

    for bad in ("banana", "0", "3", "1000000"):
        env = _env(root)
        env["TRW_SESSION_EVENT_TAIL_LINES"] = bad
        probe = _source_and_run(root, lib, "trw_degraded_event_tail_lines", env)
        assert probe.stdout.strip() == str(typed_default), (bad, probe.stdout)

    env = _env(root)
    env["TRW_SESSION_EVENT_TAIL_LINES"] = "750"
    probe = _source_and_run(root, lib, "trw_degraded_event_tail_lines", env)
    assert probe.stdout.strip() == "750"


# ---------------------------------------------------------------------------
# Row 8 — get_task_root did not sanitize a leading `/` or a `..` segment.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hostile_root", ["/etc", "..", "../../etc", "../escaped"])
def test_row8_get_task_root_rejects_traversal_and_absolute_paths(
    tmp_path: Path, hook_dir: Path, hostile_root: str
) -> None:
    """P1 CONFIRMED and fixed: a `task_root` config value starting with `/`
    or containing a `..` segment must fall back to the documented default
    ("docs") rather than widening `resolve_owned_run`'s containment check.
    """
    root = _make_project(tmp_path, hook_dir, f"row8-{re.sub(r'[^a-z]', '', hostile_root.lower()) or 'x'}")
    (root / ".trw" / "config.yaml").write_text(f"task_root: {hostile_root}\n", encoding="utf-8")
    lib = root / ".claude" / "hooks" / "lib-trw.sh"
    env = _env(root)
    probe = _source_and_run(root, lib, "get_task_root", env)
    assert probe.returncode == 0
    assert probe.stdout == "docs", f"hostile task_root {hostile_root!r} was not rejected: {probe.stdout!r}"


def test_row8_a_well_formed_task_root_is_still_honored(tmp_path: Path, hook_dir: Path) -> None:
    root = _make_project(tmp_path, hook_dir, "row8-sanity")
    (root / ".trw" / "config.yaml").write_text("task_root: work\n", encoding="utf-8")
    lib = root / ".claude" / "hooks" / "lib-trw.sh"
    probe = _source_and_run(root, lib, "get_task_root", _env(root))
    assert probe.stdout == "work"


# ---------------------------------------------------------------------------
# Row 9 — the no-jq awk fallback matched "event"/"tool_name" as an unanchored
# substring anywhere on the line, including inside another tool call's
# payload text, and extracted the first "ts" field rather than the row's own.
# ---------------------------------------------------------------------------


def test_row9_no_jq_fallback_does_not_false_positive_on_payload_text(tmp_path: Path, hook_dir: Path) -> None:
    """P2 CONFIRMED and fixed: replaced the unanchored `awk` text match with a
    real JSON parser (python3), matching the precedent already set by
    resolve_owned_run and _trw_pin_rows.
    """
    now = datetime.now(timezone.utc)
    root = _make_project(tmp_path, hook_dir, "row9")
    _run(root, "session-start.sh", {"source": "startup", "session_id": "r9"})
    # A single line that is INVALID JSON: a writer bug (or a torn/partial
    # flush) left an unescaped quote inside the "command" argument, so the
    # embedded text "event": "tool_invocation", "tool_name": "trw_checkpoint"
    # appears on the raw line with real, unescaped double quotes -- exactly
    # what a properly-escaped JSON string value can never produce (escaping
    # would leave `\"`, which the awk pattern below does not match, as
    # verified directly: a hostile value built through json.dumps, escaped
    # correctly, does NOT fool the old fallback either). A real JSON parser
    # rejects this line outright; the unanchored `awk` regex does not care
    # whether the line parses at all.
    hostile_line = (
        '{"event": "tool_invocation", "tool_name": "Bash", "success": true, '
        f'"ts": "{_iso(now)}", '
        '"args": {"command": "echo "event": "tool_invocation", "tool_name": "trw_checkpoint", '
        '"ts": "9999-01-01T00:00:00""}}'
    )
    with pytest.raises(json.JSONDecodeError):
        json.loads(hostile_line)
    events_path = root / ".trw" / "context" / "session-events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(hostile_line + "\n", encoding="utf-8")
    _age_epoch(root, seconds=600, prompt_index=1, key="r9")

    bin_dir = tmp_path / "bin-row9-nojq"
    bin_dir.mkdir(exist_ok=True)
    for tool in (
        "sh",
        "python3",
        "grep",
        "head",
        "sed",
        "tr",
        "cat",
        "cut",
        "tail",
        "awk",
        "wc",
        "mkdir",
        "rm",
        "mv",
        "date",
        "find",
        "dirname",
        "kill",
    ):
        resolved = shutil.which(tool)
        assert resolved is not None, f"required tool missing from the test environment: {tool}"
        (bin_dir / tool).symlink_to(resolved)
    env = _env(root)
    env["PATH"] = str(bin_dir)

    result = subprocess.run(
        ["sh", str(root / ".claude" / "hooks" / "user-prompt-submit.sh")],
        input=json.dumps({"prompt": "go", "session_id": "r9"}),
        text=True,
        capture_output=True,
        cwd=root,
        env=env,
        check=False,
    )
    assert result.returncode == 0
    assert "TRW DEGRADED MODE" in result.stdout, (
        "a malformed line whose unescaped TEXT merely mentions event/tool_name/trw_ fields fooled "
        "the no-jq fallback into treating it as a real trw_ tool call"
    )

    # Red half: run the SAME hostile input through the pre-fix library's
    # scan primitive directly and confirm it WAS fooled.
    old_lib = tmp_path / "old-lib-trw-row9.sh"
    old_lib.write_text(_old_lib_text(), encoding="utf-8")
    cut = _iso(now - timedelta(seconds=1))
    old_probe = _source_and_run(
        root,
        old_lib,
        f'_trw_scan_log_for_trw_call "{events_path}" "{cut}"',
        env,
    )
    assert old_probe.returncode == 0, (
        "the pre-fix awk fallback did NOT match this hostile payload text -- this fixture does "
        "not exercise the row 9 regression"
    )


# ---------------------------------------------------------------------------
# Row 13 — the static empty-argument regex in test_run_ownership.py admits a
# LITERAL empty-string argument (`resolve_owned_run ""`). Added here as
# supplementary coverage rather than editing that locked file.
# ---------------------------------------------------------------------------

_EMPTY_STRING_ARG_RE = re.compile(r"""resolve_owned_run\s+(""|'')(?!\S)""")


def test_row13_no_hook_calls_resolve_owned_run_with_a_literal_empty_string() -> None:
    """P2 CONFIRMED and closed by a stronger, supplementary static check.

    ``test_run_ownership.py::test_no_hook_resolves_ownership_with_an_empty_argument_list``
    only checks that ``resolve_owned_run`` is followed by ``"``, ``'``, or
    ``$`` -- a literal ``resolve_owned_run ""`` satisfies that and is never
    caught. This is a narrower, closable gap than "an unquoted empty
    variable", which is a dynamic value and cannot be verified statically.
    """
    offenders: list[str] = []
    for hook_dir_path in _HOOK_DIRS:
        for path in sorted(hook_dir_path.rglob("*.sh")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if _EMPTY_STRING_ARG_RE.search(stripped):
                    offenders.append(f"{path}:{lineno}: {stripped}")
    assert offenders == [], f"resolve_owned_run called with a literal empty-string argument: {offenders}"


def test_row13_regex_is_not_vacuous() -> None:
    """A planted violation must be caught, or the check above proves nothing."""
    assert _EMPTY_STRING_ARG_RE.search('resolve_owned_run ""')
    assert _EMPTY_STRING_ARG_RE.search("resolve_owned_run ''")
    # A real fallback argument must NOT be flagged.
    assert not _EMPTY_STRING_ARG_RE.search('resolve_owned_run "$_stdin_session_id"')
    assert not _EMPTY_STRING_ARG_RE.search('resolve_owned_run "${1:-}"')

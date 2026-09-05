"""PRD-FIX-124: auto-recall scoring, tunables, deadline and fail-open behaviour.

Every behavioural assertion here drives the REAL hook script as a subprocess
(FR10). There is no Python reimplementation of the scorer to test against: the
shipped ``user-prompt-submit.sh`` is the only implementation, and a test that
restated its formula in Python would pass while the hook was broken — which is
precisely the state this PRD found the mechanism in.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests._auto_recall_hook_harness import (
    _HOOK_PATHS,
    _copy_hook_to_temp,
    _run_hook,
)
from tests._auto_recall_hook_harness import diagnostic as _diagnostic

_BUNDLED_HOOK = next(p for p in _HOOK_PATHS if "/src/trw_mcp/data/hooks/" in p.as_posix())


# --------------------------------------------------------------------------
# FR01 / FR02 — scoring against the learning's own summary + tags
# --------------------------------------------------------------------------


def test_score_uses_learning_summary_and_tags(tmp_path: Path) -> None:
    """FR01: a learning matched ONLY through its tags scores above zero.

    Before this change ``matches`` was counted against the summary alone
    (`keyword in lower_summary`), so a prompt whose vocabulary lived in the tag
    list could not score at all.
    """
    result = _run_hook(
        tmp_path / "tags-only",
        _BUNDLED_HOOK,
        prompt="procrastinate worker schema",
        phase="implement",
        learnings=[
            {
                "learning_id": "L-tagged",
                "status": "active",
                "summary": "The relation is created by a separate apply step, not by a migration",
                "tags": ["procrastinate", "worker", "schema"],
            },
        ],
    )

    assert "[L-tagged]" in result.stdout
    diagnostic = _diagnostic(result.project_root)
    # Every prompt keyword is present in the learning's token set -> exactly 1.0.
    assert diagnostic["top_score"] == "1.000"
    assert diagnostic["decision"] == "fired"


def test_reads_block_sequence_tags(tmp_path: Path) -> None:
    """FR02: a ``tags:`` block sequence parses to exactly those strings.

    Proven behaviourally rather than by inspecting a parser: a prompt built only
    from the tag strings fires, and the same prompt against an otherwise
    identical entry with the tags removed does not.
    """
    with_tags = _run_hook(
        tmp_path / "with-tags",
        _BUNDLED_HOOK,
        prompt="alembic procrastinate migrations",
        phase="implement",
        learnings=[
            {
                "learning_id": "L-with-tags",
                "status": "active",
                "summary": "Unrelated wording that shares nothing with the prompt",
                "tags": ["alembic", "procrastinate", "migrations"],
            },
        ],
    )
    without_tags = _run_hook(
        tmp_path / "without-tags",
        _BUNDLED_HOOK,
        prompt="alembic procrastinate migrations",
        phase="implement",
        learnings=[
            {
                "learning_id": "L-without-tags",
                "status": "active",
                "summary": "Unrelated wording that shares nothing with the prompt",
            },
        ],
    )

    assert "[L-with-tags]" in with_tags.stdout
    assert "[L-without-tags]" not in without_tags.stdout
    assert _diagnostic(without_tags.project_root)["decision"] == "no_match"


def test_missing_tags_key_still_scores_on_summary(tmp_path: Path) -> None:
    """FR02: no ``tags`` key yields an empty list, and the summary still scores."""
    result = _run_hook(
        tmp_path / "no-tags",
        _BUNDLED_HOOK,
        prompt="alembic migration ordering",
        phase="implement",
        learnings=[
            {
                "learning_id": "L-summary-only",
                "status": "active",
                "summary": "alembic migration ordering is decided by down_revision",
            },
        ],
    )

    assert "[L-summary-only]" in result.stdout


def test_matching_is_token_exact_not_substring(tmp_path: Path) -> None:
    """FR01: ``core`` must not match ``score``, ``test`` must not match ``latest``.

    Substring containment silently inflated the match count; the PRD measured it
    admitting its first off-domain firing a full 0.10 above the token-exact
    variant.
    """
    result = _run_hook(
        tmp_path / "substring",
        _BUNDLED_HOOK,
        prompt="core test",
        phase="implement",
        learnings=[
            {
                "learning_id": "L-substrings",
                "status": "active",
                "summary": "the latest score was recorded",
            },
        ],
    )

    assert "[L-substrings]" not in result.stdout
    assert _diagnostic(result.project_root)["decision"] == "no_match"


# --------------------------------------------------------------------------
# FR06 / FR07 — the typed tunables
# --------------------------------------------------------------------------


def test_default_threshold_is_recalibrated() -> None:
    """FR06: all three declaration sites report 0.35.

    The hook cannot import Pydantic, so its inline fallback is a third
    declaration site; nothing but this assertion keeps the three in step.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._sub_models import OrchestrationConfig

    assert TRWConfig().auto_recall_min_score == 0.35
    assert OrchestrationConfig().auto_recall_min_score == 0.35
    for hook_path in _HOOK_PATHS:
        content = hook_path.read_text(encoding="utf-8")
        assert '_auto_recall_min_score="0.35"' in content
        assert "DEFAULT_MIN_SCORE = 0.35" in content


def test_threshold_is_bounded_to_the_unit_interval() -> None:
    """FR06/NFR03: a value outside [0.0, 1.0] is rejected at config load."""
    from pydantic import ValidationError

    from trw_mcp.models.config import TRWConfig

    for bad in (-0.1, 1.5):
        with pytest.raises(ValidationError):
            TRWConfig(auto_recall_min_score=bad)


def test_hook_applies_the_default_threshold_with_no_config(tmp_path: Path) -> None:
    """FR06: no config file and no env override -> the hook applies 0.35."""
    result = _run_hook(
        tmp_path / "default-threshold",
        _BUNDLED_HOOK,
        prompt="wal reset corruption",
        phase="implement",
        learnings=[{"learning_id": "L-x", "status": "active", "summary": "unrelated"}],
    )

    assert _diagnostic(result.project_root)["threshold"] == "0.350"


def test_scan_cap_is_typed_config(tmp_path: Path) -> None:
    """FR07: ``auto_recall_scan_cap`` is a typed, bounded, admitted field, and the
    diagnostic's ``scanned`` equals ``min(store_size, cap)`` on both sides of it."""
    from pydantic import ValidationError

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._field_admission_registry import FIELD_ADMISSIONS
    from trw_mcp.models.config._sub_models import OrchestrationConfig

    assert TRWConfig().auto_recall_scan_cap == 10000
    assert OrchestrationConfig().auto_recall_scan_cap == 10000
    assert isinstance(TRWConfig().auto_recall_scan_cap, int)
    with pytest.raises(ValidationError):
        TRWConfig(auto_recall_scan_cap=0)
    assert FIELD_ADMISSIONS["auto_recall_scan_cap"].budget_decision == "admitted"

    # store smaller than the cap -> the whole store is scanned
    under = _run_hook(
        tmp_path / "under-cap",
        _BUNDLED_HOOK,
        prompt="entirely unrelated vocabulary",
        phase="implement",
        learnings=[{"learning_id": f"L-u{i}", "status": "active", "summary": "filler"} for i in range(7)],
    )
    assert _diagnostic(under.project_root)["scanned"] == "7"

    # store larger than the cap -> exactly cap entries are scanned
    over = _run_hook(
        tmp_path / "over-cap",
        _BUNDLED_HOOK,
        prompt="entirely unrelated vocabulary",
        phase="implement",
        learnings=[{"learning_id": f"L-o{i}", "status": "active", "summary": "filler"} for i in range(12)],
        env_overrides={"TRW_AUTO_RECALL_SCAN_CAP": "5"},
    )
    assert _diagnostic(over.project_root)["scanned"] == "5"


def test_scan_cap_reads_config_yaml(tmp_path: Path) -> None:
    """FR07: the cap is read from ``.trw/config.yaml`` like the other four knobs."""
    result = _run_hook(
        tmp_path / "cap-from-yaml",
        _BUNDLED_HOOK,
        prompt="entirely unrelated vocabulary",
        phase="implement",
        learnings=[{"learning_id": f"L-c{i}", "status": "active", "summary": "filler"} for i in range(9)],
        config_yaml="auto_recall_scan_cap: 4\n",
    )

    assert _diagnostic(result.project_root)["scanned"] == "4"


# --------------------------------------------------------------------------
# FR08 — the deadline emits best-so-far
# --------------------------------------------------------------------------


#: Forced deadline for the FR08 cases. Sized with margin at both ends against
#: the measured cost of the filler corpus below: comfortably longer than the
#: pre-scan glob (so the scan always starts) and comfortably shorter than a full
#: pass (so it always expires mid-scan).
_FORCED_DEADLINE_NS = 40_000_000
_DEADLINE_FILLER = 3000


def _filler(count: int) -> list[dict[str, Any]]:
    """Entries that are expensive to parse and match nothing in the prompt."""
    return [
        {
            "learning_id": f"L-filler-{i}",
            "status": "active",
            "summary": "unrelated filler wording " * 80,
            "file_stem": f"zzz-{i:05d}",
        }
        for i in range(count)
    ]


def _hook_with_deadline(source_hook: Path, target: Path, timeout_ns: int) -> Path:
    """Write a copy of the real hook whose only edit is the deadline constant.

    The scan path, the scorer and the emission loop are byte-for-byte the
    shipped ones; forcing the deadline is the only way to observe a mid-scan
    expiry deterministically.
    """
    content = source_hook.read_text(encoding="utf-8")
    assert "TIMEOUT_NS = 500_000_000" in content
    target.write_text(content.replace("TIMEOUT_NS = 500_000_000", f"TIMEOUT_NS = {timeout_ns}"), encoding="utf-8")
    target.chmod(0o755)
    return target


#: Bounded retries absorb a scheduler stall between process start and the
#: first loop iteration -- the 40 ms deadline above is sized with margin
#: against a typical box (comfortably past the pre-scan glob, comfortably
#: short of a full pass); a box running several concurrent agent sessions can
#: still starve the process before it even reaches the loop, driving
#: ``scanned`` to 0. Confirmed 2026-09-04: this test failed under a
#: ``make test-release`` run with concurrent load, passing 3/3 serially. The
#: deadline itself is never widened -- each attempt reruns with the SAME
#: ``_FORCED_DEADLINE_NS`` against a fresh fixture, so a genuine regression
#: (the hook still discards everything it accumulated) fails identically on
#: every attempt, same shape as the retry established in 1577304208.
_DEADLINE_SCAN_RETRY_ATTEMPTS = 3


def test_deadline_emits_best_so_far(tmp_path: Path) -> None:
    """FR08: a mid-scan deadline emits what it already found instead of discarding it.

    The pre-change hook raised ``SystemExit(0)`` here, throwing away every match
    already accumulated — with the raised scan cap that is the difference
    between a partial answer and no answer at all.
    """

    def _attempt(index: int) -> tuple[dict[str, Any], str]:
        staging = tmp_path / f"staging-{index}"
        staging.mkdir(parents=True, exist_ok=True)
        slow_hook = _hook_with_deadline(_BUNDLED_HOOK, staging / "user-prompt-submit.sh", _FORCED_DEADLINE_NS)

        result = _run_hook(
            tmp_path / f"deadline-{index}",
            slow_hook,
            prompt="alembic procrastinate schema apply",
            phase="implement",
            # "aaa-match" sorts first, so the match is always inside the prefix
            # the scan gets through; the expensive filler behind it guarantees
            # the deadline expires before the scan can finish.
            learnings=[
                {
                    "learning_id": "L-early-match",
                    "status": "active",
                    "summary": "alembic procrastinate schema apply is a separate step",
                    "file_stem": "aaa-match",
                },
                *_filler(_DEADLINE_FILLER),
            ],
        )
        return _diagnostic(result.project_root), result.stdout

    diagnostic: dict[str, Any] = {}
    stdout = ""
    for index in range(_DEADLINE_SCAN_RETRY_ATTEMPTS):
        diagnostic, stdout = _attempt(index)
        scanned = int(diagnostic["scanned"])
        if (
            diagnostic["decision"] == "deadline"
            and 0 < scanned <= _DEADLINE_FILLER
            and "TRW RECALL:" in stdout
            and "[L-early-match]" in stdout
        ):
            return

    scanned = int(diagnostic["scanned"])
    assert diagnostic["decision"] == "deadline"
    # Non-vacuous on BOTH sides: the scan must have started (or the assertion
    # below would be about an empty prefix) and must not have finished.
    assert 0 < scanned <= _DEADLINE_FILLER, (
        f"deadline did not cut the scan mid-way on any of {_DEADLINE_SCAN_RETRY_ATTEMPTS} attempts: scanned={scanned}"
    )
    assert "TRW RECALL:" in stdout
    assert "[L-early-match]" in stdout


def test_deadline_before_any_match_is_silent(tmp_path: Path) -> None:
    """FR08: with no above-threshold match accumulated, stdout stays empty."""
    staging = tmp_path / "staging-empty"
    staging.mkdir(parents=True, exist_ok=True)
    slow_hook = _hook_with_deadline(_BUNDLED_HOOK, staging / "user-prompt-submit.sh", _FORCED_DEADLINE_NS)

    result = _run_hook(
        tmp_path / "deadline-empty",
        slow_hook,
        prompt="alembic procrastinate schema apply",
        phase="done",
        learnings=list(_filler(_DEADLINE_FILLER)),
    )

    assert result.stdout == ""
    assert _diagnostic(result.project_root)["decision"] == "deadline"


# --------------------------------------------------------------------------
# FR12 — the mirror's status field is the candidate gate
# --------------------------------------------------------------------------


def test_obsolete_status_in_mirror_excludes_entry(tmp_path: Path) -> None:
    """FR12: an entry whose MIRRORED status is obsolete is never a candidate,
    even when it is the best lexical match in the store."""
    result = _run_hook(
        tmp_path / "obsolete",
        _BUNDLED_HOOK,
        prompt="wal reset corruption recovery",
        phase="implement",
        learnings=[
            {
                "learning_id": "L-retired",
                "status": "obsolete",
                "summary": "wal reset corruption recovery is handled by the salvage path",
                "tags": ["wal", "corruption", "recovery"],
            },
            {
                "learning_id": "L-live",
                "status": "active",
                "summary": "wal reset corruption needs a passive checkpoint gate",
            },
        ],
    )

    assert "[L-retired]" not in result.stdout
    assert _diagnostic(result.project_root)["top_id"] != "L-retired"


def test_read_model_contract_document_exists() -> None:
    """FR12: the contract is written down, not implicit in the code."""
    doc = Path(__file__).resolve().parents[2] / "docs/documentation/operational-knowledge/auto-recall-calibration.md"
    text = doc.read_text(encoding="utf-8")
    assert "read model" in text.lower()
    for field in ("`status`", "`id`", "`summary`", "`tags`"):
        assert field in text, f"the contract must name the {field} field it depends on"
    assert "0.35" in text, "the committed record must state the shipped threshold"


# --------------------------------------------------------------------------
# NFR01-NFR06
# --------------------------------------------------------------------------


def test_scoring_budget_under_deadline(tmp_path: Path) -> None:
    """NFR01: a 10,000-entry store scores inside the 500ms deadline."""
    result = _run_hook(
        tmp_path / "budget",
        _BUNDLED_HOOK,
        prompt="wal reset corruption recovery in the memory database",
        phase="implement",
        learnings=[
            {
                "learning_id": f"L-perf-{i}",
                "status": "active",
                "summary": f"entry {i} about assorted engineering topics and their gotchas",
                "tags": ["performance", "scan", f"topic-{i % 40}"],
                "file_stem": f"perf-{i:05d}",
            }
            for i in range(10_000)
        ],
    )

    diagnostic = _diagnostic(result.project_root)
    assert diagnostic["scanned"] == "10000"
    assert diagnostic["decision"] != "deadline"
    assert int(diagnostic["elapsed_ms"]) < 500


@pytest.mark.parametrize(
    ("case", "env_overrides", "entry_body"),
    [
        ("malformed_yaml", None, "id: [unclosed\nstatus: active\nsummary: \x00\x01 broken\n"),
        ("scalar_tags", None, 'id: "L-scalar"\nstatus: active\nsummary: "wal reset"\ntags: notalist\n'),
        ("empty_summary", None, 'id: "L-empty"\nstatus: active\nsummary: ""\n'),
        ("bad_threshold", {"TRW_AUTO_RECALL_MIN_SCORE": "abc"}, 'id: "L-ok"\nstatus: active\nsummary: "wal reset"\n'),
        ("negative_cap", {"TRW_AUTO_RECALL_SCAN_CAP": "-1"}, 'id: "L-ok"\nstatus: active\nsummary: "wal reset"\n'),
    ],
)
def test_fail_open_on_corrupt_inputs(
    tmp_path: Path, case: str, env_overrides: dict[str, str] | None, entry_body: str
) -> None:
    """NFR02: every corrupt input exits 0 and never blocks the prompt."""
    project_root, hook_path, entries_dir = _copy_hook_to_temp(tmp_path / case, _BUNDLED_HOOK)
    (entries_dir / "entry.yaml").write_text(entry_body, encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "TRW_PROJECT_ROOT": str(project_root),
            "TRW_TEST_PHASE": "implement",
            "TRW_HOOK_LOG": str(project_root / "hook.log"),
        }
    )
    if env_overrides:
        env.update(env_overrides)

    completed = subprocess.run(
        ["sh", str(hook_path)],
        input=json.dumps({"prompt": "totally unrelated sourdough baking question"}),
        text=True,
        capture_output=True,
        cwd=project_root,
        env=env,
        check=False,
    )

    assert completed.returncode == 0
    assert "TRW RECALL:" not in completed.stdout


def test_fail_open_when_entries_directory_is_absent(tmp_path: Path) -> None:
    """NFR02: an absent entries directory exits 0 with no recall output."""
    project_root, hook_path, entries_dir = _copy_hook_to_temp(tmp_path / "no-entries", _BUNDLED_HOOK)
    entries_dir.rmdir()

    completed = subprocess.run(
        ["sh", str(hook_path)],
        input=json.dumps({"prompt": "wal reset corruption recovery"}),
        text=True,
        capture_output=True,
        cwd=project_root,
        env={
            **os.environ,
            "TRW_PROJECT_ROOT": str(project_root),
            "TRW_TEST_PHASE": "implement",
            "TRW_HOOK_LOG": str(project_root / "hook.log"),
        },
        check=False,
    )

    assert completed.returncode == 0
    assert "TRW RECALL:" not in completed.stdout


def test_diagnostic_line_omits_prompt_text(tmp_path: Path) -> None:
    """NFR03: the record carries counts, scores and learning IDs — never text."""
    secret = "hunter2correcthorsebattery"
    result = _run_hook(
        tmp_path / "secrets",
        _BUNDLED_HOOK,
        prompt=f"deploy with the token {secret} to production",
        phase="implement",
        learnings=[
            {
                "learning_id": "L-deploy",
                "status": "active",
                "summary": "deploy production token rotation is manual",
                "detail_text": "a detail body that must never be logged",
            },
        ],
    )

    log = (result.project_root / "hook.log").read_text(encoding="utf-8")
    assert secret not in log
    assert secret not in result.stderr
    assert "a detail body that must never be logged" not in log
    diagnostic = _diagnostic(result.project_root)
    assert set(diagnostic) == {
        "event",
        "keywords",
        "scanned",
        "top_score",
        "top_id",
        "threshold",
        "injected",
        "decision",
        "elapsed_ms",
    }


def test_no_keywords_still_records_a_decision(tmp_path: Path) -> None:
    """FR05/NFR03: a prompt of only stop words records ``no_keywords`` and stays silent."""
    result = _run_hook(
        tmp_path / "stopwords",
        _BUNDLED_HOOK,
        prompt="what will they take from this",
        phase="implement",
        learnings=[{"learning_id": "L-any", "status": "active", "summary": "wal reset"}],
    )

    diagnostic = _diagnostic(result.project_root)
    assert diagnostic["decision"] == "no_keywords"
    assert diagnostic["keywords"] == "0"
    assert "TRW RECALL:" not in result.stdout


def test_hook_opens_no_sqlite_handle() -> None:
    """NFR05: the hook never touches the memory database, so it cannot contend
    for a SQLite lock with a live MCP process."""
    for hook_path in _HOOK_PATHS:
        content = hook_path.read_text(encoding="utf-8")
        assert "sqlite3" not in content
        assert "memory.db" not in content
        assert "import trw_mcp" not in content


def test_token_budget_is_enforced(tmp_path: Path) -> None:
    """NFR06: the injected block stays inside ``auto_recall_max_tokens * 4`` chars."""
    max_tokens = 40
    result = _run_hook(
        tmp_path / "budget-cap",
        _BUNDLED_HOOK,
        prompt="alembic procrastinate schema apply migration ordering",
        phase="done",  # keeps the phase-guidance line out of stdout
        learnings=[
            {
                "learning_id": f"L-big-{i}",
                "status": "active",
                "summary": "alembic procrastinate schema apply migration ordering",
                "file_stem": f"big-{i}",
            }
            for i in range(3)
        ],
        env_overrides={"TRW_AUTO_RECALL_MAX_TOKENS": str(max_tokens)},
    )

    # Three learnings clear the threshold and only two fit, so this is a
    # truncation assertion and not an "it happened to fit" one.
    assert result.stdout.count("TRW RECALL:") == 2
    assert _diagnostic(result.project_root)["injected"] == "2"
    assert len(result.stdout) <= max_tokens * 4

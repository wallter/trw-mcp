"""``trw_checkpoint`` refuses a malformed ``factory:1`` payload at write time (FACTORY-START-VALIDATE).

W1 recorded STARTs with ``"subject"`` instead of ``"attempt"``. The tool accepted them silently and they only surfaced
in ``factory status`` as a negative start_to_ready that excluded three attempts. The journal is append-only, so the
fix belongs at the write: a known kind, a required ``attempt``, the reader's own schema, and no unknown keys, each
refusal naming the key. New writes only; the reader's classification of history is unchanged.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.state import _factory_experiment as fx

pytestmark = pytest.mark.integration

SHA = "a" * 40


@pytest.fixture
def run(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_project.parent))
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_project))
    monkeypatch.setenv("TRW_FACTORY_ENABLED", "1")
    monkeypatch.setattr(fx, "_utc_now", lambda: datetime(2026, 9, 29, 12, tzinfo=timezone.utc))
    path = tmp_project / ".trw" / "runs" / "task" / "run1"
    (path / "meta").mkdir(parents=True)
    (path / "meta" / "run.yaml").write_text("run_id: run1\nstatus: active\n", encoding="utf-8")
    (path / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    return path


def _write(run: Path, payload: dict[str, Any]) -> dict[str, Any]:
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    return execute_checkpoint(str(run), json.dumps({"factory": 1, **payload}), None)  # type: ignore[return-value]


def _journal(run: Path) -> str:
    return (run / "meta" / "events.jsonl").read_text(encoding="utf-8")


def _refused(run: Path, payload: dict[str, Any], *fragments: str) -> None:
    before = _journal(run)
    result = _write(run, payload)
    assert result["recorded"] is False, result
    assert result["reason"] == "factory_payload_invalid" and result["error_type"] == "factory_payload_invalid"
    for fragment in fragments:
        assert fragment in str(result["remedy"]), (fragment, result["remedy"])
    assert _journal(run) == before, "a refused payload must not touch the journal"


def test_a_start_with_subject_instead_of_attempt_is_refused_naming_the_missing_key(run: Path) -> None:
    _refused(run, {"kind": "START", "subject": "loop-speed-X"}, "missing key: attempt", "subject")


def test_an_unknown_key_is_refused_naming_it(run: Path) -> None:
    _refused(run, {"kind": "START", "attempt": "a1", "subjct_sha": SHA}, "unknown key", "subjct_sha")


def test_an_unknown_kind_is_refused(run: Path) -> None:
    _refused(run, {"kind": "STOP", "attempt": "a1", "reason": "x"}, "unknown kind", "START")
    # The value the caller sent is not repeated (E2E-INC-125); the accepted kinds are named instead.
    assert "STOP" not in str(_write(run, {"kind": "STOP", "attempt": "a1", "reason": "x"})["remedy"])
    _refused(run, {"kind": None, "attempt": "a1"}, "kind is required")


@pytest.mark.parametrize(
    ("payload", "fragment"),
    [
        ({"kind": "READY", "attempt": "a1", "receipts": {"verification": ["v1"]}}, "verification belongs to USED"),
        ({"kind": "START", "attempt": "a1", "receipts": {"build": ["b1"]}}, "START carries no receipts"),
        ({"kind": "USED", "attempt": "a1"}, "requires a nonempty receipts map"),
        ({"kind": "VOID", "attempt": "a1"}, "printable reason"),
        ({"kind": "START", "attempt": "has space"}, "attempt id must match"),
    ],
)
def test_the_readers_own_schema_is_enforced_at_write_time(run: Path, payload: dict[str, Any], fragment: str) -> None:
    _refused(run, payload, fragment)


def test_an_unknown_key_for_the_kind_is_refused_even_when_another_kind_allows_it(run: Path) -> None:
    _refused(run, {"kind": "VOID", "attempt": "a1", "reason": "r", "receipts": {"build": ["b1"]}}, "receipts")


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "START", "attempt": "a1"},
        {"kind": "START", "attempt": "a1", "subject_sha": SHA, "branch": "b", "base": "c", "note": "n"},
        {"kind": "START", "attempt": "a1", "model_id": "m", "tier": "frontier", "effort": "high", "client": "claude"},
    ],
)
def test_every_shape_the_workers_and_the_verifier_really_write_is_still_accepted(
    run: Path, payload: dict[str, Any]
) -> None:
    assert _write(run, payload)["recorded"] is True


def test_ready_and_used_with_real_receipts_are_still_accepted(run: Path, build_check_invoke: Any) -> None:
    built = build_check_invoke(tests_passed=True, scope="feature", run_path=str(run))
    assert _write(run, {"kind": "START", "attempt": "a1", "subject_sha": SHA})["recorded"] is True
    ready = _write(
        run,
        {"kind": "READY", "attempt": "a1", "subject_sha": SHA, "receipts": {"build": [built["build_receipt_id"]]}},
    )
    assert ready["recorded"] is True, ready

    used_shape = {
        "kind": "USED",
        "attempt": "a1",
        "by": "swarm-verifier",
        "receipts": {"verification": [{"run_path": ".trw/runs/task/run1", "receipt_id": "verification-x"}]},
    }
    # The receipt does not exist, so the RECEIPT gate refuses it; the point is that the PAYLOAD gate does not.
    result = _write(run, used_shape)
    assert result["recorded"] is False and result["reason"] == "factory_receipt_unresolved", result


def test_non_factory_messages_are_never_touched(run: Path) -> None:
    from trw_mcp.tools._orchestration_checkpoint import execute_checkpoint

    for message in ("finished the parser", '{"factory": 2, "kind": "whatever"}', '{"note": "no discriminator"}'):
        assert execute_checkpoint(str(run), message, None)["recorded"] is True  # type: ignore[index]


# --- E2E-INC-093..096 (S12 black-box report): write-time checks the reader or admit.py made only later ----------


@pytest.mark.parametrize("sha", ["xyz", "zz", "A" * 40, "a" * 39, "a" * 41, "g" * 40])
@pytest.mark.parametrize("kind", ["START", "READY"])
def test_a_subject_sha_that_is_not_40_lowercase_hex_is_refused(run: Path, kind: str, sha: str) -> None:
    """INC-093: admit.py refuses these later, but the append-only journal would keep the bad value forever."""
    payload: dict[str, Any] = {"kind": kind, "attempt": "a1", "subject_sha": sha}
    if kind == "READY":
        payload["receipts"] = {"build": ["build-x"]}
    _refused(run, payload, "subject_sha must be 40 lowercase hex")


def test_a_missing_kind_names_the_kinds(run: Path) -> None:
    """INC-094: not ``unknown kind 'None'``."""
    _refused(run, {"attempt": "a1"}, "kind is required (START|READY|USED|VOID)")


@pytest.mark.parametrize(("kind", "family"), [("READY", "build"), ("USED", "verification")])
def test_a_receipts_value_that_is_not_a_map_shows_the_shape(run: Path, kind: str, family: str) -> None:
    """INC-094: INC-065 gave the list branch an example; the string and missing branches had none."""
    _refused(run, {"kind": kind, "attempt": "a1", "receipts": "build-1"}, f'e.g. "receipts": {{"{family}": [')


def test_a_refusal_names_the_command_that_shows_attempt_state(run: Path) -> None:
    """INC-095: the agent cannot see its attempts from trw_status; the refusal points at the reader."""
    _refused(run, {"kind": "START", "attempt": "has space"}, "trw-mcp factory status --run")


def _lifecycle_refused(run: Path, payload: dict[str, Any], *fragments: str) -> None:
    before = _journal(run)
    result = _write(run, payload)
    assert result["recorded"] is False, result
    assert result["reason"] == result["error_type"] == "factory_lifecycle_invalid", result
    for fragment in (*fragments, "trw-mcp factory status --run"):
        assert fragment in str(result["remedy"]), (fragment, result["remedy"])
    assert _journal(run) == before, "a refused transition must not touch the journal"


def test_ready_without_a_start_is_refused(run: Path) -> None:
    """INC-096: the reader would count it incomplete (or excluded, once a START lands after it)."""
    _lifecycle_refused(
        run, {"kind": "READY", "attempt": "a1", "subject_sha": SHA, "receipts": {"build": ["b1"]}}, "no START"
    )


def test_void_for_an_attempt_that_never_reached_ready_is_refused(run: Path) -> None:
    """INC-096: the reader ignores a VOID before any READY; recording it retracts nothing."""
    _lifecycle_refused(run, {"kind": "VOID", "attempt": "ghost", "reason": "verifier FAIL"}, "no READY")
    assert _write(run, {"kind": "START", "attempt": "a1"})["recorded"] is True
    _lifecycle_refused(run, {"kind": "VOID", "attempt": "a1", "reason": "verifier FAIL"}, "no READY")


def test_a_second_start_for_the_same_attempt_is_refused(run: Path) -> None:
    """INC-096: a differing second START makes the attempt a conflict the reader excludes."""
    assert _write(run, {"kind": "START", "attempt": "a1", "subject_sha": SHA})["recorded"] is True
    _lifecycle_refused(run, {"kind": "START", "attempt": "a1", "subject_sha": "b" * 40}, "already recorded")
    _lifecycle_refused(run, {"kind": "START", "attempt": "a1", "subject_sha": SHA}, "already recorded")


def test_the_full_lifecycle_is_still_accepted(run: Path, build_check_invoke: Any) -> None:
    built = build_check_invoke(tests_passed=True, scope="feature", run_path=str(run))
    assert _write(run, {"kind": "START", "attempt": "a1", "subject_sha": SHA})["recorded"] is True
    ready = {"kind": "READY", "attempt": "a1", "subject_sha": SHA, "receipts": {"build": [built["build_receipt_id"]]}}
    assert _write(run, ready)["recorded"] is True
    void = {"kind": "VOID", "attempt": "a1", "reason": "verifier FAIL", "by": "swarm-verifier"}
    assert _write(run, void)["recorded"] is True  # the VOID shapes the verifier writes, now after a READY


def _git_commit(root: Path) -> str:
    import subprocess

    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}  # fmt: skip
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_a_subject_sha_that_names_no_commit_is_refused(run: Path, tmp_project: Path) -> None:
    """CHECKPOINT-SUBJECT-SHA-VERIFY: a hand-extended short sha is 40 hex but names nothing; refused at write time."""
    _git_commit(tmp_project)
    _refused(run, {"kind": "START", "attempt": "a1", "subject_sha": "1" * 40}, "subject_sha", "not a commit")


def test_a_subject_sha_that_names_a_commit_is_recorded(run: Path, tmp_project: Path) -> None:
    head = _git_commit(tmp_project)
    assert _write(run, {"kind": "START", "attempt": "a1", "subject_sha": head})["recorded"] is True


def test_a_git_that_cannot_run_is_a_structured_refusal(
    run: Path, tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r1 KI: a git execution failure is refused by name, never raised out of the checkpoint."""
    import subprocess

    head = _git_commit(tmp_project)

    def no_git(*_a: object, **_k: object) -> object:
        raise subprocess.TimeoutExpired(cmd="git", timeout=20)

    monkeypatch.setattr(subprocess, "run", no_git)
    _refused(run, {"kind": "START", "attempt": "a1", "subject_sha": head}, "could not be verified")


def test_a_git_dir_override_cannot_redirect_the_check(
    run: Path, tmp_project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """codex r1 KI: an inherited GIT_DIR pointing at another repository must not decide which commits exist."""
    other = tmp_path / "other"
    other.mkdir()
    (other / "f").write_text("another repository\n", encoding="utf-8")
    _git_commit(other)
    import subprocess

    subprocess.run(["git", "add", "f"], cwd=other, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "foreign"], cwd=other, check=True
    )
    foreign = subprocess.run(["git", "rev-parse", "HEAD"], cwd=other, capture_output=True, text=True).stdout.strip()
    _git_commit(tmp_project)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    _refused(run, {"kind": "START", "attempt": "a1", "subject_sha": foreign}, "not a commit")


def test_a_ready_without_subject_sha_is_refused_and_a_bound_one_is_not(run: Path) -> None:
    unbound = {"kind": "READY", "attempt": "a1", "receipts": {"build": ["build-x"]}}
    _refused(run, unbound, "READY needs subject_sha")

    result = _write(run, {**unbound, "subject_sha": "a" * 40})

    assert "READY needs subject_sha" not in str(result.get("remedy", ""))


def test_an_oversized_payload_is_refused_before_it_is_written_and_burns_no_attempt_id(run: Path) -> None:
    from trw_mcp.state._factory_receipt_gate import MAX_PAYLOAD_CHARS

    huge = {"kind": "START", "attempt": "a1", "note": "x" * (MAX_PAYLOAD_CHARS + 1)}
    _refused(run, huge, "over the", str(MAX_PAYLOAD_CHARS))

    # The attempt id is still free: the same attempt records normally afterwards.
    ok = _write(run, {"kind": "START", "attempt": "a1"})
    assert ok["recorded"] is True, ok


def test_the_offline_cli_checkpoint_refuses_what_the_mcp_tool_refuses(run: Path) -> None:
    """E2E-INC-125: `trw-mcp local checkpoint` wrote any factory line, skipping the size cap and the READY commit rule."""
    import json as _json

    from trw_mcp.services.orchestration_service import write_checkpoint

    before = _journal(run)
    for payload in (
        {"factory": 1, "kind": "START", "attempt": "a1", "note": "x" * 9000},
        {"factory": 1, "kind": "READY", "attempt": "a1", "receipts": {"build": ["build-x"]}},
        {"factory": 1, "kind": "READY", "attempt": "a2", "subject_sha": "a" * 40, "receipts": {"build": ["build-x"]}},
    ):
        with pytest.raises(ValueError, match="nothing was written"):
            write_checkpoint(_json.dumps(payload), run_path=run)

    assert _journal(run) == before, "a refused factory line must not touch the journal"
    write_checkpoint("plain checkpoint prose", run_path=run)  # an ordinary checkpoint is untouched
    write_checkpoint(_json.dumps({"factory": 1, "kind": "START", "attempt": "a1"}), run_path=run)

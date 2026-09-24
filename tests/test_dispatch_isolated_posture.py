"""PRD-CORE-297-FR02: the ``isolated-review`` posture exists and is refused everywhere.

No registry entry sets ``isolated_review`` in this slice, so every client refuses.
Admission needs read_only, an ``isolated_review`` spec, ``host_confinement`` and a
non-empty confinement prefix. The posture never claims ``posture_enforced``.
"""

from __future__ import annotations

import pytest

from trw_mcp.dispatch._client_specs import SUPPORTED_CLIENTS


@pytest.mark.parametrize("client", SUPPORTED_CLIENTS)
def test_every_client_is_refused_without_an_isolated_spec(client: str) -> None:
    from trw_mcp.dispatch._posture import ReviewerPostureError, verify_reviewer_posture

    with pytest.raises(ReviewerPostureError, match="isolated-review"):
        verify_reviewer_posture(client, "isolated-review", read_only=True)


def _admit(monkeypatch: pytest.MonkeyPatch, *, prefix: list[str], host_confinement: bool = True) -> None:
    from trw_mcp.dispatch import _posture
    from trw_mcp.dispatch._client_spec_types import IsolatedReviewSpec
    from trw_mcp.dispatch._client_specs import client_spec_for

    spec = client_spec_for("grok").model_copy(
        update={
            "isolated_review": IsolatedReviewSpec(mcp_list_argv=("grok", "mcp", "list"), mcp_empty_marker="none"),
            "host_confinement": host_confinement,
        }
    )
    monkeypatch.setattr(_posture, "client_spec_for", lambda _client: spec)
    monkeypatch.setattr(_posture, "confinement_prefix", lambda: prefix)


def test_a_confined_isolated_spec_is_admitted(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch._posture import reviewer_posture_enforced, verify_reviewer_posture

    _admit(monkeypatch, prefix=["sandbox-exec", "-p", "(version 1)"])
    verify_reviewer_posture("grok", "isolated-review", read_only=True)
    assert reviewer_posture_enforced("grok", "isolated-review") is False


@pytest.mark.parametrize(
    ("prefix", "host_confinement", "read_only"),
    [([], True, True), (["sandbox-exec"], False, True), (["sandbox-exec"], True, False)],
    ids=["no-wrapper-on-host", "spec-not-confinable", "writes-requested"],
)
def test_each_missing_condition_refuses(
    monkeypatch: pytest.MonkeyPatch, prefix: list[str], host_confinement: bool, read_only: bool
) -> None:
    from trw_mcp.dispatch._posture import ReviewerPostureError, verify_reviewer_posture

    _admit(monkeypatch, prefix=prefix, host_confinement=host_confinement)
    with pytest.raises(ReviewerPostureError):
        verify_reviewer_posture("grok", "isolated-review", read_only=read_only)


def test_resolution_refuses_with_exit_2_and_derives_the_posture_list() -> None:
    from trw_mcp.dispatch._resolve import DispatchResolutionError, _resolve_posture

    with pytest.raises(DispatchResolutionError) as refused:
        _resolve_posture("isolated-review", client="agy", read_only=True)
    assert refused.value.exit_code == 2 and "unknown dispatch posture" not in str(refused.value)
    with pytest.raises(DispatchResolutionError, match="isolated-review"):
        _resolve_posture("bogus", client="agy", read_only=True)


@pytest.mark.parametrize("client", SUPPORTED_CLIENTS)
def test_the_runner_refuses_every_client_before_spawn(monkeypatch: pytest.MonkeyPatch, client: str) -> None:
    from trw_mcp.dispatch import _runner, dispatch
    from trw_mcp.dispatch._types import DispatchRequest

    spawned: list[object] = []
    monkeypatch.setattr(_runner.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    result = dispatch(DispatchRequest(client=client, prompt="review", read_only=True, posture="isolated-review"))  # type: ignore[arg-type]
    assert result.ok is False and not spawned and "reviewer posture refused" in result.raw_stderr
    assert result.posture_enforced is False and result.isolation == "none"


def test_an_admissible_spec_enters_the_lane_never_the_ordinary_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch import _runner, dispatch
    from trw_mcp.dispatch._types import DispatchRequest

    _admit(monkeypatch, prefix=["sandbox-exec", "-p", "(version 1)"])
    spawned: list[object] = []
    monkeypatch.setattr(_runner.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    monkeypatch.setattr(_runner, "run_isolated", lambda req, _spawn: spawned.append("lane") or _spawn_refused(req))
    dispatch(DispatchRequest(client="grok", prompt="review", read_only=True, posture="isolated-review"))
    assert spawned == ["lane"]
    unlaned = dispatch(
        DispatchRequest(client="grok", prompt="r", read_only=True, posture="isolated-review"), _lane_home=None
    )
    assert spawned == ["lane", "lane"] and unlaned.isolation == "none"


def _spawn_refused(req: object) -> object:
    from trw_mcp.dispatch._runner import _early_result

    return _early_result(req, [], exit_code=-1, stderr="lane stub")  # type: ignore[arg-type]


def test_a_contaminated_result_is_never_ok() -> None:
    from trw_mcp.dispatch._types import DispatchResult

    common = {
        "client": "grok",
        "argv_redacted": [],
        "read_only_enforced": True,
        "exit_code": 0,
        "timed_out": False,
        "duration_s": 1.0,
        "text": "Verdict: PASS",
        "raw_stdout": "",
        "raw_stderr": "",
    }
    clean = DispatchResult(**common, isolation="snapshot-write-confined", contamination="clean")
    dirty = DispatchResult(
        **common, isolation="snapshot-write-confined", contamination="contaminated", changed_paths=["src/a.py"]
    )
    assert clean.ok is True
    assert dirty.ok is False and dirty.text == "Verdict: PASS", "findings are kept, marked untrusted by ok"
    assert DispatchResult(**common).contamination == "not-checked"

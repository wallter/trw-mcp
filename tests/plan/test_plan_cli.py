"""PRD-CORE-275-FR04/FR08/NFR01: the CLI adapter itself.

WHY THIS FILE EXISTS. An independent adversarial review found the CLI had ZERO
test coverage, and three of its findings were defects only reachable through the
CLI: `propose` wrote the precheck table to STDOUT above the JSON so
`plan propose > body.json` produced a file the next command could not parse; an
uncaught FormationError printed a traceback carrying absolute paths; and the
body reader echoed the path it failed to open. Every test here pins one of those.

The verbs run in-process through the real argument parser and handler rather than
through a subprocess, so the assertions are on the streams the handler writes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.plan.conftest import Scene

PID = "a" * 32


def _run(argv: list[str], scene: Scene | None = None) -> tuple[int, str, str]:
    """Invoke one plan verb through the real parser; return (exit, out, err)."""
    from trw_mcp.server._cli_argparse import _build_arg_parser
    from trw_mcp.tools._plan_cli import run_plan

    args = _build_arg_parser().parse_args(argv)
    import io
    import sys

    out, err = io.StringIO(), io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        run_plan(args)
        code = 0
    except SystemExit as exit_code:
        code = int(exit_code.code or 0)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return code, out.getvalue(), err.getvalue()


def _propose_argv(scene: Scene, **over: Any) -> list[str]:
    argv = [
        "plan",
        "propose",
        "--plan-id",
        over.get("plan_id", PID),
        "--revision",
        str(over.get("revision", 1)),
        "--path",
        over.get("path", "src/a.py"),
        "--summary",
        over.get("summary", "refactor a"),
        "--run",
        str(scene.runs["alpha"]),
    ]
    return argv


def test_propose_writes_only_json_to_stdout(scene: Scene) -> None:
    """THE regression. `plan propose > body.json` must produce parseable JSON.

    Before the fix stdout carried the precheck table above the body, so the file
    the next command read began with `src/a.py :: alpha :: ...` and json.loads
    failed on it.
    """
    code, out, err = _run(_propose_argv(scene))

    assert code == 0, err
    parsed = json.loads(out)  # the WHOLE stream, not the last line
    assert parsed["plan_id"] == PID
    assert parsed["paths"] == ["src/a.py"]


def test_propose_still_shows_the_precheck_on_stderr(scene: Scene) -> None:
    """Moving the table off stdout must not silently delete it."""
    _, _, err = _run(_propose_argv(scene))

    assert "src/a.py" in err
    assert "alpha" in err
    assert "not live intent" in err  # the advisory note


def test_precheck_writes_its_table_to_stdout(scene: Scene) -> None:
    """The one verb whose OUTPUT is the table."""
    code, out, _ = _run(["plan", "precheck", "src/a.py", "--run", str(scene.runs["alpha"])])

    assert code == 0
    assert "src/a.py" in out and "alpha" in out


def test_a_missing_formation_refuses_without_a_traceback_or_a_path(tmp_path: Any, scene: Scene) -> None:
    """FormationError used to escape uncaught, printing absolute paths."""
    code, out, err = _run(["plan", "precheck", "src/a.py", "--run", str(tmp_path / "not-a-run")])

    assert code == 1
    assert out == ""
    assert "Traceback" not in err
    assert "formation_unavailable" in err
    assert "/home/" not in err and str(tmp_path) not in err


def test_a_bad_path_refuses_with_its_reason_and_no_body(scene: Scene) -> None:
    code, out, err = _run(_propose_argv(scene, path="/etc/passwd"))

    assert code == 1
    assert out == "", "a refusal must not emit a partial body"
    assert "absolute_path" in err


def test_an_unreadable_body_does_not_echo_the_path(scene: Scene, tmp_path: Any) -> None:
    """NFR01: the caller already knows what it passed; a peer must not learn it."""
    secret = tmp_path / "secret-directory-name" / "body.json"

    code, out, err = _run(["plan", "review", "--body", str(secret), "--run", str(scene.runs["beta"])])

    assert code == 1
    assert out == ""
    assert "secret-directory-name" not in err
    assert str(tmp_path) not in err


def test_review_refuses_a_tampered_proposal(scene: Scene, tmp_path: Any) -> None:
    _, body, _ = _run(_propose_argv(scene))
    tampered = json.loads(body)
    tampered["summary"] = "changed after digesting"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(tampered), encoding="utf-8")

    code, out, err = _run(["plan", "review", "--body", str(path), "--run", str(scene.runs["beta"])])

    assert code == 1
    assert out == ""
    assert "digest_mismatch" in err


def test_review_emits_only_json_and_carries_the_reviewers_own_finding(scene: Scene, tmp_path: Any) -> None:
    _, body, _ = _run(_propose_argv(scene))
    path = tmp_path / "proposal.json"
    path.write_text(body, encoding="utf-8")

    code, out, err = _run(["plan", "review", "--body", str(path), "--run", str(scene.runs["beta"])])

    assert code == 0, err
    review = json.loads(out)
    assert review["type"] == "review"
    assert any("alpha" in finding for finding in review["findings"])


def test_verify_exits_three_on_a_stale_review(scene: Scene, tmp_path: Any) -> None:
    """A non-current verdict is scriptable, not just printable."""
    _, first, _ = _run(_propose_argv(scene, revision=1))
    proposal_one = tmp_path / "p1.json"
    proposal_one.write_text(first, encoding="utf-8")
    review_path = tmp_path / "r1.json"
    _, review, _ = _run(["plan", "review", "--body", str(proposal_one), "--run", str(scene.runs["beta"])])
    review_path.write_text(review, encoding="utf-8")

    _, second, _ = _run(_propose_argv(scene, revision=2, summary="refactor a, again"))
    proposal_two = tmp_path / "p2.json"
    proposal_two.write_text(second, encoding="utf-8")

    current, out_current, _ = _run(["plan", "verify", "--review", str(review_path), "--proposal", str(proposal_one)])
    stale, out_stale, _ = _run(["plan", "verify", "--review", str(review_path), "--proposal", str(proposal_two)])

    assert (current, "CURRENT" in out_current) == (0, True)
    assert (stale, "STALE" in out_stale) == (3, True)


def test_an_oversized_body_refuses_before_stdout(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    """Over the LOCAL bound is a refusal, and nothing partial escapes."""
    from trw_mcp.models.config import TRWConfig

    tiny = TRWConfig(comms_body_max_bytes=64)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: tiny)

    code, out, err = _run(_propose_argv(scene, summary="x" * 400))

    assert code == 1
    assert out == ""
    assert "field_too_large" in err


def test_an_unknown_verb_exits_two(scene: Scene) -> None:
    code, _, err = _run(["plan"])

    assert code == 2
    assert "usage:" in err

"""PRD-CORE-300-FR07 slice S5: `trw-mcp prd create` / `trw-mcp prd diff` golden replay.

Three assertions:

(a) `trw-mcp prd create --json` into a temp project writes a PRD that
    `trw_prd_validate` accepts (a real end-to-end round trip, not a stub).
(b) The three golden PRD fixtures (``tests/fixtures/golden_prds``) keep the
    verdicts pinned in ``verdicts.json`` — reuses ``test_prd_golden_verdicts``'s
    own helper so the two files can never silently diverge on what "the
    verdict" means.
(c) The reviewer role and a dispatched child both refuse `prd create` and
    leave no file behind.

The CLI is invoked in-process (the real parser plus the real dispatch in
``_cli.py::main``), exactly like ``test_cli_replacement_contract.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_prds"


def _run_cli(argv: list[str], cwd: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, str, str]:
    """Invoke the real ``trw-mcp`` CLI entry in-process; return (exit_code, stdout, stderr)."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    from trw_mcp.server._cli import main

    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "argv", ["trw-mcp", *argv])
    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            main()
    except SystemExit as exc:
        code = 0 if exc.code is None else int(exc.code) if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


def _tree_files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# (a) create --json round-trips through trw_prd_validate
# ---------------------------------------------------------------------------


def test_prd_create_json_writes_a_prd_that_validate_accepts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".trw").mkdir()

    code, out, err = _run_cli(
        [
            "prd",
            "create",
            "--input-text",
            "Add rate limiting to the public API endpoints",
            "--category",
            "CORE",
            "--priority",
            "P1",
            "--json",
        ],
        tmp_path,
        monkeypatch,
    )
    assert code == 0, err
    document = json.loads(out)
    assert isinstance(document, dict)
    output_path = Path(document["output_path"])
    assert output_path.is_file(), f"prd create reported a path it did not write: {output_path}"
    assert document["prd_id"].startswith("PRD-CORE-")

    from trw_mcp.models.config import get_config
    from trw_mcp.tools.requirements import validate_prd_quality_v2

    verdict = validate_prd_quality_v2(
        output_path.read_text(encoding="utf-8"), get_config(), include_dynamic_checks=False
    )
    # A fresh skeleton is a low-tier draft, not an error — trw_prd_validate
    # accepts it (runs to completion, returns a real score) rather than
    # rejecting the shape entirely.
    assert verdict.total_score >= 0
    assert verdict.quality_tier is not None


def test_prd_diff_reports_the_golden_fixtures_delta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`trw-mcp prd diff` on two golden fixtures matches the underlying helper's own report."""
    from trw_mcp.tools.query_tools import prd_diff_report

    before = _GOLDEN / "tier_draft.md"
    after = _GOLDEN / "tier_approved.md"
    expected = prd_diff_report(before_path=str(before), after_path=str(after))

    code, out, err = _run_cli(
        ["prd", "diff", "--before-path", str(before), "--after-path", str(after), "--json"],
        tmp_path,
        monkeypatch,
    )
    assert code == 0, err
    assert json.loads(out) == expected


# ---------------------------------------------------------------------------
# (b) golden verdicts keep pinned (delegates to test_prd_golden_verdicts)
# ---------------------------------------------------------------------------


def test_golden_prd_verdicts_stay_pinned_through_the_cli_move() -> None:
    from tests.test_prd_golden_verdicts import _fixtures, verdict

    recorded = json.loads((_GOLDEN / "verdicts.json").read_text(encoding="utf-8"))
    for fixture in _fixtures():
        assert verdict(fixture.read_text(encoding="utf-8")) == recorded[fixture.name], (
            f"{fixture.name} verdict moved when PRD creation moved to the CLI"
        )


# ---------------------------------------------------------------------------
# (c) reviewer / dispatched-child refusal leaves no file behind
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_key", "env_value"),
    [("TRW_SURFACE_ROLE", "reviewer"), ("TRW_DISPATCH_CHILD", "1")],
    ids=["reviewer_role", "dispatched_child"],
)
def test_prd_create_refuses_under_guard_and_writes_nothing(
    env_key: str, env_value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".trw").mkdir()
    before = _tree_files(tmp_path)
    monkeypatch.setenv(env_key, env_value)

    code, out, err = _run_cli(
        ["prd", "create", "--input-text", "Should never be written", "--category", "CORE", "--json"],
        tmp_path,
        monkeypatch,
    )

    assert code != 0
    assert "prd create" in err
    assert _tree_files(tmp_path) == before, "a refused prd create must write no file"


def test_prd_diff_runs_under_reviewer_role_and_dispatched_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`prd diff` is read-only, so it is NOT covered by the state-changing refusal."""
    for env_key, env_value in (("TRW_SURFACE_ROLE", "reviewer"), ("TRW_DISPATCH_CHILD", "1")):
        monkeypatch.setenv(env_key, env_value)
        code, out, err = _run_cli(
            [
                "prd",
                "diff",
                "--before-path",
                str(_GOLDEN / "tier_draft.md"),
                "--after-path",
                str(_GOLDEN / "tier_approved.md"),
                "--json",
            ],
            tmp_path,
            monkeypatch,
        )
        assert code == 0, err
        monkeypatch.delenv(env_key, raising=False)

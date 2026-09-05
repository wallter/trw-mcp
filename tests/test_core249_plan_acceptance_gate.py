"""PRD-CORE-249 FR04/FR06 + NFR01/NFR02 — the plan-acceptance gate.

FR04: anchored enumeration, the unresolved-scope rule, and the four-way block
predicate (mode x task_type x enumerated x unmet).
FR06: the same behaviour proved through the REAL ``run_trw_deliver`` with no
patch on ``evaluate_delivery_gates`` or the gate module — and deleting the
dispatch call site must turn that test red.
NFR01: the added deliver latency stays inside its budget.
NFR02: fail-CLOSED on a malformed declaration; fail-OPEN on an unwritable
handoff file and on a raising readback.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.plan_acceptance import AcceptanceDeclarationError, AcceptanceStatus, parse_status_token
from trw_mcp.tools import _plan_acceptance_gate as gate

# --------------------------------------------------------------------------- #
# FR04 — enumeration (pure)
# --------------------------------------------------------------------------- #

_ANCHORED_PLAN = """# Plan

P-1: preconditions gathered
- X-1 the first acceptance item
- [ ] X-2: unchecked item
* [x] AC-3 — done item
| X-4 | a table row |
X-5

Some prose that mentions X-99 in the middle of a sentence, and a line
ending with P-77 mid-thought, neither of which is a declaration.
"""


def test_anchored_enumeration_accepts_positions_and_rejects_prose() -> None:
    """FR04: line start, list marker, checkbox, and table pipe; not mid-sentence."""
    found = gate.enumerate_plan_identifiers(_ANCHORED_PLAN)
    assert found == ["P-1", "X-1", "X-2", "AC-3", "X-4", "X-5"]
    assert "X-99" not in found
    assert "P-77" not in found


def test_fr_identifiers_come_from_headings_only() -> None:
    """FR04: an FR heading is an identifier; a prose FR reference is not."""
    body = (
        "### PRD-CORE-249-FR01: the first\n"
        "Prose that references FR12 and FR13 without declaring them.\n"
        "### FR02 the second\n"
        "#### FR03 is too deep to be an FR heading\n"
    )
    assert gate.enumerate_prd_fr_identifiers(body) == ["FR01", "FR02"]


def test_prd_scope_bare_id_and_full_path_normalise_identically() -> None:
    """FR04: both live corpus shapes resolve to one identifier."""
    from trw_mcp.state.prd_utils import extract_prd_identifier

    assert extract_prd_identifier("PRD-CORE-249") == "PRD-CORE-249"
    assert extract_prd_identifier("docs/requirements-aare-f/prds/PRD-CORE-249-deferral.md") == "PRD-CORE-249"
    assert extract_prd_identifier("eval/seeds/**") is None


def test_status_token_parser_and_unknown_class_rejected() -> None:
    """FR04: the token grammar is typed; an unknown class is not 'satisfied'."""
    parsed = parse_status_token("X-1", "blocked:human-only:ops@example — rotate credentials")
    assert (parsed.kind, parsed.blocking_class, parsed.owner, parsed.reason) == (
        "blocked",
        "human-only",
        "ops@example",
        "rotate credentials",
    )
    assert parsed.is_accepted_blocked and not parsed.is_unmet
    assert parse_status_token("X-2", "blocked:automatable:pool").is_unmet
    for bad in ("blocked:invented:me", "blocked", "done", "", 3):
        with pytest.raises(AcceptanceDeclarationError):
            parse_status_token("X-3", bad)


def test_owner_containing_a_hyphen_is_not_split_into_a_reason() -> None:
    """Round-1 finding: a single hyphen is not a reason separator.

    ``ops - team@example`` and ``sre-oncall - us-east`` are real owner spellings.
    Accepting `" - "` split the owner at its first hyphen-space and filed the
    remainder as the reason, mis-attributing the person who holds the work --
    the one field a handoff row exists to carry.
    """
    kept = parse_status_token("X-1", "blocked:ops-only:ops - team@example")
    assert kept.owner == "ops - team@example"
    assert kept.reason == ""

    both = parse_status_token("X-2", "blocked:human-only:sre-oncall - us-east — cluster quota")
    assert both.owner == "sre-oncall - us-east"
    assert both.reason == "cluster quota"

    ascii_form = parse_status_token("X-3", "blocked:ops-only:ops - team -- rotate creds")
    assert ascii_form.owner == "ops - team"
    assert ascii_form.reason == "rotate creds"


def test_block_predicate_truth_table_without_touching_disk() -> None:
    """FR04: the pure predicate over (mode, task_type, enumerated, declared)."""
    unmet = {"X-1": AcceptanceStatus(gate_id="X-1", kind="unaddressed")}
    ok = {"X-1": parse_status_token("X-1", "blocked:human-only:ops")}

    def blocks(mode: str, task_type: str, ids: list[str], declared: dict[str, AcceptanceStatus]) -> bool:
        return gate.resolve_plan_acceptance_block(mode=mode, task_type=task_type, enumerated=ids, declared=declared)

    assert blocks("block_coding", "coding", ["X-1"], unmet) is True
    assert blocks("block_all", "eval", ["X-1"], unmet) is True
    assert blocks("advisory", "coding", ["X-1"], unmet) is False
    assert blocks("block_coding", "docs", ["X-1"], unmet) is False
    assert blocks("block_coding", "coding", [], unmet) is False
    assert blocks("block_coding", "coding", ["X-1"], ok) is False
    # An enumerated id with NO entry is unaddressed — omission is not an escape.
    assert blocks("block_coding", "coding", ["X-1"], {}) is True


# --------------------------------------------------------------------------- #
# FR04 — evaluation against a real run directory
# --------------------------------------------------------------------------- #


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)
    return tmp_path


def _make_run(project: Path, *, plan: str, gates: str | None, task_type: str = "coding", scope: str = "[]") -> Path:
    run_dir = project / ".trw" / "runs" / "task" / "20260903T000000Z-fr04"
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "reports").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(
        f"run_id: 20260903T000000Z-fr04\nstatus: active\nphase: deliver\ntask_type: {task_type}\nprd_scope: {scope}\n",
        encoding="utf-8",
    )
    (run_dir / "reports" / "plan.md").write_text(plan, encoding="utf-8")
    if gates is not None:
        (run_dir / "reports" / "acceptance.yaml").write_text(gates, encoding="utf-8")
    return run_dir


def _evaluate(run_dir: Path, monkeypatch: pytest.MonkeyPatch, **cfg: object) -> gate.PlanAcceptanceOutcome:
    from trw_mcp.state.persistence import FileStateReader

    config = TRWConfig(**cfg)  # type: ignore[arg-type]
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
    # ``_deliver_gate_mode`` binds ``get_config`` at import, so the mode lookup
    # this gate shares with the build gate reads the CONSUMER-site name. Without
    # this second patch the mode assertions below would silently read the real
    # project config and pass for the wrong reason.
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: config)
    run_data = FileStateReader().read_yaml(run_dir / "meta" / "run.yaml")
    return gate.evaluate_plan_acceptance(run_dir, run_data)


def test_block_unaddressed_and_automatable(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR04: block on unaddressed, block on automatable, pass on human-only."""
    plan = "# Plan\n\n- P-1: precondition\n- X-1: acceptance item\n"

    undeclared = _make_run(project, plan=plan, gates='schema_version: 1\ngates:\n  "P-1": "satisfied"\n')
    outcome = _evaluate(undeclared, monkeypatch)
    assert outcome.should_block is True
    assert "X-1" in outcome.message
    assert "unaddressed" in outcome.message

    automatable = _make_run(
        project,
        plan=plan,
        gates='schema_version: 1\ngates:\n  "P-1": "satisfied"\n  "X-1": "blocked:automatable:agent-pool"\n',
    )
    outcome = _evaluate(automatable, monkeypatch)
    assert outcome.should_block is True
    assert "Automatable work is not an accepted blocker" in outcome.message
    assert "X-1" in outcome.message

    human = _make_run(
        project,
        plan=plan,
        gates='schema_version: 1\ngates:\n  "P-1": "satisfied"\n  "X-1": "blocked:human-only:operator — needs a human"\n',
    )
    outcome = _evaluate(human, monkeypatch)
    assert outcome.should_block is False
    assert [s.gate_id for s in outcome.accepted_blocked] == ["X-1"]


def test_advisory_mode_never_blocks_a_coding_run(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The mode leg is really wired: the same run blocks under block_coding and not under advisory."""
    run_dir = _make_run(project, plan="- X-1: item\n", gates=None)
    assert _evaluate(run_dir, monkeypatch, deliver_gate_mode="block_coding").should_block is True
    relaxed = _evaluate(run_dir, monkeypatch, deliver_gate_mode="advisory")
    assert relaxed.should_block is False
    assert "X-1" in relaxed.warning
    per_type = _evaluate(
        run_dir,
        monkeypatch,
        deliver_gate_mode="block_coding",
        deliver_gate_task_type_overrides={"coding": "advisory"},
    )
    assert per_type.should_block is False


def test_docs_task_type_is_advisory_only(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR04: a non-build-bearing task type warns and never blocks."""
    run_dir = _make_run(project, plan="- X-1: item\n", gates=None, task_type="docs")
    outcome = _evaluate(run_dir, monkeypatch)
    assert outcome.should_block is False
    assert "X-1" in outcome.warning


def test_unresolved_scope_is_surfaced_not_passed(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR04: a prd_scope entry naming nothing real is never a zero-enumeration pass."""
    run_dir = _make_run(project, plan="# Plan\n", gates=None, scope='["eval/seeds/**", "PRD-CORE-998"]')
    outcome = _evaluate(run_dir, monkeypatch)
    assert outcome.should_block is False
    assert set(outcome.unresolved_scope_entries) == {"eval/seeds/**", "PRD-CORE-998"}
    assert "resolve to no PRD file" in outcome.warning
    assert "NOT a pass" in outcome.warning


def test_empty_enumeration_and_empty_scope_is_a_no_op(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR04: nothing enumerated and no scope => the gate sets no key at all."""
    run_dir = _make_run(project, plan="# Plan\n\nNo identifiers here.\n", gates=None)
    outcome = _evaluate(run_dir, monkeypatch)
    assert outcome == gate.PlanAcceptanceOutcome()


def test_prd_scope_leg_enumerates_fr_headings(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR04: FR ids come from a resolvable PRD named in prd_scope."""
    prds = project / "docs" / "requirements-aare-f" / "prds"
    prds.mkdir(parents=True)
    (prds / "PRD-CORE-997-example.md").write_text(
        "# PRD-CORE-997\n\n### PRD-CORE-997-FR01: first\n\n### PRD-CORE-997-FR02: second\n", encoding="utf-8"
    )
    run_dir = _make_run(project, plan="# Plan\n", gates=None, scope='["PRD-CORE-997"]')
    outcome = _evaluate(run_dir, monkeypatch)
    assert set(outcome.enumerated) == {"FR01", "FR02"}
    assert outcome.unresolved_scope_entries == ()
    assert outcome.should_block is True


# --------------------------------------------------------------------------- #
# NFR02 — fail posture
# --------------------------------------------------------------------------- #


def test_prd_scope_id_does_not_prefix_match_a_longer_sequence(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-1 finding: ``PRD-CORE-997*`` must not resolve to ``PRD-CORE-9970``.

    A bare prefix glob enumerated a DIFFERENT requirement's FR headings as this
    run's gates -- silently, and in the blocking direction.
    """
    prds = project / "docs" / "requirements-aare-f" / "prds"
    prds.mkdir(parents=True)
    (prds / "PRD-CORE-9970-decoy.md").write_text("### PRD-CORE-9970-FR01: not ours\n", encoding="utf-8")

    run_dir = _make_run(project, plan="# Plan\n", gates=None, scope='["PRD-CORE-997"]')
    outcome = _evaluate(run_dir, monkeypatch)
    assert outcome.enumerated == (), "a longer sequence number was enumerated as this run's gates"
    assert outcome.unresolved_scope_entries == ("PRD-CORE-997",)

    # The real PRD still resolves once it exists.
    (prds / "PRD-CORE-997-example.md").write_text("### PRD-CORE-997-FR01: ours\n", encoding="utf-8")
    outcome = _evaluate(run_dir, monkeypatch)
    assert outcome.enumerated == ("FR01",)
    assert outcome.unresolved_scope_entries == ()


def test_gate_mode_resolution_is_shared_with_the_build_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR04 reuses the PRD-CORE-184 policy rather than re-deriving it.

    Both gates read the mode through one resolver, so a per-task-type override
    cannot apply to one gate and not the other.
    """
    from trw_mcp.tools import _deliver_gate_mode as dgm

    assert gate.resolve_gate_mode is dgm.resolve_gate_mode
    cfg = TRWConfig(  # type: ignore[call-arg]
        deliver_gate_mode="block_coding",
        deliver_gate_task_type_overrides={"coding": "advisory"},
    )
    monkeypatch.setattr("trw_mcp.tools._deliver_gate_mode.get_config", lambda: cfg)
    assert dgm.resolve_gate_mode("coding") == "advisory"
    assert dgm.resolve_gate_mode("eval") == "block_coding"


def test_failclosed_gate_failopen_write(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR02: malformed declaration BLOCKS; an unwritable handoff does NOT."""
    run_dir = _make_run(project, plan="- X-1: item\n", gates="gates: [not, a, mapping]\n")
    outcome = _evaluate(run_dir, monkeypatch)
    assert outcome.should_block is True
    assert "unreadable" in outcome.message and "gates" in outcome.message

    run_dir = _make_run(project, plan="- X-1: item\n", gates="gates:\n  X-1: : : not yaml [\n")
    outcome = _evaluate(run_dir, monkeypatch)
    assert outcome.should_block is True
    assert "could not be read" in outcome.message

    # Fail-OPEN half: an unwritable handoff target records failed and returns.
    from trw_mcp.tools import _project_handoff as ph

    config = TRWConfig(project_handoff_path="nope/HANDOFF.md")  # type: ignore[call-arg]
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
    with patch(
        "trw_mcp.state.persistence.FileStateWriter.write_text",
        side_effect=OSError("read-only filesystem"),
    ):
        status = ph.write_handoff_rows(
            run_id="R",
            accepted=[parse_status_token("X-1", "blocked:ops-only:me")],
            resolved_gate_ids=[],
        )
    assert status["status"] == "failed"
    assert "read-only filesystem" in str(status["error"])


# --------------------------------------------------------------------------- #
# NFR01 — latency budget
# --------------------------------------------------------------------------- #


@pytest.mark.integration
def test_gate_latency_budget(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR01: gate <=150 ms p95 at 200 identifiers; handoff write <=100 ms p95."""
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools import _project_handoff as ph

    plan = "# Plan\n\n" + "".join(f"- X-{i}: item {i}\n" for i in range(1, 201))
    declarations = "".join(f'  "X-{i}": "blocked:human-only:ops"\n' for i in range(1, 201))
    run_dir = _make_run(project, plan=plan, gates=f"schema_version: 1\ngates:\n{declarations}")
    config = TRWConfig()
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
    run_data = FileStateReader().read_yaml(run_dir / "meta" / "run.yaml")
    assert len(gate.evaluate_plan_acceptance(run_dir, run_data).enumerated) == 200

    def p95(samples: list[float]) -> float:
        return sorted(samples)[int(len(samples) * 0.95) - 1]

    gate_samples: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        gate.evaluate_plan_acceptance(run_dir, run_data)
        gate_samples.append((time.perf_counter() - started) * 1000.0)
    assert p95(gate_samples) <= 150.0, f"gate p95 {p95(gate_samples):.1f} ms exceeds the 150 ms budget"

    target = project / ".trw" / "HANDOFF.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x" * (256 * 1024) + "\n", encoding="utf-8")
    accepted = [parse_status_token(f"X-{i}", "blocked:ops-only:sre — quota") for i in range(1, 21)]
    write_samples: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        ph.write_handoff_rows(run_id="R", accepted=accepted, resolved_gate_ids=[])
        write_samples.append((time.perf_counter() - started) * 1000.0)
    assert p95(write_samples) <= 100.0, f"write p95 {p95(write_samples):.1f} ms exceeds the 100 ms budget"


# --------------------------------------------------------------------------- #
# FR06 — the REAL run_trw_deliver path (no gate patch anywhere)
# --------------------------------------------------------------------------- #


def _write_e2e_run(tmp_path: Path, *, gates: str) -> Path:
    trw = tmp_path / ".trw"
    for sub in ("learnings/entries", "reflections", "context"):
        (trw / sub).mkdir(parents=True, exist_ok=True)
    run_id = "20260903T000000Z-e2e"
    run_dir = trw / "runs" / "task" / run_id
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "reports").mkdir(parents=True)
    (run_dir / "meta" / "run.yaml").write_text(
        f"run_id: {run_id}\nstatus: active\nphase: deliver\nprd_scope: []\n"
        "task_type: coding\ncomplexity_class: STANDARD\nowner_session_id: SESS-E2E\n",
        encoding="utf-8",
    )
    (run_dir / "meta" / "review.yaml").write_text(
        "substantive: true\nverdict: pass\ncritical_count: 0\n"
        f"reviewer:\n  source: subagent\n  run_id: {run_id}-REVIEWER\n  session_id: SESS-R\n  receipt_id: tok\n",
        encoding="utf-8",
    )
    (run_dir / "meta" / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"ts": "2026-09-03T00:00:00Z", "event": "session_start"}),
                json.dumps(
                    {
                        "ts": "2026-09-03T00:00:02Z",
                        "event": "build_check_complete",
                        "test_count": 12,
                        "scope": "pytest tests",
                        "tests_passed": True,
                        "static_checks_clean": True,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "reports" / "plan.md").write_text("# Plan\n\n- P-1: precondition\n- X-1: acceptance item\n", "utf-8")
    (run_dir / "reports" / "acceptance.yaml").write_text(gates, encoding="utf-8")
    return run_dir


def _deliver_e2e(tmp_path: Path, run_dir: Path, **kwargs: Any) -> dict[str, Any]:
    from tests.conftest import extract_tool_fn, make_test_server

    deliver_fn = extract_tool_fn(make_test_server("ceremony"), "trw_deliver")
    trw_dir = tmp_path / ".trw"
    cfg = TRWConfig(deliver_gate_mode="block_coding", evidence_receipt_mode="observe")  # type: ignore[call-arg]
    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        patch("trw_mcp.models.config.get_config", lambda: cfg),
        patch("trw_mcp.tools.ceremony.get_config", lambda: cfg),
        patch("trw_mcp.tools._delivery_helpers.get_config", lambda: cfg),
        patch(
            "trw_mcp.tools._deferred_delivery._do_index_sync",
            return_value={"status": "success", "index": {}, "roadmap": {}},
        ),
    ):
        return dict(deliver_fn(skip_reflect=True, **kwargs))


@pytest.mark.integration
def test_real_deliver_path_end_to_end(tmp_path: Path) -> None:
    """FR06: the block and the write are both observed on run_trw_deliver's return.

    No patch touches ``evaluate_delivery_gates``, ``_plan_acceptance_gate``, or
    ``_project_handoff``. Deleting the ``_evaluate_plan_acceptance`` call from
    ``_deliver_gate_dispatch.evaluate_delivery_gates`` turns the first assertion
    red, which is the attribution ``docs/documentation/wiring-defect-patterns.md``
    section 4 requires.
    """
    blocked_run = _write_e2e_run(tmp_path, gates='schema_version: 1\ngates:\n  "P-1": "satisfied"\n')
    result = _deliver_e2e(tmp_path, blocked_run)
    assert result["success"] is False
    assert "plan_acceptance_block" in result, "the FR04 gate never ran on the real deliver path"
    assert "X-1" in result["plan_acceptance_block"]
    assert not (tmp_path / ".trw" / "HANDOFF.md").exists(), "a blocked deliver must not write handoff rows"

    # Declaring it blocked:human-only lets delivery proceed AND writes the row.
    (blocked_run / "reports" / "acceptance.yaml").write_text(
        'schema_version: 1\ngates:\n  "P-1": "satisfied"\n  "X-1": "blocked:human-only:ops@example — rotate creds"\n',
        encoding="utf-8",
    )
    proceeded = _deliver_e2e(tmp_path, blocked_run)
    assert proceeded["success"] is True, proceeded.get("errors")
    assert "plan_acceptance_block" not in proceeded
    assert proceeded["project_handoff"]["status"] == "written"
    handoff = (tmp_path / ".trw" / "HANDOFF.md").read_text(encoding="utf-8")
    assert "X-1" in handoff and "ops@example" in handoff
    final_md = (blocked_run / "reports" / "final.md").read_text(encoding="utf-8")
    assert "## Remaining work handoff" in final_md and "X-1" in final_md

    # And the next session reads it back with a derived age (FR02 -> FR03).
    with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
        cfg = TRWConfig()
        with patch("trw_mcp.models.config.get_config", lambda: cfg):
            from trw_mcp.tools._project_handoff_readback import read_open_handoff

            readback = read_open_handoff()
    assert readback["status"] == "measured"
    assert readback["total"] == 1
    assert readback["items"][0]["gate_id"] == "X-1"
    assert readback["items"][0]["age_days"] == 0


@pytest.mark.integration
def test_real_deliver_structured_override_proceeds_over_the_block(tmp_path: Path) -> None:
    """FR04: the PRD-CORE-191 record is the sanctioned escape, prose is not."""
    run_dir = _write_e2e_run(tmp_path, gates='schema_version: 1\ngates:\n  "P-1": "satisfied"\n')
    prose = _deliver_e2e(tmp_path, run_dir, allow_unverified=True, unverified_reason="I'll do it later")
    assert prose["success"] is False
    assert "plan_acceptance_block" in prose

    expiry = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
    record = json.dumps(
        {
            "failed_command": "X-1 acceptance item",
            "residual_risk": "the item is tracked for the next run",
            "owner": "agent-run-e2e",
            "expiry_iso": expiry,
        }
    )
    allowed = _deliver_e2e(tmp_path, run_dir, allow_unverified=True, unverified_reason=record)
    assert allowed["success"] is True, allowed.get("errors")
    assert "plan_acceptance_block" not in allowed

"""PRD-CORE-191 — structured acceptable-failure schema for the trw_deliver override.

Asserts FR01-FR05 + NFR02:

- FR01: ``AcceptableFailureRecord`` requires failed_command/residual_risk/owner/
  expiry_iso; ``unverified_reason`` is parsed as JSON first, YAML-subset second;
  plain prose is rejected.
- FR02: an expired ``expiry_iso`` blocks delivery; same-day passes.
- FR03: a successful structured override appends a unique YAML ledger entry
  under ``.trw/overrides/`` with the four fields + gate type.
- FR04: the result dict carries ``acceptable_failure_record`` (accept) /
  ``acceptable_failure_error`` (reject).
- FR05: a non-empty plain string returns an error containing
  ``acceptable-failure schema required`` + a copy-pasteable example.
- NFR02: a ledger write failure is fail-closed (the hard gate remains closed).

Tests assert parsed dict contents, ledger file contents, error message text, and
real deliver success/blocked values — not existence.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp import FastMCP

from tests.conftest import get_tools_sync
from trw_mcp.tools.ceremony import register_ceremony_tools

_FUTURE = (datetime.now(timezone.utc) + timedelta(days=30)).date().isoformat()
_TODAY = datetime.now(timezone.utc).date().isoformat()
_YESTERDAY = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()


def _record_json(expiry: str) -> str:
    return json.dumps(
        {
            "failed_command": "pytest trw-mcp/tests/ -q",
            "residual_risk": "two flaky integration tests; core logic verified manually",
            "owner": "agent-run-abc123",
            "expiry_iso": expiry,
        }
    )


class TestSchemaSurfaces:
    """Every primary tool/lifecycle surface names the executable schema."""

    def test_trw_deliver_description_names_all_required_fields(self) -> None:
        doc = _make_deliver_fn().__doc__ or ""

        assert "structured acceptable-failure" in doc
        for field in ("failed_command", "residual_risk", "owner", "expiry_iso"):
            assert field in doc
        assert "Free text and review-verdict labels are" in doc

    def test_bundled_lifecycle_uses_schema_not_verdict_label(self) -> None:
        from trw_mcp.state.claude_md.sections._tool_lifecycle import load_tool_lifecycle

        lifecycle = load_tool_lifecycle()
        assert "`allow_unverified=true`" in lifecycle
        for field in ("`failed_command`", "`residual_risk`", "`owner`", "`expiry_iso`"):
            assert field in lifecycle
        assert "`review_verdict` carries an explicit `acceptable-failure` label" not in lifecycle

    def test_delivery_skills_expose_three_gate_paths(self) -> None:
        data = Path(__file__).parents[1] / "src" / "trw_mcp" / "data"
        for relative in (
            "skills/trw-deliver/SKILL.md",
            "codex/skills/trw-deliver/SKILL.md",
            "copilot/skills/trw-deliver/SKILL.md",
            "copilot/plugin/skills/trw-deliver/SKILL.md",
            "opencode/skills/trw-deliver/SKILL.md",
        ):
            content = (data / relative).read_text(encoding="utf-8")
            assert "Deliver gate — no fourth path" in content, relative
            assert "passing `trw_build_check`" in content, relative
            assert "allow_unverified=true" in content, relative
            assert "authorized operator/config" in content, relative
            assert "Free-text limitations and review-verdict labels are not" in content, relative
            for field in ("failed_command", "residual_risk", "owner", "expiry_iso"):
                assert field in content, (relative, field)

    @pytest.mark.parametrize("profile", ["claude", "gpt", "qwen", "generic"])
    def test_prompt_profile_uses_structured_schema(self, profile: str) -> None:
        path = Path(__file__).parents[1] / "src" / "trw_mcp" / "data" / "prompting" / f"{profile}.md"
        content = path.read_text(encoding="utf-8")

        assert "structured acceptable-failure record" in content
        assert "explicitly label an acceptable failure" not in content


# ── FR01: schema parse ─────────────────────────────────────────────────────


@pytest.mark.unit
class TestSchemaParse:
    def test_valid_json_accepted(self) -> None:
        from trw_mcp.tools._acceptable_failure_validation import parse_acceptable_failure

        record, error = parse_acceptable_failure(_record_json(_FUTURE))
        assert error is None
        assert record is not None
        assert record.failed_command == "pytest trw-mcp/tests/ -q"
        assert record.owner == "agent-run-abc123"
        assert record.expiry_iso == _FUTURE

    def test_valid_yaml_subset_accepted(self) -> None:
        from trw_mcp.tools._acceptable_failure_validation import parse_acceptable_failure

        yaml_reason = (
            f"failed_command: trw_build_check\n"
            f"residual_risk: no reviewer available in CI\n"
            f"owner: operator-tyler\n"
            f"expiry_iso: {_FUTURE}\n"
        )
        record, error = parse_acceptable_failure(yaml_reason)
        assert error is None
        assert record is not None
        assert record.owner == "operator-tyler"

    def test_prose_string_rejected(self) -> None:
        from trw_mcp.tools._acceptable_failure_validation import parse_acceptable_failure

        record, error = parse_acceptable_failure("just testing")
        assert record is None
        assert error is not None
        assert "acceptable-failure schema required" in error

    def test_missing_field_rejected(self) -> None:
        from trw_mcp.tools._acceptable_failure_validation import parse_acceptable_failure

        partial = json.dumps({"failed_command": "x", "owner": "y", "expiry_iso": _FUTURE})
        record, error = parse_acceptable_failure(partial)
        assert record is None
        assert error is not None
        assert "residual_risk" in error

    def test_yaml_null_field_rejected(self) -> None:
        """codex cross-model review: a YAML-null field (``failed_command: ~``) must

        be treated as MISSING, not coerced to the string "None" (which slipped
        past ``min_length=1`` and blessed a hollow override).
        """
        from trw_mcp.tools._acceptable_failure_validation import parse_acceptable_failure

        yaml_reason = f"failed_command: ~\nresidual_risk: some risk\nowner: agent\nexpiry_iso: {_FUTURE}\n"
        record, error = parse_acceptable_failure(yaml_reason)
        assert record is None, "YAML-null failed_command must not pass as the string 'None'"
        assert error is not None
        assert "failed_command" in error

    def test_yaml_explicit_null_field_rejected(self) -> None:
        """``residual_risk: null`` (explicit null keyword) is also treated as missing."""
        from trw_mcp.tools._acceptable_failure_validation import parse_acceptable_failure

        yaml_reason = f"failed_command: pytest\nresidual_risk: null\nowner: agent\nexpiry_iso: {_FUTURE}\n"
        record, error = parse_acceptable_failure(yaml_reason)
        assert record is None
        assert error is not None
        assert "residual_risk" in error

    def test_json_null_field_rejected(self) -> None:
        """A JSON ``null`` field is rejected (real None fails min_length)."""
        from trw_mcp.tools._acceptable_failure_validation import parse_acceptable_failure

        payload = json.dumps({"failed_command": None, "residual_risk": "risk", "owner": "agent", "expiry_iso": _FUTURE})
        record, error = parse_acceptable_failure(payload)
        assert record is None
        assert error is not None
        assert "failed_command" in error

    def test_yaml_all_fields_present_still_accepts(self) -> None:
        """Regression guard: the null-drop must NOT reject a fully-populated YAML."""
        from trw_mcp.tools._acceptable_failure_validation import parse_acceptable_failure

        yaml_reason = f"failed_command: pytest\nresidual_risk: risk\nowner: agent\nexpiry_iso: {_FUTURE}\n"
        record, error = parse_acceptable_failure(yaml_reason)
        assert error is None
        assert record is not None
        assert record.failed_command == "pytest"

    def test_whitespace_only_field_rejected(self) -> None:
        """A whitespace-only field is rejected (round-2 mutation hardening).

        ``min_length=1`` only rejects an EMPTY string; a whitespace-only value
        (``"   "``, len 3) slips past it. The ``_strip_non_empty`` field-validator
        is the load-bearing guard that strips then raises ``field must be
        non-empty`` so a blank override cannot be blessed as a hollow attestation.
        A round-2 mutant that removed the validator's ``raise`` survived the whole
        suite — this test kills it by asserting a whitespace-only ``residual_risk``
        is rejected.
        """
        from trw_mcp.tools._acceptable_failure_validation import parse_acceptable_failure

        payload = json.dumps(
            {
                "failed_command": "pytest",
                "residual_risk": "   ",
                "owner": "agent",
                "expiry_iso": _FUTURE,
            }
        )
        record, error = parse_acceptable_failure(payload)
        assert record is None, "whitespace-only residual_risk must not pass the strip validator"
        assert error is not None
        assert "residual_risk" in error


# ── FR02: expiry ───────────────────────────────────────────────────────────


@pytest.mark.unit
class TestExpiry:
    def test_expired_blocks(self) -> None:
        from trw_mcp.tools._acceptable_failure_validation import parse_acceptable_failure

        record, error = parse_acceptable_failure(_record_json(_YESTERDAY))
        assert record is None
        assert error is not None
        assert "expired" in error.lower()
        assert _YESTERDAY in error

    def test_same_day_passes(self) -> None:
        from trw_mcp.tools._acceptable_failure_validation import parse_acceptable_failure

        record, error = parse_acceptable_failure(_record_json(_TODAY))
        assert error is None
        assert record is not None
        assert record.expiry_iso == _TODAY


# ── FR05: error message has example ────────────────────────────────────────


@pytest.mark.unit
class TestErrorMessage:
    def test_error_contains_example(self) -> None:
        from trw_mcp.tools._acceptable_failure_validation import parse_acceptable_failure

        _, error = parse_acceptable_failure("legacy reason")
        assert error is not None
        assert "acceptable-failure schema required" in error
        # copy-pasteable example with the four field names
        for field in ("failed_command", "residual_risk", "owner", "expiry_iso"):
            assert field in error


# ── FR03: ledger write ─────────────────────────────────────────────────────


@pytest.mark.integration
class TestLedger:
    def test_ledger_written(self, tmp_path: Path) -> None:
        from trw_mcp.tools._acceptable_failure_validation import (
            parse_acceptable_failure,
            write_override_ledger,
        )

        record, error = parse_acceptable_failure(_record_json(_FUTURE))
        assert error is None and record is not None
        trw_dir = tmp_path / ".trw"
        run_id = "20260611T000000Z-run"
        written, error = write_override_ledger(trw_dir, run_id, record, gate_type="review_block", run_path="/some/run")
        assert written is True
        assert error is None

        overrides_dir = trw_dir / "overrides"
        files = list(overrides_dir.glob("*.yaml"))
        assert files, "no ledger file written"
        import yaml

        data = yaml.safe_load(files[0].read_text())
        assert data["failed_command"] == "pytest trw-mcp/tests/ -q"
        assert data["residual_risk"]
        assert data["owner"] == "agent-run-abc123"
        assert data["expiry_iso"] == _FUTURE
        assert data["gate_type"] == "review_block"
        assert "timestamp" in data
        # filename carries the date + run id
        assert run_id in files[0].name

    def test_two_overrides_same_run_produce_two_files(self, tmp_path: Path) -> None:
        """P1-B audit fix — FR03 append semantics: two overrides on the same run-id

        produce two distinct, independently-readable ledger files (the old
        ``YYYY-MM-DD-<run-id>.yaml`` path overwrote the first record).
        """
        from trw_mcp.tools._acceptable_failure_validation import (
            parse_acceptable_failure,
            write_override_ledger,
        )

        trw_dir = tmp_path / ".trw"
        run_id = "20260611T000000Z-run"

        record_a, err_a = parse_acceptable_failure(_record_json(_FUTURE))
        assert err_a is None and record_a is not None
        write_override_ledger(trw_dir, run_id, record_a, gate_type="review_block", run_path="/run-a")

        # Second override on the SAME run-id and SAME day, distinct contents.
        second = json.dumps(
            {
                "failed_command": "mypy --strict src/",
                "residual_risk": "one pre-existing type error in a vendored module",
                "owner": "operator-second",
                "expiry_iso": _FUTURE,
            }
        )
        record_b, err_b = parse_acceptable_failure(second)
        assert err_b is None and record_b is not None
        # Force a distinct epoch second so the uniquified filename differs even on
        # a fast machine (the production path relies on >=1s spacing between real
        # multi-override deliveries).
        from datetime import datetime, timezone
        from unittest.mock import patch as _patch

        real_now = datetime.now(timezone.utc)
        later = real_now.replace(year=real_now.year + 1)
        with _patch(
            "trw_mcp.tools._acceptable_failure_validation.datetime",
        ) as mock_dt:
            mock_dt.now.return_value = later
            mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)  # noqa: DTZ001 - emulate datetime ctor
            write_override_ledger(trw_dir, run_id, record_b, gate_type="build_gate", run_path="/run-b")

        import yaml

        files = sorted((trw_dir / "overrides").glob("*.yaml"))
        assert len(files) == 2, f"expected 2 ledger files, found {[f.name for f in files]}"

        owners = set()
        gate_types = set()
        for f in files:
            data = yaml.safe_load(f.read_text())
            assert data["expiry_iso"] == _FUTURE
            assert run_id in f.name
            owners.add(data["owner"])
            gate_types.add(data["gate_type"])
        # Both records survived with their distinct contents — no overwrite.
        assert owners == {"agent-run-abc123", "operator-second"}
        assert gate_types == {"review_block", "build_gate"}

    def test_two_overrides_same_second_produce_two_files(self, tmp_path: Path) -> None:
        """codex cross-model review #4: two overrides written within the SAME second

        for the same run-id must produce two distinct ledger files. The uuid4-hex
        filename suffix uniquifies them even when ``int(epoch)`` collides — no time
        mocking, so both writes land in the same wall-clock second on a fast box.
        """
        from trw_mcp.tools._acceptable_failure_validation import (
            parse_acceptable_failure,
            write_override_ledger,
        )

        trw_dir = tmp_path / ".trw"
        run_id = "20260611T000000Z-run"
        record, err = parse_acceptable_failure(_record_json(_FUTURE))
        assert err is None and record is not None

        # Two back-to-back writes in the same second (no datetime patch).
        write_override_ledger(trw_dir, run_id, record, gate_type="review_block", run_path="/run")
        write_override_ledger(trw_dir, run_id, record, gate_type="build_gate", run_path="/run")

        files = list((trw_dir / "overrides").glob("*.yaml"))
        assert len(files) == 2, f"same-second writes collided: {[f.name for f in files]}"

    def test_ledger_body_carries_run_id_and_gate_type(self, tmp_path: Path) -> None:
        """codex cross-model review #5: the ledger body records run_id + gate_type

        so a record's run binding is auditable without parsing the filename.
        """
        from trw_mcp.tools._acceptable_failure_validation import (
            parse_acceptable_failure,
            write_override_ledger,
        )

        trw_dir = tmp_path / ".trw"
        run_id = "20260611T000000Z-bound"
        record, err = parse_acceptable_failure(_record_json(_FUTURE))
        assert err is None and record is not None
        write_override_ledger(trw_dir, run_id, record, gate_type="delivery_blocked", run_path="/the/run")

        import yaml

        files = list((trw_dir / "overrides").glob("*.yaml"))
        assert files
        data = yaml.safe_load(files[0].read_text())
        assert data["run_id"] == run_id
        assert data["gate_type"] == "delivery_blocked"
        assert data["run_path"] == "/the/run"

    def test_ledger_write_failure_returns_explicit_error(self, tmp_path: Path) -> None:
        from trw_mcp.tools._acceptable_failure_validation import (
            parse_acceptable_failure,
            write_override_ledger,
        )

        record, _ = parse_acceptable_failure(_record_json(_FUTURE))
        assert record is not None
        # The persistence boundary converts the write exception into an explicit
        # failure result; the delivery caller uses it to keep the hard gate closed.
        with patch(
            "trw_mcp.tools._acceptable_failure_validation.FileStateWriter.write_yaml",
            side_effect=OSError("disk full"),
        ):
            written, error = write_override_ledger(
                tmp_path / ".trw", "run-x", record, gate_type="review_block", run_path="/r"
            )

        assert written is False
        assert error is not None
        assert "acceptable-failure ledger persistence failed" in error
        assert "disk full" in error


# ── FR01/FR04/FR05: real trw_deliver path ─────────────────────────────────


def _make_deliver_fn() -> Callable[..., dict[str, Any]]:
    server = FastMCP("test")
    register_ceremony_tools(server)
    return get_tools_sync(server)["trw_deliver"].fn


def _write_block_run(tmp_path: Path) -> Path:
    """CODING run with a work event but NO passing build check.

    Under the default ``deliver_gate_mode=block_coding`` a coding run missing
    build evidence trips the HARD ``delivery_blocked`` gate — the build-bearing
    hard gate the CORE-191 structured override now guards (v26.1 posture). There
    is deliberately no ``build_check_complete`` event (so the gate fires) and no
    ``review.yaml`` (so ``review_block`` / scope gates stay silent and the
    delivery_blocked build gate is the sole thing under test).
    """
    # Lives under the configured runs_root (.trw/runs) with run_id == dir name so
    # the deliver-path run-identity gate accepts it and the build-bearing
    # delivery_blocked gate (the one under test) is what fires.
    run_id = "20260611T000000Z-cod"
    run_dir = tmp_path / ".trw" / "runs" / "task" / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        f"run_id: {run_id}\nstatus: active\nphase: deliver\nprd_scope: []\ncomplexity_class: STANDARD\ntask_type: coding\n",
        encoding="utf-8",
    )
    (meta / "events.jsonl").write_text(
        json.dumps({"ts": "2026-06-11T00:00:00Z", "event": "session_start"})
        + "\n"
        + json.dumps({"ts": "2026-06-11T00:00:01Z", "event": "file_modified", "data": {"path": "src/x.py"}})
        + "\n",
        encoding="utf-8",
    )
    return run_dir


def _deliver(tmp_path: Path, run_dir: Path, **kwargs: Any) -> dict[str, Any]:
    deliver_fn = _make_deliver_fn()
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "reflections").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)
    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
        patch(
            "trw_mcp.tools.ceremony._do_reflect",
            return_value={"status": "success", "events_analyzed": 0, "learnings_produced": 0},
        ),
        patch(
            "trw_mcp.tools._deferred_delivery._do_index_sync",
            return_value={"status": "success", "index": {}, "roadmap": {}},
        ),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
    ):
        return deliver_fn(run_path=str(run_dir), skip_reflect=True, **kwargs)


@pytest.mark.integration
class TestDeliverOverride:
    """CORE-191 structured override against the HARD build gate (v26.1 posture).

    ``_write_block_run`` is a CODING run missing build evidence, so the
    build-bearing ``delivery_blocked`` gate is the hard gate under test — the
    task type where the gate now lives. Free text is rejected; only a structured,
    unexpired record sanctions the block; every override writes a ledger entry.
    """

    def test_prose_reason_blocks_with_error(self, tmp_path: Path) -> None:
        """Prose on a coding build block is REJECTED — delivery stays blocked."""
        run_dir = _write_block_run(tmp_path)
        result = _deliver(tmp_path, run_dir, allow_unverified=True, unverified_reason="WIP")
        assert result["success"] is False
        assert result.get("delivery_blocked")
        assert "acceptable_failure_error" in result
        assert "acceptable-failure schema required" in str(result["acceptable_failure_error"])

    def test_structured_record_proceeds_and_surfaces(self, tmp_path: Path) -> None:
        """A valid structured record on a coding build block lets delivery PROCEED."""
        run_dir = _write_block_run(tmp_path)
        result = _deliver(
            tmp_path,
            run_dir,
            allow_unverified=True,
            unverified_reason=_record_json(_FUTURE),
        )
        assert result["success"] is True
        record = result.get("acceptable_failure_record")
        assert isinstance(record, dict)
        for key in ("failed_command", "residual_risk", "owner", "expiry_iso"):
            assert key in record
        # truthfulness_gate_bypassed echoes the structured record (FR04).
        assert result.get("truthfulness_gate_bypassed")

    def test_expired_record_blocks(self, tmp_path: Path) -> None:
        """An expired record on a coding build block is REJECTED — stays blocked."""
        run_dir = _write_block_run(tmp_path)
        result = _deliver(
            tmp_path,
            run_dir,
            allow_unverified=True,
            unverified_reason=_record_json(_YESTERDAY),
        )
        assert result["success"] is False
        assert result.get("delivery_blocked")
        assert "acceptable_failure_error" in result
        assert "expired" in str(result["acceptable_failure_error"]).lower()

    def test_ledger_written_on_override(self, tmp_path: Path) -> None:
        """A sanctioned coding build-block override appends a durable ledger entry."""
        run_dir = _write_block_run(tmp_path)
        result = _deliver(
            tmp_path,
            run_dir,
            allow_unverified=True,
            unverified_reason=_record_json(_FUTURE),
        )
        assert result["success"] is True
        files = list((tmp_path / ".trw" / "overrides").glob("*.yaml"))
        assert files, "no override ledger written on successful override delivery"
        import yaml

        data = yaml.safe_load(files[0].read_text())
        assert data["gate_type"] == "delivery_blocked"
        assert data["owner"] == "agent-run-abc123"

    def test_ledger_write_failure_keeps_hard_gate_closed(self, tmp_path: Path) -> None:
        """NFR02: a failed ledger write keeps the hard build gate CLOSED."""
        run_dir = _write_block_run(tmp_path)
        with patch(
            "trw_mcp.tools._acceptable_failure_validation.write_override_ledger",
            return_value=(False, "acceptable-failure ledger persistence failed: permission denied"),
        ):
            result = _deliver(
                tmp_path,
                run_dir,
                allow_unverified=True,
                unverified_reason=_record_json(_FUTURE),
            )

        assert result["success"] is False
        assert result["delivery_blocked"]
        error = str(result.get("acceptable_failure_error", ""))
        assert "acceptable-failure ledger persistence failed" in error
        assert "permission denied" in error
        assert "acceptable_failure_record" not in result
        assert "truthfulness_gate_bypassed" not in result

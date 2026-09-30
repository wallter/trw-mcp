"""E2E-INC-106: an unreadable review receipt makes the safety-critical scope UNKNOWN, never smaller.

Before the fix ``_scope_from_receipts`` returned at the FIRST malformed receipt and
dropped every later receipt's ``prd_ids``; with no run.yaml scope the union went
empty, the scope resolved ``not_declared`` and the FR04 gate was INERT for a run
governed by a ``safety_critical: true`` PRD. Receipts here are real ``trw_review``
manual-mode receipts; the malformed one sorts BEFORE them (``0-corrupt.json``).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.test_safety_critical_adversarial_gate import _BLOCK_KEY, _deliver, _seed_run, _write_prd
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._delivery_helpers import check_delivery_gates
from trw_mcp.tools._delivery_safety_critical_gate import (
    NOT_DECLARED,
    UNKNOWN_SCOPE,
    _resolve_scope,
    declared_scope,
    declared_scope_union,
    safety_critical_gate_result,
)
from trw_mcp.tools._review_manual import handle_manual_mode


def _receipt_dir(run: Path) -> Path:
    return run / "meta" / "receipts" / "review"


def _valid_receipt(run: Path, prd_id: str, review_id: str = "rv-1") -> None:
    handle_manual_mode([], run, review_id, datetime.now(timezone.utc).isoformat(), [prd_id], review_completed=True)


def _corrupt(run: Path, name: str = "0-corrupt", body: str = "{not json") -> None:
    directory = _receipt_dir(run)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.json").write_text(body, encoding="utf-8")


@pytest.fixture(autouse=True)
def _project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))


class TestScopeIsUnknownNotSmaller:
    def test_malformed_then_valid_receipt_is_unknown_scope_naming_the_receipt(self, tmp_path: Path) -> None:
        _write_prd(tmp_path, "PRD-SEC-999", safety_critical=True)
        run = _seed_run(tmp_path, scope="[]")
        _valid_receipt(run, "PRD-SEC-999")
        _corrupt(run)

        resolution = _resolve_scope(run)

        assert resolution.value == UNKNOWN_SCOPE
        assert resolution.unreadable_receipts == ("0-corrupt",)
        # the readable receipt still contributes: one scope rule, no fork
        assert declared_scope(run).union == ["PRD-SEC-999"]
        assert declared_scope(run).unreadable_receipts == ("0-corrupt",)

    def test_malformed_then_valid_gate_blocks_naming_the_receipt(self, tmp_path: Path) -> None:
        _write_prd(tmp_path, "PRD-SEC-999", safety_critical=True)
        run = _seed_run(tmp_path, scope="[]")
        _valid_receipt(run, "PRD-SEC-999")
        _corrupt(run)

        outcome = safety_critical_gate_result(run)

        assert outcome.should_block is True
        assert outcome.resolution == UNKNOWN_SCOPE
        assert "0-corrupt" in outcome.message
        assert "safety_critical_adversarial_audit_missing" in outcome.message
        assert "meta/receipts/review" in outcome.message
        gates = check_delivery_gates(run, FileStateReader(), tmp_path / ".trw")
        assert "0-corrupt" in gates[_BLOCK_KEY]

    def test_real_deliver_path_blocks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write_prd(tmp_path, "PRD-SEC-999", safety_critical=True)
        run = _seed_run(tmp_path, scope="[]")
        _valid_receipt(run, "PRD-SEC-999")
        _corrupt(run)

        result = _deliver(tmp_path, run, monkeypatch)

        assert result["success"] is False
        assert "0-corrupt" in result[_BLOCK_KEY]

    def test_malformed_receipt_alone_still_blocks(self, tmp_path: Path) -> None:
        run = _seed_run(tmp_path, scope="[]")
        _corrupt(run, body="")

        assert safety_critical_gate_result(run).should_block is True

    def test_malformed_receipt_with_run_yaml_scope_is_still_unknown(self, tmp_path: Path) -> None:
        """The receipt could have widened a benign declared scope, so the scope is unknown."""
        _write_prd(tmp_path, "PRD-CORE-901", safety_critical=False)
        run = _seed_run(tmp_path, scope="[PRD-CORE-901]")
        assert _resolve_scope(run).value is False  # baseline: benign scope, no receipts
        _corrupt(run)

        resolution = _resolve_scope(run)

        assert resolution.value == UNKNOWN_SCOPE
        assert resolution.unreadable_receipts == ("0-corrupt",)
        assert safety_critical_gate_result(run).should_block is True

    def test_every_malformed_receipt_is_named(self, tmp_path: Path) -> None:
        run = _seed_run(tmp_path, scope="[]")
        _corrupt(run, "0-a")
        _corrupt(run, "1-b", body='{"prd_ids": "not-a-list"}')

        assert declared_scope(run).unreadable_receipts == ("0-a", "1-b")

    def test_unlistable_receipt_directory_fails_closed(self, tmp_path: Path) -> None:
        _write_prd(tmp_path, "PRD-SEC-999", safety_critical=True)
        run = _seed_run(tmp_path, scope="[]")
        _valid_receipt(run, "PRD-SEC-999")
        directory = _receipt_dir(run)
        directory.chmod(0o000)
        try:
            if _readable(directory):
                pytest.skip("running with privileges that ignore directory modes")  # skip-category: host-tool
            assert _resolve_scope(run).value == UNKNOWN_SCOPE
        finally:
            directory.chmod(0o755)


def _readable(directory: Path) -> bool:
    try:
        next(directory.iterdir(), None)
    except OSError:  # trw-fail-silent-allow: False IS the answer to "can this directory be listed?"
        return False
    return True


class TestUnreadableRunYamlIsUnknown:
    """Lead ruling on E2E-INC-106: an UNREADABLE run.yaml is corruption, not a deliberate empty scope."""

    def test_unparseable_run_yaml_is_unknown_scope_naming_it(self, tmp_path: Path) -> None:
        run = _seed_run(tmp_path, scope="[]")
        (run / "meta" / "run.yaml").write_text("prd_scope: [unclosed\n  : : :\n", encoding="utf-8")

        resolution = _resolve_scope(run)

        assert resolution.value == UNKNOWN_SCOPE
        assert "meta/run.yaml" in resolution.unreadable_receipts
        assert safety_critical_gate_result(run).should_block is True

    def test_an_unreadable_run_yaml_is_named_as_run_metadata_not_a_receipt(self, tmp_path: Path) -> None:
        """E2E-INC-124: the block names meta/run.yaml as run metadata, and its remedy points at run.yaml, not receipts."""
        _write_prd(tmp_path, "PRD-SEC-999", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-999]")
        (run / "meta" / "run.yaml").write_text("- not a mapping\n", encoding="utf-8")

        outcome = safety_critical_gate_result(run)

        assert outcome.should_block is True
        assert "metadata file meta/run.yaml" in outcome.message
        assert "repair meta/run.yaml" in outcome.message
        assert "review receipt(s) meta/run.yaml" not in outcome.message
        assert "meta/receipts/review" not in outcome.message  # no receipt is unreadable here

    def test_both_an_unreadable_run_yaml_and_receipt_are_each_named(self, tmp_path: Path) -> None:
        _write_prd(tmp_path, "PRD-SEC-999", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-999]")
        _corrupt(run)
        (run / "meta" / "run.yaml").write_text("- not a mapping\n", encoding="utf-8")

        message = safety_critical_gate_result(run).message

        assert "metadata file meta/run.yaml" in message
        assert "review receipt(s) 0-corrupt" in message and "meta/receipts/review" in message

    def test_non_list_prd_scope_is_unknown_not_empty(self, tmp_path: Path) -> None:
        run = _seed_run(tmp_path, scope="PRD-CORE-901")  # a bare string where a list is required

        assert _resolve_scope(run).value == UNKNOWN_SCOPE
        assert declared_scope(run).unreadable_receipts == ("meta/run.yaml",)

    @pytest.mark.parametrize("shape", ["directory", "dangling_symlink", "looping_symlink", "symlink_to_valid"])
    def test_a_run_yaml_that_exists_but_is_not_a_regular_file_is_unknown(self, tmp_path: Path, shape: str) -> None:
        """Codex r2: only true absence means 'no scope'; any other non-file named run.yaml fails closed."""
        run = _seed_run(tmp_path, scope="[]")
        run_yaml = run / "meta" / "run.yaml"
        valid = tmp_path / "elsewhere.yaml"
        valid.write_text(run_yaml.read_text(encoding="utf-8"), encoding="utf-8")
        run_yaml.unlink()
        if shape == "directory":
            run_yaml.mkdir()
        elif shape == "dangling_symlink":
            run_yaml.symlink_to(tmp_path / "missing.yaml")
        elif shape == "looping_symlink":
            (run / "meta" / "loop").symlink_to(run_yaml)
            run_yaml.symlink_to(run / "meta" / "loop")
        else:
            run_yaml.symlink_to(valid)

        assert _resolve_scope(run).value == UNKNOWN_SCOPE
        assert declared_scope(run).unreadable_receipts == ("meta/run.yaml",)

    @pytest.mark.parametrize("body", ["- PRD-SEC-999\n", "", "~\n", "---\n"], ids=["list", "empty", "null", "bare-doc"])
    def test_a_non_mapping_run_yaml_root_is_unknown(self, tmp_path: Path, body: str) -> None:
        """An existing run.yaml whose root is not a mapping (a null root included) is UNKNOWN, never NOT_DECLARED."""
        run = _seed_run(tmp_path, scope="[]")
        (run / "meta" / "run.yaml").write_text(body, encoding="utf-8")

        assert _resolve_scope(run).value == UNKNOWN_SCOPE

    def test_an_unstatable_run_yaml_is_unknown_not_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SAFETY-SCOPE-HARDEN: an lstat error other than ENOENT (e.g. EACCES on a parent) is never 'absent'."""
        run = _seed_run(tmp_path, scope="[]")
        real_lstat = os.lstat

        def lstat(path: object, *args: object, **kwargs: object) -> os.stat_result:
            if str(path).endswith("run.yaml"):
                raise PermissionError(13, "Permission denied", str(path))
            return real_lstat(path, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(os, "lstat", lstat)
        assert declared_scope(run).unreadable_receipts == ("meta/run.yaml",)

    def test_a_file_swapped_between_classification_and_read_is_unknown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SAFETY-SCOPE-HARDEN: the bytes parsed must be the file lstat judged (inode compare on the no-follow fd)."""
        from trw_mcp.tools import _delivery_safety_critical_gate as gate

        _write_prd(tmp_path, "PRD-SEC-999", safety_critical=True)
        run = _seed_run(tmp_path, scope="[PRD-SEC-999]")
        run_yaml = run / "meta" / "run.yaml"
        from trw_mcp._checkout_access import open_under as real_open_under

        def swap_then_open(anchor: Path, relative_path: str) -> int:
            replacement = run_yaml.with_name("run.yaml.new")
            replacement.write_text("prd_scope: []\n", encoding="utf-8")
            os.replace(replacement, run_yaml)  # new inode at the same name, after lstat
            return real_open_under(anchor, relative_path)

        monkeypatch.setattr("trw_mcp.state._evidence_bound_read.open_under", swap_then_open)
        assert gate._resolve_scope(run).value == UNKNOWN_SCOPE

    def test_absent_run_yaml_still_declares_nothing(self, tmp_path: Path) -> None:
        run = _seed_run(tmp_path, scope="[]")
        (run / "meta" / "run.yaml").unlink()

        assert _resolve_scope(run).value == NOT_DECLARED
        assert declared_scope(run).unreadable_receipts == ()


class TestUnchangedWhenReadable:
    def test_valid_only_receipts_keep_their_verdict(self, tmp_path: Path) -> None:
        _write_prd(tmp_path, "PRD-SEC-999", safety_critical=True)
        run = _seed_run(tmp_path, scope="[]")
        _valid_receipt(run, "PRD-SEC-999")

        assert _resolve_scope(run).value is True
        assert _resolve_scope(run).unreadable_receipts == ()
        assert declared_scope_union(run) == ["PRD-SEC-999"]

    def test_no_receipts_and_no_scope_stays_inert(self, tmp_path: Path) -> None:
        run = _seed_run(tmp_path, scope="[]")

        assert _resolve_scope(run).value == NOT_DECLARED
        assert safety_critical_gate_result(run).should_block is False

    def test_non_json_files_in_the_receipt_dir_are_ignored(self, tmp_path: Path) -> None:
        run = _seed_run(tmp_path, scope="[]")
        (_receipt_dir(run)).mkdir(parents=True)
        (_receipt_dir(run) / "notes.txt").write_text("x", encoding="utf-8")

        assert _resolve_scope(run).value == NOT_DECLARED


class TestDriftConsumerDecision:
    """Drift REPORTS an unreadable receipt (own warn entry) and never blocks on it."""

    def _report(self, run: Path) -> dict:  # type: ignore[type-arg]
        from trw_mcp.tools._deliver_requirement_drift import compute_requirement_drift

        return dict(compute_requirement_drift(run))

    def test_unreadable_receipt_only_is_declared_with_a_warn_entry(self, tmp_path: Path) -> None:
        run = _seed_run(tmp_path, scope="[]")
        _corrupt(run)

        report = self._report(run)

        assert report["scope"] == "declared"
        entry = report["prds"]["receipt:0-corrupt"]
        assert (entry["baseline_status"], entry["reason"], entry["effective_mode"]) == (
            "not_evaluated",
            "receipt_unreadable",
            "warn",
        )

    def test_an_unreadable_run_yaml_gets_a_run_metadata_entry_not_a_receipt_entry(self, tmp_path: Path) -> None:
        run = _seed_run(tmp_path, scope="[]")
        (run / "meta" / "run.yaml").write_text("- not a mapping\n", encoding="utf-8")

        prds = self._report(run)["prds"]

        assert "receipt:meta/run.yaml" not in prds
        entry = prds["run_metadata:meta/run.yaml"]
        assert (entry["baseline_status"], entry["reason"], entry["effective_mode"]) == (
            "not_evaluated",
            "run_yaml_unreadable",
            "warn",
        )

    def test_readable_receipts_still_get_their_own_entries(self, tmp_path: Path) -> None:
        _write_prd(tmp_path, "PRD-Y-404", safety_critical=False)
        run = _seed_run(tmp_path, scope="[]")
        _valid_receipt(run, "PRD-Y-404")
        _corrupt(run)

        assert sorted(self._report(run)["prds"]) == ["PRD-Y-404", "receipt:0-corrupt"]

    def test_unreadable_receipt_never_blocks_drift_but_is_advised(self, tmp_path: Path) -> None:
        from trw_mcp.tools._deliver_requirement_drift import _split, drift_warning

        run = _seed_run(tmp_path, scope="[]")
        _corrupt(run)
        report = self._report(run)

        block, warn = _split(report, True)  # type: ignore[arg-type]  # blocks_task=True: the strictest case
        assert block == []
        assert any("receipt:0-corrupt" in item for item in warn)
        assert "receipt:0-corrupt" in drift_warning(report, True)  # type: ignore[arg-type]

    def test_no_receipts_no_scope_is_still_not_declared(self, tmp_path: Path) -> None:
        run = _seed_run(tmp_path, scope="[]")

        assert self._report(run) == {"scope": "not_declared", "prds": {}}


class TestReceiptNameShapes:
    """Codex r1 P0 on E2E-SAFETY-SCOPE: no receipt NAME shape may make the gate inert."""

    def _rename_only_receipt(self, run: Path, new_name: str) -> None:
        receipts = sorted(_receipt_dir(run).glob("*.json"))
        assert len(receipts) == 1
        receipts[0].rename(_receipt_dir(run) / new_name)

    @pytest.mark.parametrize("name", [".json", "x.JSON", "X.Json"])
    def test_a_valid_receipt_under_any_json_name_still_declares_its_scope(self, tmp_path: Path, name: str) -> None:
        _write_prd(tmp_path, "PRD-SEC-999", safety_critical=True)
        run = _seed_run(tmp_path, scope="[]")
        _valid_receipt(run, "PRD-SEC-999")
        self._rename_only_receipt(run, name)

        assert "PRD-SEC-999" in declared_scope(run).union
        assert _resolve_scope(run).value is True

    def test_dotfile_named_receipt_blocks_delivery(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The exact bypass: the sole receipt renamed to `.json` must still govern delivery."""
        _write_prd(tmp_path, "PRD-SEC-999", safety_critical=True)
        run = _seed_run(tmp_path, scope="[]")
        _valid_receipt(run, "PRD-SEC-999")
        self._rename_only_receipt(run, ".json")

        result = _deliver(tmp_path, run, monkeypatch)

        assert result["success"] is False

    @pytest.mark.parametrize("name", ["x.json.bak", "x.json~", "x.txt"])
    def test_non_receipt_suffixes_are_ignored(self, tmp_path: Path, name: str) -> None:
        run = _seed_run(tmp_path, scope="[]")
        _receipt_dir(run).mkdir(parents=True, exist_ok=True)
        (_receipt_dir(run) / name).write_text("{not json", encoding="utf-8")

        assert declared_scope(run).unreadable_receipts == ()
        assert _resolve_scope(run).value == NOT_DECLARED

    def test_appledouble_sidecar_blocks_deliberately(self, tmp_path: Path) -> None:
        """`._x.json` (macOS AppleDouble) is unparseable; it blocks and is named. Ignoring `._*` would reopen the
        bypass (rename a receipt to `._x.json`), so the operator removes the sidecar instead."""
        run = _seed_run(tmp_path, scope="[]")
        _receipt_dir(run).mkdir(parents=True, exist_ok=True)
        (_receipt_dir(run) / "._x.json").write_bytes(b"\x00\x05\x16\x07AppleDouble")

        assert _resolve_scope(run).value == UNKNOWN_SCOPE
        assert declared_scope(run).unreadable_receipts == ("._x",)

    def test_a_directory_named_like_a_receipt_blocks(self, tmp_path: Path) -> None:
        run = _seed_run(tmp_path, scope="[]")
        (_receipt_dir(run) / "x.json").mkdir(parents=True)

        assert _resolve_scope(run).value == UNKNOWN_SCOPE

    def test_a_symlinked_receipt_is_never_followed_and_blocks(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.json"
        outside.write_text('{"prd_ids": ["PRD-ELSEWHERE"]}', encoding="utf-8")
        run = _seed_run(tmp_path, scope="[]")
        _receipt_dir(run).mkdir(parents=True, exist_ok=True)
        (_receipt_dir(run) / "linked.json").symlink_to(outside)

        declared = declared_scope(run)
        assert "PRD-ELSEWHERE" not in declared.union
        assert declared.unreadable_receipts == ("linked",)
        assert _resolve_scope(run).value == UNKNOWN_SCOPE

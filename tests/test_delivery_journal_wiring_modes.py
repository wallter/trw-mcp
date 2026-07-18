"""Mode/error contracts for the live CORE-208 journal adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._delivery_journal_wiring import DeliverJournal, open_deferred_journal, open_delivery_journal
from trw_mcp.tools._delivery_models import OperationState, StepState


def _open(tmp_path, mode: str, *, delivery_id: str = ""):  # type: ignore[no-untyped-def]
    return open_delivery_journal(
        tmp_path,
        TRWConfig(delivery_operations_mode=mode),
        run_identity="",
        skip_reflect=False,
        skip_index_sync=False,
        allow_unverified=False,
        delivery_id=delivery_id,
        capability_token="x" * 32,
    )


def test_off_mode_is_an_explicit_disabled_rollback(tmp_path) -> None:
    journal, blocked = _open(tmp_path, "off")
    assert journal.enabled is False
    assert blocked is None


@pytest.mark.parametrize("mode,blocked", [("observe", False), ("enforce", True)])
def test_coordinator_open_failure_respects_mode(tmp_path, mode: str, blocked: bool) -> None:
    with patch("trw_mcp.tools._delivery_journal_wiring.DeliveryCoordinator", side_effect=OSError("no store")):
        journal, result = _open(tmp_path, mode)
    assert journal.enabled is False
    assert (result is not None) is blocked


def test_claim_failure_blocks_explicit_id_but_observe_server_id_is_diagnostic(tmp_path) -> None:
    coordinator = MagicMock()
    coordinator.claim.side_effect = OSError("claim failed")
    with patch("trw_mcp.tools._delivery_journal_wiring.DeliveryCoordinator", return_value=coordinator):
        _journal, explicit = _open(tmp_path, "observe", delivery_id="019f4ea7-fba4-7817-b1cb-96057e9a835c")
        generated, implicit = _open(tmp_path, "observe")
    assert explicit is not None and explicit["success"] is False
    assert generated.enabled is False and implicit is None


def test_step_failure_is_finalized_failed() -> None:
    coordinator = MagicMock()
    journal = DeliverJournal(coordinator=coordinator, operation_id="op", mode="enforce")
    with pytest.raises(ValueError, match="effect"):
        with journal.step("S01"):
            raise ValueError("effect")
    coordinator.finalize_step.assert_called_once_with("op", "S01", state=StepState.FAILED)


@pytest.mark.parametrize(
    "method,args", [("begin_step", ("S01",)), ("mark_operation_state", ()), ("enqueue_deferred", ())]
)
def test_journal_io_failure_is_open_only_in_observe(method: str, args: tuple[str, ...]) -> None:
    coordinator = MagicMock()
    getattr(coordinator, method).side_effect = OSError("journal")
    observe = DeliverJournal(coordinator=coordinator, operation_id="op", mode="observe")
    enforce = DeliverJournal(coordinator=coordinator, operation_id="op", mode="enforce")

    if method == "begin_step":
        assert observe._begin(*args) is False
        with pytest.raises(OSError):
            enforce._begin(*args)
    elif method == "mark_operation_state":
        observe.mark_state(OperationState.FAILED)
        with pytest.raises(OSError):
            enforce.mark_state(OperationState.FAILED)
    else:
        observe.enqueue_deferred("digest")
        with pytest.raises(OSError):
            enforce.enqueue_deferred("digest")


def test_wait_for_step_terminal_handles_disabled_success_and_bounded_timeout(monkeypatch) -> None:
    assert DeliverJournal().wait_for_step_terminal("S20") is True
    coordinator = MagicMock()
    coordinator.project_status.return_value = {"steps": {"S20": {"state": "started"}}}
    journal = DeliverJournal(coordinator=coordinator, operation_id="op", mode="enforce")
    monkeypatch.setattr("trw_mcp.tools._delivery_journal_wiring.time.sleep", lambda _seconds: None)
    assert journal.wait_for_step_terminal("S20", timeout_seconds=0.0001) is False


def test_open_deferred_journal_enforces_config_and_store_errors(tmp_path) -> None:
    with patch("trw_mcp.models.config.get_config", side_effect=RuntimeError("config")):
        with pytest.raises(RuntimeError, match="config unavailable"):
            open_deferred_journal(tmp_path, "op")
    with (
        patch("trw_mcp.models.config.get_config", return_value=TRWConfig(delivery_operations_mode="observe")),
        patch("trw_mcp.tools._delivery_journal_wiring.DeliveryCoordinator", side_effect=OSError("store")),
    ):
        assert open_deferred_journal(tmp_path, "op").enabled is False

"""PRD-CORE-300-FR03 slice S1: delivery status is a ``trw_status`` mode; recovery is a CLI verb.

``trw_status(delivery=<id>)`` returns the read-only projection the delivery
status tool returned, plus ``resume_pid``. ``trw-mcp delivery recover`` runs the
recovery actions with the same capability, revision and reason checks.

A ``resume`` grant is consumed by ``trw_deliver`` only in the process that holds
the lease, so from the CLI a resume must name the server's pid; without
``--new-pid`` it would lease the operation to the CLI's own short-lived process
and block it until the lease expired. The CLI refuses that instead.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from tests._delivery_support import make_coordinator, make_uuid7, project_metadata_snapshot, strong_capability
from tests.conftest import extract_tool_fn, get_tools_sync, make_test_server


def _status_fn():  # type: ignore[no-untyped-def]
    return extract_tool_fn(make_test_server("orchestration"), "trw_status")


def _revision(coord, delivery_id: str) -> int:  # type: ignore[no-untyped-def]
    return int(coord.project_status(delivery_id)["revision"])


def test_status_mode_reads_a_live_operation_without_a_run_or_a_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability())
    monkeypatch.setattr("trw_mcp.tools.delivery_ops._coordinator", lambda: coord)
    monkeypatch.chdir(tmp_path)  # no active run anywhere: the mode must not need one

    before = project_metadata_snapshot(tmp_path)
    status = _status_fn()(delivery=did)
    after = project_metadata_snapshot(tmp_path)

    assert status["result"] == "ok"
    assert status["operation_id"] == did
    assert status["resume_pid"] == os.getpid()
    assert status["envelope"]["request_id"] == did
    assert before == after


def test_status_mode_on_an_unknown_id_is_a_typed_result_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    coord = make_coordinator(tmp_path)
    monkeypatch.setattr("trw_mcp.tools.delivery_ops._coordinator", lambda: coord)

    status = _status_fn()(delivery=make_uuid7())

    assert status["result"] != "ok"
    assert "envelope" in status


def test_only_trw_status_carries_delivery_status() -> None:
    names = set(get_tools_sync(make_test_server("orchestration")))
    assert "trw_status" in names
    from trw_mcp.models.surface_packs import PACK_TOOLS

    assert "delivery_operations" not in PACK_TOOLS


def test_the_transport_loss_protocol_names_trw_status_as_the_delivery_locator() -> None:
    from trw_mcp.bootstrap._client_integrations import render_transport_loss_guidance

    assert "`trw_status(delivery=<delivery_id>)`" in render_transport_loss_guidance()


def _cli(argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    from trw_mcp.server._cli import main

    monkeypatch.setattr(sys, "argv", ["trw-mcp", "delivery", "recover", *argv, "--json"])
    code = 0
    try:
        main()
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 0 if exc.code is None else 1
    return code, json.loads(capsys.readouterr().out)


def test_cli_recover_runs_an_action_with_its_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    capability = strong_capability()
    coord.claim(delivery_id=did, capability_token=capability)
    monkeypatch.setattr("trw_mcp.tools.delivery_ops._coordinator", lambda: coord)
    revision = str(_revision(coord, did))

    wrong, refused = _cli(
        ["--delivery-id", did, "--action", "request_cancel", "--capability-token", "wrong",
         "--expected-revision", revision, "--reason", "operator cancel"],
        monkeypatch,
        capsys,
    )  # fmt: skip
    code, result = _cli(
        ["--delivery-id", did, "--action", "request_cancel", "--capability-token", capability,
         "--expected-revision", revision, "--reason", "operator cancel"],
        monkeypatch,
        capsys,
    )  # fmt: skip

    assert refused["status"] != "ok", "a wrong capability must not authorize recovery"
    assert wrong == 0, "a refusal is a result, reported in status"
    assert (code, result["status"]) == (0, "ok")


def test_cli_resume_without_the_server_pid_refuses_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    capability = strong_capability()
    coord.claim(delivery_id=did, capability_token=capability)
    monkeypatch.setattr("trw_mcp.tools.delivery_ops._coordinator", lambda: coord)
    revision = _revision(coord, did)

    code, result = _cli(
        ["--delivery-id", did, "--action", "resume", "--capability-token", capability,
         "--expected-revision", str(revision), "--reason", "finish it"],
        monkeypatch,
        capsys,
    )  # fmt: skip

    assert code != 0
    assert result["reason_code"] == "resume_needs_server_pid"
    assert _revision(coord, did) == revision


def test_cli_unknown_action_lists_the_supported_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("trw_mcp.tools.delivery_ops._coordinator", lambda: make_coordinator(tmp_path))

    code, result = _cli(["--delivery-id", make_uuid7(), "--action", "nope"], monkeypatch, capsys)

    assert code != 0
    assert result["supported"] == [
        "takeover_pending",
        "resume",
        "reconcile_applied",
        "reconcile_not_applied",
        "request_cancel",
    ]

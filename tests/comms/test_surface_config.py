"""PRD-CORE-274-NFR06: bounded surface, explicit off switch, refusing config bounds."""

from __future__ import annotations

import re

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.conftest import call_peers, enable_comms, joined_member
from trw_mcp.comms._store import DATABASE_FILENAME
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.surface_packs import KERNEL_TOOLS, PACK_TOOLS, STANDARD_TASK_PACKS
from trw_mcp.server._surface_manifest_registry import _TOOL_OWNER
from trw_mcp.server._tools import raw_registered_tool_names
from trw_mcp.state.claude_md._tool_manifest import TOOL_DESCRIPTIONS


def test_default_on_outside_a_formation_creates_no_comms_state(
    comms_server: FastMCP, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default-on (Amendment 02 A5) must still create NOTHING for a session outside any formation.

    Asserted by walking the tree for the database file: "refused" and "wrote no
    state" are different claims, and only the second is the one NFR06 makes.
    """
    monkeypatch.setenv("TRW_SESSION_ID", "pin-a")
    config = TRWConfig()
    assert config.comms_enabled is True
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)

    payload = call_peers(comms_server, "enroll")

    assert payload["status"] == "refused"
    assert list(formation_env.project_root.rglob(DATABASE_FILENAME)) == []


def test_explicit_off_creates_no_comms_state_even_for_a_member(
    comms_server: FastMCP, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``comms_enabled: false`` is the kill switch: silent, and no database, for a real member."""
    joined_member(formation_env, "impl-1", "pin-a")
    monkeypatch.setenv("TRW_SESSION_ID", "pin-a")
    config = TRWConfig(comms_enabled=False)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)

    payload = call_peers(comms_server, "enroll")

    assert payload["status"] == "disabled"
    assert payload["reason"] == "comms_disabled"
    assert list(formation_env.project_root.rglob(DATABASE_FILENAME)) == []


def test_enabled_enroll_persists_an_endpoint(
    comms_server: FastMCP, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive control for the test above: with comms on, state IS created."""
    joined_member(formation_env, "impl-1", "pin-a")
    monkeypatch.setenv("TRW_SESSION_ID", "pin-a")
    enable_comms(monkeypatch)

    payload = call_peers(comms_server, "enroll")

    assert payload["status"] == "ok"
    assert payload["member_id"] == "impl-1"
    assert "delivery" not in payload
    assert [peer["member_id"] for peer in payload["peers"]] == ["impl-1"]
    assert list(formation_env.project_root.rglob(DATABASE_FILENAME)) != []


def test_unbound_caller_refuses_with_a_reason_code_not_an_exception_string(
    comms_server: FastMCP, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal another agent reads must be a closed code, never raw exception text."""
    monkeypatch.setenv("TRW_SESSION_ID", "pin-never-written")
    enable_comms(monkeypatch)

    payload = call_peers(comms_server, "enroll")

    assert payload["status"] == "refused"
    assert payload["reason"] == "no_pinned_run"
    assert "Traceback" not in payload["detail"]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {"comms_body_max_bytes": 20000},
            "comms_response_max_bytes (65536) must be at least 6 * comms_body_max_bytes + 4096 = 124096",
        ),
        (
            {"comms_poll_interval_seconds": 120, "comms_lease_ttl_seconds": 120},
            "comms_lease_ttl_seconds (120) must be at least 2 * comms_poll_interval_seconds = 240",
        ),
    ],
)
def test_incompatible_bounds_refuse_the_configuration(overrides: dict[str, int], reason: str) -> None:
    """Refuse, never clamp: a clamped bound is a system whose stated limits lie."""
    with pytest.raises(ValueError, match=re.escape(reason)):
        TRWConfig.model_validate(overrides)


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"comms_body_max_bytes": 20000, "comms_response_max_bytes": 124096}, "exactly at the response floor"),
        ({"comms_poll_interval_seconds": 120, "comms_lease_ttl_seconds": 240}, "exactly at the lease floor"),
    ],
)
def test_boundary_legal_bounds_are_accepted(overrides: dict[str, int], why: str) -> None:
    """NEGATIVE CONTROL for the two refusals: the boundary itself must pass.

    Without this a validator that refused everything would look correct.
    """
    config = TRWConfig.model_validate(overrides)
    assert all(getattr(config, field) == value for field, value in overrides.items())


# --- PRD-CORE-274 Amendment 01 (FR11): wait field bounds ----------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("comms_wait_max_seconds", 0),
        ("comms_wait_max_seconds", 300),
        ("comms_wait_interval_ms", 100),
        ("comms_wait_interval_ms", 15000),
    ],
    ids=["max_seconds_floor", "max_seconds_ceiling", "interval_ms_floor", "interval_ms_ceiling"],
)
def test_wait_field_bounds_are_accepted_at_the_edges(field: str, value: int) -> None:
    assert getattr(TRWConfig.model_validate({field: value}), field) == value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("comms_wait_max_seconds", -1),
        ("comms_wait_max_seconds", 301),
        ("comms_wait_interval_ms", 99),
        ("comms_wait_interval_ms", 15001),
    ],
    ids=[
        "max_seconds_below_floor",
        "max_seconds_above_ceiling",
        "interval_ms_below_floor",
        "interval_ms_above_ceiling",
    ],
)
def test_wait_field_bounds_refuse_outside_the_edges(field: str, value: int) -> None:
    with pytest.raises(ValueError, match=field):
        TRWConfig.model_validate({field: value})


COMMS_TOOLS = ("trw_peers", "trw_send", "trw_inbox")


def _parity_gaps() -> dict[str, set[str]]:
    """Every way the comms surface can be registered inconsistently.

    ONE oracle, called by both the assertion and its negative control, so the
    control cannot pass against a weaker check than the one being defended.
    """
    registered = set(raw_registered_tool_names())
    packed = {tool for tools in PACK_TOOLS.values() for tool in tools}
    comms = set(COMMS_TOOLS)
    return {
        "not_registered": comms - registered,
        "not_in_any_pack": comms - packed,
        "no_owner": comms - set(_TOOL_OWNER),
        "no_description": comms - set(TOOL_DESCRIPTIONS),
        "registrar_pack_mismatch": registered ^ packed,
    }


def test_comms_surface_has_registrar_pack_owner_description_parity() -> None:
    """NFR06 parity, all four registries — not just the two the pack change touched.

    This exists because the earlier version of this test asserted PACK_TOOLS,
    KERNEL_TOOLS and STANDARD_TASK_PACKS only. TOOL_DESCRIPTIONS was missing two
    comms entries, every assertion here still passed, and the break surfaced as
    ~108 failures in an unrelated module's import-time assertion during a full
    suite run. A requirement's own proof has to cover the registries the
    requirement names, not the ones its author happened to be editing.
    """
    assert _parity_gaps() == {
        "not_registered": set(),
        "not_in_any_pack": set(),
        "no_owner": set(),
        "no_description": set(),
        "registrar_pack_mismatch": set(),
    }


@pytest.mark.parametrize("missing", COMMS_TOOLS)
def test_a_missing_tool_description_fails_the_parity_oracle(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    """NEGATIVE CONTROL for the parity assertion, on the registry that actually broke.

    Removing one description must make the SAME oracle report it. Without this,
    the `no_description` key could be permanently empty for the wrong reason —
    a typo in the key, a set built from the wrong source — and look healthy.
    """
    monkeypatch.delitem(TOOL_DESCRIPTIONS, missing)

    assert _parity_gaps()["no_description"] == {missing}


def test_comms_is_opt_in_and_not_kernel() -> None:
    """The three comms tools live in an opt-in pack, never the kernel (NFR06)."""
    assert PACK_TOOLS["peer_comms"] == COMMS_TOOLS
    assert not set(PACK_TOOLS["peer_comms"]) & set(KERNEL_TOOLS)
    assert "peer_comms" not in STANDARD_TASK_PACKS


def test_the_formation_manifest_is_owner_only(formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default-on puts membership authority (pin keys, run paths) in the manifest: 0600, whatever the umask."""
    import os
    import stat

    previous = os.umask(0o022)
    try:
        joined_member(formation_env, "impl-1", "pin-a")
        joined_member(formation_env, "impl-2", "pin-b")  # a rewrite keeps the mode too
    finally:
        os.umask(previous)
    assert stat.S_IMODE(formation_env.manifest_path().stat().st_mode) == 0o600

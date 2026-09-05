"""PRD-CORE-248 FR03 — boot builds one FastMCP and registers each tool once.

``_assert_manifest_parity`` used to obtain the registered names by constructing
a second, throwaway ``FastMCP("trw-surface-parity-probe")`` and re-running every
registrar against it, purely to emit an advisory drift warning — a full
duplicate pass of Pydantic schema generation for the whole tool surface on every
process start of every client. The live app is already fully registered by that
point, so the names are read off it instead.

Measured, and stated so it cannot be oversold: 0.066 s of a 1.106 s cold import,
about 6 %. Worth removing because it is pure waste, not because it closes the
reported latency gap.
"""

from __future__ import annotations

import pytest

from tests._structlog_capture import captured_structlog as captured_structlog


def test_boot_registers_each_tool_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A counting spy over the registrar list records exactly one call per registrar.

    Before FR03 the parity probe re-ran every registrar, so each one was invoked
    TWICE across a boot. The spy counts calls across a full ``_register_tools``.
    """
    from fastmcp import FastMCP

    from trw_mcp.server import _tools

    real_registrars = _tools._tool_registrars()
    calls: dict[str, int] = {}

    def _counted(registrar: object) -> object:
        def _wrapper(app: FastMCP) -> None:
            name = getattr(registrar, "__name__", repr(registrar))
            calls[name] = calls.get(name, 0) + 1
            registrar(app)  # type: ignore[operator]

        _wrapper.__name__ = getattr(registrar, "__name__", "registrar")
        return _wrapper

    spies = tuple(_counted(r) for r in real_registrars)
    monkeypatch.setattr(_tools, "_tool_registrars", lambda: spies)

    constructed: list[str] = []
    real_fastmcp_init = FastMCP.__init__

    def _tracking_init(self: FastMCP, *args: object, **kwargs: object) -> None:
        constructed.append(str(args[0]) if args else str(kwargs.get("name", "")))
        real_fastmcp_init(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(FastMCP, "__init__", _tracking_init)

    _tools._register_tools()

    assert calls, "the spy list must actually have been used"
    over_registered = {name: n for name, n in calls.items() if n != 1}
    assert not over_registered, f"each registrar must run exactly once per boot; got {over_registered}"
    assert not constructed, f"_register_tools must construct no additional FastMCP instance; got {constructed}"


def test_parity_check_reads_the_live_app_not_a_second_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """The boot check never calls the throwaway probe."""
    from trw_mcp.server import _tools

    def _forbidden() -> frozenset[str]:
        raise AssertionError("the boot parity check must not build a throwaway FastMCP")

    monkeypatch.setattr(_tools, "raw_registered_tool_names", _forbidden)
    _tools._assert_manifest_parity(_tools.mcp)


def test_drift_still_warns_on_a_seeded_manifest_mismatch(
    monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, object]]
) -> None:
    """A manifest that disagrees with the live surface still emits the WARNING.

    This is what FR03 preserves: reading from the live app is only acceptable if
    the drift signal survives it.
    """
    from trw_mcp.server import _surface_manifest_registry, _tools

    seeded = dict(_surface_manifest_registry.MANIFEST_BY_NAME)
    seeded["trw_ghost_tool_that_does_not_exist"] = seeded[next(iter(seeded))]
    monkeypatch.setattr(_surface_manifest_registry, "MANIFEST_BY_NAME", seeded)

    _tools._assert_manifest_parity(_tools.mcp)

    drift = [log for log in captured_structlog if log.get("event") == "surface_manifest_parity_drift"]
    assert drift, f"seeded drift must warn; got {captured_structlog}"
    assert "trw_ghost_tool_that_does_not_exist" in drift[0]["orphan_manifest_entries"]


def test_clean_surface_emits_no_drift_warning(captured_structlog: list[dict[str, object]]) -> None:
    """Non-vacuity: the drift test above is not passing because it always warns."""
    from trw_mcp.server import _tools

    _tools._assert_manifest_parity(_tools.mcp)
    assert not [log for log in captured_structlog if log.get("event") == "surface_manifest_parity_drift"]


def test_raw_registered_tool_names_is_retained_for_the_acceptance_tests() -> None:
    """FR03 keeps the throwaway probe as the TEST-side authority; only the boot call moved."""
    from trw_mcp.server._surface_manifest_registry import MANIFEST_BY_NAME
    from trw_mcp.server._tools import raw_registered_tool_names

    assert raw_registered_tool_names() == frozenset(MANIFEST_BY_NAME)


def test_live_names_are_the_raw_surface_not_the_session_mask() -> None:
    """The parity read must bypass SurfaceAuthorityMiddleware's per-session mask.

    ``FastMCP.list_tools()`` runs the middleware chain and returns the masked
    view (13 of 48 tools under the default resolution mode). Comparing THAT
    against the manifest would report drift for every tool the session cannot
    currently see.
    """
    from trw_mcp.server._surface_manifest_registry import MANIFEST_BY_NAME
    from trw_mcp.server._tools import live_registered_tool_names, mcp

    assert live_registered_tool_names(mcp) == frozenset(MANIFEST_BY_NAME)


def test_missing_raw_accessor_says_so_instead_of_reporting_clean(
    captured_structlog: list[dict[str, object]],
) -> None:
    """A FastMCP without the private accessor warns; it does not fake a clean check."""
    from trw_mcp.server import _tools

    class _NoAccessor:
        pass

    _tools._assert_manifest_parity(_NoAccessor())  # type: ignore[arg-type]
    assert any(log.get("event") == "surface_manifest_parity_unavailable" for log in captured_structlog)
    assert not [log for log in captured_structlog if log.get("event") == "surface_manifest_parity_drift"]

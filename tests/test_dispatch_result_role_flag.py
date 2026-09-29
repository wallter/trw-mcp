"""PRD-SEC-015-FR08 — ``enforcement_layers`` / ``mcp_role_note`` on ``DispatchResult``.

``read_only_enforced`` is a single filesystem-write fact; it says nothing about
the child's MCP tool surface. ``enforcement_layers`` replaces that conflation
with a per-client, per-(read_only, isolate) tuple of NAMED, verified layers
(``_enforcement_layers.enforcement_report`` — the one place the FR08 table is
encoded), with an unmeasured or refused-to-claim combination reported as an
empty tuple plus an explanatory ``mcp_role_note`` rather than a guess.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from trw_mcp.dispatch import _run_job
from trw_mcp.dispatch._client_specs import _SPEC_BY_ID, client_spec_for
from trw_mcp.dispatch._enforcement_layers import enforcement_report
from trw_mcp.dispatch._runner import dispatch
from trw_mcp.dispatch._types import DispatchRequest


def _req(client: str, **kwargs: object) -> DispatchRequest:
    return DispatchRequest(client=client, prompt="audit this", **kwargs)  # type: ignore[arg-type]


# ── FR08 table, encoded as a parametrized truth table ────────────────────────

# (client, isolate, posture_enforced, mcp_injected, read_only) -> (expected_layers, note_exact_or_None)
# note_exact_or_None: None means "assert non-empty"; "" means "assert exactly empty";
# any other string means "assert exact equality".
_CODEX_ISOLATED = "project trw MCP not loaded under --ignore-user-config"
_TABLE: list[tuple[tuple[str, bool, bool, bool, bool], tuple[tuple[str, ...], str | None]]] = [
    (("codex", False, True, True, True), (("sandbox", "mcp_allowlist", "mcp_role"), "")),
    (("codex", False, False, False, True), (("sandbox",), "")),
    (("codex", True, False, False, True), (("sandbox", "mcp_absent"), _CODEX_ISOLATED)),
    # Sol r1 P2: build_command picks the reviewer / with_trw transport BEFORE isolation, so an injected server
    # under isolate is never reported as mcp_absent.
    (("codex", True, True, True, True), (("sandbox", "mcp_allowlist", "mcp_role"), "")),
    (("codex", True, False, True, True), (("sandbox",), None)),  # with_trw only: no role, a note says so
    # Sol r1 P2: claude has no sandbox (spec sandbox="none"); headless edit denial is a permission default.
    (("claude", True, False, False, True), (("mcp_absent",), None)),
    (("claude", True, True, True, True), ((), None)),
    (("claude", False, False, False, True), ((), None)),  # not in the FR08 table -> unmeasured
    # Re-measured on agy 1.2.11: its global trw server inherits the read-only child's TRW_SURFACE_ROLE=reviewer.
    (("agy", True, False, False, True), (("mcp_role",), None)),
    (("agy", False, False, False, True), (("mcp_role",), None)),
    (("opencode", True, False, False, True), ((), None)),
    (("opencode", False, False, False, True), ((), None)),
    (("cursor-cli", True, False, False, True), ((), None)),  # a client the table never enumerates
    (("codex", False, True, True, False), ((), "")),  # read_only=False -> nothing claimed, no note needed
    (("agy", True, False, False, False), ((), "")),
]


@pytest.mark.parametrize(
    ("inputs", "expected"),
    _TABLE,
    ids=[f"{c}-isolate={i}-posture={p}-injected={m}-read_only={r}" for (c, i, p, m, r), _ in _TABLE],
)
def test_enforcement_layers_per_client(
    inputs: tuple[str, bool, bool, bool, bool], expected: tuple[tuple[str, ...], str | None]
) -> None:
    client, isolate, posture_enforced, mcp_injected, read_only = inputs
    expected_layers, expected_note = expected

    layers, note = enforcement_report(
        client, read_only=read_only, isolate=isolate, posture_enforced=posture_enforced, mcp_injected=mcp_injected
    )

    assert layers == expected_layers
    if expected_note is None:
        assert note != "", "an unmeasured/refused-to-claim row must explain itself in mcp_role_note"
    else:
        assert note == expected_note

    # A layer name is a claim of verified enforcement: never invent one outside
    # the Literal the PRD defines.
    for layer in layers:
        assert layer in ("sandbox", "permissions_allowlist", "mcp_allowlist", "mcp_role", "mcp_absent")


def test_enforcement_report_never_invents_mcp_role_without_posture_enforced() -> None:
    """codex non-isolate with no rendered reviewer template must not claim mcp_role or mcp_allowlist.

    Both layers come from the SAME reviewer_argv_template (_client_specs.py); neither
    is emitted unless the template actually rendered.
    """
    layers, _note = enforcement_report(
        "codex", read_only=True, isolate=False, posture_enforced=False, mcp_injected=False
    )
    assert "mcp_role" not in layers
    assert "mcp_allowlist" not in layers


def test_claude_never_claims_a_sandbox() -> None:
    """Sol r1 P2: claude's spec enforces no sandbox, so no claude row may report one."""
    for isolate in (True, False):
        for injected in (True, False):
            layers, _note = enforcement_report(
                "claude", read_only=True, isolate=isolate, posture_enforced=injected, mcp_injected=injected
            )
            assert "sandbox" not in layers


# ── wiring: a real dispatch() run sets the fields from the registry ──────────


@pytest.fixture
def sentinel(tmp_path: Path) -> Path:
    """A fake CLI binary that prints a minimal Codex-shaped ``item.completed`` event."""
    script = tmp_path / "sentinel.py"
    script.write_text(
        'print(\'{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\')\n',
        encoding="utf-8",
    )
    return script


def _use_sentinel(monkeypatch: pytest.MonkeyPatch, script: Path, client: str = "codex") -> None:
    spec = client_spec_for(client)
    patched = spec.model_copy(update={"binary": sys.executable, "base_argv": (sys.executable, str(script))})
    monkeypatch.setitem(_SPEC_BY_ID, client, patched)


def test_wiring_reviewer_posture_reports_full_codex_layer_set(monkeypatch: pytest.MonkeyPatch, sentinel: Path) -> None:
    """A real (faked-child) codex reviewer dispatch reports sandbox+mcp_allowlist+mcp_role."""
    from types import SimpleNamespace

    monkeypatch.setattr("trw_mcp.dispatch._posture.uuid.uuid4", lambda: SimpleNamespace(hex="test"))
    _use_sentinel(monkeypatch, sentinel)
    result = dispatch(_req("codex", posture="reviewer", read_only=True, isolate=False, timeout_s=30))

    assert result.posture_enforced is True, result.raw_stderr
    assert result.enforcement_layers == ("sandbox", "mcp_allowlist", "mcp_role")
    assert result.mcp_role_note == ""


def test_wiring_isolated_codex_reports_mcp_absent_with_note(monkeypatch: pytest.MonkeyPatch, sentinel: Path) -> None:
    """A real (faked-child) isolated codex dispatch reports sandbox+mcp_absent, never mcp_role."""
    _use_sentinel(monkeypatch, sentinel)
    result = dispatch(_req("codex", posture="default", read_only=True, isolate=True, timeout_s=30))

    assert result.enforcement_layers == ("sandbox", "mcp_absent")
    assert "project trw MCP not loaded" in result.mcp_role_note


def test_wiring_isolated_reviewer_codex_never_reports_mcp_absent(
    monkeypatch: pytest.MonkeyPatch, sentinel: Path
) -> None:
    """Sol r1 P2: isolate=True is the request default, and the reviewer transport is chosen before isolation,
    so an isolated reviewer dispatch holds an injected MCP server and must not report mcp_absent."""
    from types import SimpleNamespace

    monkeypatch.setattr("trw_mcp.dispatch._posture.uuid.uuid4", lambda: SimpleNamespace(hex="test"))
    _use_sentinel(monkeypatch, sentinel)
    result = dispatch(_req("codex", posture="reviewer", read_only=True, isolate=True, timeout_s=30))

    assert result.posture_enforced is True, result.raw_stderr
    assert "mcp_absent" not in result.enforcement_layers
    assert result.enforcement_layers == ("sandbox", "mcp_allowlist", "mcp_role")


# ── failure path: no child launched -> empty tuple, non-empty note ───────────


def test_early_result_reports_no_layers_when_launch_is_refused() -> None:
    """A pre-spawn refusal (writes requested under reviewer posture) never claims a layer."""
    bad = DispatchRequest.model_construct(
        client="codex",
        prompt="x",
        posture="reviewer",
        read_only=False,
        isolate=True,
        timeout_s=30,
        extra_args=(),
    )
    result = dispatch(bad)  # type: ignore[arg-type]

    assert result.enforcement_layers == ()
    assert result.mcp_role_note != ""


def test_run_job_crash_never_claims_an_enforcement_layer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """B71-26-style catch-all: an unexpected crash before any child ran reports () + a note."""

    def crash(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("boom before any child")

    monkeypatch.setattr(_run_job, "dispatch", crash)
    req_path, result_path = tmp_path / "req.json", tmp_path / "result.json"
    req_path.write_text(_req("codex", timeout_s=60).model_dump_json(), encoding="utf-8")
    assert _run_job.main([str(req_path), str(result_path), str(tmp_path / "pid.json")]) == 1

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    assert payload["enforcement_layers"] == []
    assert payload["mcp_role_note"] != ""

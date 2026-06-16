"""Tests for the ``trw-mcp doctor`` first-run diagnostic subcommand (PRD-QUAL-106).

Covers FR-01..FR-10 plus NFR-01 (no production-endpoint network call) and the
fail-open per-check isolation guarantee. The doctor is strictly read-only: these
tests also assert it never creates ``.trw/`` or a memory store as a side effect.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.server import _subcommands_doctor as doctor
from trw_mcp.server._subcommands_doctor import (
    CheckResult,
    _doctor_core,
    _overall_status,
    _run_doctor,
)


def _make_config(target: Path, *, client_id: str = "claude-code", backend_url: str = "") -> TRWConfig:
    """Build a TRWConfig with a known client profile + backend_url for the doctor."""
    return TRWConfig(target_platforms=[client_id], backend_url=backend_url)


def _status_of(results: list[CheckResult], name_fragment: str) -> CheckResult:
    for r in results:
        if name_fragment in r.name:
            return r
    raise AssertionError(f"no check matching {name_fragment!r} in {[r.name for r in results]}")


# ── FR-01: Python + dependency version ───────────────────────────────────────


def test_python_version_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Given a sub-3.10 interpreter, the python-version check FAILs with a 3.10 hint."""
    monkeypatch.setattr(doctor.sys, "version_info", (3, 9, 0, "final", 0))
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    py = _status_of(results, "python")
    assert py.status == "FAIL"
    assert "Python 3.10+ required" in py.message
    assert _overall_status(results) == "fail"


def test_python_version_pass(tmp_path: Path) -> None:
    """The running interpreter (>=3.10) does not FAIL the python-version check.

    The status is PASS on a current install, or WARN in a version-skewed dev env
    where the installed trw-memory predates the recommended floor — but never
    FAIL, since the interpreter itself satisfies 3.10+.
    """
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    py = _status_of(results, "python")
    assert py.status in {"PASS", "WARN"}
    assert "python 3." in py.message


# ── FR-02: config presence + parseability ────────────────────────────────────


def test_config_parse_error(tmp_path: Path) -> None:
    """An unparseable .trw/config.yaml FAILs the config check and the overall run."""
    trw = tmp_path / ".trw"
    trw.mkdir()
    (trw / "config.yaml").write_text("key: [unterminated\n  : : bad", encoding="utf-8")
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    cfg = _status_of(results, "config")
    assert cfg.status == "FAIL"
    assert "parse error" in cfg.message.lower()
    assert _overall_status(results) == "fail"


def test_config_absent_warn(tmp_path: Path) -> None:
    """No config file -> WARN (defaults apply), not FAIL."""
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    cfg = _status_of(results, "config")
    assert cfg.status == "WARN"


def test_config_valid_pass(tmp_path: Path) -> None:
    trw = tmp_path / ".trw"
    trw.mkdir()
    (trw / "config.yaml").write_text("framework_version: v26_TRW\n", encoding="utf-8")
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    cfg = _status_of(results, "config")
    assert cfg.status == "PASS"


# ── FR-03: MCP server smoke (import only) ────────────────────────────────────


def test_mcp_import_check_pass(tmp_path: Path) -> None:
    """The FastMCP app imports cleanly in the test env -> PASS."""
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    mcp = _status_of(results, "mcp")
    assert mcp.status == "PASS"


def test_mcp_import_check_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An import failure is reported as FAIL with the exception message, not raised."""

    def _boom() -> None:
        raise ImportError("synthetic app import failure")

    monkeypatch.setattr(doctor, "_import_mcp_app", _boom)
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    mcp = _status_of(results, "mcp")
    assert mcp.status == "FAIL"
    assert "synthetic app import failure" in mcp.message


# ── FR-04: client-profile detection ──────────────────────────────────────────


def test_profile_detection(tmp_path: Path) -> None:
    """A known client_profile.client_id PASSes and is named in the message."""
    results = _doctor_core(tmp_path, _make_config(tmp_path, client_id="cursor-ide"))
    prof = _status_of(results, "profile")
    assert prof.status == "PASS"
    assert "profile: cursor-ide" in prof.message


def test_profile_unknown_warn(tmp_path: Path) -> None:
    """An unrecognised requested platform falls back -> WARN."""
    results = _doctor_core(tmp_path, _make_config(tmp_path, client_id="totally-bogus"))
    prof = _status_of(results, "profile")
    assert prof.status == "WARN"


# ── FR-05: JSON output + overall/exit logic ──────────────────────────────────


def test_overall_status_precedence() -> None:
    """FAIL beats WARN beats PASS; SKIP is ignored."""
    assert _overall_status([CheckResult("a", "PASS", ""), CheckResult("b", "SKIP", "")]) == "pass"
    assert _overall_status([CheckResult("a", "PASS", ""), CheckResult("b", "WARN", "")]) == "warn"
    assert _overall_status([CheckResult("a", "WARN", ""), CheckResult("b", "FAIL", "")]) == "fail"
    assert _overall_status([CheckResult("a", "SKIP", "")]) == "pass"


def test_json_output_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """--format json emits valid JSON with >=8 checks and an overall key.

    Mirrors the real CLI, which raises subcommand logging to WARNING so INFO
    diagnostics never pollute the machine-readable stdout payload.
    """
    from trw_mcp._logging import configure_logging

    configure_logging(debug=False, verbosity=0, log_level="WARNING", package_name="trw-mcp")
    args = argparse.Namespace(target_dir=str(tmp_path), format="json", fix=False)
    with pytest.raises(SystemExit):
        _run_doctor(args)
    out = capsys.readouterr().out
    payload = json.loads(out)  # must parse
    assert "overall" in payload
    assert isinstance(payload["checks"], list)
    assert len(payload["checks"]) >= 8
    for entry in payload["checks"]:
        assert set(entry) == {"name", "status", "message"}
        assert entry["status"] in {"PASS", "WARN", "FAIL", "SKIP"}


# ── FR-06: CLI registration ──────────────────────────────────────────────────


def test_cli_help_lists_doctor() -> None:
    """The argparse parser lists 'doctor' as a subcommand."""
    from trw_mcp.server._cli_argparse import _build_arg_parser

    parser = _build_arg_parser()
    subactions = [
        a
        for a in parser._subparsers._actions  # type: ignore[union-attr]
        if isinstance(a, argparse._SubParsersAction)
    ]
    choices: set[str] = set()
    for a in subactions:
        choices.update(a.choices.keys())
    assert "doctor" in choices


def test_doctor_registered_in_handlers() -> None:
    """The doctor handler is registered in SUBCOMMAND_HANDLERS for table dispatch."""
    from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

    assert "doctor" in SUBCOMMAND_HANDLERS


# ── FR-07: instruction-file presence + deliver-gate statement ────────────────


def _gated_block(include_gate: bool) -> str:
    from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

    gate = f"{DELIVER_GATE_PHRASE} you have a green build." if include_gate else "some other text"
    return f"# Project\n\n<!-- trw:start -->\n{gate}\n<!-- trw:end -->\n"


def test_instruction_gate_missing_fail(tmp_path: Path) -> None:
    """A TRW block missing the deliver-gate phrase FAILs the instruction check."""
    (tmp_path / "CLAUDE.md").write_text(_gated_block(include_gate=False), encoding="utf-8")
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    instr = _status_of(results, "instruction")
    assert instr.status == "FAIL"
    assert "CLAUDE.md" in instr.message
    assert "deliver-gate" in instr.message.lower()
    assert _overall_status(results) == "fail"


def test_instruction_gate_present_pass(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text(_gated_block(include_gate=True), encoding="utf-8")
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    instr = _status_of(results, "instruction")
    assert instr.status == "PASS"


def test_instruction_absent_warn(tmp_path: Path) -> None:
    """No instruction surface yet (pre-init) -> WARN, not FAIL."""
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    instr = _status_of(results, "instruction")
    assert instr.status == "WARN"


def test_instruction_reuses_canonical_phrase() -> None:
    """FR-07 must consume the canonical DELIVER_GATE_PHRASE, not a hardcoded copy."""
    from trw_mcp.state.claude_md.sections._tool_lifecycle import DELIVER_GATE_PHRASE

    assert doctor.DELIVER_GATE_PHRASE is DELIVER_GATE_PHRASE


# ── FR-08: .trw directory integrity ──────────────────────────────────────────


def test_trw_dir_absent_warn(tmp_path: Path) -> None:
    """No .trw/ directory -> WARN naming init-project; does not fail the run."""
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    td = _status_of(results, "trw_dir")
    assert td.status == "WARN"
    assert ".trw" in td.message
    assert "init-project" in td.message
    # FR-08 alone must not fail the overall run when everything else is PASS/WARN.
    assert _overall_status(results) != "fail"


def test_trw_dir_present_pass(tmp_path: Path) -> None:
    (tmp_path / ".trw").mkdir()
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    td = _status_of(results, "trw_dir")
    assert td.status == "PASS"


def test_trw_dir_not_a_directory_fail(tmp_path: Path) -> None:
    (tmp_path / ".trw").write_text("i am a file", encoding="utf-8")
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    td = _status_of(results, "trw_dir")
    assert td.status == "FAIL"


# ── FR-09: memory backend health (read-only) ─────────────────────────────────


def test_memory_backend_no_store_warn(tmp_path: Path) -> None:
    """No memory store yet -> WARN, and the run creates no .trw/memory files."""
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    mem = _status_of(results, "memory")
    assert mem.status == "WARN"
    assert "no memory store" in mem.message.lower()
    # Read-only guarantee: no memory store materialised.
    assert not (tmp_path / ".trw" / "memory").exists()


def test_memory_backend_present_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A present, healthy store -> PASS (vectors available)."""
    store = tmp_path / ".trw" / "memory"
    store.mkdir(parents=True)
    (store / "memory.db").write_bytes(b"")

    monkeypatch.setattr(doctor, "_probe_memory_backend", lambda p: (12, True))
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    mem = _status_of(results, "memory")
    assert mem.status == "PASS"
    assert "12" in mem.message


def test_memory_backend_degraded_vectors_warn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / ".trw" / "memory"
    store.mkdir(parents=True)
    (store / "memory.db").write_bytes(b"")

    monkeypatch.setattr(doctor, "_probe_memory_backend", lambda p: (3, False))
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    mem = _status_of(results, "memory")
    assert mem.status == "WARN"
    assert "sqlite-vec" in mem.message


# ── FR-10: optional backend probe + installer-flag advisory ──────────────────


def test_backend_skip_and_installer_advisory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty backend_url -> SKIP with NO network call; CLM-014 advisory SKIPs clean.

    The installer-flag advisory is documentation, not a detected defect, so on a
    clean tree (no project-local install.sh) it SKIPs — and the advisory text is
    still surfaced in the SKIP message so the CLM-014 note is never lost.
    """
    called = {"http": False}

    def _tripwire(*_a: object, **_k: object) -> None:
        called["http"] = True
        raise AssertionError("doctor made a network call on an empty backend_url")

    monkeypatch.setattr(doctor, "_probe_backend_url", _tripwire)
    results = _doctor_core(tmp_path, _make_config(tmp_path, backend_url=""))

    conn = _status_of(results, "backend_connectivity")
    assert conn.status == "SKIP"
    assert called["http"] is False

    advisory = _status_of(results, "installer_flag")
    assert advisory.status == "SKIP"
    # The advisory text MUST still surface under SKIP (no silent suppression).
    assert "allow-unauthenticated" in advisory.message
    # Neither SKIP nor the advisory may fail the overall run on a clean tree.
    assert _overall_status(results) != "fail"


def test_installer_advisory_skips_and_overall_pass_on_clean_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean tree reports overall PASS — the advisory no longer pins it to WARN.

    Regression for the instruction-surfaces review F3: an unconditional advisory
    WARN made overall PASS unreachable. With every other check passing on a clean
    tree, the doctor's overall verdict must be ``pass``.
    """
    # Force every non-advisory check to PASS so the advisory row is the only thing
    # that could degrade the overall verdict.
    for fn_name in (
        "_check_python_version",
        "_check_config",
        "_check_mcp_import",
        "_check_profile",
        "_check_instruction_gate",
        "_check_trw_dir",
        "_check_memory_backend",
        "_check_backend_connectivity",
    ):
        name = fn_name.removeprefix("_check_")
        monkeypatch.setattr(doctor, fn_name, lambda _t, _c, _n=name: CheckResult(_n, "PASS", "forced pass"))

    results = _doctor_core(tmp_path, _make_config(tmp_path))
    advisory = _status_of(results, "installer_flag")
    assert advisory.status == "SKIP"
    assert _overall_status(results) == "pass"


def test_installer_advisory_warns_when_local_install_sh_present(tmp_path: Path) -> None:
    """A project-local install.sh is a detectable trigger -> advisory WARN."""
    (tmp_path / "install.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    advisory = _status_of(results, "installer_flag")
    assert advisory.status == "WARN"
    assert "install.sh" in advisory.message
    assert "allow-unauthenticated" in advisory.message


def test_backend_probe_runs_when_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-empty backend_url triggers exactly one owned-endpoint probe."""
    seen: list[str] = []

    def _probe(url: str) -> tuple[bool, str]:
        seen.append(url)
        return True, "200 OK"

    monkeypatch.setattr(doctor, "_probe_backend_url", _probe)
    results = _doctor_core(tmp_path, _make_config(tmp_path, backend_url="http://127.0.0.1:9999"))
    conn = _status_of(results, "backend_connectivity")
    assert conn.status == "PASS"
    assert seen == ["http://127.0.0.1:9999"]


# ── Fail-open per-check isolation ────────────────────────────────────────────


def test_one_broken_check_does_not_abort_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A check raising an exception becomes a single FAIL row; all others still run."""

    def _broken(_t: Path, _c: TRWConfig) -> CheckResult:
        raise RuntimeError("synthetic explosion")

    # Replace the python check with one that raises.
    monkeypatch.setattr(doctor, "_check_python_version", _broken)
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    # All checks still represented (>=8) and the broken one is a FAIL row.
    assert len(results) >= 8
    broken = _status_of(results, "python")
    assert broken.status == "FAIL"
    assert "synthetic explosion" in broken.message


# ── NFR-01: no production endpoint ever ──────────────────────────────────────


def test_no_production_endpoint_in_messages(tmp_path: Path) -> None:
    """The doctor never references or contacts a production/marketing host."""
    results = _doctor_core(tmp_path, _make_config(tmp_path))
    joined = " ".join(r.message for r in results)
    assert "api.trwframework.com" not in joined
    assert "://trwframework.com" not in joined

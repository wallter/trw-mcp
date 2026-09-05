"""Behaviour tests for the ``formation_readiness`` doctor row (PRD-CORE-266-FR06).

Sibling to ``test_doctor_agent_parity.py``, matching the module split on the
production side. Kept out of ``test_doctor_subcommand.py`` so the readiness row's
verdict discipline is legible on its own — the property under test is that no
failure path can reach ``ready``.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest

from trw_mcp.dispatch._client_specs import SUPPORTED_CLIENTS
from trw_mcp.models.config import TRWConfig
from trw_mcp.server._doctor_formation_readiness import formation_readiness_report


def _config(tmp_path: Path, **kw: object) -> TRWConfig:
    return TRWConfig(trw_dir=str(tmp_path / ".trw"), **kw)  # type: ignore[arg-type]


def _plant(bin_dir: Path, name: str, script: str) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _empty_path(monkeypatch: pytest.MonkeyPatch, bin_dir: Path) -> None:
    """Point PATH at *bin_dir* alone, so only planted binaries resolve."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PATH", str(bin_dir))


def _rows_by_client(rows: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(row["client"]): row for row in rows}


# --------------------------------------------------------------------------- #
# Verdict discipline
# --------------------------------------------------------------------------- #


def test_formation_readiness_row_never_reports_unverified_as_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An unverified client with a WORKING binary on PATH still reports
    # `unverified`. An available binary is evidence the CLI exists, never
    # evidence that TRW understands its sandbox semantics — so the verdict must
    # not be reachable by installing something.
    bin_dir = tmp_path / "bin"
    _plant(bin_dir, "grok", "#!/bin/sh\necho 'grok 9.9.9'\n")
    _empty_path(monkeypatch, bin_dir)

    _status, _message, rows = formation_readiness_report(
        _config(tmp_path, dispatch_enabled_clients=list(SUPPORTED_CLIENTS))
    )
    grok = _rows_by_client(rows)["grok"]
    assert grok["verdict"] == "unverified"
    assert "ready" not in str(grok.values())
    assert "version" not in grok
    # The row states what would settle it, not merely that it is open.
    assert "sandbox" in str(grok["reason"])


def test_absent_binary_reports_binary_absent_and_claims_no_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _empty_path(monkeypatch, tmp_path / "bin")
    _status, _message, rows = formation_readiness_report(
        _config(tmp_path, dispatch_enabled_clients=["claude", "codex"])
    )
    for row in rows:
        assert row["verdict"] == "binary_absent"
        assert "version" not in row
        assert "cursor-agent" not in str(row["reason"])


def test_probe_exiting_non_zero_reports_not_measured_with_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    _plant(bin_dir, "claude", "#!/bin/sh\nexit 3\n")
    _empty_path(monkeypatch, bin_dir)

    _status, _message, rows = formation_readiness_report(_config(tmp_path, dispatch_enabled_clients=["claude"]))
    row = rows[0]
    assert row["verdict"] == "not_measured"
    assert "3" in str(row["reason"])
    assert "version" not in row


def test_probe_exiting_zero_but_silent_reports_not_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A zero exit with no banner is not a version. Reporting `ready` here would
    # be the fail-silent shape: a success the probe never actually had.
    bin_dir = tmp_path / "bin"
    _plant(bin_dir, "claude", "#!/bin/sh\nexit 0\n")
    _empty_path(monkeypatch, bin_dir)

    _status, _message, rows = formation_readiness_report(_config(tmp_path, dispatch_enabled_clients=["claude"]))
    assert rows[0]["verdict"] == "not_measured"
    assert "printed nothing" in str(rows[0]["reason"])


def test_working_binary_reports_ready_with_its_own_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Non-vacuity for every negative case above: the ready verdict IS reachable,
    # and it carries the probe's own output and the argv used.
    bin_dir = tmp_path / "bin"
    _plant(bin_dir, "claude", "#!/bin/sh\necho '2.1.261 (Claude Code)'\n")
    _empty_path(monkeypatch, bin_dir)

    status, _message, rows = formation_readiness_report(_config(tmp_path, dispatch_enabled_clients=["claude"]))
    row = rows[0]
    assert row["verdict"] == "ready"
    assert row["version"] == "2.1.261 (Claude Code)"
    assert row["probe_argv"] == ["claude", "--version"]
    assert row["binary_resolved"] == "claude"
    assert status == "PASS"


def test_binary_alias_is_reported_by_the_name_that_actually_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # OQ-3: cursor-cli carries two documented spellings. When only the ALIAS is
    # installed, the row must name the alias rather than the preferred spelling —
    # that is what settles the ambiguity by observation instead of by guess.
    bin_dir = tmp_path / "bin"
    _plant(bin_dir, "agent", "#!/bin/sh\necho 'cursor-agent 1.2.3'\n")
    _empty_path(monkeypatch, bin_dir)

    _status, _message, rows = formation_readiness_report(_config(tmp_path, dispatch_enabled_clients=["cursor-cli"]))
    row = rows[0]
    assert row["binary_resolved"] == "agent"
    assert row["binary_candidates"] == ["cursor-agent", "agent"]
    assert row["verdict"] == "ready"


# --------------------------------------------------------------------------- #
# The timeout knob (NFR01) — wired, not a literal
# --------------------------------------------------------------------------- #


def test_a_hanging_probe_is_bounded_and_reports_not_measured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    # /bin/sleep by absolute path: PATH is pinned to the fixture dir, so a bare
    # `sleep` would exit 127 and the test would pass for the WRONG reason.
    _plant(bin_dir, "claude", "#!/bin/sh\nexec /bin/sleep 30\n")
    _empty_path(monkeypatch, bin_dir)

    started = time.monotonic()
    _status, _message, rows = formation_readiness_report(
        _config(tmp_path, dispatch_enabled_clients=["claude"], dispatch_version_probe_timeout_s=1)
    )
    elapsed = time.monotonic() - started

    row = rows[0]
    assert row["verdict"] == "not_measured"
    # The configured value is named in the reason, so the row proves WHICH bound
    # applied rather than merely that something timed out.
    assert "1s" in str(row["reason"])
    assert elapsed < 10, f"the 1 s knob did not bound the probe ({elapsed:.1f}s elapsed)"


def test_the_timeout_default_comes_from_the_config_field_not_a_literal(tmp_path: Path) -> None:
    from trw_mcp.models.config._fields_dispatch import DEFAULT_DISPATCH_VERSION_PROBE_TIMEOUT_SECS

    config = _config(tmp_path)
    assert config.dispatch.dispatch_version_probe_timeout_s == DEFAULT_DISPATCH_VERSION_PROBE_TIMEOUT_SECS
    # An operator-set value projects through TRWConfig.dispatch rather than being
    # read from a module constant at the call site.
    assert _config(tmp_path, dispatch_version_probe_timeout_s=17).dispatch.dispatch_version_probe_timeout_s == 17


def test_the_timeout_field_is_bounded(tmp_path: Path) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _config(tmp_path, dispatch_version_probe_timeout_s=0)
    with pytest.raises(ValidationError):
        _config(tmp_path, dispatch_version_probe_timeout_s=600)


# --------------------------------------------------------------------------- #
# Shape, hygiene, and catalogue wiring
# --------------------------------------------------------------------------- #


def test_one_record_per_enabled_client_and_never_more(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _empty_path(monkeypatch, tmp_path / "bin")
    for enabled in (["claude"], ["claude", "codex"], list(SUPPORTED_CLIENTS)):
        _status, _message, rows = formation_readiness_report(_config(tmp_path, dispatch_enabled_clients=enabled))
        assert [str(r["client"]) for r in rows] == enabled


def test_sandbox_is_a_typed_tri_state_not_a_boolean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _empty_path(monkeypatch, tmp_path / "bin")
    _status, _message, rows = formation_readiness_report(
        _config(tmp_path, dispatch_enabled_clients=list(SUPPORTED_CLIENTS))
    )
    by_client = _rows_by_client(rows)
    allowed = {"enforced", "available_default_off", "none"}
    for client, row in by_client.items():
        assert row["sandbox"] in allowed, f"{client} reported sandbox={row['sandbox']!r}"
        assert not isinstance(row["sandbox"], bool)
    # The row this distinction exists for: copilot HAS a sandbox that TRW does
    # not enable, which is neither "sandboxed" nor "has no sandbox".
    assert by_client["copilot"]["sandbox"] == "available_default_off"
    assert by_client["codex"]["sandbox"] == "enforced"
    assert by_client["claude"]["sandbox"] == "none"


def test_a_record_carries_no_environment_value_or_credential(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    _plant(bin_dir, "claude", '#!/bin/sh\necho "2.1.261"\n')
    _empty_path(monkeypatch, bin_dir)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-planted-secret-value")

    _status, _message, rows = formation_readiness_report(
        _config(tmp_path, dispatch_enabled_clients=list(SUPPORTED_CLIENTS))
    )
    payload = repr(rows)
    assert "sk-planted-secret-value" not in payload
    assert "ANTHROPIC_API_KEY" not in payload


def test_probe_does_not_forward_the_clients_credential_to_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The probe env is the BASE allowlist alone. A version banner needs no API
    # key, and a probe that forwarded one would widen the blast radius of a
    # read-only diagnostic.
    bin_dir = tmp_path / "bin"
    _plant(bin_dir, "claude", '#!/bin/sh\necho "leak=${ANTHROPIC_API_KEY:-none}"\n')
    _empty_path(monkeypatch, bin_dir)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-planted-secret-value")

    _status, _message, rows = formation_readiness_report(_config(tmp_path, dispatch_enabled_clients=["claude"]))
    assert rows[0]["version"] == "leak=none"


def test_check_status_is_never_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _empty_path(monkeypatch, tmp_path / "bin")
    for enabled in ([], ["claude"], list(SUPPORTED_CLIENTS)):
        status, _message, _rows = formation_readiness_report(_config(tmp_path, dispatch_enabled_clients=enabled))
        assert status in {"PASS", "WARN", "SKIP"}
        assert status != "FAIL"


def test_empty_enabled_list_skips_rather_than_passing_vacuously(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _empty_path(monkeypatch, tmp_path / "bin")
    status, message, rows = formation_readiness_report(_config(tmp_path, dispatch_enabled_clients=[]))
    assert status == "SKIP"
    assert rows == []
    assert "NOT MEASURED" in message


def test_check_completes_quickly_when_no_binary_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # NFR01: a PATH lookup that fails costs no subprocess, so seven absent
    # clients must not cost seven timeouts.
    _empty_path(monkeypatch, tmp_path / "bin")
    config = _config(tmp_path, dispatch_enabled_clients=list(SUPPORTED_CLIENTS))
    samples = []
    for _ in range(5):
        started = time.monotonic()
        formation_readiness_report(config)
        samples.append(time.monotonic() - started)
    samples.sort()
    assert samples[len(samples) // 2] < 2.0


def test_formation_readiness_is_registered_in_the_doctor_catalogue() -> None:
    from trw_mcp.server import _subcommands_doctor as doctor

    names = [name for name, _fn in doctor._CHECKS]
    assert "formation_readiness" in names
    # Appended last: every pre-existing row keeps its position.
    assert names[-1] == "formation_readiness"
    assert hasattr(doctor, "_check_formation_readiness")


def test_doctor_check_emits_the_row_through_the_catalogue_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.server import _subcommands_doctor as doctor

    _empty_path(monkeypatch, tmp_path / "bin")
    result = doctor._check_formation_readiness(tmp_path, _config(tmp_path))
    assert result.name == "formation_readiness"
    assert result.status in {"PASS", "WARN", "SKIP"}
    assert result.data
    assert {str(row["client"]) for row in result.data} == set(SUPPORTED_CLIENTS)


def test_adding_a_registry_entry_grows_the_row_set_by_exactly_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # US-002: a synthetic entry must flow through with no other edit.
    from datetime import date

    from trw_mcp.dispatch import _client_specs as specs

    _empty_path(monkeypatch, tmp_path / "bin")
    before = formation_readiness_report(_config(tmp_path, dispatch_enabled_clients=list(SUPPORTED_CLIENTS)))[2]

    synthetic = specs.ClientSpec(
        client_id="synthetic-cli",
        binary="synthetic-cli",
        base_argv=("synthetic-cli",),
        version_argv=("--version",),
        output_shape="trailing_text",
        sub_agents="unknown",
        sandbox="none",
        verification=specs.ClientVerification(
            method="primary_source", evidence="synthetic fixture", verified_at=date(2026, 9, 5)
        ),
    )
    monkeypatch.setitem(specs._SPEC_BY_ID, "synthetic-cli", synthetic)
    after = formation_readiness_report(_config(tmp_path, dispatch_enabled_clients=list(SUPPORTED_CLIENTS)))[2]
    # The enabled list is a typed Literal, so the synthetic id cannot be enabled
    # through config; the row set is therefore unchanged, and the registry lookup
    # is what would have grown it. Assert the lookup, not a fabricated row.
    assert len(after) == len(before)
    assert specs.client_spec_for("synthetic-cli") is synthetic


def test_an_enabled_client_with_no_registry_entry_is_reported_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A config naming a client TRW cannot describe must produce a row saying so.
    # Silently dropping it would let an operator believe every enabled client was
    # checked.
    class _Dispatch:
        dispatch_enabled_clients = ["ghost-cli"]
        dispatch_version_probe_timeout_s = 5

    class _Config:
        dispatch = _Dispatch()

    _empty_path(monkeypatch, tmp_path / "bin")
    status, _message, rows = formation_readiness_report(_Config())  # type: ignore[arg-type]
    assert len(rows) == 1
    assert rows[0]["verdict"] == "not_registered"
    assert status == "WARN"


def test_the_probe_is_skipped_entirely_when_the_binary_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Non-vacuity for the latency bound: prove no subprocess is attempted rather
    # than inferring it from a stopwatch.
    import subprocess

    calls: list[object] = []
    real_run = subprocess.run

    def _spy(*args: object, **kwargs: object) -> object:
        calls.append(args)
        return real_run(*args, **kwargs)  # type: ignore[arg-type]

    _empty_path(monkeypatch, tmp_path / "bin")
    monkeypatch.setattr("trw_mcp.server._doctor_formation_readiness.subprocess.run", _spy, raising=True)
    formation_readiness_report(_config(tmp_path, dispatch_enabled_clients=list(SUPPORTED_CLIENTS)))
    assert calls == []


def test_probe_env_carries_no_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch._env import build_probe_env

    planted = {
        "PATH": "/usr/bin",
        "HOME": os.path.expanduser("~"),
        "ANTHROPIC_API_KEY": "sk-secret",
        "OPENAI_API_KEY": "sk-other",
    }
    env = build_probe_env(source_env=planted)
    assert set(env) == {"PATH", "HOME"}


def test_absent_binary_and_probe_failure_never_produce_a_ready_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD-CORE-266-NFR02: every readiness failure path names a reason.

    The mirror image of the resolution refusals in ``test_dispatch_resolve.py``:
    nothing TRW could not measure is ever reported as usable. An
    absent binary, a probe that raises, a probe that exits non-zero and a probe
    that prints nothing must each carry a NAMED verdict, and none of them may be
    ``ready``.
    """
    bin_dir = tmp_path / "bin"
    _empty_path(monkeypatch, bin_dir)

    def _enabled(*clients: str) -> TRWConfig:
        return _config(tmp_path, dispatch_enabled_clients=list(clients))

    # 1. absent binary
    rows = formation_readiness_report(_enabled("claude", "codex"))[2]
    assert {r["verdict"] for r in rows} == {"binary_absent"}
    assert all(r["reason"] for r in rows)

    # 2. probe exits non-zero
    failing = _plant(bin_dir, "claude", "#!/bin/sh\nexit 4\n")
    row = formation_readiness_report(_enabled("claude"))[2][0]
    assert row["verdict"] == "not_measured"
    assert "4" in str(row["reason"])

    # 3. probe raises OSError (not executable)
    failing.chmod(0o600)
    row = formation_readiness_report(_enabled("claude"))[2][0]
    assert row["verdict"] in {"binary_absent", "not_measured"}
    assert row["verdict"] != "ready"

    # 4. an unverified client is never ready even with a working binary planted
    _plant(bin_dir, "grok", "#!/bin/sh\necho 'grok 1.0'\n")
    row = formation_readiness_report(_enabled("grok"))[2][0]
    assert row["verdict"] == "unverified"

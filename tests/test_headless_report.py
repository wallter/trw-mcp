"""Headless installer support: the schema-1 result document, the run-log scrub, and the template's no-prompt gate."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

from tests._install_trw_pip_target_contract_support import _load_installer_module
from trw_mcp.bootstrap import _headless_report as hr

_TEMPLATE = Path(__file__).resolve().parents[1] / "scripts" / "install-trw.template.py"
_FAKE_KEY = "trw_FAKEKEYabcdef0123456789"
_KEYS = {
    "schema_version",
    "ok",
    "version",
    "interpreter",
    "install_path",
    "clients_configured",
    "doctor",
    "warnings",
    "files_changed",
    "next_steps",
    "log_path",
    "dispatch",
}


@pytest.fixture(autouse=True)
def _no_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real doctor run is a subprocess over the project: stub it with a fixed verdict."""
    monkeypatch.setattr(
        hr,
        "run_doctor",
        lambda project: (
            {"status": "warn", "summary": "3 pass, 1 warn, 0 fail"},
            [{"message": "doctor WARN: config: stale", "remedy": "run 'trw-mcp init-project .'"}],
        ),
    )


def _doc(tmp_path: Path, **kwargs: object) -> dict[str, object]:
    (tmp_path / ".trw").mkdir(exist_ok=True)
    params: dict[str, object] = {
        "project": tmp_path,
        "exit_code": 0,
        "log_path": str(tmp_path / "run.log"),
        "since": time.time() - 60,
        "shell_warnings": [],
    }
    params.update(kwargs)
    return hr.build_report(**params)  # type: ignore[arg-type]


def test_success_document_has_exactly_the_schema_keys_and_types(tmp_path: Path) -> None:
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("x: 1\n", encoding="utf-8")

    doc = _doc(tmp_path)

    assert set(doc) == _KEYS
    assert doc["schema_version"] == 1
    assert doc["ok"] is True
    assert doc["install_path"] == str(tmp_path)
    assert doc["interpreter"] == sys.executable
    assert doc["doctor"] == {"status": "warn", "summary": "3 pass, 1 warn, 0 fail"}
    assert str(tmp_path / ".trw" / "config.yaml") in doc["files_changed"]  # type: ignore[operator]
    assert doc["next_steps"] and all(isinstance(s, str) for s in doc["next_steps"])  # type: ignore[attr-defined]
    json.dumps(doc)  # one serialisable document


def test_failure_document_is_not_ok_and_leads_with_the_error_and_its_remedy(tmp_path: Path) -> None:
    doc = _doc(tmp_path, exit_code=1, error="Not installing: two copies found\nRe-run with TRW_PYTHON=<python>")

    assert doc["ok"] is False
    assert doc["clients_configured"] == []
    first = doc["warnings"][0]  # type: ignore[index]
    assert first == {"message": "Not installing: two copies found", "remedy": "Re-run with TRW_PYTHON=<python>"}


def test_auth_skipped_adds_a_warning_and_a_next_step_naming_the_key(tmp_path: Path) -> None:
    doc = _doc(tmp_path, auth_skipped=True)

    assert any("No API key" in w["message"] for w in doc["warnings"])  # type: ignore[attr-defined]
    assert any("TRW_API_KEY" in s for s in doc["next_steps"])  # type: ignore[attr-defined]


def test_files_changed_skips_old_files_and_vcs_dirs(tmp_path: Path) -> None:
    old = tmp_path / "old.txt"
    old.write_text("o", encoding="utf-8")
    os.utime(old, (1, 1))
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("h", encoding="utf-8")
    new = tmp_path / "new.txt"
    new.write_text("n", encoding="utf-8")

    assert hr.files_changed(tmp_path, time.time() - 30) == [str(new)]


def test_redact_text_scrubs_a_literal_key_that_matches_no_known_pattern(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_API_KEY", "plain-secret-value-123")

    out = hr.redact_text("calling with plain-secret-value-123 and " + _FAKE_KEY)

    assert "plain-secret-value-123" not in out
    assert _FAKE_KEY not in out


def test_redact_file_rewrites_in_place_and_leaves_it_owner_only(tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    log.write_text(f"Authorization: Bearer {_FAKE_KEY}\nhello\n", encoding="utf-8")
    log.chmod(0o644)

    hr.redact_file(log)

    text = log.read_text(encoding="utf-8")
    assert _FAKE_KEY not in text
    assert "hello" in text
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_cli_report_prints_exactly_one_json_line(tmp_path: Path) -> None:
    (tmp_path / ".trw").mkdir()
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "trw_mcp.bootstrap._headless_report",
            "report",
            "--dir",
            str(tmp_path),
            "--exit-code",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)},
    )

    assert done.returncode == 0, done.stderr
    lines = done.stdout.strip().splitlines()
    assert len(lines) == 1
    assert set(json.loads(lines[0])) == _KEYS


# ── the standalone installer: headless means no prompt, ever ─────────────────


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    return _load_installer_module(_TEMPLATE)


def test_headless_env_stops_prompts_from_opening_a_terminal(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        "builtins.open", lambda path, *a, **k: opened.append(str(path)) or (_ for _ in ()).throw(OSError())
    )
    monkeypatch.setenv("TRW_HEADLESS", "1")

    assert installer._open_tty() is None
    assert installer.prompt_yes_no("Proceed?", default="y") is True  # the documented default, unasked
    assert installer.prompt_input("Name:", "dflt") == "dflt"
    assert opened == []


def test_without_headless_env_the_installer_still_tries_the_terminal(
    installer: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        "builtins.open", lambda path, *a, **k: opened.append(str(path)) or (_ for _ in ()).throw(OSError())
    )
    monkeypatch.delenv("TRW_HEADLESS", raising=False)

    assert installer._open_tty() is None  # no terminal in this environment...
    assert opened == ["/dev/tty", "CON"]  # ...but interactive mode did look for one


def test_headless_flag_is_accepted_by_the_template_cli_as_script(tmp_path: Path) -> None:
    missing = tmp_path / "no-such-dir"

    done = subprocess.run(
        [sys.executable, str(_TEMPLATE), str(missing), "--headless"],
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
        check=False,
    )

    assert "unrecognized arguments" not in done.stderr
    assert "Directory not found" in done.stderr  # parsed, then refused the missing target: nothing was asked
    assert done.returncode == 1


# -- PRD-INFRA-210-FR09: the additive ``dispatch`` object -------------------------------------------------------------


def test_dispatch_key_defaults_to_the_effective_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.dispatch._client_specs import CLIENT_SPECS, SUPPORTED_CLIENTS

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for key in tuple(os.environ):
        if key.startswith("TRW_DISPATCH_"):
            monkeypatch.delenv(key)
    doc = _doc(tmp_path)
    block = doc["dispatch"]
    assert doc["schema_version"] == 1
    assert (block["enabled"], block["decided_by"], block["default_client"]) == (False, "default", "codex")  # type: ignore[index]
    rows = {r["client"]: r for r in block["clients"]}  # type: ignore[index]
    assert list(rows) == list(SUPPORTED_CLIENTS)
    assert (rows["codex"]["model"], rows["codex"]["effort"]) == (
        CLIENT_SPECS["codex"].default_model,
        CLIENT_SPECS["codex"].default_effort,
    )
    json.dumps(doc)  # the whole document stays JSON-serialisable


def test_dispatch_key_reports_a_prior_enabled_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("dispatch_tools_exposed: true\n", encoding="utf-8")
    block = _doc(tmp_path)["dispatch"]
    assert (block["enabled"], block["decided_by"]) == (True, "prior")  # type: ignore[index]


def test_dispatch_key_takes_the_steps_own_result_when_given(tmp_path: Path) -> None:
    step = {
        "enabled": True,
        "decided_by": "flag",
        "default_client": "codex",
        "clients": [],
        "writes": [],
        "warnings": [],
    }
    assert _doc(tmp_path, dispatch=step)["dispatch"] == step


def test_report_cli_reads_the_step_result_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    step = {"enabled": True, "decided_by": "prompt", "default_client": "codex", "clients": []}
    (tmp_path / ".trw").mkdir()
    result = tmp_path / "dispatch.json"
    result.write_text(json.dumps(step), encoding="utf-8")
    assert hr.main(["report", "--dir", str(tmp_path), "--dispatch-json", str(result)]) == 0
    assert json.loads(capsys.readouterr().out)["dispatch"] == step


def test_a_credential_in_a_supplied_dispatch_result_is_redacted(tmp_path: Path) -> None:
    secret = "sk-ant-api03-" + "A1b2C3d4E5f6G7h8I9j0" * 2
    step = {"enabled": True, "warnings": [f"--dispatch-effort: bad {secret}"], "clients": [{"model": secret}]}
    doc = _doc(tmp_path, dispatch=step)
    assert secret not in json.dumps(doc)
    assert doc["dispatch"]["enabled"] is True  # type: ignore[index]

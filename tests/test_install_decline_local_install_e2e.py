"""NEW-2-INFRA200-FULL-INSTALL-CLAIM: declining sign-in must leave a local install that WORKS, not just one that installs.

``test_install_trw_client_prompt_gate`` proves the non-interactive (consent-declined) installer runs the local
phases. This drives the same real ``main()`` and then runs ``trw-mcp doctor`` against what it wrote, in a scratch
HOME with no platform key, resolving config from the project's installed .trw/config.yaml as the CLI does: every check the local install owns must pass (no FAIL), and the platform-only rows may
warn. A FAIL here is a claimed-local feature that does not function without an account.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._install_trw_main_support import drive_main
from tests._install_trw_pip_target_contract_support import _load_installer_module

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.timeout(600)]

_TEMPLATE = Path(__file__).resolve().parents[1] / "scripts" / "install-trw.template.py"

_DOCTOR = """
import json, sys
from pathlib import Path
from trw_mcp.server import _subcommands_doctor as d
target = Path(sys.argv[1]).resolve()
# The config `trw-mcp doctor` itself uses: the project's installed .trw/config.yaml, not bare defaults.
rows = d._doctor_core(target, d._resolve_target_config(target))
print(json.dumps([[r.name, r.status, r.message] for r in rows]))
"""


def test_declining_sign_in_leaves_a_local_install_doctor_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_daemon: object
) -> None:
    installer = _load_installer_module(_TEMPLATE)
    user_dir = memory_daemon.user_dir  # type: ignore[attr-defined]
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TRW_USER_DIR", str(user_dir))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TRW_PROJECT_NAMESPACE", raising=False)
    monkeypatch.delenv("TRW_API_KEY", raising=False)
    target = tmp_path / "project"
    (target / ".git").mkdir(parents=True)

    run = drive_main(
        installer, monkeypatch, target, extra_argv=("--ide", "claude-code"), project_setup=True, semantic="ok"
    )
    assert not run.calls["configure"], "no sign-in was offered or taken"

    env = {k: v for k, v in os.environ.items() if not k.startswith("TRW_")}
    env.update(HOME=str(home), TRW_USER_DIR=str(user_dir), TRW_EMBEDDINGS_ENABLED="false")
    proc = subprocess.run(
        [sys.executable, "-c", _DOCTOR, str(target)],
        capture_output=True,
        text=True,
        env=env,
        cwd=target,
        check=False,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    rows = json.loads(proc.stdout.strip().splitlines()[-1])
    by_name = {name: status for name, status, _message in rows}
    assert "target_platforms" in (target / ".trw" / "config.yaml").read_text(encoding="utf-8")
    assert len(rows) > 20 and by_name.get("instruction_surface") == "PASS", "doctor really ran over the install"
    failing = [(name, message) for name, status, message in rows if status == "FAIL"]
    assert failing == [], "a local-install feature fails without an account"
    assert "api_key" not in (target / ".trw" / "config.yaml").read_text(encoding="utf-8"), "no key was invented"

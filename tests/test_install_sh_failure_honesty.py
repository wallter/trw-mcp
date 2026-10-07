"""INSTALL-FAILURE-HONESTY: when every install rung fails, the message names the REAL cause.

``scripts/install.sh`` ran its isolated-install fallbacks (pipx, the managed venv, uv) with their output sent to
``/dev/null`` and then ended on a fixed "your Python looks externally managed (PEP 668)" whatever had failed: an
unresolvable dependency, no network and a missing python3-venv all read as PEP 668. The managed-venv rung also
left a half-built ``~/.local/share/trw/venv`` behind. The last rung's own output is shown now, PEP 668 is named
only when pip reported it, and a venv this run did not create is never removed.

Every test runs the real script with stubs on PATH (helpers from ``test_install_sh_flow``).
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from tests._layout import requires_monorepo

pytestmark = requires_monorepo

from tests.test_install_sh_flow import _CURL_STUB, _REPO_BOOTSTRAP, _write_stub

_REAL_CAUSE = "ERROR: Could not find a version that satisfies the requirement zzz-missing-dependency"

# python3 stub: system and --user pip print $TRW_TEST_SYSTEM_PIP_MSG and fail; `-m venv` builds a directory whose
# python installs nothing named trw-mcp, failing with the dependency error a wheelhouse-only install hits.
_PYTHON3_STUB = f"""#!/usr/bin/env bash
args="$*"
case "$args" in
  *'print(f"'*)                         echo "3.12" ;;
  *'print(sys.version_info.major)'*)    echo "3" ;;
  *'print(sys.version_info.minor)'*)    echo "12" ;;
  *"-m venv "*)
    d="${{args##*-m venv }}"
    mkdir -p "$d/bin"
    cat > "$d/bin/python" <<'VENVPY'
#!/usr/bin/env bash
case "$*" in
  *"pip install"*trw-mcp*) echo "{_REAL_CAUSE}" >&2; exit 1 ;;
  *) exit 0 ;;
esac
VENVPY
    chmod +x "$d/bin/python"
    exit 0 ;;
  *"importlib.metadata"*)               echo "" ;;
  *"-m pip install"*)                   echo "$TRW_TEST_SYSTEM_PIP_MSG" >&2; exit 1 ;;
  *"-m pip"*)                           exit 1 ;;
  *"-m trw_mcp.server"*)                exit 1 ;;
  *)                                    exit 0 ;;
esac
exit 0
"""


def _run_failing_install(
    tmp_path: Path, system_pip_msg: str, *, preexisting_venv: bool = False, empty_user_dir: bool = False
) -> tuple[str, int, Path]:
    stub_bin = tmp_path / "bin"
    project = tmp_path / "project"
    home = tmp_path / "home"
    for d in (stub_bin, project, home):
        d.mkdir(parents=True)
    _write_stub(stub_bin / "python3", _PYTHON3_STUB)
    _write_stub(stub_bin / "curl", _CURL_STUB)
    (project / ".git").mkdir()
    venv = home / ".local" / "share" / "trw" / "venv"
    if empty_user_dir:  # someone else's directory at the venv path: `mkdir` cannot claim it, so nothing may remove it
        venv.mkdir(parents=True)
    if preexisting_venv:
        venv.mkdir(parents=True)
        (venv / "earlier-install.txt").write_text("a good install from an earlier run\n", encoding="utf-8")
    env = {
        "PATH": f"{stub_bin}:/usr/bin:/bin",  # no pipx, no uv
        "HOME": str(home),
        "TRW_TEST_SYSTEM_PIP_MSG": system_pip_msg,
        "TRW_ALLOW_SYSTEM_PYTHON": "false",
        "TERM": "dumb",
    }
    result = subprocess.run(
        ["bash", str(_REPO_BOOTSTRAP), "--allow-unauthenticated"],
        cwd=str(project),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout + result.stderr, result.returncode, venv


def test_a_non_pep668_failure_shows_its_real_cause_and_does_not_blame_pep668(tmp_path: Path) -> None:
    output, code, _venv = _run_failing_install(tmp_path, "ERROR: network unreachable")

    assert code != 0, output
    assert _REAL_CAUSE in output, f"the last rung's real error was discarded.\n--- output ---\n{output}"
    assert "PEP 668" not in output.replace("not a PEP 668 refusal", ""), output
    assert "not a PEP 668 refusal" in output


def test_a_real_pep668_refusal_is_still_named_beside_the_fallback_error(tmp_path: Path) -> None:
    output, code, _venv = _run_failing_install(tmp_path, "error: externally-managed-environment")

    assert code != 0, output
    assert "externally managed (PEP 668)" in output
    assert _REAL_CAUSE in output, output


def test_a_partial_venv_this_run_created_is_named_with_its_removal_command_not_deleted(tmp_path: Path) -> None:
    """Review round 2 (P0): the pathname is shared, so failure cleanup never ``rm -rf``s it. The run says what it left."""
    output, code, venv = _run_failing_install(tmp_path, "ERROR: network unreachable")

    assert code != 0
    assert venv.is_dir(), "a failed install must not delete a directory by pathname"
    assert "This run created a partial environment at" in output
    # The command is shell-quoted (printf %q), so the contract is what a shell makes of it, not a quote style.
    (command,) = [ln for ln in output.splitlines() if "rm -rf " in ln]
    assert shlex.split(command.split("rm -rf ", 1)[1]) == [str(venv)]


def test_a_venv_an_earlier_install_left_is_kept_when_the_retry_fails(tmp_path: Path) -> None:
    output, code, venv = _run_failing_install(tmp_path, "ERROR: network unreachable", preexisting_venv=True)

    assert code != 0
    assert "partial environment" not in output, (
        "an environment this run did not create is never described as its leftover"
    )
    assert (venv / "earlier-install.txt").read_text(encoding="utf-8") == "a good install from an earlier run\n"


def test_an_empty_directory_someone_else_made_at_the_venv_path_is_not_removed(tmp_path: Path) -> None:
    """Review round 1 (P0): ownership is an exclusive `mkdir`, not "I saw it absent", so a directory this run did
    not create (here an empty one; in a race, one another writer filled) is never removed by the failure cleanup."""
    output, code, venv = _run_failing_install(tmp_path, "ERROR: network unreachable", empty_user_dir=True)

    assert code != 0
    assert venv.is_dir()
    assert "partial environment" not in output

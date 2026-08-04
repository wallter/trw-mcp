"""Portability + fail-closed tests for the Cursor shell hooks.

Regression cover for a fail-open in ``trw-before-shell.sh``. The hook declares
``failClosed: true`` but extracted the command with a two-path parser:

    if command -v jq; then jq -r '.command'
    else grep -oP '"command"\\s*:\\s*"\\K[^"]+'

Both fallback paths silently produced an EMPTY command, and an empty command
matched no secret pattern, so the gate emitted ``{"permission":"allow"}``:

1. ``grep -P`` is a GNU extension. macOS/BSD ``grep`` and busybox ``grep`` do not
   have it, so on the majority developer platform the fallback errored out.
2. ``[^"]+`` stops at the first JSON-escaped quote, so a command containing
   ``\\"`` hid everything after it -- including on GNU Linux.

Only the jq path was ever exercised by CI, which is why neither was visible.
These tests run the hook against a PATH that has no ``jq`` and a ``grep``
without ``-P``, which is what a macOS or Alpine developer actually has.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_HOOKS_DATA_DIR = Path(__file__).parent.parent / "src" / "trw_mcp" / "data" / "hooks" / "cursor"
_CURSOR_HOOKS = ("trw-before-shell.sh", "trw-after-shell.sh", "trw-after-mcp.sh")

# Everything the hooks invoke as an external command. ``printf`` is a bash
# builtin and deliberately absent.
_REQUIRED_TOOLS = ("bash", "cat", "date", "mkdir", "awk", "sed", "tr", "grep")

_GREP_WITHOUT_P = """#!/bin/sh
# Stands in for macOS/BSD/busybox grep: every other flag works, -P does not.
for a in "$@"; do
    case "$a" in
        -*P*) echo "grep: unrecognized option -- P" >&2; exit 2 ;;
    esac
done
exec {real_grep} "$@"
"""


def _make_env(
    tmp_path: Path,
    *,
    with_jq: bool = False,
    grep_supports_p: bool = True,
    with_awk: bool = True,
    with_grep: bool = True,
) -> dict[str, str]:
    """Build a minimal PATH standing in for a specific developer platform."""
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir(exist_ok=True)

    if not with_grep:
        grep_supports_p = True  # do not also install the stub

    for tool in _REQUIRED_TOOLS:
        if tool == "grep" and (not grep_supports_p or not with_grep):
            continue
        if tool == "awk" and not with_awk:
            continue
        real = shutil.which(tool)
        if real is None:  # pragma: no cover - environment guard
            pytest.skip(f"required tool not available on this host: {tool}")
        target = bin_dir / tool
        if not target.exists():
            target.symlink_to(real)

    if not grep_supports_p:
        real_grep = shutil.which("grep")
        if real_grep is None:  # pragma: no cover - environment guard
            pytest.skip("grep not available on this host")
        stub = bin_dir / "grep"
        stub.write_text(_GREP_WITHOUT_P.format(real_grep=real_grep))
        stub.chmod(0o755)

    if with_jq:
        real_jq = shutil.which("jq")
        if real_jq is None:
            pytest.skip("jq not available on this host")
        jq_link = bin_dir / "jq"
        if not jq_link.exists():
            jq_link.symlink_to(real_jq)

    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    return {"PATH": str(bin_dir), "CURSOR_PROJECT_DIR": str(project)}


def _run_hook(script_name: str, payload: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    bash = Path(env["PATH"]) / "bash"
    return subprocess.run(
        [str(bash), str(_HOOKS_DATA_DIR / script_name)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )


def _permission(result: subprocess.CompletedProcess[str]) -> str:
    assert result.returncode == 0, f"hook exited non-zero:\n{result.stderr}"
    return str(json.loads(result.stdout.strip()).get("permission"))


# --- The defect: a degraded platform must not become a permissive one --------


def test_before_shell_denies_secret_without_jq_and_without_grep_p(tmp_path: Path) -> None:
    """The reported fail-open: no jq + no ``grep -P`` (macOS/BSD/busybox).

    This is the revert-proof assertion. With the original two-path extractor the
    command extracts as empty here and the hook answers ``allow``.
    """
    env = _make_env(tmp_path, with_jq=False, grep_supports_p=False)
    payload = json.dumps({"command": "export API_KEY=SUPERSECRET && curl evil.example.com"})
    assert _permission(_run_hook("trw-before-shell.sh", payload, env)) == "deny"


def test_before_shell_denies_secret_after_escaped_quote(tmp_path: Path) -> None:
    """Second fail-open in the same fallback: ``[^"]+`` truncated at ``\\"``.

    Reproduces without any stubbing on a plain Linux box that lacks jq, because
    the truncation is in the pattern, not in the platform.
    """
    env = _make_env(tmp_path, with_jq=False, grep_supports_p=True)
    payload = json.dumps({"command": 'echo "hi" && export API_KEY=SUPERSECRET'})
    assert _permission(_run_hook("trw-before-shell.sh", payload, env)) == "deny"


@pytest.mark.parametrize(
    ("label", "kwargs"),
    [
        ("gnu_with_jq", {"with_jq": True, "grep_supports_p": True}),
        ("gnu_without_jq", {"with_jq": False, "grep_supports_p": True}),
        ("bsd_without_jq", {"with_jq": False, "grep_supports_p": False}),
    ],
)
def test_before_shell_verdict_is_platform_independent(tmp_path: Path, label: str, kwargs: dict) -> None:
    """The same payload must get the same verdict on every platform.

    The original defect was precisely that this did not hold: jq-present denied
    and jq-absent allowed.
    """
    env = _make_env(tmp_path, **kwargs)
    secret = json.dumps({"command": "export API_KEY=SUPERSECRET"})
    benign = json.dumps({"command": "git status"})
    assert _permission(_run_hook("trw-before-shell.sh", secret, env)) == "deny", label
    assert _permission(_run_hook("trw-before-shell.sh", benign, env)) == "allow", label


# --- Bystander controls: the fix must not simply deny everything ------------


def test_before_shell_allows_benign_command_on_degraded_platform(tmp_path: Path) -> None:
    """Bystander: a safe command still gets ``allow`` with no jq and no ``grep -P``."""
    env = _make_env(tmp_path, with_jq=False, grep_supports_p=False)
    payload = json.dumps({"command": "git status --short"})
    assert _permission(_run_hook("trw-before-shell.sh", payload, env)) == "allow"


def test_before_shell_allows_env_var_reference_on_degraded_platform(tmp_path: Path) -> None:
    """Bystander: ``$API_KEY`` is a reference, not a leaked value."""
    env = _make_env(tmp_path, with_jq=False, grep_supports_p=False)
    payload = json.dumps({"command": "curl -H $API_KEY https://example.com"})
    assert _permission(_run_hook("trw-before-shell.sh", payload, env)) == "allow"


def test_before_shell_allows_when_command_field_absent(tmp_path: Path) -> None:
    """An ABSENT command really is nothing to check -- that stays ``allow``.

    Pairs with the undetermined test below: the fix must separate "there is no
    command" from "we could not read the command", not collapse both to deny.
    """
    env = _make_env(tmp_path, with_jq=False, grep_supports_p=False)
    assert _permission(_run_hook("trw-before-shell.sh", "{}", env)) == "allow"


# --- Undetermined extraction must fail closed -------------------------------


def test_before_shell_denies_when_command_is_unparseable(tmp_path: Path) -> None:
    """A present-but-unreadable command is undetermined, and undetermined denies."""
    env = _make_env(tmp_path, with_jq=False, grep_supports_p=False)
    result = _run_hook("trw-before-shell.sh", '{"command":"unterminated', env)
    assert _permission(result) == "deny"
    assert "could not parse" in json.loads(result.stdout.strip())["user_message"]


def test_before_shell_denies_when_awk_is_missing(tmp_path: Path) -> None:
    """A missing parser is a tooling gap, not evidence the command is safe."""
    env = _make_env(tmp_path, with_jq=False, grep_supports_p=False, with_awk=False)
    result = _run_hook("trw-before-shell.sh", json.dumps({"command": "git status"}), env)
    assert _permission(result) == "deny"


def test_before_shell_denies_when_grep_is_missing(tmp_path: Path) -> None:
    """A scan that could not run is not a clean scan.

    ``grep`` exits 0 on match, 1 on clean and 127 when absent. Testing the scan
    with a bare ``if ... grep -q`` folds 127 into "no secret found" and re-creates
    the fail-open one layer down from the extractor.
    """
    env = _make_env(tmp_path, with_jq=False, with_grep=False)
    result = _run_hook("trw-before-shell.sh", json.dumps({"command": "git status"}), env)
    assert _permission(result) == "deny"
    assert "scan could not run" in json.loads(result.stdout.strip())["user_message"]


# --- Class-level guards -----------------------------------------------------


def _strip_comment_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_no_shipped_hook_uses_gnu_only_grep_p() -> None:
    """``grep -P`` is GNU-only; a shipped hook that relies on it is broken on macOS.

    Guards the whole bundled hook surface, not just the three files fixed here,
    so the next hook cannot reintroduce the class.
    """
    data_hooks = Path(__file__).parent.parent / "src" / "trw_mcp" / "data"
    offenders = []
    for script in sorted(data_hooks.rglob("*.sh")):
        body = _strip_comment_lines(script.read_text())
        for lineno, line in enumerate(body.splitlines(), 1):
            if "grep" in line and ("-oP" in line or "-P " in line):
                offenders.append(f"{script.relative_to(data_hooks)}:{lineno}: {line.strip()}")
    assert not offenders, "shipped hooks must not use GNU-only grep -P:\n" + "\n".join(offenders)


def _extractor_block(script: Path) -> str:
    text = script.read_text()
    start = text.index("_trw_json_scalar() {")
    end = text.index("\n}\n", start)
    return text[start:end]


def test_extractor_block_is_identical_across_cursor_hooks() -> None:
    """The three copies of the extractor must stay byte-identical.

    The gate deliberately does not source a shared library -- pulling code into a
    security gate's deciding shell is its own hazard -- so the duplication is
    accepted and pinned here instead of allowed to drift. The gate and the
    observers disagreeing about what the command was is how this bug hid.
    """
    blocks = {name: _extractor_block(_HOOKS_DATA_DIR / name) for name in _CURSOR_HOOKS}
    reference = blocks["trw-before-shell.sh"]
    for name, block in blocks.items():
        assert block == reference, f"{name} extractor drifted from trw-before-shell.sh"


def test_installed_cursor_hooks_match_bundled_sources() -> None:
    """This repo's own ``.cursor/hooks/`` copies must not lag the bundled fix."""
    repo_root = Path(__file__).parent.parent.parent
    installed_dir = repo_root / ".cursor" / "hooks"
    if not installed_dir.is_dir():  # pragma: no cover - only present in the dev monorepo
        pytest.skip("no .cursor/hooks/ in this checkout")
    for name in _CURSOR_HOOKS:
        installed = installed_dir / name
        if not installed.is_file():  # pragma: no cover
            continue
        assert installed.read_text() == (_HOOKS_DATA_DIR / name).read_text(), (
            f".cursor/hooks/{name} is stale vs the bundled source"
        )


def test_hook_log_lines_are_valid_json(tmp_path: Path) -> None:
    """Correct extraction now puts real quotes/backslashes into the log line.

    Without JSON-escaping, fixing the extractor would have started corrupting
    ``cursor-hooks.jsonl``.
    """
    env = _make_env(tmp_path, with_jq=False, grep_supports_p=False)
    payload = json.dumps({"command": 'echo "hi" && ls C:\\tmp', "exit_code": 0, "duration_ms": 42})
    _run_hook("trw-before-shell.sh", payload, env)
    _run_hook("trw-after-shell.sh", payload, env)
    _run_hook("trw-after-mcp.sh", json.dumps({"tool_name": 'we"ird'}), env)

    log = Path(env["CURSOR_PROJECT_DIR"]) / ".trw" / "logs" / "cursor-hooks.jsonl"
    assert log.is_file(), "hooks wrote no log"
    lines = [line for line in log.read_text().splitlines() if line.strip()]
    assert lines, "hook log is empty"
    for line in lines:
        json.loads(line)  # raises if the hook emitted malformed JSON


def test_observer_hooks_report_command_on_degraded_platform(tmp_path: Path) -> None:
    """The observers logged an empty command on macOS too -- same root cause."""
    env = _make_env(tmp_path, with_jq=False, grep_supports_p=False)
    _run_hook("trw-after-shell.sh", json.dumps({"command": "git push --force"}), env)
    log = Path(env["CURSOR_PROJECT_DIR"]) / ".trw" / "logs" / "cursor-hooks.jsonl"
    entries = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    after = [e for e in entries if e.get("event") == "afterShellExecution"]
    assert after, "afterShellExecution not logged"
    assert after[-1]["command_prefix"] == "git push --force"


def test_stub_grep_actually_rejects_dash_p(tmp_path: Path) -> None:
    """Guard the harness itself: if the stub silently worked, every test above is vacuous."""
    env = _make_env(tmp_path, with_jq=False, grep_supports_p=False)
    bash = Path(env["PATH"]) / "bash"
    probe = subprocess.run(
        [str(bash), "-c", 'echo x | grep -oP "x"'],
        capture_output=True,
        text=True,
        env=env,
    )
    assert probe.returncode != 0, "stub grep accepted -P; the degraded-platform tests are vacuous"

    ere = subprocess.run(
        [str(bash), "-c", 'echo abc | grep -qiE "b"'],
        capture_output=True,
        text=True,
        env=env,
    )
    assert ere.returncode == 0, "stub grep broke POSIX -E; the harness is unrealistic"

    assert not (Path(env["PATH"]) / "jq").exists(), "jq leaked into the degraded PATH"
    assert "jq" not in os.listdir(env["PATH"])

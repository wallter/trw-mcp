"""PRD-CORE-300-FR02 slice S0 — the shared CLI-replacement contract.

One contract every ``trw-mcp <verb...>`` command that replaces an MCP tool
must meet, parametrized over ``CLI_REPLACEMENTS`` so a future S1-S6 registry
entry is covered automatically:

  (a) named, via the registry, by *family* (the first word of ``command``) in
      the generated AGENTS.md capability block and the served server
      instructions. The pointer is collapsed to one family-list line
      (2026-09-25, PRD-CORE-300-FR02) rather than a full per-verb enumeration;
      the full ``trw-mcp <command> --help`` text is one `trw-mcp --help` away.
  (b) ``--json`` prints exactly one parseable JSON document on stdout.
  (c) an unknown subcommand under the verb group exits non-zero and lists the
      valid ones.
  (d) a state-changing entry refuses under ``TRW_SURFACE_ROLE=reviewer`` or
      ``TRW_DISPATCH_CHILD`` (presence), and changes nothing on disk.
  (e) a read-only entry still runs under both.

S0's only real entry is ``local deliver``. The CLI is invoked in-process
(the real parser plus the real dispatch in ``_cli.py::main``), never via
subprocess.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from trw_mcp.server._cli_replacements import CLI_REPLACEMENTS, CliReplacement

pytestmark = pytest.mark.integration

#: Golden PRD fixtures (frozen, unrelated to the tmp_path scratch tree) — used
#: as the ``prd diff`` contract's before/after files, so this check needs no
#: extra scaffolding beyond the shared offline run.
_GOLDEN_PRDS = Path(__file__).resolve().parent / "fixtures" / "golden_prds"

#: The arguments each registry command takes in these contract checks, after its
#: verb path. ``{run_path}`` is an offline run the test scaffolds. A slice that
#: adds a registry entry adds its arguments here, or the coverage test fails.
CONTRACT_ARGV: dict[str, tuple[str, ...]] = {
    "code index": (".",),
    "code risk": (".",),
    "delivery recover": (
        "--delivery-id",
        "0190a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b",
        "--capability-token",
        "contract",
        "--expected-revision",
        "1",
        "--reason",
        "contract",
    ),
    "local deliver": ("--run-path", "{run_path}"),
    "probe run": ("--hypothesis", "true exits zero", "--command", "true", "--run-id", "contract"),
    "probe budget": ("--run-id", "contract"),
    "meta-tune propose": (
        "--target-path",
        "CLAUDE.md",
        "--candidate-content",
        "x",
        "--proposer-id",
        "contract",
        "--sandbox-command",
        "true",
    ),
    "meta-tune rollback": ("--proposal-id", "contract"),
    "telemetry events": ("--session-id", "contract"),
    "telemetry classify": ("--path", "CLAUDE.md"),
    "telemetry surface-diff": ("--snapshot-id-a", "a", "--snapshot-id-b", "b"),
    "telemetry security": (),
    "telemetry channel-stats": ("--repo-root", "."),
    "prd create": ("--input-text", "contract test prd body", "--title", "Contract Test PRD"),
    "profile explain": (),
    "prd diff": (
        "--before-path",
        str(_GOLDEN_PRDS / "tier_draft.md"),
        "--after-path",
        str(_GOLDEN_PRDS / "tier_approved.md"),
    ),
    "memory reembed": (),
    "telemetry pipeline-health": (),
    "run adopt": ("--run-path", "{run_path}", "--session-id", "contract-session"),
    "instructions sync": ("--dry-run",),
}

#: Environment a command needs to reach its result path in the ``--json`` check.
CONTRACT_ENV: dict[str, dict[str, str]] = {
    "probe run": {"TRW_PROBE_ENABLED": "1"},
}


def _argv(entry: CliReplacement, run_path: str) -> list[str]:
    return [*entry.command.split(), *(arg.format(run_path=run_path) for arg in CONTRACT_ARGV[entry.command])]


def test_every_registry_entry_has_contract_arguments() -> None:
    assert set(CONTRACT_ARGV) == {entry.command for entry in CLI_REPLACEMENTS}


def _run_cli(argv: list[str], cwd: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Invoke the real ``trw-mcp`` CLI entry in-process; return its exit code."""
    from trw_mcp.server._cli import main

    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "argv", ["trw-mcp", *argv])
    try:
        main()
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code) if isinstance(code, int) else 1
    return 0


def _tree_hash(root: Path) -> str:
    """A stable digest of every file's path + content under *root*."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _init_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> str:
    """Scaffold an offline run via the real CLI so ``local deliver`` has a target."""
    code = _run_cli(["local", "init", "--task", "cli-replacement-contract"], tmp_path, monkeypatch)
    out = capsys.readouterr().out
    assert code == 0, out
    for line in out.splitlines():
        if line.strip().startswith("Path:"):
            return line.split("Path:", 1)[1].strip()
    raise AssertionError(f"local init printed no run path:\n{out}")


# ---------------------------------------------------------------------------
# (a) pointer, rendered from the registry
# ---------------------------------------------------------------------------


def test_cli_replacements_pointer_is_named_in_the_served_server_instructions() -> None:
    from trw_mcp.server._app import _load_server_instructions
    from trw_mcp.server._cli_replacements import render_cli_replacements_pointer

    pointer = render_cli_replacements_pointer()
    assert pointer, "registry is non-empty but the rendered pointer is blank"
    assert pointer in _load_server_instructions(), "collapsed CLI-replacements pointer missing from served instructions"


def test_cli_replacements_pointer_is_named_in_the_generated_agents_md_block() -> None:
    from trw_mcp.bootstrap._client_integration_appendix import render_client_integration_appendix
    from trw_mcp.server._cli_replacements import render_cli_replacements_pointer

    pointer = render_cli_replacements_pointer()
    rendered = render_client_integration_appendix("claude-code")
    assert pointer in rendered, "collapsed CLI-replacements pointer missing from generated capability block"


# ---------------------------------------------------------------------------
# (b) --json is one parseable document
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", CLI_REPLACEMENTS, ids=lambda e: e.command)
def test_json_flag_prints_exactly_one_parseable_document(
    entry: CliReplacement, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_path = _init_run(tmp_path, monkeypatch, capsys)
    capsys.readouterr()
    for key, value in CONTRACT_ENV.get(entry.command, {}).items():
        monkeypatch.setenv(key, value)

    argv = [*_argv(entry, run_path), "--json"]
    code = _run_cli(argv, tmp_path, monkeypatch)
    out = capsys.readouterr().out

    assert code == 0, out
    payload = json.loads(out)  # raises if stdout carries more than one document / stray prose
    assert isinstance(payload, dict)


# ---------------------------------------------------------------------------
# (c) unknown subcommand under the verb group
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", CLI_REPLACEMENTS, ids=lambda e: e.command)
def test_unknown_subcommand_exits_nonzero_and_lists_valid_ones(
    entry: CliReplacement, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    verb_group = entry.command.split()[0]
    code = _run_cli([verb_group, "not-a-real-subcommand-xyz"], tmp_path, monkeypatch)
    captured = capsys.readouterr()

    assert code != 0
    assert "not-a-real-subcommand-xyz" in captured.err
    # every real subcommand under this verb group is named as a valid choice
    for sibling in CLI_REPLACEMENTS:
        if sibling.command.split()[0] != verb_group:
            continue
        assert sibling.command.split()[1] in captured.err


# ---------------------------------------------------------------------------
# (d) state-changing refusal under reviewer role / dispatched child
# ---------------------------------------------------------------------------


_STATE_CHANGING = [e for e in CLI_REPLACEMENTS if e.state_changing]


@pytest.mark.parametrize("entry", _STATE_CHANGING, ids=lambda e: e.command)
@pytest.mark.parametrize(
    ("env_key", "env_value"),
    [("TRW_SURFACE_ROLE", "reviewer"), ("TRW_DISPATCH_CHILD", "1")],
    ids=["reviewer_role", "dispatched_child"],
)
def test_state_changing_entry_refuses_under_guard_and_changes_nothing(
    entry: CliReplacement,
    env_key: str,
    env_value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_path = _init_run(tmp_path, monkeypatch, capsys)
    capsys.readouterr()
    before = _tree_hash(tmp_path)

    monkeypatch.setenv(env_key, env_value)
    argv = _argv(entry, run_path)
    code = _run_cli(argv, tmp_path, monkeypatch)
    captured = capsys.readouterr()

    after = _tree_hash(tmp_path)
    assert code != 0
    assert entry.command in captured.err
    assert after == before, "a refused state-changing command must change nothing on disk"


# ---------------------------------------------------------------------------
# (e) read-only entries still run under either guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env_key", "env_value"),
    [("TRW_SURFACE_ROLE", "reviewer"), ("TRW_DISPATCH_CHILD", "1")],
    ids=["reviewer_role", "dispatched_child"],
)
def test_a_read_only_fake_entry_runs_under_both_guards(
    env_key: str, env_value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_path = _init_run(tmp_path, monkeypatch, capsys)
    capsys.readouterr()

    fake = CliReplacement(command="local status", replaces="", state_changing=False, summary="fixture: read-only")
    monkeypatch.setattr("trw_mcp.server._cli_replacements.CLI_REPLACEMENTS", (fake,))

    monkeypatch.setenv(env_key, env_value)
    code = _run_cli(["local", "status", "--run-path", run_path], tmp_path, monkeypatch)
    captured = capsys.readouterr()

    assert code == 0, captured.out + captured.err
    assert "Status: active" in captured.out


# ---------------------------------------------------------------------------
# Planted negatives — a registry entry that violates the contract must show it.
# ---------------------------------------------------------------------------


def test_planted_negative_entry_without_json_handling_fails_the_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``local status`` has no ``--json`` support: claiming it in the registry
    must NOT pass the ``--json`` check above."""
    run_path = _init_run(tmp_path, monkeypatch, capsys)
    capsys.readouterr()

    fake = CliReplacement(
        command="local status", replaces="", state_changing=False, summary="fixture: no --json support"
    )
    monkeypatch.setattr("trw_mcp.server._cli_replacements.CLI_REPLACEMENTS", (fake,))

    code = _run_cli(["local", "status", "--run-path", run_path, "--json"], tmp_path, monkeypatch)
    out = capsys.readouterr().out

    assert code != 0  # argparse rejects the unrecognized --json flag
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_planted_negative_entry_missing_from_pointer_fails_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """The "named in the pointer" check is non-vacuous: a family registered
    via the (monkeypatched) registry appears in the rendered pointer, while a
    family that was never registered does not — the pointer text tracks the
    registry, not the other way around. And whichever pointer the registry
    produces must actually propagate into both surfaces."""
    from trw_mcp.bootstrap._client_integration_appendix import render_client_integration_appendix
    from trw_mcp.server._app import _load_server_instructions
    from trw_mcp.server._cli_replacements import render_cli_replacements_pointer

    registered = CliReplacement(
        command="local status", replaces="", state_changing=False, summary="fixture: registered via monkeypatch"
    )
    unregistered = CliReplacement(
        command="phantom verb-that-was-never-wired",
        replaces="",
        state_changing=False,
        summary="fixture: never registered",
    )
    monkeypatch.setattr("trw_mcp.server._cli_replacements.CLI_REPLACEMENTS", (registered,))

    pointer = render_cli_replacements_pointer()
    assert "local" in pointer, "registering an entry must make its family appear in the pointer"
    assert "phantom" not in pointer, "an unregistered command's family must not be claimed"

    served = _load_server_instructions()
    assert pointer in served, "the rendered pointer must propagate into the served server instructions"

    rendered = render_client_integration_appendix("claude-code")
    assert pointer in rendered, "the rendered pointer must propagate into the generated capability block"

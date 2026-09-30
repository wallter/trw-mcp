"""CODEX-P0-B-ZERO-TOOL (runner side): a child whose TRW MCP server never reported is not a passing review.

Measured on codex-cli 0.159.0: a nonexistent ``mcp_servers.trw.command`` still exits 0 with the ordinary four ``--json``
events and nothing on stderr. These tests stand in for that child with a script that prints the same four events, so the
runner's demand for the server's nonce-bound receipt is exercised without a model.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from trw_mcp.dispatch import _handshake, _runner
from trw_mcp.dispatch._types import DispatchRequest
from trw_mcp.models.surface_packs import REVIEWER_TOOLS

_EVENTS = (
    '{"type":"thread.started","thread_id":"t"}\n{"type":"turn.started"}\n'
    '{"type":"item.completed","item":{"id":"i","type":"agent_message","text":"No findings."}}\n'
    '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n'
)

_FAKE = """#!/bin/sh
# a stand-in `codex exec --json`: prints the four success events; optionally plays the TRW server's part
mode=$(cat "$(dirname "$0")/mode")
for a in "$@"; do
  case "$a" in
    mcp_servers.trw_dispatch_*.env.TRW_DISPATCH_HANDSHAKE=*) path=${a#*=};;
    mcp_servers.trw_dispatch_*.env.TRW_DISPATCH_NONCE=*) nonce=${a#*=};;
  esac
done
path=$(printf %s "$path" | tr -d '"'); nonce=$(printf %s "$nonce" | tr -d '"')
case "$mode" in
  good) printf '{"nonce":"%s","tools":TOOLS,"pid":1}' "$nonce" > "$path";;
  forged) printf '{"nonce":"someone-elses","tools":TOOLS,"pid":1}' > "$path";;
  empty) printf '{"nonce":"%s","tools":[],"pid":1}' "$nonce" > "$path";;
  partial) printf '{"nonce":"%s","tools":["trw_recall"],"pid":1}' "$nonce" > "$path";;
esac
printf '%b' 'EVENTS'
"""


@pytest.fixture
def fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "codex"
    tools = json.dumps(sorted(REVIEWER_TOOLS))
    script.write_text(_FAKE.replace("TOOLS", tools).replace("EVENTS", _EVENTS.replace("\n", "\\n")), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bindir}:/usr/bin:/bin")
    monkeypatch.setattr(_runner, "unadvertised_flags", lambda *_a, **_k: None)  # the fake has no --help to probe

    def run(mode: str, *, require: bool = True) -> object:
        (bindir / "mode").write_text(mode, encoding="utf-8")
        if require:
            monkeypatch.setenv(_handshake.REQUIRE_ENV, "1")
        else:
            monkeypatch.delenv(_handshake.REQUIRE_ENV, raising=False)
        req = DispatchRequest(
            client="codex",
            prompt="review this",
            posture="reviewer",
            read_only=True,
            cwd=tmp_path,
            timeout_s=30,
            isolate=False,
        )
        return _runner.dispatch(req)

    return run


def test_a_child_that_lists_the_review_tools_is_a_proven_review(fake_codex) -> None:  # type: ignore[no-untyped-def]
    result = fake_codex("good")
    assert result.ok and result.silence_reason is None
    assert "mcp_started" in result.enforcement_layers
    assert "-c" in result.argv_redacted and "TRW_DISPATCH_HANDSHAKE" not in " ".join(result.argv_redacted), (
        "the receipt channel goes to the child; the reported argv stays the redacted base"
    )


def test_a_dead_server_child_looks_like_a_success_to_every_other_signal_but_is_not_ok(fake_codex) -> None:  # type: ignore[no-untyped-def]
    result = fake_codex("none")
    assert result.exit_code == 0 and result.text == "No findings."
    assert result.structured is not None and result.structured["stop_reason"] == "completed"
    assert result.silence_reason == "trw_server_not_started" and not result.ok
    assert "mcp_started" not in result.enforcement_layers
    assert "NOT proven" in result.mcp_role_note and "never reported" in result.mcp_role_note


@pytest.mark.parametrize(
    ("mode", "why"),
    [
        ("forged", "another run's nonce"),
        ("empty", "listed no tools"),
        ("partial", "did not list the review tools"),
    ],
)
def test_a_receipt_that_is_not_this_runs_full_proof_is_refused(fake_codex, mode: str, why: str) -> None:  # type: ignore[no-untyped-def]
    result = fake_codex(mode)
    assert result.silence_reason == "trw_server_not_started" and not result.ok
    assert why in result.mcp_role_note


def test_without_the_switch_nothing_changes(fake_codex) -> None:  # type: ignore[no-untyped-def]
    result = fake_codex("none", require=False)
    assert result.ok and result.silence_reason is None
    assert "mcp_started" not in result.enforcement_layers and "NOT proven" not in result.mcp_role_note


def test_the_receipt_directory_is_removed_after_the_run(fake_codex, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    import glob
    import tempfile

    before = set(glob.glob(f"{tempfile.gettempdir()}/trw-handshake-*"))
    fake_codex("good")
    fake_codex("none")
    assert set(glob.glob(f"{tempfile.gettempdir()}/trw-handshake-*")) == before


def _rendered(req: DispatchRequest) -> list[str]:
    from trw_mcp.dispatch._commands import build_command

    return build_command(req)


def test_the_channel_is_only_offered_where_it_can_be_placed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(_handshake.REQUIRE_ENV, "1")
    codex = DispatchRequest(client="codex", prompt="p", posture="reviewer", read_only=True, cwd=tmp_path, isolate=False)
    argv = _rendered(codex)
    assert _handshake.wanted(codex, argv)
    assert not _handshake.wanted(codex, [*argv, "--json"]), "prompt must be the last argument"
    assert not _handshake.wanted(codex, ["codex", "exec", "p"]), (
        "no generated active server in the argv: nothing to hand it to"
    )
    other = DispatchRequest(
        client="claude", prompt="p", posture="reviewer", read_only=True, cwd=tmp_path, isolate=False
    )
    assert not _handshake.wanted(other, ["claude", "p"])
    plain = DispatchRequest(client="codex", prompt="p", read_only=True, cwd=tmp_path, isolate=False)
    assert not _handshake.wanted(plain, _rendered(plain)), "a child with no TRW server owes no receipt"


@pytest.mark.parametrize("kind", ["reviewer", "with_trw"])
def test_the_receipt_goes_to_the_active_generated_server_never_the_disabled_trw_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str
) -> None:
    """codex review of the first slice: the renderer runs the child's server as ``trw_dispatch_<uuid>`` and leaves
    ``mcp_servers.trw`` present but ``enabled=false``; a receipt channel under ``trw`` would never reach the server."""
    monkeypatch.setenv(_handshake.REQUIRE_ENV, "1")
    extra = {"posture": "reviewer"} if kind == "reviewer" else {"with_trw": True}
    req = DispatchRequest(client="codex", prompt="p", read_only=True, cwd=tmp_path, isolate=False, **extra)
    base = _rendered(req)
    server = _handshake.active_server(base)
    assert server is not None and server.startswith("trw_dispatch_")
    assert "mcp_servers.trw.enabled=false" in base, "the disabled table is real and must not be the target"
    handshake, argv = _handshake.begin(req, base)
    try:
        assert handshake is not None
        env_keys = [a for a in argv if ".env.TRW_DISPATCH_HANDSHAKE=" in a or ".env.TRW_DISPATCH_NONCE=" in a]
        assert len(env_keys) == 2 and all(k.startswith(f"mcp_servers.{server}.env.") for k in env_keys)
        assert not any(k.startswith("mcp_servers.trw.env.TRW_DISPATCH") for k in argv)
    finally:
        _handshake.discard(handshake)


def test_the_switch_survives_the_hop_to_the_background_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """``start_background`` builds the detached runner's env from an allowlist that dropped every TRW_* name."""
    from trw_mcp.dispatch._env import build_runner_env

    on = build_runner_env("codex", source_env={_handshake.REQUIRE_ENV: "1", "TRW_SECRET_TOKEN": "x"})
    assert on.get(_handshake.REQUIRE_ENV) == "1" and "TRW_SECRET_TOKEN" not in on
    assert _handshake.REQUIRE_ENV not in build_runner_env("codex", source_env={})


def test_a_symlinked_or_oversized_receipt_is_no_proof(tmp_path: Path) -> None:
    handshake = _handshake.Handshake(tmp_path, "n" * 32)
    target = tmp_path / "elsewhere.json"
    target.write_text(json.dumps({"nonce": handshake.nonce, "tools": sorted(REVIEWER_TOOLS)}), encoding="utf-8")
    handshake.path.symlink_to(target)
    assert _handshake.proof(handshake, REVIEWER_TOOLS)[0] is False
    handshake.path.unlink()
    handshake.path.write_text("x" * (70 * 1024), encoding="utf-8")
    assert _handshake.proof(handshake, REVIEWER_TOOLS)[0] is False


def test_a_non_ascii_nonce_in_a_receipt_is_a_refusal_not_a_crash(tmp_path: Path) -> None:
    handshake = _handshake.Handshake(tmp_path, "n" * 32)
    handshake.path.write_text(json.dumps({"nonce": "n\u00e9" * 16, "tools": sorted(REVIEWER_TOOLS)}), encoding="utf-8")
    assert _handshake.proof(handshake, REVIEWER_TOOLS) == (False, "receipt carries another run's nonce")


def test_the_spliced_arguments_are_valid_toml_strings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import tomllib

    monkeypatch.setenv(_handshake.REQUIRE_ENV, "1")
    req = DispatchRequest(client="codex", prompt="p", posture="reviewer", read_only=True, cwd=tmp_path, isolate=False)
    base = _rendered(req)
    handshake, argv = _handshake.begin(req, base)
    try:
        assert handshake is not None and argv[-1] == "p"
        added = argv[len(base) - 1 : -1]
        pairs = [added[i + 1] for i, a in enumerate(added) if a == "-c"]
        parsed = [tomllib.loads(pair.split(".env.", 1)[1]) for pair in pairs]
        assert parsed == [{_handshake.PATH_ENV: str(handshake.path)}, {_handshake.NONCE_ENV: handshake.nonce}]
    finally:
        _handshake.discard(handshake)

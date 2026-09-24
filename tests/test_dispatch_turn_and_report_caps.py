"""PRD-CORE-290-FR04: bounded reports and turns.

Dispatch derives the turn cap from the role's task-class row (a bare prompt takes
the 30-turn default; review and security classes are exempt), applies it only
through a client's verified turn-limit flag, and records requested/applied/
unsupported; an operator may override it. A cap hit is reported as incomplete
work with a next-read pointer, never as a success, and the partial transcript is
kept whole. Rendered agents ask for a concise final report (default 800
characters) that links durable findings, except review and security agents.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.dispatch import dispatch
from trw_mcp.dispatch._client_specs import CLIENT_SPECS
from trw_mcp.dispatch._commands import build_command
from trw_mcp.dispatch._policy import policy_record
from trw_mcp.dispatch._resolve import resolve_dispatch_request
from trw_mcp.dispatch._types import DispatchRequest

_TURN_FLAG_CLIENTS = sorted(c for c, spec in CLIENT_SPECS.items() if spec.max_turns_flag)


class _Cfg:
    def __init__(self, **overrides: Any) -> None:
        self.dispatch_enabled_clients = list(CLIENT_SPECS)
        self.dispatch_default_client = "grok"
        self.dispatch_default_models: dict[str, str] = {}
        self.dispatch_default_timeout_s = 600
        self.dispatch_default_read_only = True
        self.dispatch_role_client: dict[str, str] = {}
        self.dispatch_default_effort: str | None = None
        for key, value in overrides.items():
            setattr(self, key, value)


def _resolve(client: str, role: str | None = None, **cfg: Any) -> DispatchRequest:
    return resolve_dispatch_request(
        client=client,
        prompt="review this",
        role=role,
        model=None,
        cwd=Path("/tmp"),
        timeout_s=None,
        isolate=True,
        use_pty=False,
        dispatch_cfg=_Cfg(**cfg),
    )


def test_only_clients_with_a_verified_turn_flag_carry_one() -> None:
    """grok 1.0.34 documents --max-turns in its --help; the others do not (probed 2026-09-22)."""
    assert _TURN_FLAG_CLIENTS == ["grok"]
    assert CLIENT_SPECS["grok"].max_turns_flag == "--max-turns"  # type: ignore[index]


def test_the_default_cap_is_30_and_reaches_the_argv() -> None:
    req = _resolve("grok")
    assert (req.max_turns, req.max_turns_source) == (30, "default")
    argv = build_command(req)
    assert argv[argv.index("--max-turns") + 1] == "30"
    assert policy_record(req)["turns"] == {"requested": 30, "applied": 30, "source": "default"}


@pytest.mark.parametrize("role", ["code-review", "design-audit", "architectural-audit", "adversarial-audit"])
def test_review_and_security_roles_are_exempt_from_the_cap(role: str) -> None:
    req = _resolve("grok", role=role)
    assert (req.max_turns, req.max_turns_source) == (None, "exempt")
    assert "--max-turns" not in build_command(req)
    assert policy_record(req)["turns"] == {"requested": None, "applied": None, "source": "exempt"}


@pytest.mark.parametrize(("configured", "expected", "source"), [(5, 5, "config"), (0, None, "disabled")])
@pytest.mark.parametrize("role", [None, "code-review"])
def test_operator_policy_overrides_the_cap(
    role: str | None, configured: int, expected: int | None, source: str
) -> None:
    req = _resolve("grok", role=role, dispatch_default_max_turns=configured)
    assert (req.max_turns, req.max_turns_source) == (expected, source)
    assert ("--max-turns" in build_command(req)) is (expected is not None)


def _real_cfg(**fields: Any) -> Any:
    from trw_mcp.models.config import TRWConfig

    return TRWConfig(**fields).dispatch


def test_the_real_config_carries_the_operators_turn_and_effort_defaults() -> None:
    """The DispatchConfig projection must expose the FR03/FR04 fields, or the operator's value never arrives."""
    req = resolve_dispatch_request(
        client="grok",
        prompt="p",
        role=None,
        model=None,
        cwd=Path("/tmp"),
        timeout_s=None,
        isolate=True,
        use_pty=False,
        dispatch_cfg=_real_cfg(dispatch_default_max_turns=7, dispatch_default_effort="high"),
    )
    assert (req.max_turns, req.max_turns_source) == (7, "config")
    assert (req.effort, req.effort_source) == ("high", "config")


def test_an_operator_value_equal_to_the_default_is_still_the_operators() -> None:
    """Source is decided by what the operator set, not by comparing against the default."""
    assert _real_cfg().operator_set == frozenset()
    assert "dispatch_default_max_turns" in _real_cfg(dispatch_default_max_turns=30).operator_set
    req = resolve_dispatch_request(
        client="grok",
        prompt="p",
        role="code-review",
        model=None,
        cwd=Path("/tmp"),
        timeout_s=None,
        isolate=True,
        use_pty=False,
        dispatch_cfg=_real_cfg(dispatch_default_max_turns=30),
    )
    assert (req.max_turns, req.max_turns_source) == (30, "config")


@pytest.mark.parametrize("client", ["claude", "codex"])
def test_a_client_without_a_verified_flag_gets_no_flag_and_says_unsupported(client: str) -> None:
    req = _resolve(client)
    assert "--max-turns" not in build_command(req)
    assert policy_record(req)["turns"] == {"requested": 30, "applied": None, "source": "unsupported"}


def _stub(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake-grok"
    script.write_text("#!/usr/bin/env bash\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    return script


def test_a_cap_hit_is_incomplete_with_a_next_read_pointer_never_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stub replays grok's measured cap-exhaustion shape: partial JSON, exit 1, stderr marker."""
    partial = json.dumps({"text": "Finding 1: the gate never runs. Still checking two.txt", "stopReason": "cancelled"})
    stub = _stub(tmp_path, f"echo '{partial}'\necho 'Error: max turns reached' >&2\nexit 1\n")
    monkeypatch.setattr("trw_mcp.dispatch._runner.build_command", lambda _req, *, confined=False: [str(stub)])

    result = dispatch(DispatchRequest(client="grok", prompt="review", max_turns=2, max_turns_source="config"))

    assert result.ok is False
    assert result.silence_reason == "turn_cap_reached"
    assert "Finding 1: the gate never runs" in result.text, "the partial findings must survive whole"
    assert "raw_stdout" in result.next_read and "dispatch_default_max_turns" in result.next_read


def test_an_ordinary_failure_is_not_mislabelled_a_cap_hit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _stub(tmp_path, "echo 'boom' >&2\nexit 1\n")
    monkeypatch.setattr("trw_mcp.dispatch._runner.build_command", lambda _req, *, confined=False: [str(stub)])
    result = dispatch(DispatchRequest(client="grok", prompt="review", max_turns=2))
    assert result.silence_reason != "turn_cap_reached"
    assert result.next_read == ""


# ── Rendered agents: a concise final report that links durable findings ──────


def _bundled(name: str) -> str:
    from trw_mcp.bootstrap._utils import _DATA_DIR

    return (_DATA_DIR / "agents" / f"{name}.md").read_text(encoding="utf-8")


def test_rendered_agent_asks_for_a_bounded_report_by_default() -> None:
    from trw_mcp.agents.tier_resolver import materialize_agent

    rendered = materialize_agent(_bundled("trw-implementer"), client="claude-code")
    assert "800 characters" in rendered
    assert "link" in rendered.lower()


@pytest.mark.parametrize("name", ["trw-reviewer", "trw-auditor", "trw-requirement-reviewer", "trw-adversarial-auditor"])
def test_review_and_security_agents_carry_no_report_cap(name: str) -> None:
    """Their findings schema is structured YAML that a character cap would cut."""
    from trw_mcp.agents.tier_resolver import materialize_agent

    assert "## Final report" not in materialize_agent(_bundled(name), client="claude-code", report_max_chars=400)


@pytest.mark.parametrize(("chars", "present"), [(400, True), (0, False)])
def test_operator_policy_overrides_the_report_cap(chars: int, present: bool) -> None:
    from trw_mcp.agents.tier_resolver import materialize_agent

    rendered = materialize_agent(_bundled("trw-implementer"), client="claude-code", report_max_chars=chars)
    assert ("400 characters" in rendered) is present
    assert "800 characters" not in rendered


def test_an_installed_agent_carries_the_projects_configured_cap(tmp_path: Path) -> None:
    from trw_mcp.bootstrap import init_project

    (tmp_path / ".git").mkdir()
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("agent_report_max_chars: 500\n", encoding="utf-8")
    init_project(tmp_path, ide="claude-code")

    installed = (tmp_path / ".claude" / "agents" / "trw-implementer.md").read_text(encoding="utf-8")
    assert "500 characters" in installed


def test_the_report_cap_env_var_beats_the_project_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.bootstrap import init_project

    (tmp_path / ".git").mkdir()
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("agent_report_max_chars: 500\n", encoding="utf-8")
    monkeypatch.setenv("TRW_AGENT_REPORT_MAX_CHARS", "300")
    init_project(tmp_path, ide="claude-code")

    installed = (tmp_path / ".claude" / "agents" / "trw-implementer.md").read_text(encoding="utf-8")
    assert "300 characters" in installed


def test_a_render_outside_install_or_update_takes_the_live_config_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers such as the antigravity explorer render outside a project scope; they get TRWConfig's value."""
    from types import SimpleNamespace

    from trw_mcp.agents.tier_resolver import materialize_agent

    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: SimpleNamespace(agent_report_max_chars=250))
    assert "250 characters" in materialize_agent(_bundled("trw-implementer"), client="claude-code")

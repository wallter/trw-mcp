"""E2E-DISPATCH-CLI-TARGETS (INC-042): ``--client NAME`` accepts every name the MCP tool accepts.

``trw-mcp dispatch --client sonnet`` (and ``antigravity``, ``grok:grok-4.7``, ``claude:sonnet``) failed as
"client 'sonnet' is disabled": only the comma / multi-prompt path parsed the name, so the single-client path
handed the raw string to the resolver. Both CLI forms now resolve through the same ``parse_target``.
"""

from __future__ import annotations

import argparse

import pytest

from trw_mcp.dispatch._cli import run_dispatch
from trw_mcp.dispatch._client_aliases import CLIENT_ALIASES, MODEL_SHORTHANDS
from trw_mcp.dispatch._client_specs import SUPPORTED_CLIENTS
from trw_mcp.dispatch._targets import Target, parse_target

NAMES = sorted({*SUPPORTED_CLIENTS, *CLIENT_ALIASES, *MODEL_SHORTHANDS, "grok:grok-4.7", "claude:sonnet"})


class _Resolved(Exception):
    """Raised by the resolver stub to stop before any launch."""


@pytest.fixture(autouse=True)
def _never_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here may start a real client: on a regression the default client would really run."""

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a real dispatch launch was attempted")

    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch", refuse)
    monkeypatch.setattr("trw_mcp.dispatch._cli.dispatch_with_fallback", refuse)
    monkeypatch.setattr("trw_mcp.dispatch._cli._fan_out", refuse)  # the comma form launches through here
    monkeypatch.setattr("trw_mcp.tools._dispatch_fanout._run_fanout", refuse)


def _ns(client: str) -> argparse.Namespace:
    return argparse.Namespace(
        client=client,
        prompt="p",
        prompt_file=None,
        role=None,
        model=None,
        cwd=None,
        timeout=60,
        output_file=None,
        no_isolate=False,
        allow_writes=False,
        pty=False,
        json=False,
    )


@pytest.mark.parametrize("name", NAMES)
def test_the_single_client_form_resolves_the_same_target_the_tool_does(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Target] = []

    def resolve(*, client: str | None, model: str | None, **_: object) -> object:
        seen.append(Target(client, model))  # type: ignore[arg-type]
        raise _Resolved

    monkeypatch.setattr("trw_mcp.dispatch._cli.resolve_dispatch_request", resolve)
    with pytest.raises(_Resolved):
        run_dispatch(_ns(name))

    assert seen == [parse_target(name)]


@pytest.mark.parametrize("name", NAMES)
def test_the_comma_form_resolves_the_same_target(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    lanes: list[object] = []

    def fan_out(lanes_in: list[object], *_: object) -> None:
        lanes.extend(lanes_in)
        raise _Resolved

    monkeypatch.setattr("trw_mcp.dispatch._cli._fan_out", fan_out)
    with pytest.raises(_Resolved):
        run_dispatch(_ns(f"{name},{name}"))  # a duplicate is dropped, so one lane

    assert lanes == [parse_target(name)]


def test_an_unknown_name_is_named_as_unknown_not_disabled(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        run_dispatch(_ns("bogus"))

    assert exited.value.code == 2
    err = capsys.readouterr().err
    assert "unknown dispatch target 'bogus'" in err
    assert "disabled" not in err


@pytest.mark.parametrize("blank", ["", " ", ",", " , ,"])
def test_an_explicit_empty_client_is_a_usage_error(blank: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Lead ruling: a given-but-empty --client is a scripting bug, never a silent fall to the default client."""
    with pytest.raises(SystemExit) as exited:
        run_dispatch(_ns(blank))

    assert exited.value.code == 2
    assert "--client given but empty; omit it to use the configured default" in capsys.readouterr().err


def test_an_omitted_client_still_uses_the_configured_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Target] = []

    def resolve(*, client: str | None, model: str | None, **_: object) -> object:
        seen.append(Target(client, model))  # type: ignore[arg-type]
        raise _Resolved

    monkeypatch.setattr("trw_mcp.dispatch._cli.resolve_dispatch_request", resolve)
    with pytest.raises(_Resolved):
        run_dispatch(_ns(None))  # type: ignore[arg-type]

    assert seen == [Target(None, None)]  # type: ignore[arg-type]

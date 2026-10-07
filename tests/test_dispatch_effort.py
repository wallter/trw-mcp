"""Dispatch reasoning effort: role defaults, client flags, clamping, never an invented flag.

Opus 5.5 defaults to ``medium`` effort (Opus 5 defaulted to ``high``) and Claude
Code ignores the top-level ``effortLevel`` setting for it, so a dispatched child
that TRW sends no effort now runs at ``medium`` whatever the caller intended. The
operator's model-tier policy (2026-09-22) sets review/planning at ``medium`` and
security / adversarial audit at ``medium`` too (operator, 2026-09-24); ``xhigh``/``max`` only with evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args

import pytest

from trw_mcp.dispatch._client_spec_types import EFFORT_LEVELS, ClientSpec, DispatchEffort
from trw_mcp.dispatch._client_specs import CLIENT_SPECS
from trw_mcp.dispatch._commands import build_command
from trw_mcp.dispatch._policy import resolve_effort
from trw_mcp.dispatch._resolve import resolve_dispatch_request
from trw_mcp.dispatch._roles import ROLE_TABLE
from trw_mcp.dispatch._types import DispatchRequest

pytestmark = pytest.mark.unit

_FLAG_CLIENTS = sorted(c for c, spec in CLIENT_SPECS.items() if spec.effort_flag or spec.effort_config_key)
_NO_FLAG_CLIENTS = sorted(c for c, spec in CLIENT_SPECS.items() if not (spec.effort_flag or spec.effort_config_key))


def _request(client: str, **kw: Any) -> DispatchRequest:
    return DispatchRequest(client=client, prompt="probe", cwd=Path("/tmp"), **kw)  # type: ignore[arg-type]


def _effort_tokens(client: str, argv: list[str]) -> list[str]:
    spec = CLIENT_SPECS[client]  # type: ignore[index]
    if spec.effort_config_key:  # a ``-c KEY="VALUE"`` override, not a flag
        prefix = f"{spec.effort_config_key}="
        return [
            argv[i + 1][len(prefix) :].strip('"')
            for i, token in enumerate(argv[:-1])
            if token == "-c" and argv[i + 1].startswith(prefix)
        ]
    return [argv[i + 1] for i, token in enumerate(argv[:-1]) if token == spec.effort_flag]


# --------------------------------------------------------------------------- #
# Role defaults: the operator policy, and totality over the role set.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("code-review", "medium"),
        ("design-audit", "medium"),
        ("architectural-audit", "medium"),
        ("adversarial-audit", "medium"),
        (None, None),
        ("", None),
        ("not-a-role", None),
    ],
)
def test_role_defaults_follow_the_operator_policy(role: str | None, expected: str | None) -> None:
    assert resolve_effort(None, role, None)[0] == expected


def test_every_role_chooses_an_effort() -> None:
    """Every role resolves a table effort, never the client default."""
    assert all(resolve_effort(None, role, None)[1] == "table" for role in ROLE_TABLE)


def test_no_role_defaults_to_xhigh_or_max() -> None:
    """The operator wants xhigh/max only with evidence, never as a standing default."""
    assert not {"xhigh", "max"} & {resolve_effort(None, role, None)[0] for role in ROLE_TABLE}


def test_the_portable_vocabulary_and_the_literal_agree() -> None:
    assert EFFORT_LEVELS == get_args(DispatchEffort)


# --------------------------------------------------------------------------- #
# Argv mapping.
# --------------------------------------------------------------------------- #


def test_claude_carries_the_requested_effort() -> None:
    argv = build_command(_request("claude", effort="high"))
    assert _effort_tokens("claude", argv) == ["high"]


def test_a_level_the_client_lacks_clamps_down_never_up() -> None:
    """agy accepts low|medium|high: xhigh and max run at high, not at an invented value."""
    for level in ("xhigh", "max"):
        assert _effort_tokens("agy", build_command(_request("agy", effort=level))) == ["high"]
    assert _effort_tokens("agy", build_command(_request("agy", effort="low"))) == ["low"]


@pytest.mark.parametrize("client", _FLAG_CLIENTS)
@pytest.mark.parametrize("level", EFFORT_LEVELS)
def test_an_emitted_value_is_always_in_the_clients_own_vocabulary(client: str, level: str) -> None:
    values = _effort_tokens(client, build_command(_request(client, effort=level)))
    assert len(values) == 1
    assert values[0] in CLIENT_SPECS[client].effort_levels  # type: ignore[index]


@pytest.mark.parametrize("client", _NO_FLAG_CLIENTS)
def test_a_client_with_no_documented_flag_gets_no_effort(client: str) -> None:
    """Never invent a flag: the argv is byte-identical to the same request without effort."""
    assert build_command(_request(client, effort="high")) == build_command(_request(client))


@pytest.mark.parametrize("client", sorted(CLIENT_SPECS))
def test_no_effort_leaves_every_clients_argv_unchanged(client: str) -> None:
    """The recorded argv baselines stay valid for a request that carries no effort."""
    argv = build_command(_request(client))
    assert _effort_tokens(client, argv) == [] if client in _FLAG_CLIENTS else True


@pytest.mark.parametrize(
    "model",
    [
        "haiku",  # ambiguous alias: 5.5 on the Anthropic API, 4.5 on Bedrock/Vertex/Foundry -> drop effort
        "HAIKU",
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
        "anthropic.claude-haiku-4-5",
        "claude-3-5-haiku-20241022",
        "claude-3-haiku-20240307",
    ],
)
def test_a_haiku_model_gets_no_effort(model: str) -> None:
    """Haiku accepts no effort parameter at all; sending one is an error, not a no-op."""
    assert _effort_tokens("claude", build_command(_request("claude", model=model, effort="high"))) == []


@pytest.mark.parametrize(
    "model",
    ["claude-haiku-5-5", "anthropic.claude-haiku-5-5", "global.anthropic.claude-haiku-5-5", "claude-haiku-6"],
)
def test_haiku_5_and_later_keep_their_effort(model: str) -> None:
    """Haiku 5.5 is the first Haiku with effort (low..max); dropping it would silently run at the default."""
    assert _effort_tokens("claude", build_command(_request("claude", model=model, effort="high"))) == ["high"]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("haiku", True),
        ("claude-haiku-4-5-20251001", True),
        ("claude-3-5-haiku-20241022", True),
        ("claude-haiku-5-5", False),
        ("claude-haiku-5-5-20260101", False),
        ("claude-opus-5-5", False),
        ("claude-sonnet-4-5", False),
        (None, False),
        ("", False),
    ],
)
def test_model_takes_no_effort_predicate(model: str | None, expected: bool) -> None:
    from trw_mcp.dispatch._commands import model_takes_no_effort

    assert model_takes_no_effort(model) is expected


def test_require_effort_refusal_skips_haiku_4_but_not_haiku_5() -> None:
    """The second consumer of the predicate (_policy): require-effort must still refuse a 5.5 launch lacking effort."""
    from trw_mcp.dispatch._policy import require_effort as refusal

    assert refusal("claude", "claude-haiku-4-5", "none") is None
    assert refusal("claude", "haiku", "none") is None
    assert refusal("claude", "claude-haiku-5-5", "none") is not None


def test_a_non_haiku_model_keeps_its_effort() -> None:
    """Non-vacuity for the guard above: it must not swallow every explicit model."""
    argv = build_command(_request("claude", model="claude-opus-5-5", effort="high"))
    assert _effort_tokens("claude", argv) == ["high"]


# --------------------------------------------------------------------------- #
# End to end through the resolver: role in, flag out.
# --------------------------------------------------------------------------- #


class _Cfg:
    def __init__(self) -> None:
        self.dispatch_enabled_clients = ["claude", "agy", "codex"]
        self.dispatch_default_client = "claude"
        self.dispatch_default_models: dict[str, str] = {}
        self.dispatch_default_timeout_s = 600
        self.dispatch_default_read_only = True


def _resolved(role: str | None, client: str = "claude") -> DispatchRequest:
    return resolve_dispatch_request(
        client=client,
        prompt="review this",
        role=role,
        model=None,
        cwd=Path("/tmp"),
        timeout_s=None,
        isolate=True,
        use_pty=False,
        dispatch_cfg=_Cfg(),
    )


def test_an_adversarial_audit_reaches_claude_at_medium_effort() -> None:
    req = _resolved("adversarial-audit")
    assert req.effort == "medium"
    assert _effort_tokens("claude", build_command(req)) == ["medium"]


def test_a_code_review_reaches_claude_at_medium_effort() -> None:
    assert _effort_tokens("claude", build_command(_resolved("code-review"))) == ["medium"]


def test_a_bare_prompt_passes_no_effort() -> None:
    """No role, no classification: the child runs at its own default, not a level TRW guessed."""
    req = _resolved(None)
    assert req.effort is None
    assert _effort_tokens("claude", build_command(req)) == []


def test_a_role_on_codex_now_reaches_it_as_a_config_override() -> None:
    """Codex's client default takes effort as a quoted TOML config override."""
    req = _resolved("adversarial-audit", client="codex")
    assert req.effort == "low"
    assert "--effort" not in build_command(req)
    assert _effort_tokens("codex", build_command(req)) == ["low"]


def test_codex_effort_is_a_quoted_toml_override_pair() -> None:
    argv = build_command(_request("codex", effort="high"))
    assert ["-c", 'model_reasoning_effort="high"'] == argv[argv.index("-c") : argv.index("-c") + 2]


@pytest.mark.parametrize(("asked", "sent"), [("max", "max"), ("xhigh", "xhigh"), ("low", "low")])
def test_codex_clamps_to_its_accepted_levels(asked: str, sent: str) -> None:
    assert _effort_tokens("codex", build_command(_request("codex", effort=asked))) == [sent]


def test_codex_policy_record_applies_high() -> None:
    from trw_mcp.dispatch._policy import policy_record

    record = policy_record(_request("codex", effort="high"))
    assert record["effort"]["applied"] == "high"
    assert record["model"]["applied"] is None  # no --model given: no model is claimed


def test_a_user_c_override_is_still_refused_for_codex() -> None:
    with pytest.raises(ValueError, match="-c"):
        _request("codex", extra_args=["-c", 'model_reasoning_effort="low"'])


def test_effort_flag_and_config_key_are_exclusive_and_need_levels() -> None:
    base = CLIENT_SPECS["codex"].model_dump()  # type: ignore[union-attr]
    spec_type = type(CLIENT_SPECS["codex"])
    with pytest.raises(ValueError, match="must be set together"):
        spec_type(**{**base, "effort_flag": "--effort"})
    with pytest.raises(ValueError, match="must be set together"):
        spec_type(**{**base, "effort_levels": ()})


# --------------------------------------------------------------------------- #
# Registry invariant.
# --------------------------------------------------------------------------- #


def _spec(**kw: Any) -> ClientSpec:
    base = CLIENT_SPECS["claude"].model_dump()  # type: ignore[index]
    base.update(kw)
    return ClientSpec(**base)


def test_a_flag_without_a_vocabulary_is_rejected_at_import() -> None:
    with pytest.raises(ValueError, match="must be set together"):
        _spec(effort_flag="--effort", effort_levels=())


def test_a_vocabulary_without_a_flag_is_rejected_at_import() -> None:
    with pytest.raises(ValueError, match="must be set together"):
        _spec(effort_flag=None, effort_levels=("low",))


def test_an_unknown_level_is_rejected_at_import() -> None:
    with pytest.raises(ValueError, match="not in"):
        _spec(effort_flag="--effort", effort_levels=("low", "turbo"))

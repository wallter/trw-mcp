"""PRD-LOCAL-074-FR07: the eight client bypass flags added to the dispatch floor on 2026-09-04.

Kept in its own module so the token tests can land independently of the
`_commands.py` / `_client_specs` restructuring (PRD-CORE-266) that edits
`test_dispatch_commands.py` concurrently.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.dispatch._commands import build_command
from trw_mcp.dispatch._types import _FORBIDDEN_EXTRA_ARG_TOKENS, DispatchRequest


def _req(client: str, **kw: object) -> DispatchRequest:
    return DispatchRequest(client=client, prompt="audit this", **kw)  # type: ignore[arg-type]


# --- PRD-LOCAL-074-FR07: the 8 client bypass flags added 2026-09-04 -------------
#
# The parametrized sweeps above derive from ``_FORBIDDEN_EXTRA_ARG_TOKENS``, so a
# token DELETED from the set silently shrinks them and stays green. These tests
# name each new token literally: dropping one from the source makes a test fail by
# that token's own name. Provenance (which CLI/version's help output was read)
# lives beside the set in ``trw_mcp/dispatch/_types.py``.

_NEW_BYPASS_TOKENS: tuple[str, ...] = (
    "--dangerously-bypass-approvals-and-sandbox",
    "--dangerously-bypass-hook-trust",
    "--approve-for-me",
    "--ask-for-approval",
    "--full-auto",
    "--auto",
    "--allow-dangerously-skip-permissions",
    "--permission-prompts",
)


def _assert_rejected_on_both_surfaces(token: str) -> None:
    """Assert *token* is refused as an extra_args entry AND as the model value."""

    with pytest.raises(ValidationError) as extra_exc:
        _req("codex", extra_args=[token])
    assert "security flag" in str(extra_exc.value)
    with pytest.raises(ValidationError) as model_exc:
        _req("codex", model=token)
    assert "security flag" in str(model_exc.value)


def test_new_bypass_tokens_rejected_on_both_surfaces() -> None:
    # The FR07 aggregate: all 8 tokens are in the live set and all 16 rejections
    # fire. Removing a token from the source turns the membership assertion red
    # with the token in the message.
    assert len(_NEW_BYPASS_TOKENS) == 8
    missing = [token for token in _NEW_BYPASS_TOKENS if token not in _FORBIDDEN_EXTRA_ARG_TOKENS]
    assert not missing, f"tokens dropped from _FORBIDDEN_EXTRA_ARG_TOKENS: {missing}"
    for token in _NEW_BYPASS_TOKENS:
        _assert_rejected_on_both_surfaces(token)


_FR09_TOKENS: tuple[str, ...] = ("--allowed-tools", "--allowedTools")


def test_forbidden_extra_arg_token_set_is_exactly_twenty_two() -> None:
    # 12 original + the 8 above + the 2 FR09 tool-preauthorisation spellings
    # (PRD-LOCAL-074 success metric). trw-loop mirrors this set and its parity test
    # asserts the superset relation from source.
    assert len(_FORBIDDEN_EXTRA_ARG_TOKENS) == 22


def test_allowed_tools_both_spellings_rejected_on_both_surfaces() -> None:
    # FR09. ``--allowed-tools`` / ``--allowedTools`` pre-authorise tool use with no
    # human prompt, defeating the mechanism ``read_only`` relies on. Both spellings
    # are live in Claude Code 2.1.261, so blocking one is blocking neither.
    for token in _FR09_TOKENS:
        assert token in _FORBIDDEN_EXTRA_ARG_TOKENS
        _assert_rejected_on_both_surfaces(token)


def test_allowed_tools_rejected_in_flag_value_form() -> None:
    for token in _FR09_TOKENS:
        with pytest.raises(ValidationError):
            _req("claude", extra_args=[f"{token}=Bash"])


def test_new_bypass_token_dangerously_bypass_approvals_and_sandbox() -> None:
    _assert_rejected_on_both_surfaces("--dangerously-bypass-approvals-and-sandbox")


def test_new_bypass_token_dangerously_bypass_hook_trust() -> None:
    _assert_rejected_on_both_surfaces("--dangerously-bypass-hook-trust")


def test_new_bypass_token_approve_for_me() -> None:
    _assert_rejected_on_both_surfaces("--approve-for-me")


def test_new_bypass_token_ask_for_approval() -> None:
    _assert_rejected_on_both_surfaces("--ask-for-approval")


def test_new_bypass_token_full_auto() -> None:
    # LEGACY-UNVERIFIED spelling: absent from codex-cli 0.153.2's help output, so
    # this asserts only that the FLOOR rejects it — never that the CLI accepts it.
    _assert_rejected_on_both_surfaces("--full-auto")


def test_new_bypass_token_auto() -> None:
    _assert_rejected_on_both_surfaces("--auto")


def test_new_bypass_token_allow_dangerously_skip_permissions() -> None:
    _assert_rejected_on_both_surfaces("--allow-dangerously-skip-permissions")


def test_new_bypass_token_permission_prompts() -> None:
    _assert_rejected_on_both_surfaces("--permission-prompts")


def test_new_bypass_tokens_rejected_in_flag_value_form() -> None:
    # The '=' head split applies to the new tokens too.
    for token in _NEW_BYPASS_TOKENS:
        with pytest.raises(ValidationError):
            _req("codex", extra_args=[f"{token}=1"])


def test_benign_token_containing_a_new_token_as_a_substring_is_accepted() -> None:
    # The match is token-exact, not substring: a path that merely CONTAINS a
    # forbidden spelling stays a legal argv token.
    argv = build_command(_req("codex", extra_args=["/tmp/notes--auto-plan.txt"]))
    assert "/tmp/notes--auto-plan.txt" in argv

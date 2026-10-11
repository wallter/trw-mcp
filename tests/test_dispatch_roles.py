"""Behavior tests for audit-role application (PRD-CORE-297-FR01: one role table)."""

from __future__ import annotations

from trw_mcp.dispatch import ROLE_TABLE, apply_role


def test_known_role_wraps_prompt_with_preamble() -> None:
    out = apply_role("code-review", "Look at file X.")
    assert out.endswith("Look at file X.")
    assert out != "Look at file X."
    assert ROLE_TABLE["code-review"].preamble in out


def test_all_roles_enforce_read_only_and_severity() -> None:
    for role, spec in ROLE_TABLE.items():
        preamble = spec.preamble.lower()
        assert "read-only" in preamble, role
        assert "severity" in preamble, role
        assert "do not edit" in preamble, role


def test_none_role_passes_prompt_through_unchanged() -> None:
    assert apply_role(None, "raw prompt") == "raw prompt"


def test_empty_role_passes_prompt_through_unchanged() -> None:
    assert apply_role("", "raw prompt") == "raw prompt"


def test_unknown_role_passes_prompt_through_unchanged() -> None:
    assert apply_role("totally-made-up", "raw prompt") == "raw prompt"


def test_adversarial_role_distinct_from_code_review() -> None:
    assert ROLE_TABLE["adversarial-audit"].preamble != ROLE_TABLE["code-review"].preamble


def test_role_table_replaces_two_dicts() -> None:
    """One table owns preamble and task class; the parallel dicts are gone."""
    from trw_mcp.agents.task_policy import TASK_POLICY
    from trw_mcp.dispatch import _roles

    assert not hasattr(_roles, "ROLE_TEMPLATES")
    assert not hasattr(_roles, "ROLE_TASK_CLASS")
    assert {role: spec.task_class for role, spec in ROLE_TABLE.items()} == {
        "code-review": "review",
        "design-audit": "review",
        "architectural-audit": "review",
        "adversarial-audit": "security",
        "plan": "plan",
    }
    assert all(spec.task_class in TASK_POLICY for spec in ROLE_TABLE.values())


def test_unknown_role_passes_through() -> None:
    from trw_mcp.dispatch._roles import role_task_class

    assert role_task_class("code-review") == "review"
    assert role_task_class("totally-made-up") is None
    assert role_task_class(None) is None
    assert role_task_class("") is None


def test_cli_role_choices_derive_from_the_table() -> None:
    import argparse

    from trw_mcp.server._cli_argparse_dispatch import add_dispatch_subcommand

    subparsers = argparse.ArgumentParser().add_subparsers(dest="cmd")
    add_dispatch_subcommand(subparsers)
    role_action = next(a for a in subparsers.choices["dispatch"]._actions if a.dest == "role")
    assert list(role_action.choices or ()) == sorted(ROLE_TABLE)


def test_the_roles_that_review_a_change_ask_whether_its_premise_is_true() -> None:
    """A review told only what changed checks the implementation and accepts the reason for it. On 2026-10-10 a
    review that was asked found a false premise from the code; one that was not accepted a change withdrawn
    twenty minutes later. The question reaches the child as part of the prompt, ahead of the caller's text."""
    for role in ("code-review", "adversarial-audit"):
        prompt = apply_role(role, "review the diff of commit abc")
        question = prompt.index("First check the premise: is the problem this change says it fixes real")
        assert question < prompt.index("review the diff of commit abc")
        assert "or say that you could not find one" in prompt

    # A design or architecture audit and a plan review no change's claim, so they are not asked.
    for role in ("design-audit", "architectural-audit", "plan"):
        assert "check the premise" not in apply_role(role, "x")

    # The adversarial audit's parsed contract still ends the preamble: the question sits before it.
    audit = apply_role("adversarial-audit", "x")
    assert audit.index("check the premise") < audit.index("Blocking rule:")

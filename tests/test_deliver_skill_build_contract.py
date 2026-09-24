"""The shipped delivery skills must treat `trw_build_check` as a REPORTER.

Replaces `test_eval_deliver_skill_build_contract.py`, deleted in `69abca6cfc`.
That module guarded this same property, but only on a vendored trw-mcp mirror
kept inside the (proprietary) eval package — a tree deleted wholesale by
`a77650f238`. Because its paths
sat behind `skipif(not all(p.exists()))`, it had already degraded to a permanent
skip asserting nothing, and its assertion strings appear nowhere in the canonical
skills, so it could not simply be repointed.

The invariant itself is framework-level, not eval-local:
`.trw/frameworks/FRAMEWORK.md` states `trw_build_check` "records observed
project-native validation at VALIDATE and before DELIVER after code/test
changes; **it does not run checks**". A delivery skill that tells an agent the
tool runs validation produces exactly the failure the deliver gate exists to
prevent — a passing build record bound to no executed command.

So the guard is restored here, against the surfaces that actually ship.
"""

from __future__ import annotations

import re

import pytest

from tests._layout import PACKAGE_ROOT as _ROOT

#: Every projection of the delivery skill. Codex, copilot and opencode no
#: longer carry their own fork on disk (PRD-CORE-291-FR04) -- they render the
#: one canonical body, so the parametrized surfaces are the canonical text
#: plus each client's rendering of it.
_CANONICAL_TEXT = (_ROOT / "src/trw_mcp/data/skills/trw-deliver/SKILL.md").read_text(encoding="utf-8")


def _rendered(client: str | None) -> str:
    if client is None:
        return _CANONICAL_TEXT
    from trw_mcp.bootstrap._client_skills import render_skill_md

    return render_skill_md(_CANONICAL_TEXT, client)


DELIVER_SURFACES = (None, "codex", "copilot", "opencode")

#: Phrasings that would tell an agent the tool executes validation itself.
#: Matched case-insensitively against the whole document.
_EXECUTOR_CLAIMS = (
    r"trw_build_check[^.\n]{0,40}\bruns?\b[^.\n]{0,30}\b(test|check|validation|pytest|suite)",
    r"trw_build_check[^.\n]{0,40}\bexecut",
    r"(call|use)\s+`?trw_build_check`?[^.\n]{0,30}\bto run\b",
)


@pytest.mark.unit
@pytest.mark.parametrize("client", DELIVER_SURFACES, ids=lambda c: c or "canonical")
def test_deliver_skill_does_not_claim_build_check_runs_validation(client: str | None) -> None:
    """No projection may describe `trw_build_check` as executing the checks."""
    content = _rendered(client)
    for pattern in _EXECUTOR_CLAIMS:
        match = re.search(pattern, content, re.IGNORECASE)
        assert match is None, (
            f"{client or 'canonical'} implies trw_build_check executes validation "
            f"(matched {match.group(0)!r}); the tool only RECORDS a result"
        )


@pytest.mark.unit
@pytest.mark.parametrize("client", DELIVER_SURFACES, ids=lambda c: c or "canonical")
def test_deliver_skill_runs_validation_before_recording_it(client: str | None) -> None:
    """Running the validation must be its own step, ordered before the record.

    Asserts ORDER, not layout. The projections legitimately differ in shape:
    the canonical skill splits "Run the narrowest meaningful validation" and
    "Call `trw_build_check(...)`" into two numbered steps, while the opencode
    variant combines them into one ("Run project-native validation, then record
    its observed result with `trw_build_check`"). Both are correct; a test that
    demanded two separate steps would have failed the opencode skill for its
    formatting, which is how a guard starts getting weakened to pass.

    Frontmatter is stripped first: `allowed-tools` names `trw_build_check`
    above every step, so searching the raw document would find the tool before
    any instruction and report a false inversion.
    """
    body = re.sub(r"\A---\n.*?\n---\n", "", _rendered(client), flags=re.DOTALL)
    run_step = re.search(r"\bRun\b[^.\n]{0,60}\bvalidation\b", body, re.IGNORECASE)
    record_step = re.search(r"trw_build_check", body)

    label = client or "canonical"
    assert run_step is not None, f"{label} never instructs the agent to RUN validation"
    assert record_step is not None, f"{label} never instructs the agent to record via trw_build_check"
    assert run_step.start() < record_step.start(), (
        f"{label} records the build result before running validation; "
        "build evidence must postdate the check it claims to cover"
    )

"""Guard against the schema/agent-contract collision fixed in commit 9700e9b709.

Incident (2026-07-27): ``.claude/workflows/refine-canon-doc.js`` spawned
``agentType: 'trw-researcher'`` with a JSON schema declaring
``additionalProperties: false`` and no ``open_questions`` property. The
bundled ``trw-researcher`` body mandates recording under ``open_questions``
rather than silently substituting its assigned research axis (added same-day
in commit 971a87fe91, ~9h before the collision was diagnosed). The two
contracts fought: the agent tried to emit a field the schema forbade, burned
five StructuredOutput retries with "must NOT have additional properties", and
some lenses exhausted retries and returned nothing — a silent quality loss
indistinguishable, from the caller's side, from "this lens found little".

Nothing previously checked that a workflow's schema is a superset of the
paired agent's declared output contract — "an unchecked assertion" per the
fix commit. This test makes it checked: a bundled agent declares a mandatory
output field via an inline marker,

    <!-- trw:mandatory-output-field: field_name -->

in its ``.md`` body (single source of truth — no separate list to drift), and
this test asserts every ``.claude/workflows/*.{js,mjs}`` pairing of that
agentType with an ``additionalProperties: false`` schema includes the field.

Scope: only fields marked this way are enforced. A caller schema that reshapes
the agent's default output into an unrelated, self-consistent structure (a
legitimate, narrower one-off task) is not flagged merely for differing from
the agent's default schema — only for omitting a field the agent's body says
it will emit NO MATTER WHAT it is asked. See
``docs/documentation/operational-knowledge/`` history around 2026-07-27 for
the audit that scoped this deliberately (broader schema-shape mismatches on
trw-auditor/trw-adversarial-auditor/trw-reviewer were found and reported but
judged lower-confidence and out of scope for automatic enforcement).
"""

from __future__ import annotations

import re
from pathlib import Path

from tests._layout import requires_monorepo

pytestmark = requires_monorepo

import pytest

from tests._workflow_schema_scan import (
    extract_named_schemas,
    find_agent_type_schema_pairings,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"
WORKFLOWS_DIR = REPO_ROOT / ".claude" / "workflows"

_MANDATORY_FIELD_MARKER_RE = re.compile(r"<!--\s*trw:mandatory-output-field:\s*(\w+)\s*-->")


def _mandatory_fields_by_agent() -> dict[str, frozenset[str]]:
    """Parse every bundled agent's mandatory-output-field markers.

    The markers are the single source of truth (living in the agent body,
    where a human editing the contract will see them) — nothing here
    hardcodes a per-agent field list to keep in sync by hand.
    """
    out: dict[str, set[str]] = {}
    for path in sorted(AGENTS_DIR.glob("*.md")):
        fields = set(_MANDATORY_FIELD_MARKER_RE.findall(path.read_text(encoding="utf-8")))
        if fields:
            out[path.stem] = fields
    return {k: frozenset(v) for k, v in out.items()}


def _workflow_files() -> list[Path]:
    files = list(WORKFLOWS_DIR.glob("*.js")) + list(WORKFLOWS_DIR.glob("*.mjs"))
    return sorted(p for p in files if ".test." not in p.name)


def test_mandatory_field_markers_present() -> None:
    """Non-vacuity guard: the marker convention itself must be in use.

    If this starts failing, either the marker was removed from
    trw-researcher.md (regression — see module docstring) or the convention
    was renamed without updating this test.
    """
    mandatory = _mandatory_fields_by_agent()
    assert "trw-researcher" in mandatory, (
        "trw-researcher.md lost its <!-- trw:mandatory-output-field: --> marker "
        "(should declare open_questions per commit 971a87fe91)"
    )
    assert "open_questions" in mandatory["trw-researcher"]


def test_workflow_schemas_honor_agent_mandatory_fields() -> None:
    """The actual regression guard: every strict-schema pairing includes the mandatory fields.

    Scans every ``.claude/workflows/*.{js,mjs}`` file (including
    ``refine-canon-doc.js`` — read-only regression coverage that the original
    fix holds) for ``agent(prompt, { agentType, schema })`` pairings, resolves
    ``schema`` back to its ``const X = {...}`` declaration in the same file,
    and — only where that schema sets ``additionalProperties: false`` and the
    agentType has a registered mandatory field — asserts the field is present
    among the schema's top-level ``properties``.
    """
    mandatory = _mandatory_fields_by_agent()
    violations: list[str] = []
    checked_pairings = 0

    for wf_path in _workflow_files():
        source = wf_path.read_text(encoding="utf-8")
        schemas = extract_named_schemas(source)
        pairings = find_agent_type_schema_pairings(source, wf_path)
        for pairing in pairings:
            required = mandatory.get(pairing.agent_type)
            if not required:
                continue  # agentType has no registered mandatory fields to check
            schema = schemas.get(pairing.schema_var)
            if schema is None or not schema.additional_properties_false:
                continue  # no strict schema resolved, or extra fields are allowed
            checked_pairings += 1
            missing = required - schema.top_level_properties
            if missing:
                rel = wf_path.relative_to(REPO_ROOT)
                violations.append(
                    f"{rel}:{pairing.line} — agentType={pairing.agent_type!r} "
                    f"schema={pairing.schema_var} (additionalProperties: false) "
                    f"is missing mandatory field(s) {sorted(missing)}"
                )

    # Non-vacuity: this suite currently has real trw-researcher + strict-schema
    # pairings (refine-canon-doc.js's fixed RESEARCH_SCHEMA at minimum). If the
    # scanner stops finding any, it is silently checking nothing.
    assert checked_pairings > 0, (
        "scanner found zero additionalProperties:false schema pairings for any "
        "agentType with a registered mandatory field — scanner regression, not "
        "an all-clear repo"
    )
    assert not violations, (
        "workflow schema(s) fight their paired agent's mandatory output contract "
        "(will burn StructuredOutput retries the way commit 9700e9b709 diagnosed):\n" + "\n".join(violations)
    )


@pytest.mark.parametrize(
    "rel_path",
    [
        "refine-canon-doc.js",
        "feedback-triage.mjs",
        # The provider refresh's trw-researcher pairing moved here on 2026-09-10
        # when provider-docs-refresh.mjs was generalized to a two-axis engine and
        # reduced to a thin wrapper. This test caught the move, which is its job.
        "research-refresh.mjs",
    ],
)
def test_scanner_finds_known_researcher_pairings(rel_path: str) -> None:
    """Scanner sanity check pinned to known-real pairings (catches silent scan regressions).

    If a future refactor of these workflows renames the schema constants or
    changes the call shape enough that the scanner stops seeing them, this
    fails loudly instead of the main test above silently checking fewer
    pairings than it used to.
    """
    path = WORKFLOWS_DIR / rel_path
    source = path.read_text(encoding="utf-8")
    pairings = find_agent_type_schema_pairings(source, path)
    researcher_pairings = [p for p in pairings if p.agent_type == "trw-researcher"]
    assert researcher_pairings, f"{rel_path}: scanner found no trw-researcher pairings (expected >=1)"

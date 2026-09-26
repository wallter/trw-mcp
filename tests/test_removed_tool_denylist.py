"""No rendered surface names a removed tool (PRD-CORE-300-FR01, NFR01).

PRD-CORE-300 deletes 37 MCP tools across slices S1 to S11b with no aliases.
A name left in a skill, hook, agent, instruction file or runtime string sends
an agent to a tool that no longer exists. Each slice adds the names it removes
to :data:`REMOVED_TOOLS` in the same merge; from then on this test fails on
any hit in scope, naming the file and line.

The scope is data here, not prose in the PRD: :data:`INCLUDE_GLOBS` minus
:data:`EXCLUDE_GLOBS`, over git-tracked files. History (PRDs, sprint notes,
CHANGELOGs, research) keeps naming old tools and is excluded. Inside scope a
hit is allowed only in :data:`ALLOWED_PATHS` (negative tests and recorded
replay fixtures, each added by the slice that needs it) or on the one line
shape the CLI replacement registry uses to say what a command replaces.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest

from tests._layout import MONOREPO_ROOT, requires_monorepo

pytestmark = pytest.mark.unit

#: Names removed so far. Each slice appends its names in the merge that removes them.
REMOVED_TOOLS: tuple[str, ...] = (
    "trw_probe",
    "trw_probe_budget_status",
    "trw_meta_tune_propose",
    "trw_meta_tune_rollback",
    # S4: moved to `trw-mcp code index` / `trw-mcp code risk`, or deleted outright.
    "trw_code_index_update",
    "trw_codebase_risk_report",
    "trw_ordering_compare",
    "trw_cross_repo_ordering",
    # S7: modes of trw_dispatch (action="status" / "evidence" / "validate_evidence").
    "trw_dispatch_status",
    "trw_agent_work_evidence",
    "trw_validate_agent_work_evidence",
    # S6c: deleted.
    "trw_claude_md_sync",
    "trw_replay_outcomes",
    # S1: delivery status is trw_status(delivery=...); recovery is `trw-mcp delivery recover`.
    "trw_delivery_status",
    "trw_delivery_recover",
    # S3a: moved to `trw-mcp telemetry events|classify|surface-diff|security|channel-stats`.
    "trw_query_events",
    "trw_surface_classify",
    "trw_surface_diff",
    "trw_mcp_security_status",
    "trw_channel_stats",
    # S5: moved to trw-mcp prd create / prd diff.
    "trw_prd_create",
    "trw_prd_diff",
    # S11b: the grant path and the discoverable tier are gone; the profile
    # explanation is trw_status(detail="surface") and `trw-mcp profile explain`.
    "trw_request_tool_access",
    "trw_skill_discovery",
    "trw_profile_explain",
    # S9: graph mode is trw_recall(graph_id=...); feedback mode is trw_status(feedback=...).
    "trw_graph_related",
    "trw_submit_feedback",
    # S3b: moved to `trw-mcp telemetry pipeline-health`.
    "trw_pipeline_health",
    # S8: folded into trw_inbox's `action` parameter (enroll, list, heartbeat,
    # announce, withdraw, discover, ack_pause).
    "trw_peers",
    # S6a: modes of trw_checkpoint (heartbeat=True / pre_compact=True).
    "trw_heartbeat",
    "trw_pre_compact_checkpoint",
    # S6b: moved to `trw-mcp run adopt` / `trw-mcp instructions sync`.
    "trw_adopt_run",
    "trw_instructions_sync",
    # S10: modes of trw_code (mode="search" / "symbol" / "hint", files=[...]).
    "trw_code_search",
    "trw_code_symbol",
    "trw_before_edit_hint",
    "trw_before_edit_hint_batch",
)

INCLUDE_GLOBS: tuple[str, ...] = (
    "trw-mcp/src/**",
    "trw-mcp/tests/**",
    "trw-memory/src/**",
    ".claude/**",
    ".agents/**",
    ".codex/**",
    ".cursor/**",
    ".github/**",
    ".grok/**",
    ".opencode/**",
    "AGENTS.md",
    "CLAUDE.md",
    "ANTIGRAVITY.md",
    "FRAMEWORK.md",
    "**/FRAMEWORK.md",
    "**/README.md",
    "platform/src/**",  # trw-leak-allow: proprietary_path the denylist scopes monorepo paths; public repo skips it
    "scripts/**",
    "docs/**",
)

EXCLUDE_GLOBS: tuple[str, ...] = (
    "docs/requirements-aare-f/prds/**",
    "docs/requirements-aare-f/sprints/**",
    "docs/requirements-aare-f/exec-plans/**",
    "docs/requirements-aare-f/archive/**",
    "docs/archive/**",
    "docs/sprint-*/**",
    # W49: the selection eval pins each prompt's pre-cut (s0) answer, so it names
    # removed tools by design; its post_cut answers are checked against the live surface.
    "scripts/selection_eval.py",
    "scripts/tests/test_selection_eval.py",
    "docs/research/**",  # trw-leak-allow: internal_docs the denylist scopes monorepo paths; public repo skips it
    # Dated audit reports and the PRD catalogue: INDEX/ROADMAP rows are PRD titles,
    # regenerated from the (excluded) PRDs, so they name old tools for good.
    "docs/framework-probe-audit-*/**",
    "docs/requirements-aare-f/INDEX.md",
    "docs/requirements-aare-f/ROADMAP.md",
    # S3a+S5: dated audits, a dated draft PRD and the dated improvement log.
    "docs/brand/*-20??-??-??.md",
    "docs/reviews/*-20??-??-??.md",
    "docs/eval/PRD-DRAFT-*.md",  # trw-leak-allow: internal_docs the denylist scopes monorepo paths; public repo skips it
    "docs/documentation/improvement-backlog.md",
    # More history, first hit by S6c's long-deprecated alias: older-layout PRDs, phase
    # plans and test skeletons, the PRD registry built from them, the learnings
    # archive, iteration notes, dated reasoning/prompting write-ups, PRD status pages.
    "docs/requirements-aare-f/PRD-*.md",
    "docs/requirements-aare-f/phases/**",
    "docs/requirements-aare-f/test-skeletons/**",
    "docs/.trw/registry/**",
    "docs/TRW_LEARNINGS_ARCHIVE.md",
    "docs/eval/iter-notes/**",  # trw-leak-allow: internal_docs the denylist scopes monorepo paths; public repo skips it
    "docs/research-*/**",
    "docs/brand/reasoning-*.md",
    "docs/documentation/prompting/*-20??-??-??.md",
    "docs/documentation/prd-*.md",
    # S6a: dated append-only evidence/reflection ledgers, not live guidance —
    # same class as TRW_LEARNINGS_ARCHIVE.md / iter-notes above. Rewriting a
    # 2026-07-27 comprehension-quiz snapshot or a 2026-06-17 reflection entry
    # to use a name that did not exist yet would falsify the record.
    "docs/evidence/**",
    "docs/documentation/improvement-backlog.md",
    "**/CHANGELOG.md",
    "platform/src/app/(marketing)/docs/changelog/**",  # trw-leak-allow: proprietary_path the denylist scopes monorepo paths; public repo skips it
    "platform/src/app/(marketing)/docs/upgrade/**",  # trw-leak-allow: proprietary_path the denylist scopes monorepo paths; public repo skips it
)

#: Research files that are live guidance and so stay in scope despite ``docs/research/**``. # trw-leak-allow: internal_docs the denylist scopes monorepo paths; public repo skips it
RESEARCH_LIVE: tuple[str, ...] = ()

#: In-scope files where a removed name is expected: this module and, as slices
#: add them, negative tests asserting a name is unregistered and replay fixtures.
ALLOWED_PATHS: tuple[str, ...] = (
    "trw-mcp/tests/test_removed_tool_denylist.py",
    "trw-mcp/tests/fixtures/golden_prds/**",
    # S6c (PRD-CORE-300): negative tests asserting trw_claude_md_sync /
    # trw_replay_outcomes are unregistered.
    "trw-mcp/tests/test_client_profile_docs_parity.py",
    "trw-mcp/tests/test_bootstrap_opencode_split.py",
    "trw-mcp/tests/test_tools_learning_core.py",
    "trw-mcp/tests/test_tools_learning_wiring.py",
    # S5 (PRD-CORE-300): negative test asserting the trw-prd-new alias skill
    # never embeds a literal trw_prd_create(...) call.
    "trw-mcp/tests/test_prd_score_contract_surfaces.py",
    # S5: describes trw-eval prompt variants whose templates still name
    # trw_prd_create (trw-eval follow-up, outside PRD-CORE-300's trw-mcp scope).
    "docs/documentation/eval/architecture.md",
    # S8: replays the removed tool's pre-cut source (loaded from git history)
    # as the golden oracle for trw_inbox's folded-in peer actions.
    "trw-mcp/tests/comms/test_inbox_peer_actions.py",
    # S11b (PRD-CORE-300): the reviewer child must not surface the grant tool.
    "trw-mcp/tests/test_reviewer_posture_stdio_child.py",
    # S6a: parity/negative tests for the trw_checkpoint modes that replaced
    # trw_heartbeat and trw_pre_compact_checkpoint.
    "trw-mcp/tests/test_checkpoint_modes.py",
    # S6b (PRD-CORE-300): a recorded replay fixture predates the cut and names
    # the tools that were live when captured.
    "trw-mcp/tests/hooks/fixtures/session-events-2026-07-24.jsonl",
)

#: The CLI replacement registry (PRD-CORE-300-FR02) records the tool a command
#: replaces on a ``replaces=`` line; that line, in that module only, may name it.
_REGISTRY_PATH = "trw-mcp/src/trw_mcp/server/_cli_replacements.py"
_REGISTRY_LINE = re.compile(r"\breplaces\s*=")

_PLANTED = "trw_planted_removed_tool"


def _glob_regex(glob: str) -> re.Pattern[str]:
    """``**`` spans directories, ``*`` and ``?`` stay inside one path segment."""
    out = []
    index = 0
    while index < len(glob):
        if glob.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif glob.startswith("**", index):
            out.append(".*")
            index += 2
        elif glob[index] == "*":
            out.append("[^/]*")
            index += 1
        elif glob[index] == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(glob[index]))
            index += 1
    return re.compile("".join(out) + r"\Z")


_INCLUDE = tuple(_glob_regex(glob) for glob in INCLUDE_GLOBS)
_EXCLUDE = tuple(_glob_regex(glob) for glob in EXCLUDE_GLOBS)
_ALLOWED = tuple(_glob_regex(glob) for glob in ALLOWED_PATHS)


def in_scope(path: str) -> bool:
    """True when ``path`` (repo-relative, POSIX) is a rendered surface the denylist covers."""
    if path in RESEARCH_LIVE:
        return True
    return any(p.match(path) for p in _INCLUDE) and not any(p.match(path) for p in _EXCLUDE)


def scan(root: Path, paths: Iterable[str], names: Iterable[str]) -> list[str]:
    """``path:line: name`` for every in-scope hit outside an allowed context."""
    wanted = tuple(names)
    if not wanted:
        return []
    pattern = re.compile(r"\b(" + "|".join(re.escape(name) for name in wanted) + r")\b")
    hits: list[str] = []
    for path in paths:
        if not in_scope(path) or any(p.match(path) for p in _ALLOWED):
            continue
        # A missing tracked file raises: a scan that skipped it would pass without looking.
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            found = pattern.search(line)
            if found and not (path == _REGISTRY_PATH and _REGISTRY_LINE.search(line)):
                hits.append(f"{path}:{number}: {found.group(1)}")
    return hits


def _candidate_files(root: Path, names: tuple[str, ...]) -> list[str]:
    """Tracked files containing any name, found by ``git grep`` so the scan reads only those."""
    if not names:
        return []
    argv = ["git", "grep", "-l", "-I", "-F", *(arg for name in names for arg in ("-e", name))]
    result = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"git grep failed: {result.stderr}")
    return result.stdout.splitlines()


def _tracked(root: Path) -> list[str]:
    result = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout.splitlines()


@requires_monorepo
def test_no_rendered_surface_names_a_removed_tool() -> None:
    assert MONOREPO_ROOT is not None
    hits = scan(MONOREPO_ROOT, _candidate_files(MONOREPO_ROOT, REMOVED_TOOLS), REMOVED_TOOLS)
    assert hits == [], "a rendered surface names a removed tool:\n" + "\n".join(hits)


@requires_monorepo
@pytest.mark.parametrize("glob", INCLUDE_GLOBS)
def test_a_planted_hit_fails_in_every_include_glob(glob: str, tmp_path: Path) -> None:
    """Non-vacuity: for each include glob, one real in-scope file with a planted name is caught."""
    assert MONOREPO_ROOT is not None
    pattern = _glob_regex(glob)
    allowed = tuple(p for p in _ALLOWED)
    sample = next(
        (
            path
            for path in _tracked(MONOREPO_ROOT)
            if pattern.match(path) and in_scope(path) and not any(p.match(path) for p in allowed)
        ),
        None,
    )
    if sample is None:
        pytest.skip(f"no tracked in-scope file matches {glob}")
    copy = tmp_path / sample
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(MONOREPO_ROOT / sample, copy)
    with copy.open("a", encoding="utf-8") as handle:
        handle.write(f"\ncall {_PLANTED} here\n")

    assert scan(tmp_path, [sample], [_PLANTED]) == [f"{sample}:{len(copy.read_text().splitlines())}: {_PLANTED}"]


def test_allowed_contexts_and_history_are_not_hits(tmp_path: Path) -> None:
    files = {
        "trw-mcp/tests/test_removed_tool_denylist.py": f"REMOVED = ('{_PLANTED}',)\n",
        "trw-mcp/src/trw_mcp/server/_cli_replacements.py": f'CliReplacement("x y", replaces="{_PLANTED}")\n',
        "docs/requirements-aare-f/prds/PRD-X.md": f"{_PLANTED} was removed\n",
        "docs/sprint-mcp7/PLAN.md": f"{_PLANTED}\n",
        "docs/research/topic/notes.md": f"{_PLANTED}\n",  # trw-leak-allow: internal_docs the denylist scopes monorepo paths; public repo skips it
        "trw-mcp/CHANGELOG.md": f"- removed {_PLANTED}\n",
        "trw-mcp/src/trw_mcp/data/skills/x/SKILL.md": f"a longer_{_PLANTED}_name is a different word\n",
    }
    for path, body in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(body, encoding="utf-8")

    assert scan(tmp_path, list(files), [_PLANTED]) == []


def test_a_replaces_line_outside_the_registry_is_still_a_hit(tmp_path: Path) -> None:
    path = "trw-mcp/src/trw_mcp/tools/other.py"
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text(f'x = dict(replaces="{_PLANTED}")\n', encoding="utf-8")

    assert scan(tmp_path, [path], [_PLANTED]) == [f"{path}:1: {_PLANTED}"]


def test_the_scope_globs_cover_the_panel_surfaces() -> None:
    """TOOL-CUT-REVIEW item 10's non-obvious surfaces are in scope; history is not."""
    for path in (
        ".github/skills/trw-feedback/SKILL.md",
        "trw-mcp/src/trw_mcp/data/hooks/cursor/lib-distill-hint.sh",
        "trw-mcp/src/trw_mcp/data/claude_code/hooks/pre-tool-distill-hint.sh",
        "trw-mcp/src/trw_mcp/dispatch/_types.py",
        "platform/src/app/docs/page.tsx",  # trw-leak-allow: proprietary_path the denylist scopes monorepo paths; public repo skips it
        "docs/CLIENT-PROFILES.md",
        "AGENTS.md",
        ".trw/frameworks/FRAMEWORK.md",
    ):
        assert in_scope(path), path
    for path in (
        "docs/requirements-aare-f/prds/PRD-CORE-300.md",
        "docs/sprint-mcp7/TOOL-CUT-REVIEW.md",
        "trw-memory/CHANGELOG.md",
        "platform/src/app/(marketing)/docs/changelog/changelog-page/data.tsx",  # trw-leak-allow: proprietary_path the denylist scopes monorepo paths; public repo skips it
        "docs/research/x/y.md",  # trw-leak-allow: internal_docs the denylist scopes monorepo paths; public repo skips it
        "trw-eval/src/trw_eval/x.py",  # trw-leak-allow: proprietary_path the denylist scopes monorepo paths; public repo skips it
    ):
        assert not in_scope(path), path


@pytest.mark.parametrize("name", REMOVED_TOOLS)
def test_a_removed_name_is_not_registered(name: str) -> None:
    """NFR01: no registration, alias or redirect exists for a removed name."""
    import asyncio

    from trw_mcp.server import mcp

    registered = {tool.name for tool in asyncio.run(mcp._list_tools())}
    assert name not in registered

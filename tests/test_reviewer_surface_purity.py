"""PRD-SEC-015 round-2 audit, Row 5: behavior-level purity over REAL handlers.

The pre-existing ``test_reviewer_surface_enforcement.py`` exhaustive-denial
sweep proves NAME exclusion: every non-member of ``REVIEWER_TOOLS`` is denied
by the middleware. It never invokes a real tool body, so it could not have
caught (and did not catch) Row 2's ``update_chunk_index`` write or Row 3's
ceremony/propensity/telemetry writes inside ``trw_recall`` -- both ran the
FAKE ``_execute`` sentinel instead of the registered handler.

This file runs every REAL registered handler for every member of
``REVIEWER_TOOLS`` under the reviewer role in a temp project and asserts the
``.trw`` tree is byte-identical before and after, except the ONE acknowledged
residual-write file named in :data:`_ACKNOWLEDGED_RESIDUAL_WRITES` (NFR03).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import get_tools_sync, make_test_server
from trw_mcp.models.config import reload_config
from trw_mcp.models.surface_packs import REVIEWER_TOOLS

pytestmark = pytest.mark.integration

#: PRD-SEC-015 NFR03, corrected by this audit's Row 6: exactly ONE file, not
#: three tool-scoped ones -- ``emit_tool_call``/``emit_hint_delivered`` both
#: append to the SAME sink (``channels/_telemetry.py::append_channel_event``'s
#: default ``log_path``). Written by ``trw_before_edit_hint`` and
#: ``trw_codebase_risk_report`` only; ``trw_before_edit_hint_batch`` has no
#: emission seam at all (Row 6) so it must NOT need this allowlist to pass.
_ACKNOWLEDGED_RESIDUAL_WRITES: frozenset[str] = frozenset({".trw/telemetry/channel-events.jsonl"})
#: ``_snapshot`` keys are relative to ``.trw`` itself; strip the shared prefix
#: once here rather than re-deriving it at every comparison site.
_ACKNOWLEDGED_RESIDUAL_WRITES_RELATIVE_TO_TRW: frozenset[str] = frozenset(
    path.removeprefix(".trw/") for path in _ACKNOWLEDGED_RESIDUAL_WRITES
)

_TOOL_GROUPS_FOR_REVIEWER_SURFACE = (
    "learning",
    "code_search",
    "before_edit_hint",
    "before_edit_hint_batch",
    "knowledge",
    "skill_discovery",
    "profile_explain",
    "codebase_risk_report",
)


def _snapshot(trw_dir: Path) -> dict[str, str]:
    """Return ``{relative_path: sha256}`` for every file under *trw_dir*."""
    if not trw_dir.exists():
        return {}
    out: dict[str, str] = {}
    for path in trw_dir.rglob("*"):
        if path.is_file():
            out[str(path.relative_to(trw_dir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _call(fn: Any, **kwargs: Any) -> None:
    """Invoke a real tool handler, tolerating any return type or exception.

    The purity property under test is "did the filesystem change", not
    "did the tool succeed" -- a reviewer-role tool must never write regardless
    of whether its own business logic finds anything to report.
    """
    try:
        fn(**kwargs)
    except Exception:  # justified: purity test cares about fs state, not tool success
        pass


def _invoke_every_reviewer_tool(tools: dict[str, Any], repo_root: str) -> None:
    _call(tools["trw_recall"].fn, query="*", ctx=None)
    _call(tools["trw_code_search"].fn, repo_root=repo_root, query="def ")
    _call(tools["trw_code_symbol"].fn, repo_root=repo_root, symbol="anything")
    _call(tools["trw_before_edit_hint"].fn, file_path="README.md", repo_root=repo_root, ctx=None)
    _call(tools["trw_before_edit_hint_batch"].fn, repo_root=repo_root)
    _call(tools["trw_graph_related"].fn, learning_id="nonexistent-id")
    _call(tools["trw_skill_discovery"].fn, skill_paths=[], query="anything")
    _call(tools["trw_profile_explain"].fn, ctx=None)
    _call(tools["trw_codebase_risk_report"].fn, repo_root=repo_root, ctx=None)


def test_every_reviewer_tools_real_handler_leaves_the_trw_tree_untouched(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exhaustive Row 5 purity sweep over REAL registered handlers.

    Two phases against the SAME server/tool set:
      1. Warm-up as an ordinary agent (default role) so the SQLite memory
         backend materializes on first touch -- a first-open cost every role
         pays, not a reviewer-specific write, so it must not appear as a
         "regression" in the reviewer-phase diff.
      2. The reviewer sweep: every member of REVIEWER_TOOLS via its real
         handler, then a byte-for-byte tree comparison.
    """
    repo_root = str(tmp_project)
    monkeypatch.setenv("TRW_PROJECT_ROOT", repo_root)
    monkeypatch.setenv("TRW_REPO_ROOT", repo_root)
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    reload_config(None)

    server = make_test_server(*_TOOL_GROUPS_FOR_REVIEWER_SURFACE)
    tools = get_tools_sync(server)

    assert REVIEWER_TOOLS <= set(tools), sorted(REVIEWER_TOOLS - set(tools))

    # Phase 1: agent-role warm-up (materializes memory.db etc.) -- not part
    # of the property under test.
    _invoke_every_reviewer_tool(tools, repo_root)

    trw_dir = tmp_project / ".trw"
    before = _snapshot(trw_dir)
    assert not (trw_dir / "code-index").exists(), "warm-up itself must not have created a code-index directory"

    # Phase 2: the reviewer role, exhaustively, via REAL handlers.
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    reload_config(None)
    from trw_mcp.state import _surface_role

    _surface_role.reset_surface_role_state()

    _invoke_every_reviewer_tool(tools, repo_root)

    after = _snapshot(trw_dir)

    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    unexpected = changed - _ACKNOWLEDGED_RESIDUAL_WRITES_RELATIVE_TO_TRW
    assert not unexpected, f"reviewer-role handlers mutated unacknowledged paths: {sorted(unexpected)}"
    # Non-vacuity: the allowlisted write must actually have been exercised, or
    # this sweep would pass just as well with `changed == set()` -- proving
    # nothing about the acknowledged path at all.
    assert changed, "no .trw file changed at all -- this sweep exercised nothing"

    # Row 2 pinpoint: the reviewer role must never create the code-index
    # directory at all (no manifest existed, so update_chunk_index would have
    # raised FileNotFoundError before ever writing -- this additionally pins
    # that load_chunk_index's read-only path took no directory-creating branch).
    assert not (trw_dir / "code-index").exists()


def test_the_acknowledged_allowlist_names_exactly_what_it_says(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-vacuity: confirm the allowlisted file is the REAL append target for
    both retained-telemetry tools, so the allowlist in the sweep above is not
    silently permitting a DIFFERENT, unaudited path."""
    repo_root = str(tmp_project)
    monkeypatch.setenv("TRW_PROJECT_ROOT", repo_root)
    monkeypatch.setenv("TRW_REPO_ROOT", repo_root)
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    reload_config(None)
    from trw_mcp.state import _surface_role

    _surface_role.reset_surface_role_state()

    server = make_test_server("before_edit_hint", "codebase_risk_report")
    tools = get_tools_sync(server)

    trw_dir = tmp_project / ".trw"
    assert not (trw_dir / "telemetry" / "channel-events.jsonl").exists()

    _call(tools["trw_before_edit_hint"].fn, file_path="README.md", repo_root=repo_root, ctx=None)
    _call(tools["trw_codebase_risk_report"].fn, repo_root=repo_root, ctx=None)

    for acknowledged in _ACKNOWLEDGED_RESIDUAL_WRITES:
        assert (tmp_project / acknowledged).exists(), acknowledged


def test_before_edit_hint_batch_has_no_emission_seam_row6(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Row 6, pinned directly: unlike its two siblings, the batch tool's own
    module imports neither telemetry emitter -- there is no seam to suppress
    or verify, so the PRD's prior "three retained writers" claim was wrong
    about this one, not merely untested."""
    src = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "tools" / "before_edit_hint_batch.py"
    text = src.read_text(encoding="utf-8")
    assert "emit_tool_call" not in text
    assert "emit_hint_delivered" not in text

    repo_root = str(tmp_project)
    monkeypatch.setenv("TRW_PROJECT_ROOT", repo_root)
    monkeypatch.setenv("TRW_REPO_ROOT", repo_root)
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    reload_config(None)
    from trw_mcp.state import _surface_role

    _surface_role.reset_surface_role_state()

    server = make_test_server("before_edit_hint_batch")
    tools = get_tools_sync(server)
    trw_dir = tmp_project / ".trw"
    before = _snapshot(trw_dir)
    _call(tools["trw_before_edit_hint_batch"].fn, repo_root=repo_root)
    after = _snapshot(trw_dir)
    assert before == after


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    before = os.environ.get("TRW_SURFACE_ROLE")
    before_root = os.environ.get("TRW_PROJECT_ROOT")
    yield
    if before is None:
        monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    else:
        monkeypatch.setenv("TRW_SURFACE_ROLE", before)
    if before_root is None:
        monkeypatch.delenv("TRW_PROJECT_ROOT", raising=False)
    else:
        monkeypatch.setenv("TRW_PROJECT_ROOT", before_root)
    from trw_mcp.state import _surface_role

    _surface_role.reset_surface_role_state()

"""PRD-SEC-015 round-2 audit, Row 5: behavior-level purity over REAL handlers.

The pre-existing ``test_reviewer_surface_enforcement.py`` exhaustive-denial
sweep proves NAME exclusion: every non-member of ``REVIEWER_TOOLS`` is denied
by the middleware. It never invokes a real tool body, so it could not have
caught (and did not catch) Row 2's ``update_chunk_index`` write or Row 3's
ceremony/propensity/telemetry writes inside ``trw_recall`` -- both ran the
FAKE ``_execute`` sentinel instead of the registered handler.

This file runs every REAL registered handler for every member of
``REVIEWER_TOOLS`` under the reviewer role in a temp project and asserts the
``.trw`` tree is BYTE-IDENTICAL before and after.

PRD-CORE-300 slice S10a supersedes the prior NFR03 allowlist: ``trw_code``'s
hint mode now skips ``emit_hint_delivered`` and ``_record_exposure`` entirely
under the reviewer role (see ``tools/code.py`` and ``compute_before_edit_hint``
in ``tools/_before_edit_hint_core.py``), so the one previously-acknowledged
residual write (``.trw/telemetry/channel-events.jsonl``) no longer happens and
there is NO allowlist left to name.
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

_TOOL_GROUPS_FOR_REVIEWER_SURFACE = (
    "learning",
    "code",
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
    # PRD-CORE-300-FR11 (S9): graph mode is a trw_recall call, not a separate tool.
    _call(tools["trw_recall"].fn, graph_id="nonexistent-id")
    # All three trw_code modes, so the sweep exercises the whole tool, not
    # just one branch of its dispatch.
    _call(tools["trw_code"].fn, mode="search", repo_root=repo_root, query="def ")
    _call(tools["trw_code"].fn, mode="symbol", repo_root=repo_root, query="anything")
    _call(tools["trw_code"].fn, mode="hint", files="README.md", repo_root=repo_root, ctx=None)


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
    # Non-vacuity for the sweep itself: _invoke_every_reviewer_tool's own
    # source is exactly the REVIEWER_TOOLS membership, one ``tools[...]``
    # lookup per member (trw_code appears three times, once per mode) -- so a
    # future REVIEWER_TOOLS member with no call here fails this, rather than
    # silently narrowing what the byte-identical assertion below actually covers.
    import inspect

    invoker_src = inspect.getsource(_invoke_every_reviewer_tool)
    assert all(f'tools["{name}"]' in invoker_src for name in REVIEWER_TOOLS), (
        "a REVIEWER_TOOLS member is not exercised by _invoke_every_reviewer_tool"
    )

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
    # PRD-CORE-300 slice S10a: trw_code's hint mode now skips
    # emit_hint_delivered and _record_exposure under the reviewer role, so the
    # prior NFR03 allowlist (one acknowledged telemetry-append file) is gone —
    # the tree must be byte-identical, full stop.
    assert not changed, f"reviewer-role handlers mutated the .trw tree: {sorted(changed)}"

    # Row 2 pinpoint: the reviewer role must never create the code-index
    # directory at all (no manifest existed, so update_chunk_index would have
    # raised FileNotFoundError before ever writing -- this additionally pins
    # that load_chunk_index's read-only path took no directory-creating branch).
    assert not (trw_dir / "code-index").exists()


def test_trw_code_hint_mode_writes_nothing_under_reviewer_role(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pinned directly, replacing the deleted-tool Row 6 case and the old
    allowlist-naming test: PRD-CORE-300 slice S10a made ``trw_code(mode="hint")``
    itself a no-write path under the reviewer role (``compute_before_edit_hint``
    skips ``emit_hint_delivered``/``_record_exposure``; ``tools/code.py`` skips
    ``emit_tool_call`` and the transition-nudge selector) — there is no longer
    an acknowledged residual-write file to confirm the existence of; the whole
    point is that none exists."""
    repo_root = str(tmp_project)
    monkeypatch.setenv("TRW_PROJECT_ROOT", repo_root)
    monkeypatch.setenv("TRW_REPO_ROOT", repo_root)
    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    reload_config(None)
    from trw_mcp.state import _surface_role

    _surface_role.reset_surface_role_state()

    server = make_test_server("code")
    tools = get_tools_sync(server)

    trw_dir = tmp_project / ".trw"
    before = _snapshot(trw_dir)

    _call(tools["trw_code"].fn, mode="hint", files="README.md", repo_root=repo_root, ctx=None)

    after = _snapshot(trw_dir)
    assert before == after
    assert not (trw_dir / "telemetry" / "channel-events.jsonl").exists()


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

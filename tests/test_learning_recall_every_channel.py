"""``learning_recall_enabled: false`` keeps learning content off every channel.

The master switch had three readers (the ``trw_recall`` tool, session-start
recall, phase auto-recall) plus the prompt hook, and five more channels ignored
it: the edit-hint collector, the ceremony nudge pool, the AGENTS.md learnings
section, REVIEW.md and the ``trw://learnings/summary`` resource (lead ruling
2026-09-23). Each channel below is driven at the function that produces what the
agent sees, in a project holding one learning, with the switch on and off.

``test_every_reader_call_site_is_classified`` keeps the table honest: it scans
the source for every call to a learnings reader, so a new call site fails here
until it is either added as a channel or excluded with a reason.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests._memory_fixtures import MemoryDaemon, attach_checkout
from trw_mcp.models.config import TRWConfig, reload_config
from trw_mcp.state._hook_flags import write_hook_flags

pytestmark = pytest.mark.unit


SRC = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
TOKEN = "zebrapool"
SUMMARY = f"The {TOKEN} connection pool exhausts under load"
#: REVIEW.md recalls with every review tag, and the store ANDs tags, so the probe carries all of them.
TAGS = ["gotcha", "bug", "security", "type-safety", "testing", "pattern"]


def _recall_tool(trw_dir: Path, config: TRWConfig) -> str:
    from trw_mcp.tools._recall_impl import execute_recall

    return str(execute_recall(TOKEN, trw_dir, config)["learnings"])


def _session_start(trw_dir: Path, config: TRWConfig) -> str:
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._session_recall_helpers import perform_session_recalls

    return str(perform_session_recalls(trw_dir, TOKEN, config, FileStateReader())[0])


def _prompt_hook(trw_dir: Path, config: TRWConfig) -> str:
    import trw_mcp

    write_hook_flags(trw_dir, config)
    hook = Path(trw_mcp.__file__).parent / "data" / "hooks" / "user-prompt-submit.sh"
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRW_AUTO_RECALL")}
    env |= {"CLAUDE_PROJECT_DIR": str(trw_dir.parent), "HOME": str(trw_dir.parent / ".home")}
    return subprocess.run(
        ["/bin/sh", str(hook)],
        input=f'{{"prompt":"why does the {TOKEN} pool exhaust","session_id":"s"}}',
        text=True,
        env=env,
        cwd=trw_dir.parent,
        capture_output=True,
        check=False,
    ).stdout


def _edit_hint(_trw_dir: Path, _config: TRWConfig) -> str:
    """The collector behind the five hint/report tools, both transition nudges and the edit-hint hooks."""
    from trw_mcp.tools._learnings_collector import collect_learnings

    return str(collect_learnings([TOKEN]))


def _build_check_nudge(_trw_dir: Path, _config: TRWConfig) -> str:
    from trw_mcp.tools._ceremony_status_context import _build_check_learning_candidates

    return str(_build_check_learning_candidates([f"{TOKEN} pool exhausted"]))


def _nudge_pool(trw_dir: Path, _config: TRWConfig) -> str:
    """The pool behind the Watch-out line and the status learning nudge."""
    from trw_mcp.state.recall_factories import recall_for_nudge_pool

    return str(recall_for_nudge_pool(trw_dir, query=TOKEN))


def _agents_md(trw_dir: Path, config: TRWConfig) -> str:
    from trw_mcp.state.claude_md._agents_md import _inject_learnings_to_agents

    return _inject_learnings_to_agents(trw_dir, config)


def _review_md(trw_dir: Path, _config: TRWConfig) -> str:
    from trw_mcp.state.claude_md._sync import generate_review_md

    generate_review_md(trw_dir, trw_dir.parent)
    return (trw_dir.parent / "REVIEW.md").read_text(encoding="utf-8")


def _summary_resource(trw_dir: Path, config: TRWConfig) -> str:
    from trw_mcp.resources.config import _build_learnings_summary

    return _build_learnings_summary(trw_dir, config)


CHANNELS: dict[str, Callable[[Path, TRWConfig], str]] = {
    "trw_recall": _recall_tool,
    "session_start": _session_start,
    "prompt_hook": _prompt_hook,
    "edit_hint": _edit_hint,
    "build_check_nudge": _build_check_nudge,
    "nudge_pool": _nudge_pool,
    "agents_md": _agents_md,
    "review_md": _review_md,
    "summary_resource": _summary_resource,
}


def _project(root: Path, monkeypatch: pytest.MonkeyPatch, memory_daemon: MemoryDaemon) -> Path:
    from trw_mcp.state.memory_adapter import store_learning

    trw_dir = root / ".trw"
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(root))
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    monkeypatch.setenv("HOME", str(root / ".home"))
    for key in ("TRW_DEDUP_ENABLED", "TRW_EMBEDDINGS_ENABLED"):
        monkeypatch.setenv(key, "false")
    monkeypatch.setattr(
        "trw_memory.daemon.client.start_daemon_detached",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("a test tried to start a second memory daemon")),
    )
    attach_checkout(trw_dir, memory_daemon)
    reload_config()
    store_learning(trw_dir, "L-probe", SUMMARY, "Raise the pool size.", tags=TAGS, impact=0.9)
    # The YAML mirror trw_learn keeps beside the store; the prompt hook reads it, not SQLite.
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "learnings" / "entries" / "L-probe.yaml").write_text(
        f'id: "L-probe"\nstatus: active\nsummary: "{SUMMARY}"\nimpact: 0.9\ntags: [{", ".join(TAGS)}]\n',
        encoding="utf-8",
    )
    return trw_dir


@pytest.mark.parametrize("channel", CHANNELS)
def test_recall_off_keeps_learning_content_off_the_channel(
    channel: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_daemon: MemoryDaemon
) -> None:
    probe = CHANNELS[channel]
    try:
        # Both variants share ONE trw_dir: the global ``_isolate_trw_dir``
        # autouse fixture forces every trw_dir-implicit reader
        # (``resolve_trw_dir()``, used by the edit_hint / build_check_nudge
        # channels) to ``tmp_path / ".trw"`` regardless of what root a test
        # writes to, so a channel's own root and the isolation root must be
        # the same directory or those two channels silently read nothing.
        trw_dir = _project(tmp_path, monkeypatch, memory_daemon)
        on = TRWConfig(learning_recall_enabled=True)
        reload_config(on)
        assert TOKEN in probe(trw_dir, on), f"non-vacuity: {channel} delivers nothing even with recall on"

        # Same checkout, just the in-memory config toggled: attach_checkout is
        # not re-run here (it would append a second, duplicate-key
        # ``project_namespace`` line to the same config.yaml).
        off = TRWConfig(learning_recall_enabled=False)
        reload_config(off)
        assert TOKEN not in probe(trw_dir, off), f"{channel} delivered a learning with learning_recall_enabled=false"
    finally:
        reload_config()


#: Every call to a learnings reader in production code, by file. A file is either
#: a channel above (it produces what the agent sees, and is gated) or excluded
#: with the reason it delivers no learning content.
_READER = re.compile(
    r"\b(recall_learnings|collect_learnings|recall_for_\w+|recall_baseline_high_impact|recall_focused"
    r"|recall_recent_bypass|list_active_learnings|execute_recall|_inject_learnings_to_agents)\("
    # An aliased import (``recall_learnings as _recall``) hides the call from the names above.
    r"|\b(recall_learnings|list_active_learnings)\s+as\s+(?!\2\b)\w+"
)
CLASSIFIED: dict[str, str] = {
    "tools/learning.py": "channel trw_recall (the registered tool calls execute_recall)",
    "services/local_surface_service.py": "channel trw_recall (`trw-mcp local recall`, via execute_recall)",
    "tools/_learnings_collector.py": "channel edit_hint (the gated collector)",
    "tools/_before_edit_hint_core.py": "channel edit_hint",
    "tools/before_edit_hint_batch.py": "channel edit_hint",
    "tools/codebase_risk_report.py": "channel edit_hint",
    "tools/cross_repo_ordering.py": "channel edit_hint",
    "tools/ordering_compare.py": "channel edit_hint",
    "tools/_ceremony_status_context.py": "channel build_check_nudge; the deliver nudge shares the collector",
    "state/recall_factories.py": "channels nudge_pool and review_md (the gated factories); session_start",
    "state/_ceremony_nudge_selectors.py": "channel nudge_pool (Watch-out line)",
    "tools/_ceremony_status_nudge.py": "channel nudge_pool (status learning nudge)",
    "state/claude_md/_agents_md.py": "channel agents_md",
    "state/claude_md/_sync.py": "channel review_md",
    "resources/config.py": "channel summary_resource",
    "cognitive_scaling/_scout_signals.py": "excluded: counts precedent hits, emits no learning text",
    "state/claude_md/_promotion.py": "excluded: deprecated and uncalled (PRD-CORE-093)",
    "state/_tier_sweep.py": "excluded: tier maintenance, no agent output",
    "state/tiers.py": "excluded: tier maintenance, no agent output",
    "state/analytics/counters.py": "excluded: counters",
    "state/analytics/dedup.py": "excluded: dedup metrics",
    "state/analytics/entries.py": "excluded: analytics",
    "state/consolidation/_clustering.py": "excluded: consolidation, no agent output",
    "state/claude_md/_review_md.py": "channels agents_md and review_md (aliased; the reader holds the gate)",
    "state/learning_injection.py": "channel edit_hint (aliased; the reader holds the gate)",
    "tools/_recall_impl.py": "channel trw_recall (aliased; the reader holds the gate)",
    "tools/_learn_preflight.py": "excluded: an unused compatibility default, never called",
}


def test_every_reader_call_site_is_classified() -> None:
    found = {
        path.relative_to(SRC).as_posix()
        for path in SRC.rglob("*.py")
        if any(
            not line.lstrip().startswith(("def ", "#")) and _READER.search(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        )
    }
    assert sorted(found - set(CLASSIFIED)) == [], "classify each new learnings reader call site above"
    assert sorted(set(CLASSIFIED) - found) == [], "a classified file no longer calls a reader; drop it"


def test_phase_auto_recall_is_a_deleted_channel_not_a_gated_one() -> None:
    """PRD-CORE-294 FR02 deleted phase auto-recall: no reader call site, no gate path.

    The eval overlay behaviour test asserts the same, so no arm can claim to ablate it.
    """
    import importlib.util
    from typing import get_args

    from trw_mcp.state._recall_gate import RecallPath

    assert "phase_auto" not in get_args(RecallPath)
    assert importlib.util.find_spec("trw_mcp.tools._session_recall_phase") is None
    assert not [path for path, why in CLASSIFIED.items() if "phase" in path or "phase" in why]


def test_the_shared_readers_hold_the_gate_for_an_aliased_call_site(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_daemon: MemoryDaemon
) -> None:
    """The gate sits in the readers, so a call the census cannot name is still gated.

    ``_review_md`` imports ``recall_learnings as _recall``; the census regex never
    matched that call. Maintenance readers opt out by name, never by default.
    """
    from trw_mcp.state.claude_md import _review_md
    from trw_mcp.state.memory_adapter import list_active_learnings

    try:
        trw_dir = _project(tmp_path, monkeypatch, memory_daemon)
        reload_config(TRWConfig(learning_recall_enabled=True))
        assert TOKEN in str(_review_md.recall_learnings(trw_dir, tags=["gotcha"])), "non-vacuity: aliased reader"
        reload_config(TRWConfig(learning_recall_enabled=False))
        assert TOKEN not in str(_review_md.recall_learnings(trw_dir, tags=["gotcha"]))
        assert TOKEN not in str(list_active_learnings(trw_dir))
        assert TOKEN not in str(list_active_learnings(trw_dir, purpose="mantenance")), "an unknown purpose is gated"  # type: ignore[arg-type]
        assert TOKEN in str(list_active_learnings(trw_dir, purpose="maintenance"))
    finally:
        reload_config()


def test_switching_recall_off_withdraws_learnings_already_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_daemon: MemoryDaemon
) -> None:
    """An arm that turns recall off must not read what an earlier arm wrote to AGENTS.md or REVIEW.md."""
    user_text = "# Project notes\n\nKeep this line.\n"
    try:
        trw_dir = _project(tmp_path, monkeypatch, memory_daemon)
        root = trw_dir.parent
        (root / "AGENTS.md").write_text(user_text, encoding="utf-8")
        on = TRWConfig(learning_recall_enabled=True)
        reload_config(on)
        _agents_md_sync(trw_dir, on)
        _review_md(trw_dir, on)
        assert TOKEN in (root / "AGENTS.md").read_text(encoding="utf-8"), "non-vacuity: AGENTS.md"
        assert TOKEN in (root / "REVIEW.md").read_text(encoding="utf-8"), "non-vacuity: REVIEW.md"

        off = TRWConfig(learning_recall_enabled=False)
        reload_config(off)
        assert _run_session_start_withdraw(off) == []

        agents = (root / "AGENTS.md").read_text(encoding="utf-8")
        assert TOKEN not in agents
        assert "Key Learnings" not in agents
        assert agents.startswith(user_text), "user content outside the markers is untouched"
        assert "<!-- trw:start -->" in agents, "the managed section stays; only its learnings go"
        assert TOKEN not in (root / "REVIEW.md").read_text(encoding="utf-8")
    finally:
        reload_config()


def test_withdrawal_ignores_a_marker_mentioned_in_user_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_daemon: MemoryDaemon
) -> None:
    """Markers match whole lines only (.claude/rules/trw-mcp-python.md §Marker / Sentinel Matching)."""
    user_text = (
        "# Project notes\n\nTRW writes between `<!-- trw:start -->` and its end marker.\n\n"
        "## Key Learnings\n\nOur own learning, kept.\n"
    )
    try:
        trw_dir = _project(tmp_path, monkeypatch, memory_daemon)
        root = trw_dir.parent
        (root / "AGENTS.md").write_text(user_text, encoding="utf-8")
        on = TRWConfig(learning_recall_enabled=True)
        reload_config(on)
        _agents_md_sync(trw_dir, on)
        assert TOKEN in (root / "AGENTS.md").read_text(encoding="utf-8"), "non-vacuity: AGENTS.md"

        off = TRWConfig(learning_recall_enabled=False)
        reload_config(off)
        assert _run_session_start_withdraw(off) == []

        agents = (root / "AGENTS.md").read_text(encoding="utf-8")
        assert agents.startswith(user_text), "user prose, including its own Key Learnings, is untouched"
        assert TOKEN not in agents
        assert "\n<!-- trw:start -->\n" in agents
    finally:
        reload_config()


def _run_session_start_withdraw(config: TRWConfig) -> list[str]:
    """Drive the session-start runner over its withdraw step; return the recorded errors."""
    from trw_mcp.tools import ceremony
    from trw_mcp.tools._ceremony_step_table import SESSION_START_STEPS, SessionStartContext, run_steps

    sctx = SessionStartContext(query="", config=config, ctx=None, is_focused=False, results={}, errors=[])
    run_steps([step for step in SESSION_START_STEPS if step.key == "recall_withdraw"], sctx, ceremony)
    return sctx.errors


def _agents_md_sync(trw_dir: Path, config: TRWConfig) -> None:
    from trw_mcp.state.claude_md._agents_md import _sync_agents_md_if_needed

    _sync_agents_md_if_needed(True, config, trw_dir.parent, trw_dir, client="claude-code", force=True)

"""PRD-FIX-085 FR05: named recall factories cover the major call sites.

Pre-fix, 10+ call sites used recall_learnings() with divergent
parameter combinations. Each call site was its own bug surface.

Post-fix, named factories enumerate the actual usage patterns:
- recall_session_start: session_start focused on user query
- recall_for_nudge_pool: nudge content selection
- recall_for_review_tags: claude_md review/publish
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._memory_fixtures import DaemonCheckout
from trw_mcp.state import recall_factories


def _captured_call(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Side effect that records args/kwargs and returns a sentinel result."""
    _captured_call.args = args  # type: ignore[attr-defined]
    _captured_call.kwargs = kwargs  # type: ignore[attr-defined]
    return [{"id": "L-sentinel"}]


def test_recall_session_start_pins_min_impact(tmp_path: Path) -> None:
    """recall_session_start uses min_impact=0.3 by default and propagates query."""
    with patch.object(recall_factories, "_default_recall", return_value=_captured_call):
        recall_factories.recall_session_start(tmp_path, "auth scoring", max_results=10)
    kwargs = _captured_call.kwargs  # type: ignore[attr-defined]
    assert kwargs["query"] == "auth scoring"
    assert kwargs["min_impact"] == 0.3
    assert kwargs["compact"] is False
    assert kwargs["max_results"] == 10
    assert kwargs["status"] == "active"


def test_recall_for_nudge_pool_uses_compact_false(tmp_path: Path) -> None:
    """recall_for_nudge_pool uses compact=False because rendering needs summaries."""
    with patch.object(recall_factories, "_default_recall", return_value=_captured_call):
        recall_factories.recall_for_nudge_pool(tmp_path, query="*", tags=["audit"], min_impact=0.5, max_results=10)
    kwargs = _captured_call.kwargs  # type: ignore[attr-defined]
    assert kwargs["query"] == "*"
    assert kwargs["tags"] == ["audit"]
    assert kwargs["min_impact"] == 0.5
    assert kwargs["max_results"] == 10
    assert kwargs["compact"] is False
    assert kwargs["status"] == "active"


def test_recall_for_review_tags_pins_status_active(tmp_path: Path) -> None:
    """recall_for_review_tags pins status='active'."""
    with patch.object(recall_factories, "_default_recall", return_value=_captured_call):
        recall_factories.recall_for_review_tags(tmp_path, tags=["pattern"], min_impact=0.7, max_results=20)
    kwargs = _captured_call.kwargs  # type: ignore[attr-defined]
    assert kwargs["tags"] == ["pattern"]
    assert kwargs["min_impact"] == 0.7
    assert kwargs["max_results"] == 0  # each tag in full; the cap applies after impact ranking (L-hzMb)
    assert kwargs["status"] == "active"


@pytest.fixture
def _real_trw_dir(daemon_checkout: DaemonCheckout, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real store-backed .trw dir (no mocks) for a behavioural L-hzMb check."""
    from trw_mcp.models.config import reload_config

    for key in ("TRW_DEDUP_ENABLED", "TRW_EMBEDDINGS_ENABLED"):
        monkeypatch.setenv(key, "false")
    reload_config()
    yield daemon_checkout.trw_dir
    reload_config()


def test_recall_for_review_tags_retains_older_high_impact_across_full_tag_union(
    _real_trw_dir: Path,
) -> None:
    """L-hzMb regression: an older impact-0.95 learning must survive the top-20 cap.

    Real store, real ``recall_for_review_tags`` call -- no mocked recall
    function. 25 newer impact-0.75 learnings and 1 older impact-0.95 learning
    all carry the same review tag. Pre-fix (65d2f908c), ``recall_for_review_tags``
    passed the caller's ``max_results`` straight through to a single
    ``recall_learnings`` call, which orders ``updated_at DESC, id DESC`` --
    the 20-row SQL LIMIT truncated the oldest (highest-impact) row before impact
    ranking ever saw it. Proven red against that implementation in this session
    (9 failures including this scenario's ``generate_review_md`` sibling test in
    ``test_review_md_real_store.py``, when recall_for_review_tags's body was
    swapped back to the pre-fix single-call form). Post-fix, each tag is recalled
    in full (``max_results=0``) and ranked by impact locally, so the older row
    survives the cap regardless of SQL recency ordering.
    """
    from trw_mcp.state.memory_adapter import store_learning
    from trw_mcp.state.recall_factories import recall_for_review_tags

    store_learning(_real_trw_dir, "L-aaa-old", "Old but critical", "detail", tags=["gotcha"], impact=0.95)
    for n in range(25):
        store_learning(_real_trw_dir, f"L-new{n:02d}", f"Newer {n}", "detail", tags=["gotcha"], impact=0.75)

    results = recall_for_review_tags(_real_trw_dir, tags=["gotcha"], min_impact=0.5, max_results=20)

    assert len(results) == 20
    assert "L-aaa-old" in [r.get("id") for r in results]


def test_recall_for_review_tags_max_results_zero_is_unlimited(_real_trw_dir: Path) -> None:
    """max_results=0 means unlimited, matching recall_learnings' own convention.

    A literal ``ranked[:0]`` slice would silently return an empty list; the
    fix (``ranked[: max_results or None]``) instead returns every ranked row.
    """
    from trw_mcp.state.memory_adapter import store_learning
    from trw_mcp.state.recall_factories import recall_for_review_tags

    for n in range(7):
        store_learning(_real_trw_dir, f"L-u{n:02d}", f"Entry {n}", "detail", tags=["gotcha"], impact=0.8)

    results = recall_for_review_tags(_real_trw_dir, tags=["gotcha"], min_impact=0.5, max_results=0)

    assert len(results) == 7


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"impact": None}, 0.0),
        ({"impact": ""}, 0.0),
        ({"impact": "not-a-number"}, 0.0),
        ({}, 0.0),
        ({"impact": 0.75}, 0.75),
        ({"impact": "0.6"}, 0.6),
    ],
)
def test_parse_entry_impact_falls_back_to_zero_on_malformed_input(
    entry: dict[str, object],
    expected: float,
) -> None:
    """Missing key, None, empty string, and non-numeric strings all fall back to 0.0."""
    assert recall_factories.parse_entry_impact(entry) == expected


@pytest.mark.parametrize(
    ("factory", "args", "kwargs"),
    [
        ("recall_session_start", ("auth scoring",), {"max_results": 5}),
        ("recall_for_nudge_pool", (), {"max_results": 5}),
        ("recall_for_review_tags", (), {"tags": ["pattern"], "min_impact": 0.7, "max_results": 5}),
    ],
)
def test_every_factory_passes_nonempty_query(
    tmp_path: Path,
    factory: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> None:
    """Regression guard: every factory must pass a non-empty ``query``.

    A factory that omits ``query`` (or passes ``""``) silently degrades the
    underlying ``recall_learnings`` call — exactly the bug fixed in
    ``recall_for_review_tags`` (it had no ``query='*'``). recall_learnings has
    a required ``query`` positional, so an omission raises TypeError at runtime;
    this test pins the contract at the factory layer so the regression is caught
    in the fast unit tier, not in production.
    """
    with patch.object(recall_factories, "_default_recall", return_value=_captured_call):
        getattr(recall_factories, factory)(tmp_path, *args, **kwargs)
    captured = _captured_call.kwargs  # type: ignore[attr-defined]
    assert "query" in captured, f"{factory} did not pass a query kwarg to recall_learnings"
    assert isinstance(captured["query"], str)
    assert captured["query"].strip() != "", f"{factory} passed an empty query"


def test_factories_pass_query_positional_to_real_recall_signature(tmp_path: Path) -> None:
    """The recall signature requires ``query`` — a missing query is a hard error.

    Binds each factory's call against the REAL ``recall_learnings`` signature
    (not a permissive ``*args, **kwargs`` stub). If a factory ever drops the
    required ``query`` argument, ``Signature.bind`` raises TypeError here.
    """
    import inspect

    from trw_mcp.state.memory_adapter import recall_learnings

    sig = inspect.signature(recall_learnings)
    bound_calls: list[inspect.BoundArguments] = []

    def _binding_recall(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        bound_calls.append(sig.bind(*args, **kwargs))
        return []

    with patch.object(recall_factories, "_default_recall", return_value=_binding_recall):
        recall_factories.recall_session_start(tmp_path, "q", max_results=5)
        recall_factories.recall_for_nudge_pool(tmp_path, max_results=5)
        recall_factories.recall_for_review_tags(tmp_path, tags=["pattern"], min_impact=0.7, max_results=5)

    assert len(bound_calls) == 3
    for call in bound_calls:
        call.apply_defaults()
        assert str(call.arguments["query"]).strip() != ""


def test_known_callers_use_factories() -> None:
    """Source-grep audit: the 4 migrated call sites use a factory function.

    Pre-fix these called recall_learnings(...) directly with divergent params.
    Post-fix they call a named factory. Future regressions of "ad-hoc
    parameter drift" are caught by this test.
    """
    import subprocess

    result = subprocess.run(
        [
            "grep",
            "-rn",
            "--include=*.py",
            r"recall_learnings(",
            "src/",
        ],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode not in (0, 1):
        pytest.skip(f"grep failed: {result.stderr}")

    # The 5 migrated call sites must NO LONGER call recall_learnings directly.
    # Allowed contexts: the wrapper definition itself, imports, comments,
    # and learning_injection.py's local wrapper (intentional file-level DRY).
    forbidden_files = {
        "src/trw_mcp/tools/_ceremony_status.py",
        "src/trw_mcp/state/ceremony_nudge.py",
        "src/trw_mcp/state/claude_md/_sync.py",
        "src/trw_mcp/tools/_session_recall_helpers.py",
    }
    offenders: list[str] = []
    for line in result.stdout.splitlines():
        for path in forbidden_files:
            if line.startswith(path + ":"):
                # Only flag actual call lines (not imports / aliases / comments).
                _, _, src = line.split(":", 2)
                stripped = src.strip()
                if stripped.startswith("#"):
                    continue
                if "from " in stripped or "import " in stripped:
                    continue
                if "as adapter_recall" in stripped or "as recall_learnings" in stripped:
                    continue
                if "recall_learnings(" in stripped:
                    offenders.append(line)
    assert offenders == [], (
        "FR05: migrated call sites must use a recall_factories factory, "
        "not recall_learnings(...) directly:\n" + "\n".join(offenders)
    )

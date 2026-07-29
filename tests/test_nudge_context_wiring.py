"""Regression guard for the reactive nudge-context wiring (ledger UF-006).

Commit ``093b1fd49e`` (2026-04-10) renamed ``_apply_ceremony_nudge`` ->
``_apply_ceremony_status`` and dropped the ``NudgeContext`` construction from
every production call site. ``context`` then defaulted to ``None`` everywhere
for ~3.5 months, which silently disabled:

* the ``context`` nudge pool (``resolve_pool_content`` short-circuits on
  ``pool == "context" and context``, so every draw was wasted into
  ``record_pool_ignore`` -> cooldown);
* the build-failure / P0 pool bypass in ``_select_nudge_pool``;
* the per-tool dispatch in ``_context_reactive_message``.

Three layers of guard here:

1. behavioural spies proving each live call site CONSTRUCTS and PASSES a
   ``NudgeContext`` with the right label/payload;
2. an AST invariant proving no owned call site can regress to the
   context-less form;
3. downstream reachability proving a non-``None`` context actually changes
   pool selection and produces context-pool content.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from tests.conftest import extract_tool_fn, make_test_server
from trw_mcp.state._ceremony_progress_state import CeremonyState, NudgeContext
from trw_mcp.tools._ceremony_status_context import (
    KNOWN_TOOL_NAMES,
    append_ceremony_status_for_tool,
    build_nudge_context,
    resolve_tool_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_SRC = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"

# Every production module that decorates a tool response with ceremony status.
# The last three closed ledger UF-042/UF-043: build_check and deliver never
# called the injector at all (leaving ``NudgeContext.build_passed`` with no
# writer anywhere), and recall's call site was dropped by merge ``70bb84843f``.
OWNED_CALL_SITE_MODULES = (
    _SRC / "tools" / "_orchestration_lifecycle.py",
    _SRC / "tools" / "_ceremony_helpers.py",
    _SRC / "tools" / "requirements.py",
    _SRC / "tools" / "review.py",
    _SRC / "tools" / "_learn_impl.py",
    _SRC / "tools" / "_recall_impl.py",
    _SRC / "tools" / "build" / "_registration.py",
    _SRC / "tools" / "_ceremony_deliver_tool.py",
)


@pytest.fixture
def status_spy(monkeypatch: pytest.MonkeyPatch) -> list[NudgeContext | None]:
    """Capture the ``context`` argument reaching ``append_ceremony_status``.

    Patches the SOURCE module attribute; every call site resolves the symbol
    lazily at call time, so the spy observes the real production path.
    """
    seen: list[NudgeContext | None] = []

    def _spy(
        response: dict[str, object],
        trw_dir: Path | None = None,
        context: NudgeContext | None = None,
    ) -> dict[str, object]:
        seen.append(context)
        return response

    monkeypatch.setattr("trw_mcp.tools._ceremony_status.append_ceremony_status", _spy)
    return seen


def _only_context(seen: list[NudgeContext | None]) -> NudgeContext:
    """Assert exactly one injection happened and that it carried a context."""
    assert len(seen) == 1, f"expected exactly one ceremony-status injection, got {len(seen)}"
    context = seen[0]
    assert context is not None, "call site passed context=None — the 093b1fd49e regression"
    assert isinstance(context, NudgeContext)
    assert context.tool_name in KNOWN_TOOL_NAMES, f"unknown tool label {context.tool_name!r}"
    return context


# ---------------------------------------------------------------------------
# Label resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("INIT", "init"),
        ("STATUS", "status"),
        ("CHECKPOINT", "checkpoint"),
        ("session_start", "session_start"),
        ("review", "review"),
        ("learn", "learn"),
        ("prd_create", "prd_create"),
        ("prd_validate", "prd_validate"),
    ],
)
def test_resolve_tool_name_maps_every_live_label(label: str, expected: str) -> None:
    """Both the facade's constant NAME and its VALUE resolve to a real ToolName."""
    assert resolve_tool_name(label) == expected
    assert expected in KNOWN_TOOL_NAMES


def test_resolve_tool_name_passes_unknown_labels_through() -> None:
    """Unknown labels degrade (downstream dispatch returns None) rather than raise."""
    assert resolve_tool_name("not_a_tool") == "not_a_tool"


def test_build_nudge_context_carries_review_payload() -> None:
    context = build_nudge_context("review", review_verdict="block", review_p0_count=3)
    assert context.tool_name == "review"
    assert context.review_verdict == "block"
    assert context.review_p0_count == 3


# ---------------------------------------------------------------------------
# Call site 1-3: orchestration lifecycle (trw_init / trw_status / trw_checkpoint)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("facade_label", "expected"),
    [("INIT", "init"), ("STATUS", "status"), ("CHECKPOINT", "checkpoint")],
)
def test_orchestration_lifecycle_threads_tool_name_into_context(
    facade_label: str,
    expected: str,
    tmp_path: Path,
    status_spy: list[NudgeContext | None],
) -> None:
    """``tool_name`` is no longer an unread parameter — it reaches the context."""
    from trw_mcp.tools._orchestration_lifecycle import _apply_ceremony_status

    _apply_ceremony_status(
        {"status": "ok"},
        tool_name=facade_label,
        debug_event="test_event",
        trw_dir=tmp_path,
    )

    assert _only_context(status_spy).tool_name == expected


def test_trw_init_tool_reaches_injector_with_init_context(
    tmp_project: Path,
    status_spy: list[NudgeContext | None],
) -> None:
    """End-to-end: orchestration.py's "INIT" literal survives to the context."""
    server = make_test_server("orchestration")
    extract_tool_fn(server, "trw_init")(task_name="uf006-init-wiring")

    assert _only_context(status_spy).tool_name == "init"


# ---------------------------------------------------------------------------
# Call site 4: trw_session_start
# ---------------------------------------------------------------------------


def test_step_ceremony_status_passes_session_start_context(
    tmp_project: Path,
    status_spy: list[NudgeContext | None],
) -> None:
    from trw_mcp.tools._ceremony_helpers import step_ceremony_status

    step_ceremony_status({"status": "ok"})

    assert _only_context(status_spy).tool_name == "session_start"


# ---------------------------------------------------------------------------
# Call sites 5-6: trw_prd_create / trw_prd_validate
# ---------------------------------------------------------------------------


def test_prd_create_passes_prd_create_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_spy: list[NudgeContext | None],
) -> None:
    from unittest.mock import patch

    from trw_mcp.tools.requirements import register_requirements_tools

    server = make_test_server()
    register_requirements_tools(server)
    (tmp_path / "docs" / "requirements-aare-f" / "prds").mkdir(parents=True)

    with (
        patch("trw_mcp.tools.requirements.resolve_project_root", return_value=tmp_path),
        patch("trw_mcp.tools.requirements.next_prd_sequence", return_value=901),
        patch("trw_mcp.tools.requirements.get_config") as mock_cfg,
    ):
        mock_cfg.return_value.prds_relative_path = "docs/requirements-aare-f/prds"
        mock_cfg.return_value.trw_dir = ".trw"
        mock_cfg.return_value.index_auto_sync_on_status_change = False
        mock_cfg.return_value.ambiguity_rate_max = 0.3
        mock_cfg.return_value.completeness_min = 0.7
        mock_cfg.return_value.traceability_coverage_min = 0.5
        extract_tool_fn(server, "trw_prd_create")(
            input_text="UF-006 nudge context wiring",
            category="CORE",
            priority="P1",
        )

    assert _only_context(status_spy).tool_name == "prd_create"


def test_prd_validate_passes_prd_validate_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_spy: list[NudgeContext | None],
) -> None:
    from trw_mcp.tools.requirements import register_requirements_tools

    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)
    server = make_test_server()
    register_requirements_tools(server)

    prd_file = tmp_path / "PRD-CORE-901.md"
    prd_file.write_text(
        "# PRD-CORE-901\n\n## Problem\nNudges are dead.\n\n## Requirements\nFR01: restore context.\n",
        encoding="utf-8",
    )
    extract_tool_fn(server, "trw_prd_validate")(prd_path=str(prd_file))

    assert _only_context(status_spy).tool_name == "prd_validate"


# ---------------------------------------------------------------------------
# Call site 7: trw_review (the one site that also carries reactive payload)
# ---------------------------------------------------------------------------


def test_review_passes_verdict_and_p0_count_into_context(
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_spy: list[NudgeContext | None],
) -> None:
    """Review is the only live producer of the P0 signal the context pool needs."""
    from trw_mcp.tools import review as review_mod

    monkeypatch.setattr(
        "trw_mcp.tools._review_manual.handle_manual_mode",
        lambda *args, **kwargs: {
            "verdict": "block",
            "critical_count": 2,
            "substantive": True,
            "dimensions": [],
        },
    )

    server = make_test_server()
    review_mod.register_review_tools(server)
    extract_tool_fn(server, "trw_review")(
        mode="manual",
        findings=[{"severity": "critical", "description": "null deref"}],
    )

    context = _only_context(status_spy)
    assert context.tool_name == "review"
    assert context.review_verdict == "block"
    assert context.review_p0_count == 2


# ---------------------------------------------------------------------------
# Call site 8: trw_learn
# ---------------------------------------------------------------------------


def test_learn_passes_learn_context(
    tmp_path: Path,
    status_spy: list[NudgeContext | None],
) -> None:
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._learn_impl import execute_learn

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True)

    execute_learn(
        summary="uf006 wiring probe",
        detail="proves the learn call site passes a nudge context",
        trw_dir=trw_dir,
        config=get_config(),
        _adapter_store=lambda *a, **kw: {"learning_id": "L-uf006", "status": "recorded"},
        _generate_learning_id=lambda: "L-uf006",
        _save_learning_entry=lambda _dir, _entry: trw_dir / "learnings" / "entries" / "uf006.yaml",
        _update_analytics=lambda *a: None,
        _list_active_learnings=lambda _dir: [],
        _check_and_handle_dedup=lambda *a, **kw: None,
    )

    assert _only_context(status_spy).tool_name == "learn"


# ---------------------------------------------------------------------------
# Static invariant: no owned call site may drop the context again
# ---------------------------------------------------------------------------


def _ceremony_status_calls(module_path: Path) -> Iterator[ast.Call]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in {"append_ceremony_status", "append_ceremony_status_for_tool"}:
            yield node


def test_every_owned_call_site_supplies_a_nudge_context() -> None:
    """AST invariant: this is the exact shape 093b1fd49e regressed.

    A context-less ``append_ceremony_status(result, trw_dir)`` in any owned
    tool module fails here even if every behavioural test still passes,
    because the nudge layer degrades silently rather than erroring.
    """
    offenders: list[str] = []
    total = 0
    for module_path in OWNED_CALL_SITE_MODULES:
        for call in _ceremony_status_calls(module_path):
            total += 1
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            keywords = {kw.arg for kw in call.keywords}
            if name == "append_ceremony_status_for_tool":
                supplies_context = "tool_name" in keywords
            else:
                supplies_context = "context" in keywords or len(call.args) >= 3
            if not supplies_context:
                offenders.append(f"{module_path.name}:{call.lineno}")

    assert total >= 9, f"expected at least 9 owned call sites, found {total}"
    assert not offenders, f"ceremony-status call sites without a nudge context: {offenders}"


# ---------------------------------------------------------------------------
# Downstream reachability: the context now actually changes behaviour
# ---------------------------------------------------------------------------


def test_context_pool_yields_content_only_when_context_is_present() -> None:
    """``resolve_pool_content("context", ...)`` was unconditionally dead."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._ceremony_status_pool import resolve_pool_content

    cfg = TRWConfig()
    state = CeremonyState(session_started=True, phase="implement")

    without = resolve_pool_content("context", state, cfg, None, Path("/nonexistent"))
    assert without is None, "context pool without a context must stay empty (pre-fix behaviour)"

    with_context = resolve_pool_content(
        "context",
        state,
        cfg,
        build_nudge_context("review", review_verdict="block", review_p0_count=1),
        Path("/nonexistent"),
    )
    assert isinstance(with_context, str)
    assert with_context.strip()


def test_p0_context_forces_the_context_pool_bypass() -> None:
    """``_select_nudge_pool`` short-circuits to "context" on P0 — needs a context."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.ceremony_nudge import _select_nudge_pool

    weights = TRWConfig().client_profile.nudge_pool_weights
    state = CeremonyState(session_started=True, phase="review")

    assert (
        _select_nudge_pool(
            state,
            weights,
            build_nudge_context("review", review_verdict="block", review_p0_count=2),
        )
        == "context"
    )


def test_reversion_prompt_becomes_reachable_with_the_restored_context() -> None:
    """Phase-reversion prompts return None for every context-less caller.

    This proves the INPUT contract. The OUTPUT is now wired too (ledger UF-041)
    — ``append_ceremony_status`` calls ``attach_reversion_prompt`` and the text
    lands on ``response["reversion_prompt"]``; see
    ``tests/test_nudge_step_attribution.py`` and the build_check end-to-end case
    in ``tests/test_nudge_reward_and_call_sites.py``.
    """
    from trw_mcp.state.ceremony_nudge import _reversion_prompt

    state = CeremonyState(session_started=True, phase="review")
    assert _reversion_prompt(None, state) is None

    prompt = _reversion_prompt(build_nudge_context("review", review_p0_count=1), state)
    assert prompt is not None
    assert "PLAN" in prompt


def test_review_p0_context_reaches_response_end_to_end(tmp_path: Path) -> None:
    """Full live path: helper -> append_ceremony_status -> context pool -> response."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "config.yaml").write_text("nudge_enabled: true\n", encoding="utf-8")

    response: dict[str, Any] = {"verdict": "block"}
    append_ceremony_status_for_tool(
        response,
        trw_dir,
        tool_name="review",
        review_verdict="block",
        review_p0_count=2,
    )

    assert "P0 findings detected" in str(response.get("nudge_content", ""))

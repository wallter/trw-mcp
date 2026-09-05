"""PRD-CORE-246 FR01/FR02 — the detector reads the objective and the whole vocabulary.

Before FR01 the ONLY free-text field describing the work (``objective``) never
reached ``detect_task_type``: ``task_name`` is constrained to
``^[a-zA-Z0-9][a-zA-Z0-9_-]*$``, ``prd_scope`` entries are identifiers, and
``run_type`` is enumerated. The Scout classifier in the SAME ``trw_init`` call
already joined ``task_name + objective + prd_scope``; the detector two functions
away did not. FR01 gives both classifiers the same joined text.

FR02 completes ``_RUN_TYPE_MAP`` over the ``TaskType`` vocabulary and corrects
the module docstring, which listed the ``run_type`` mapping ABOVE the keyword
scan while the code executed them the other way round.

Nothing here monkeypatches the detector: every assertion runs the real
``detect_task_type`` and, for the wiring proof, the real
``resolve_init_profile``.
"""

from __future__ import annotations

import inspect
from typing import get_args

import pytest

from trw_mcp.models.task_profile_types import TaskType
from trw_mcp.tools import _task_type_detection as detection_mod
from trw_mcp.tools._task_type_detection import _RUN_TYPE_MAP, detect_task_type

pytestmark = pytest.mark.unit


# ── FR01: the objective drives detection ────────────────────────────────


def test_objective_text_drives_detection() -> None:
    """FR01 AC1-AC3, the headline case from the PRD.

    An opaque ticket id plus a code-bearing objective plus a *wrong* ``run_type``
    used to resolve to ``research`` — a surface with no verification pack AND
    (pre-FR03) a gate that would not ask for one.

    PRD PREMISE CORRECTION: FR01 AC1 predicts ``rca`` for this objective. The
    keyword table the same PRD documents falsifies that: the rca list is
    ``debug | rca | root cause | investigate | trace | stacktrace`` and contains
    neither "crash" nor "null-pointer", while "fix" is in the coding list. The
    resolved type is therefore ``coding``. What the success criterion actually
    requires — "resolves to a BUILD-BEARING task type instead of falling through
    to the run_type default" — holds, because ``coding`` is in
    ``_BUILD_ARTIFACT_TASK_TYPES``. Adding "crash" to the rca list would change
    classification for existing workloads (RISK-003) and is not in scope, so the
    expectation is corrected here rather than the keyword table edited.
    """
    from trw_mcp.tools._deliver_gate_mode import _BUILD_ARTIFACT_TASK_TYPES

    # AC1: the objective wins over the run_type default.
    result = detect_task_type(
        task_name="ticket-88213",
        objective="Fix the null-pointer crash in payment processing",
        run_type="research",
    )
    assert result.task_type == "coding"
    assert result.detection_method == "keyword"
    assert result.task_type in _BUILD_ARTIFACT_TASK_TYPES
    # The pre-FR01 answer, pinned so the regression is legible.
    assert result.task_type != "research"

    # The rca half of AC1, with an objective that actually carries an rca keyword.
    rca = detect_task_type(
        task_name="ticket-88213",
        objective="Investigate the null-pointer crash in payment processing",
        run_type="research",
    )
    assert (rca.task_type, rca.detection_method) == ("rca", "keyword")

    # AC2: with an EMPTY objective the same call falls through to run_type —
    # this is the pre-FR01 behavior, retained, and it is what makes AC1
    # attributable to the objective rather than to some other change.
    empty = detect_task_type(task_name="ticket-88213", objective="", run_type="research")
    assert empty.task_type == "research"
    assert empty.detection_method == "run_type"

    # AC3: a MULTI-WORD keyword matches, proving the joined text is scanned and
    # not just the regex-constrained task_name. "root cause" can never occur in
    # a task_name (space is outside the accepted character class), so a hit here
    # is only explicable by the objective being scanned.
    multiword = detect_task_type(task_name="ticket-4", objective="run a root cause analysis on the outage")
    assert multiword.task_type == "rca"
    assert multiword.detection_method == "keyword"


@pytest.mark.parametrize(
    ("keyword", "expected"),
    [("root cause", "rca"), ("write ", "docs"), ("add ", "coding")],
)
def test_the_three_task_name_unreachable_keywords_are_reachable_via_objective(keyword: str, expected: str) -> None:
    """FR01 boundary: exactly 3 of the 35 keywords contain a character outside
    ``[a-zA-Z0-9_-]`` and therefore can never appear in an accepted
    ``task_name``. They are RETAINED, not deleted, because FR01 is what makes
    them reachable."""
    import re

    result = detect_task_type(task_name="ticket-9", objective=f"please {keyword}something")

    assert result.task_type == expected
    assert result.detection_method == "keyword"
    # The control: this keyword cannot occur in a task_name the tool ACCEPTS, so
    # the hit above is signal the join CREATES, not signal it merely relocates.
    assert not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$", keyword), (
        f"{keyword!r} is expressible in a task_name; it is not one of the three unreachable keywords"
    )


def test_detector_and_scout_read_byte_identical_text() -> None:
    """FR01 assertion ``value_equals``: one join, not two.

    The Scout builds its classification text inline in
    ``_orchestration_scaling.run_scout_for_init``. The detector's
    ``_join_detection_text`` must produce the same string for the same inputs,
    or the two classifiers in a single ``trw_init`` disagree about what the task
    even is.
    """
    task_name, objective, prd_scope = "ticket-88213", "Fix the crash", ["PRD-CORE-246", "PRD-FIX-119"]

    detector_text = detection_mod._join_detection_text(task_name, objective, prd_scope)
    scout_text = "\n".join(part for part in (task_name, objective, " ".join(prd_scope)) if part)

    assert detector_text == scout_text
    # And the inline Scout expression is still the one this pins: if it is
    # edited, this literal must be edited with it.
    source = inspect.getsource(
        __import__("trw_mcp.tools._orchestration_scaling", fromlist=["run_scout_for_init"]).run_scout_for_init
    )
    assert '"\\n".join(part for part in (task_name, objective, " ".join(prd_scope or [])) if part)' in source


def test_resolve_init_profile_forwards_the_objective() -> None:
    """FR01 wiring: the production seam, not the leaf function.

    ``resolve_init_profile`` had no ``objective`` parameter at all, so the value
    supplied at ``trw_init`` stopped at the Scout. This drives the REAL profile
    resolver with a real config.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.tools._orchestration_scaling import resolve_init_profile

    config = get_config()
    profile = resolve_init_profile(
        config,
        task_name="ticket-88213",
        objective="Investigate the null-pointer crash in payment processing",
        run_type="research",
        prd_scope=None,
        task_type=None,
        complexity_hint=None,
        complexity_signals=None,
    )

    assert profile.task_type == "rca"
    assert profile.detection.detection_method == "keyword"


def test_trw_init_passes_the_objective_through() -> None:
    """FR01 end-to-end on the real ``trw_init`` tool."""
    from tests.conftest import extract_tool_fn, make_test_server

    trw_init = extract_tool_fn(make_test_server("orchestration"), "trw_init")
    result = trw_init(
        task_name="ticket-88213",
        objective="Investigate the null-pointer crash in payment processing",
        run_type="research",
    )

    assert result["task_type"] == "rca"
    assert result["task_type_detection_method"] == "keyword"


# ── FR02: the run_type map covers the vocabulary ────────────────────────


def test_run_type_map_covers_every_task_type() -> None:
    """FR02 AC1/AC2: every ``TaskType`` member round-trips, aliases preserved."""
    for member in get_args(TaskType):
        assert _RUN_TYPE_MAP[member] == member, f"{member} does not round-trip through _RUN_TYPE_MAP"
        result = detect_task_type(run_type=member)
        assert result.task_type == member
        assert result.detection_method == "run_type"

    # The legacy alias the framework's own trw_init default still emits.
    assert _RUN_TYPE_MAP["implementation"] == "coding"
    assert detect_task_type(run_type="implementation").task_type == "coding"

    # AC2 boundary: ``run_type="unknown"`` resolves to unknown by DETECTION, not
    # by fallback — FR04 makes that distinction visible to the caller.
    explicit_unknown = detect_task_type(run_type="unknown")
    assert (explicit_unknown.task_type, explicit_unknown.detection_method) == ("unknown", "run_type")
    assert detect_task_type().detection_method == "fallback"


def test_keyword_scan_still_precedes_the_run_type_map() -> None:
    """FR02 AC3: the identity entries must not reorder evaluation.

    ``run_type="docs"`` now maps, but a coding keyword in the description still
    wins — otherwise a caller's coarse run_type could shadow a specific
    description signal, which is the opposite of what FR01 is for.
    """
    assert detect_task_type(task_name="fix-thing", run_type="docs").task_type == "coding"
    assert detect_task_type(task_name="fix-thing", run_type="docs").detection_method == "keyword"
    # And the explicit caller override still beats everything.
    override = detect_task_type(task_name="fix-thing", run_type="docs", task_type="planning")
    assert (override.task_type, override.detection_method) == ("planning", "explicit_override")


def test_keyword_precedence_is_unchanged_by_the_join() -> None:
    """FR01 boundary: precedence is by keyword-LIST order, not by position in the
    joined string, so appending the objective after the task_name cannot reorder
    the documented rca > docs > eval > planning > coding > research ranking."""
    # "debug" (rca) sits later in the text than "implement" (coding) and still wins.
    assert detect_task_type(objective="implement the parser then debug it").task_type == "rca"
    # ...and the reverse ordering resolves the same way.
    assert detect_task_type(objective="debug the parser then implement the fix").task_type == "rca"


def test_module_docstring_states_the_executed_order() -> None:
    """FR02 ``grep_absent``: the stale priority sentence is gone.

    The old docstring listed ``2. run_type mapping`` / ``3. keyword scan`` while
    the code ran keywords first. A docstring that contradicts its own function
    is how the wrong mental model spread into the PRD intake in the first place.
    """
    doc = detection_mod.__doc__ or ""
    assert "2. ``run_type`` mapping" not in doc
    assert "4. ``prd_scope`` keyword scan" not in doc
    keyword_pos = doc.index("Keyword scan over the JOINED description text")
    run_type_pos = doc.index("``run_type`` mapping")
    assert keyword_pos < run_type_pos, "the docstring must list the order the code executes"


# ── NFR02/NFR03: fail-open posture and the bounded echo ─────────────────


def test_detection_is_fail_open_on_malformed_input() -> None:
    """NFR02: detection is a CLASSIFIER, not a gate — it must never block trw_init."""
    result = detect_task_type(task_name=None, prd_scope=[object()])  # type: ignore[arg-type]
    assert result.task_type == "unknown"
    assert result.detection_method == "fallback"


def test_detect_task_type_performs_no_io_or_model_call() -> None:
    """NFR01/Non-Goal: heuristic-only, and the join did not smuggle in I/O."""
    source = inspect.getsource(detection_mod)
    for forbidden in ("import requests", "httpx", "open(", "Path(", "subprocess", "litellm", "openai"):
        assert forbidden not in source, f"detection module must stay pure; found {forbidden!r}"

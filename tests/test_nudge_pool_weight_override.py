"""PRD-CORE-335: a project-level nudge pool-weight override, three-tier resolved.

Precedence (FR01): explicit ``TRWConfig.nudge_pool_weights`` > the pinned
run's task_profile tuple > ``client_profile.nudge_pool_weights``. The served
tests drive ``append_ceremony_status`` -- the path every tool response takes --
with a real ``.trw/config.yaml`` and measure the pool distribution
``select_pool`` produces with a seeded RNG and cooldown bypassed, so a
chi-square fit measures the WEIGHTS, not cooldown suppression.
"""

from __future__ import annotations

import ast
import random
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests import _source_index as source_index
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._client_profile import NudgePoolWeights

_SEED = 20260926
# Each trial drives the whole served path (~2.5 ms of state I/O), so the count is sized to the smallest separation
# asserted: the task tuple vs the client default contributes ~0.233 chi-square per draw, so 400 draws expect ~93
# against the 16.27 critical value (and a correct tuple's fit is decided by the fixed seed, not by chance).
_TRIALS = 400
_POOLS = ("workflow", "learnings", "ceremony", "context")
# chi-square critical value, df=3, alpha=0.001
_CHI2_CRIT_DF3 = 16.266
_SRC = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"

_DEFAULT = (40, 30, 20, 10)  # claude-code profile default
_OVERRIDE = (10, 20, 30, 40)
_TASK_TUPLE = (20, 40, 20, 20)  # the "rca" per-task-type tuple


def _as_dict(weights: tuple[int, int, int, int]) -> dict[str, int]:
    return dict(zip(_POOLS, weights, strict=True))


def _chi_square(counts: Counter[str], weights: tuple[int, int, int, int]) -> float:
    total = sum(counts.values())
    stat = 0.0
    for pool, weight in zip(_POOLS, weights, strict=True):
        expected = total * weight / 100
        if expected == 0:
            assert counts.get(pool, 0) == 0, f"{pool} has weight 0 but was selected"
            continue
        stat += (counts.get(pool, 0) - expected) ** 2 / expected
    return stat


# --- FR01 / NFR01: resolution -----------------------------------------------------


@pytest.mark.parametrize(
    ("override", "task_tuple", "expected"),
    [
        pytest.param(_OVERRIDE, _TASK_TUPLE, _OVERRIDE, id="tier1-override-beats-task-profile"),
        pytest.param(_OVERRIDE, None, _OVERRIDE, id="tier1-override-alone"),
        pytest.param(None, _TASK_TUPLE, _TASK_TUPLE, id="tier2-task-profile-beats-client-profile"),
        pytest.param(None, None, _DEFAULT, id="tier3-client-profile-fallback"),
    ],
)
def test_effective_pool_weights_three_tier_precedence(
    override: tuple[int, int, int, int] | None,
    task_tuple: tuple[int, int, int, int] | None,
    expected: tuple[int, int, int, int],
) -> None:
    kwargs: dict[str, object] = {"target_platforms": ["claude-code"]}
    if override is not None:
        kwargs["nudge_pool_weights"] = _as_dict(override)
    cfg = TRWConfig.model_validate(kwargs)
    resolved = cfg.effective_nudge_pool_weights(task_tuple)
    assert isinstance(resolved, NudgePoolWeights)
    assert resolved.model_dump() == _as_dict(expected)


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"workflow": 40, "learnings": 30, "ceremony": 20, "context": 9}, id="sum-99"),
        pytest.param({"workflow": 110, "learnings": -10, "ceremony": 0, "context": 0}, id="negative"),
    ],
)
def test_override_reuses_the_existing_sum_to_100_validator(bad: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        TRWConfig.model_validate({"nudge_pool_weights": bad})


def test_override_field_is_typed_as_the_existing_model() -> None:
    """NFR01: the field's type IS NudgePoolWeights, so no second validator exists."""
    field = TRWConfig.model_fields["nudge_pool_weights"]
    assert field.default is None
    assert NudgePoolWeights in getattr(field.annotation, "__args__", ())


# --- served path: .trw/config.yaml -> append_ceremony_status -> select_pool ----------


def _served_pool_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    config_yaml: str,
    task_tuple: tuple[int, int, int, int] | None,
) -> tuple[Counter[str], list[NudgePoolWeights]]:
    """Drive ``append_ceremony_status`` N times; return pool counts + weights used."""
    import trw_mcp.state._nudge_rules as nudge_rules
    import trw_mcp.state._paths as paths_mod
    import trw_mcp.state.ceremony_nudge as ceremony_nudge
    import trw_mcp.tools._ceremony_status as ceremony_status

    monkeypatch.setenv("MEMORY_DAEMON_AUTOSTART", "false")
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")

    run_dir: Path | None = None
    if task_tuple is not None:
        run_dir = tmp_path / "run"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: r1\ntask_profile:\n  nudge_pool_weights: [" + ", ".join(map(str, task_tuple)) + "]\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(paths_mod, "get_pinned_run", lambda **_: run_dir)

    monkeypatch.setattr(nudge_rules, "_RNG", random.Random(_SEED))
    monkeypatch.setattr(nudge_rules, "resolve_pool_cooldown", lambda *_a, **_k: False)
    # Content is irrelevant to the distribution; None keeps each trial cheap.
    monkeypatch.setattr(ceremony_status, "resolve_pool_content", lambda *_a, **_k: None)

    used: list[NudgePoolWeights] = []
    real_select = ceremony_nudge._select_nudge_pool

    def _spy(state, weights, *args, **kwargs):  # type: ignore[no-untyped-def]
        used.append(weights)
        return real_select(state, weights, *args, **kwargs)

    monkeypatch.setattr(ceremony_nudge, "_select_nudge_pool", _spy)

    counts: Counter[str] = Counter()
    real_pool = ceremony_status.select_pool

    def _record_pool(*args, **kwargs):  # type: ignore[no-untyped-def]
        pool = real_pool(*args, **kwargs)
        if pool:
            counts[pool] += 1
        return pool

    monkeypatch.setattr(ceremony_status, "select_pool", _record_pool)
    for _ in range(_TRIALS):
        ceremony_status.append_ceremony_status({}, trw_dir=trw_dir, context=None)
    assert sum(counts.values()) == _TRIALS
    return counts, used


_OVERRIDE_YAML = (
    "nudge_enabled: true\ntarget_platforms: [claude-code]\n"
    "nudge_pool_weights:\n  workflow: 10\n  learnings: 20\n  ceremony: 30\n  context: 40\n"
)
_PLAIN_YAML = "nudge_enabled: true\ntarget_platforms: [claude-code]\n"


def test_override_changes_routing_distribution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR01 + R8 T2(7): a project config override governs the served distribution,
    beating a pinned run's task_profile tuple (tier 1 > tier 2)."""
    counts, _used = _served_pool_counts(tmp_path, monkeypatch, config_yaml=_OVERRIDE_YAML, task_tuple=_TASK_TUPLE)
    assert _chi_square(counts, _OVERRIDE) < _CHI2_CRIT_DF3, counts
    assert _chi_square(counts, _TASK_TUPLE) > _CHI2_CRIT_DF3, counts
    assert _chi_square(counts, _DEFAULT) > _CHI2_CRIT_DF3, counts


@pytest.mark.parametrize(
    ("config_yaml", "expected"),
    [
        pytest.param(_PLAIN_YAML, _TASK_TUPLE, id="no-override-task-profile-governs"),
        pytest.param(_OVERRIDE_YAML, _OVERRIDE, id="override-governs-both"),
    ],
)
def test_displayed_weights_equal_effective_weights(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config_yaml: str,
    expected: tuple[int, int, int, int],
) -> None:
    """FR04: select_pool routes by the effective tuple, and trw_status displays that same tuple."""
    import trw_mcp.models.config as config_mod
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._orchestration_status_assembly import assemble_status_result

    counts, used = _served_pool_counts(tmp_path, monkeypatch, config_yaml=config_yaml, task_tuple=_TASK_TUPLE)
    assert _chi_square(counts, expected) < _CHI2_CRIT_DF3, counts
    assert _chi_square(counts, _DEFAULT) > _CHI2_CRIT_DF3, counts
    assert {w.model_dump() == _as_dict(expected) for w in used} == {True}

    status_cfg = config_mod.TRWConfig(**FileStateReader().read_yaml(tmp_path / ".trw" / "config.yaml"))
    monkeypatch.setattr(config_mod, "get_config", lambda: status_cfg)
    state_data: dict[str, object] = {
        "run_id": "r1",
        "task_profile": {"nudge_pool_weights": list(_TASK_TUPLE)},
    }
    result = assemble_status_result(state_data, [], tmp_path / "run", FileStateReader(), tmp_path / "meta")
    assert result["nudge_pool_weights"] == used[0].model_dump() == _as_dict(expected)


# --- FR03: unset override + no task profile == pre-change routing -------------------


def test_no_override_no_task_profile_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR03: the served sequence equals the pre-change direct client-profile draw, same seed."""
    import trw_mcp.state._nudge_rules as nudge_rules
    from trw_mcp.state.ceremony_progress import CeremonyState

    cfg = TRWConfig.model_validate({"target_platforms": ["claude-code"]})
    assert cfg.effective_nudge_pool_weights(None) == cfg.client_profile.nudge_pool_weights

    counts, used = _served_pool_counts(tmp_path, monkeypatch, config_yaml=_PLAIN_YAML, task_tuple=None)
    assert {w == cfg.client_profile.nudge_pool_weights for w in used} == {True}

    # Pre-change behaviour: select_pool passed client_profile.nudge_pool_weights straight through.
    monkeypatch.setattr(nudge_rules, "_RNG", random.Random(_SEED))
    state = CeremonyState(session_started=True, phase="implement")
    baseline = Counter(
        nudge_rules._select_nudge_pool(state, cfg.client_profile.nudge_pool_weights, None) for _ in range(_TRIALS)
    )
    assert counts == baseline


# --- FR02: census of direct client_profile.nudge_pool_weights reads ------------------


_CONFIG_RECEIVERS = frozenset({"self", "cfg", "config"})


def _pool_weight_read_sites(source: str, rel: str) -> set[tuple[str, str]]:
    """``(file, enclosing function)`` for every ``<x>.nudge_pool_weights`` load.

    Excluded: a bare TRWConfig receiver (``self``/``cfg``/``config``), which reads
    the PRD-CORE-335 project override, not the client profile. Every other base --
    ``cfg.client_profile``, ``profile``, ... -- is a client-profile read."""
    tree = ast.parse(source)
    sites: set[tuple[str, str]] = set()

    def visit(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if (
                isinstance(child, ast.Attribute)
                and child.attr == "nudge_pool_weights"
                and isinstance(child.ctx, ast.Load)
                and not (isinstance(child.value, ast.Name) and child.value.id in _CONFIG_RECEIVERS)
            ):
                sites.add((rel, func))
            visit(child, name)

    visit(tree, "<module>")
    return sites


def _census() -> set[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC).as_posix()
        sites |= _pool_weight_read_sites(path.read_text(encoding="utf-8"), rel)
    return sites


_ACCEPTED_SITES = {
    # runtime: select_pool's only weights source, tier 3 of FR01
    ("models/config/_main.py", "effective_nudge_pool_weights"),
    # documentation-table generation only; never affects routing
    ("client_profiles/catalog.py", "_format_pool_weights"),
}


def test_exactly_two_read_sites() -> None:
    assert _census() == _ACCEPTED_SITES


def test_select_pool_reads_through_the_resolution_method() -> None:
    tree = source_index.tree(_SRC / "tools" / "_ceremony_status_pool.py")
    select_pool = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "select_pool")
    calls = {
        n.func.attr for n in ast.walk(select_pool) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "effective_nudge_pool_weights" in calls


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        pytest.param(
            "def rogue(cfg):\n    return cfg.client_profile.nudge_pool_weights\n",
            {("x.py", "rogue")},
            id="third-direct-read-is-caught",
        ),
        pytest.param(
            "def docs(profile):\n    return profile.nudge_pool_weights\n",
            {("x.py", "docs")},
            id="bare-profile-read-is-caught",
        ),
        pytest.param("def ok(self):\n    return self.nudge_pool_weights\n", set(), id="override-self-read-ignored"),
        pytest.param("def ok(cfg):\n    return cfg.nudge_pool_weights\n", set(), id="override-cfg-read-ignored"),
        pytest.param("W = {'nudge_pool_weights': 1}\n", set(), id="string-key-ignored"),
    ],
)
def test_census_detects_a_third_read_site(snippet: str, expected: set[tuple[str, str]]) -> None:
    assert _pool_weight_read_sites(snippet, "x.py") == expected

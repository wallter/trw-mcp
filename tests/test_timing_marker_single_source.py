"""PRD-QUAL-141 FR01/FR04: one host-resource marker, and nothing deterministic hides behind it.

``requires_local_timing`` tests are skipped on CI runners, so an ordinary ``assert`` inside one
leaves the gating suite. The rules, checked over every test file of this package (of the sibling
trw-memory in the monorepo, and of the monorepo's own repo-root ``scripts/tests/`` gate suite,
which shares the root ``pyproject.toml`` and its own ``tests/_timing.py`` mirror):

1. ``requires_local_timing`` is a registered marker and ``perf`` is gone (one marker, not two).
2. A marked test asserts only through ``assert_budget``: no bare ``assert``.
3. ``assert_budget`` is called only from marked tests (a budget outside one would gate CI).
4. An unmarked test does not compare a measured duration/latency/RSS against a fixed ceiling,
   unless it is a correctness deadline listed in ``CORRECTNESS_DEADLINES`` with its reason.

Each rule is a function over source text, and the negative fixtures below prove each one fails.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT
from tests._timing import MARKER, apply_timing_policy, assert_budget

_PACKAGES = [PACKAGE_ROOT] + ([MONOREPO_ROOT / "trw-memory"] if MONOREPO_ROOT is not None else [])

#: (label, tests dir, pyproject.toml with the markers list) for every suite the rules below cover.
#: trw-mcp and trw-memory each own a package-scoped ``tests/`` next to their own ``pyproject.toml``;
#: the monorepo's ``scripts/tests/`` gate suite instead shares the repo-root ``pyproject.toml``, so
#: it cannot use the ``pkg / "tests"`` + ``pkg / "pyproject.toml"`` convention the packages share.
_SUITES: list[tuple[str, Path, Path]] = [(pkg.name, pkg / "tests", pkg / "pyproject.toml") for pkg in _PACKAGES]
if MONOREPO_ROOT is not None:
    _SUITES.append(("scripts", MONOREPO_ROOT / "scripts" / "tests", MONOREPO_ROOT / "pyproject.toml"))

#: Unmarked tests whose bound is a correctness deadline (a hang, a timeout contract), not a
#: host-resource budget. Reviewed by hand; each entry says why the bound stays gating.
CORRECTNESS_DEADLINES: dict[str, str] = {
    "scripts/test_suite_lock.py::test_stale_lock_reclaimed_within_poll_interval": (
        "a dead holder's lock is reclaimed on the next poll instead of blocking every later suite; 2 s is a hang detector"
    ),
    "test_bootstrap_claude_md_sync_split.py::TestClaudeMdSyncTimeoutFix::test_sync_timeout_returns_promptly": (
        "the sync must give up instead of hanging; 10 s is a hang detector"
    ),
    "comms/test_two_stdio_members.py::test_bounded_wait_on_one_real_process_observes_a_message_sent_by_another": (
        "the message must arrive before the wait's own deadline, not by exhausting it"
    ),
    "test_boot_sequence_deferral.py::test_start_boot_sequence_deferred_returns_before_slow_sweep_completes": (
        "non-blocking: the caller returns before a sweep stubbed to block 5 s (bound is half of that)"
    ),
    "test_git_commit_hooks.py::TestBlockingHookTimeout::test_hanging_hook_times_out_and_fails_closed": (
        "a hanging hook is interrupted and fails closed instead of wedging the commit"
    ),
    "test_heartbeat.py::TestHeartbeatAwareStaleness::test_get_last_activity_uses_heartbeat": (
        "the returned activity time is the heartbeat just written (freshness of a value, not speed)"
    ),
    "test_heartbeat.py::TestHeartbeatAwareStaleness::test_get_last_activity_heartbeat_only_no_checkpoints": (
        "the returned activity time is the heartbeat just written (freshness of a value, not speed)"
    ),
    "test_heartbeat_and_adopt.py::test_heartbeat_returns_stale_after_ts": (
        "date arithmetic tolerance on a computed staleness, not a measured duration"
    ),
    "test_installer_process_subprocess.py::TestDetectInstalledExtras::test_uses_short_timeout": (
        "the short probe timeout is applied instead of the 120 s default"
    ),
    "test_intent_contract_round5_hooks.py::test_fa_a_lib_that_refuses_to_return_cannot_stop_the_hook_blocking": (
        "a library that never returns cannot stop the hook from blocking (bypass prevention)"
    ),
    "test_prd_validate_budget.py::test_refresh_tiny_budget_returns_partial_shape_no_hang": (
        "a tiny budget yields a partial result instead of hanging"
    ),
    "test_sync_concurrency.py::test_pull_does_not_block_concurrent_coroutine": (
        "non-blocking: a blocked loop would delay the fast coroutine the full 1 s (bound is half of that)"
    ),
    "test_sync_concurrency.py::test_push_does_not_block_concurrent_coroutine": (
        "non-blocking: a blocked loop would delay the fast coroutine the full 1 s (bound is half of that)"
    ),
    "test_telemetry_pipeline_lifecycle.py::TestStopDrain::test_stop_timeout_returns_within_bound": (
        "stop(timeout=...) bounds a flush that would otherwise hang"
    ),
    "unit/meta_tune/test_sandbox.py::test_sandbox_timeout_via_signal_alarm": (
        "SIGALRM cancels a runaway sandbox call instead of letting it run"
    ),
    "trw-memory/test_daemon_offload.py::test_shutdown_is_bounded_when_a_worker_will_not_stop": (
        "shutdown returns even when a worker refuses to stop"
    ),
}

_MEASURED = re.compile(
    r"(elapsed|duration|latency|\bp50\b|\bp95\b|\bp99\b|_ms\b|_seconds\b|\brss\b|per_1000|wall_|took)", re.IGNORECASE
)
_CEILING_OPS = (ast.Lt, ast.LtE)
_FLOOR_OPS = (ast.Gt, ast.GtE)


def _is_marker(node: ast.expr) -> bool:
    text = ast.unparse(node)
    return MARKER in text


def _tests(tree: ast.Module) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, bool]]:
    """(qualified name, function, marked) for every test function in a module."""
    module_marked = any(
        isinstance(n, ast.Assign)
        and any(getattr(t, "id", "") == "pytestmark" for t in n.targets)
        and _is_marker(n.value)
        for n in tree.body
    )
    out: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef, bool]] = []

    def visit(nodes: list[ast.stmt], prefix: str, marked: bool) -> None:
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}::", marked or any(map(_is_marker, node.decorator_list)))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test"):
                out.append((prefix + node.name, node, marked or any(map(_is_marker, node.decorator_list))))

    visit(tree.body, "", module_marked)
    return out


def marked_tests_with_bare_asserts(source: str) -> list[str]:
    return [
        name
        for name, fn, marked in _tests(ast.parse(source))
        if marked and any(isinstance(n, ast.Assert) for n in ast.walk(fn))
    ]


def unmarked_budget_calls(source: str) -> list[str]:
    return [
        name
        for name, fn, marked in _tests(ast.parse(source))
        if not marked
        and any(isinstance(n, ast.Call) and ast.unparse(n.func).endswith("assert_budget") for n in ast.walk(fn))
    ]


def _is_fixed_bound(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool):
        return node.value > 0
    return bool(re.search(r"(budget|limit|threshold|ceiling|max_|_max)", ast.unparse(node), re.IGNORECASE))


def unmarked_fixed_ceilings(source: str) -> list[str]:
    hits: list[str] = []
    for name, fn, marked in _tests(ast.parse(source)):
        if marked:
            continue
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare) and len(node.test.ops) == 1):
                continue
            left, op, right = node.test.left, node.test.ops[0], node.test.comparators[0]
            if isinstance(op, _FLOOR_OPS):
                left, right = right, left
            elif not isinstance(op, _CEILING_OPS):
                continue
            if _MEASURED.search(ast.unparse(left)) and _is_fixed_bound(right):
                hits.append(name)
                break
    return hits


def _test_files() -> list[tuple[str, Path]]:
    """(key, file) for every test module; the key is the path under tests/, the sibling prefixed."""
    return [
        (("" if label == PACKAGE_ROOT.name else f"{label}/") + f.relative_to(tests_dir).as_posix(), f)
        for label, tests_dir, _pyproject in _SUITES
        for f in sorted(tests_dir.rglob("test_*.py"))
    ]


def _key(file_key: str, _path: Path, test: str) -> str:
    return f"{file_key}::{test}"


def _markers(pyproject: Path) -> list[str]:
    """The ``[tool.pytest.ini_options] markers`` names, without a TOML parser (py3.10 floor)."""
    text = pyproject.read_text(encoding="utf-8")
    block = text.split("markers = [", 1)[1].split("]", 1)[0]
    return [line.strip().strip('",').split(":", 1)[0] for line in block.splitlines() if line.strip().startswith('"')]


# -- rule 1: one registered marker -------------------------------------------------------------


@pytest.mark.parametrize(("label", "tests_dir", "pyproject"), _SUITES, ids=[label for label, _, _ in _SUITES])
def test_marker_is_registered_and_perf_is_gone(label: str, tests_dir: Path, pyproject: Path) -> None:
    markers = _markers(pyproject)
    assert MARKER in markers
    assert "perf" not in markers
    users = [f.name for f in tests_dir.rglob("*.py") if re.search(r"mark\.perf\b", f.read_text(encoding="utf-8"))]
    assert users == []


# -- rules 2-4 over the real suites ------------------------------------------------------------


def test_marked_tests_assert_only_through_assert_budget() -> None:
    offenders = [
        _key(p, f, t) for p, f in _test_files() for t in marked_tests_with_bare_asserts(f.read_text(encoding="utf-8"))
    ]
    assert offenders == [], "a marked test is skipped on CI, so its bare asserts leave the gate; split it"


def test_assert_budget_is_only_called_from_marked_tests() -> None:
    offenders = [_key(p, f, t) for p, f in _test_files() for t in unmarked_budget_calls(f.read_text(encoding="utf-8"))]
    assert offenders == [], "a budget in an unmarked test gates CI on runner speed; mark it requires_local_timing"


def test_no_unmarked_fixed_ceiling_on_a_measured_value() -> None:
    offenders = [
        key
        for p, f in _test_files()
        for t in unmarked_fixed_ceilings(f.read_text(encoding="utf-8"))
        if (key := _key(p, f, t)) not in CORRECTNESS_DEADLINES
    ]
    assert offenders == [], (
        "unmarked budget on a measured value: move it to a requires_local_timing test via assert_budget, "
        "or, if it is a correctness deadline, list it in CORRECTNESS_DEADLINES with the reason"
    )


def test_correctness_deadlines_still_exist() -> None:
    names = {_key(p, f, t) for p, f in _test_files() for t, _, _ in _tests(ast.parse(f.read_text(encoding="utf-8")))}
    # Only the suites present are checked: the public layout ships trw-mcp alone, so an entry
    # for a sibling suite (``trw-memory/``, ``scripts/``) has nothing to be checked against there.
    absent = {"trw-memory", "scripts"} - {label for label, _, _ in _SUITES}
    present = {k for k in CORRECTNESS_DEADLINES if k.split("/", 1)[0] not in absent}
    assert present <= names, "a listed deadline no longer exists; drop it from the list"


# -- negative fixtures: each rule fails when it should -----------------------------------------

_MARKED_WITH_ASSERT = """
import pytest
@pytest.mark.requires_local_timing
def test_x():
    assert result == 3
"""
_MARKED_CLEAN = """
import pytest
@pytest.mark.requires_local_timing
def test_x_budget():
    assert_budget("op", elapsed, 1.0, "s")
"""
_UNMARKED_BUDGET_CALL = """
def test_x():
    assert_budget("op", elapsed, 1.0, "s")
"""
_UNMARKED_CEILING = """
def test_x():
    assert elapsed_ms < 250
"""
_UNMARKED_FLOOR_ONLY = """
def test_x():
    assert payload["latency_ms"] >= 0
"""
_CLASS_MARKED = """
import pytest
@pytest.mark.requires_local_timing
class TestX:
    def test_y(self):
        assert items == []
"""


def test_negative_fixtures() -> None:
    assert marked_tests_with_bare_asserts(_MARKED_WITH_ASSERT) == ["test_x"]
    assert marked_tests_with_bare_asserts(_CLASS_MARKED) == ["TestX::test_y"]
    assert marked_tests_with_bare_asserts(_MARKED_CLEAN) == []
    assert unmarked_budget_calls(_UNMARKED_BUDGET_CALL) == ["test_x"]
    assert unmarked_budget_calls(_MARKED_CLEAN) == []
    assert unmarked_fixed_ceilings(_UNMARKED_CEILING) == ["test_x"]
    assert unmarked_fixed_ceilings(_UNMARKED_FLOOR_ONLY) == []
    assert unmarked_fixed_ceilings(_MARKED_CLEAN) == []


# -- FR04: the policy, and a real selected-and-executed run --------------------------------------


def _item(marked: bool) -> SimpleNamespace:
    added: list[object] = []
    return SimpleNamespace(
        get_closest_marker=lambda name: object() if marked and name == MARKER else None,
        add_marker=added.append,
        added=added,
    )


@pytest.mark.parametrize(("env", "skipped"), [({}, False), ({"CI": "true"}, True), ({"GITHUB_ACTIONS": "true"}, True)])
def test_policy_skips_marked_items_only_on_a_ci_runner(
    env: dict[str, str], skipped: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    marked, plain = _item(True), _item(False)

    apply_timing_policy([marked, plain])  # type: ignore[list-item]

    assert bool(marked.added) is skipped
    assert plain.added == []


@pytest.mark.requires_local_timing
def test_smoke_budget() -> None:
    """The one marked test the FR04 run below selects; its budget is trivially met."""
    assert_budget("smoke", 0.0, 1.0, "s")


@pytest.mark.parametrize(("ci", "expected"), [(False, "1 passed"), (True, "1 skipped")])
@pytest.mark.timeout(330)  # 30 s of margin above the nested subprocess.run(timeout=300) below --
# a tie would let the outer pytest-timeout race the inner subprocess timeout instead of always
# giving the inner one first say. The package-wide --timeout=120 default is a hang detector, not
# this test's contract: under parallel host load a full nested `python -m pytest` boot (imports,
# conftest, collection) can legitimately take longer than 120 s without hanging.
def test_marked_set_is_selected_and_executed_with_ci_unset(ci: bool, expected: str) -> None:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CI", "GITHUB_ACTIONS", "PYTEST_ADDOPTS", "PYTEST_XDIST_WORKER")
    }
    if ci:
        env["CI"] = "true"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-q",
            "-m",
            MARKER,
            "-n",
            "0",
            f"{Path(__file__).name}::test_smoke_budget",
        ],
        cwd=Path(__file__).parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert expected in proc.stdout, proc.stdout[-2000:] + proc.stderr[-2000:]


# -- the helper copies stay one helper ---------------------------------------------------------


@pytest.mark.skipif(MONOREPO_ROOT is None, reason="the sibling copies exist only in the monorepo")
def test_every_timing_helper_copy_has_the_same_code() -> None:
    """Each suite needs its own ``_timing.py`` (the public packages ship alone), so the copies must not drift:
    everything but the module docstring is identical."""
    assert MONOREPO_ROOT is not None

    def code(path: Path) -> str:
        body = ast.parse(path.read_text(encoding="utf-8")).body
        return ast.dump(
            ast.Module(
                body=body[1:] if ast.get_docstring(ast.Module(body=body, type_ignores=[])) else body, type_ignores=[]
            )
        )

    copies = [tests_dir / "_timing.py" for _label, tests_dir, _pyproject in _SUITES]
    assert len(copies) == 3
    assert {code(c) for c in copies} == {code(copies[0])}, [c.as_posix() for c in copies]

"""PRD-CORE-320 FR02/NFR01/NFR02 — declared-edge call-chain verifier.

Fixture-package tests build a tiny synthetic tree under ``tmp_path`` and point
``repo_root`` at it (integration tier: filesystem I/O). One real-repo case
exercises an actual registered MCP tool and a helper it calls directly.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests._timing import assert_budget
from trw_mcp.state.validation.call_chain import ChainVerdict, ToolSite, verify_chain
from trw_mcp.state.validation.chain_declarations import ChainDeclaration, declared_chains

pytestmark = pytest.mark.integration

_BUDGET_SECONDS = 10.0


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


@pytest.fixture
def fixture_pkg(tmp_path: Path) -> Path:
    """A tiny ``fixturepkg`` tree with a full entry -> mid -> leaf chain wired.

    ``fixturepkg.entrypoints.do_the_thing`` is monkeypatched (via
    ``raw_registered_tool_names``) to count as a registered entry point.
    """
    _write(
        tmp_path,
        "fixturepkg/__init__.py",
        "",
    )
    _write(
        tmp_path,
        "fixturepkg/entrypoints.py",
        "from fixturepkg.middle import helper as _helper\n\n\n"
        "def register(server):\n    @server.tool()\n    def do_the_thing() -> None:\n        _helper()\n",
    )
    _write(
        tmp_path,
        "fixturepkg/middle.py",
        "import fixturepkg.leaf as leaf_module\n\n\ndef helper() -> None:\n    leaf_module.capability()\n",
    )
    _write(
        tmp_path,
        "fixturepkg/leaf.py",
        "def capability() -> None:\n    return None\n",
    )
    return tmp_path


_CHAIN = (
    "fixturepkg.entrypoints.do_the_thing",
    "fixturepkg.middle.helper",
    "fixturepkg.leaf.capability",
)


def _patch_entry_point(monkeypatch: pytest.MonkeyPatch, sites: dict[str, tuple[str, int]]) -> None:
    """Register fixture tools: name -> (defining module, first line = its ``@server.tool()`` decorator)."""
    import trw_mcp.state.validation.call_chain as call_chain_module

    registered = {name: ToolSite(module, line) for name, (module, line) in sites.items()}
    monkeypatch.setattr(call_chain_module, "registered_tool_sites", lambda: registered)


#: fixturepkg's registered tool: its decorator is line 5 of fixturepkg/entrypoints.py.
_DO_THE_THING = {"do_the_thing": ("fixturepkg.entrypoints", 5)}


def test_every_hop_verified_gives_wired_with_call_sites(fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    verdict = verify_chain(fixture_pkg, _CHAIN)
    assert verdict.status == "wired"
    assert len(verdict.call_sites) == 2
    assert verdict.call_sites[0].startswith("fixturepkg/entrypoints.py:")
    assert verdict.call_sites[1].startswith("fixturepkg/middle.py:")
    assert verdict.first_unverified_hop == ""


def test_removing_a_hop_flips_wired_to_isolated(fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    # Remove the middle -> leaf call: helper() no longer calls capability().
    _write(
        fixture_pkg,
        "fixturepkg/middle.py",
        "import fixturepkg.leaf as leaf_module\n\n\ndef helper() -> None:\n    return None\n",
    )
    verdict = verify_chain(fixture_pkg, _CHAIN)
    assert verdict.status == "isolated"
    assert "fixturepkg.middle.helper" in verdict.first_unverified_hop
    assert "fixturepkg.leaf.capability" in verdict.first_unverified_hop
    assert verdict.reason != ""
    # The first (still-verified) hop's call site is preserved.
    assert len(verdict.call_sites) == 1


def test_hop_satisfied_only_inside_a_test_file_does_not_verify(
    fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    # The real (non-test) entrypoints.py no longer calls helper(); only a test
    # file does.
    _write(
        fixture_pkg,
        "fixturepkg/entrypoints.py",
        "from fixturepkg.middle import helper as _helper\n\n\n"
        "def register(server):\n    @server.tool()\n    def do_the_thing() -> None:\n        return None\n",
    )
    _write(
        fixture_pkg,
        "tests/test_entrypoints.py",
        "from fixturepkg.middle import helper as _helper\n\n\ndef do_the_thing() -> None:\n    _helper()\n",
    )
    verdict = verify_chain(fixture_pkg, _CHAIN)
    assert verdict.status == "isolated"
    assert "fixturepkg.entrypoints.do_the_thing" in verdict.first_unverified_hop


def test_first_symbol_not_an_entry_point_gives_isolated(fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_point(monkeypatch, {"some_other_tool": ("fixturepkg.entrypoints", 5)})
    verdict = verify_chain(fixture_pkg, _CHAIN)
    assert verdict.status == "isolated"
    assert verdict.first_unverified_hop == "fixturepkg.entrypoints.do_the_thing"
    assert "entry point" in verdict.reason
    assert verdict.call_sites == ()


def test_aliased_imports_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "aliaspkg/__init__.py", "")
    _write(
        tmp_path,
        "aliaspkg/entry.py",
        "from aliaspkg.leaf import real_capability as _aliased\n\n\n"
        "def register(server):\n    @server.tool()\n    def entrypoint() -> None:\n        _aliased()\n",
    )
    _write(tmp_path, "aliaspkg/leaf.py", "def real_capability() -> None:\n    return None\n")
    _patch_entry_point(monkeypatch, {"entrypoint": ("aliaspkg.entry", 5)})
    verdict = verify_chain(tmp_path, ("aliaspkg.entry.entrypoint", "aliaspkg.leaf.real_capability"))
    assert verdict.status == "wired"
    assert len(verdict.call_sites) == 1


def test_bare_name_call_to_same_named_function_in_a_different_module_does_not_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare-name call must never be trusted as a cross-module reference.

    ``entry.py`` calls a bare ``capability()`` that is NOT imported and NOT
    defined in ``entry.py`` itself; a same-named ``capability`` function
    happens to live in an unrelated module. The hop must not verify.
    """
    _write(tmp_path, "barepkg/__init__.py", "")
    _write(
        tmp_path,
        "barepkg/entry.py",
        "def register(server):\n    @server.tool()\n    def entrypoint() -> None:\n        capability()\n",
    )
    _write(tmp_path, "barepkg/unrelated.py", "def capability() -> None:\n    return None\n")
    _write(tmp_path, "barepkg/leaf.py", "def capability() -> None:\n    return None\n")
    _patch_entry_point(monkeypatch, {"entrypoint": ("barepkg.entry", 2)})
    verdict = verify_chain(tmp_path, ("barepkg.entry.entrypoint", "barepkg.leaf.capability"))
    assert verdict.status == "isolated"
    assert verdict.call_sites == ()
    assert (
        verdict.first_unverified_hop == "barepkg.entry.entrypoint -> barepkg.leaf.capability"
    )  # the hop, not the entry


def test_cli_entry_kind_is_isolated_unsupported() -> None:
    verdict = verify_chain(Path("/nonexistent"), ("cli:trw-mcp code index", "fixturepkg.leaf.capability"))
    assert verdict.status == "isolated"
    assert "unsupported entry kind" in verdict.reason


def test_hook_entry_with_a_script_that_does_not_exist_is_isolated_not_unsupported() -> None:
    """PRD-CORE-320 FR04: hook: is a supported entry kind now (slice 3) -- a missing script names the reason."""
    verdict = verify_chain(Path("/nonexistent"), ("hook:no-such-hook", "fixturepkg.leaf.capability"))
    assert verdict.status == "isolated"
    assert "unsupported entry kind" not in verdict.reason
    assert "not found" in verdict.reason


def test_empty_chain_is_isolated_never_raises() -> None:
    verdict = verify_chain(Path("/nonexistent"), ())
    assert verdict.status == "isolated"
    assert verdict.reason != ""


def test_unresolvable_symbol_is_isolated_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    verdict = verify_chain(tmp_path, ("nope.entrypoints.do_the_thing", "nope.leaf.capability"))
    assert verdict.status == "isolated"


def test_verdict_is_a_frozen_value_type() -> None:
    verdict = ChainVerdict(status="wired", call_sites=("a.py:1",))
    with pytest.raises((AttributeError, TypeError)):
        verdict.status = "isolated"  # type: ignore[misc]


_REAL_REPO_CHAIN = (
    "trw_mcp.tools.ceremony.trw_deliver",
    "trw_mcp.tools._ceremony_deliver_tool.run_trw_deliver",
)


def test_real_repo_registered_tool_and_direct_helper_verify_as_wired() -> None:
    """The correctness half: unmarked (and thus gating on CI) so it never hides
    behind ``requires_local_timing``. NFR01's speed half lives in the
    ``requires_local_timing`` test right below, which asserts ONLY through
    ``assert_budget`` per this suite's single-marker rule."""
    src_root = Path(__file__).resolve().parents[1] / "src"
    verdict = verify_chain(src_root, _REAL_REPO_CHAIN)
    assert verdict.status == "wired", verdict.reason
    assert len(verdict.call_sites) == 1
    assert verdict.call_sites[0].startswith("trw_mcp/tools/ceremony.py:")


@pytest.mark.requires_local_timing
def test_real_repo_registered_tool_and_direct_helper_verify_under_budget() -> None:
    """NFR01: verifying a declared chain against the real repo stays under 10s."""
    src_root = Path(__file__).resolve().parents[1] / "src"
    best_elapsed = float("inf")
    for _attempt in range(3):
        started = time.monotonic()
        verify_chain(src_root, _REAL_REPO_CHAIN)
        elapsed = time.monotonic() - started
        best_elapsed = min(best_elapsed, elapsed)
    assert_budget("call_chain_wall_time", best_elapsed, _BUDGET_SECONDS, "s")


def test_a_same_named_function_that_is_not_the_registered_tool_is_not_an_entry(
    fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry is the tool's own registration site, not any function sharing its name (worker-3 review)."""
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    _write(
        fixture_pkg,
        "fixturepkg/impostor.py",
        "import fixturepkg.leaf as leaf\n\n\ndef do_the_thing() -> None:\n    leaf.capability()\n",
    )
    verdict = verify_chain(fixture_pkg, ("fixturepkg.impostor.do_the_thing", "fixturepkg.leaf.capability"))
    assert verdict.status == "isolated"
    assert verdict.first_unverified_hop == "fixturepkg.impostor.do_the_thing"


# ── sol core-320-s1 r1: each way a hop could read as wired without a live call ──────────────


def _entry_calling(body: str) -> str:
    """fixturepkg/entrypoints.py whose registered do_the_thing (decorator on line 5) runs *body*."""
    lines = "\n".join(f"        {line}" for line in body.splitlines())
    return (
        "import fixturepkg.leaf as leaf\n\n\ndef register(server):\n    @server.tool()\n    def do_the_thing() -> None:\n"
        + lines
        + "\n"
    )


_ENTRY_TO_LEAF = ("fixturepkg.entrypoints.do_the_thing", "fixturepkg.leaf.capability")


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("def unused() -> None:\n    leaf.capability()", "a call inside an uncalled closure"),
        ("lambda: leaf.capability()", "a call inside a lambda"),
        ("leaf = None\nleaf.capability()", "the imported name is rebound locally"),
        (
            "from fixturepkg.leaf import capability\ncapability = print\ncapability()",
            "an import shadowed by an assignment",
        ),
    ],
    ids=["closure", "lambda", "rebound-module", "shadowed-import"],
)
def test_a_call_that_may_not_reach_the_callee_does_not_verify(
    fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch, body: str, why: str
) -> None:
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    _write(fixture_pkg, "fixturepkg/entrypoints.py", _entry_calling(body))
    verdict = verify_chain(fixture_pkg, _ENTRY_TO_LEAF)
    assert verdict.status == "isolated", why
    assert verdict.first_unverified_hop == " -> ".join(_ENTRY_TO_LEAF)


def test_a_direct_call_through_the_module_import_verifies(fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The positive control for the cases above: the same file with a plain call is wired."""
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    _write(fixture_pkg, "fixturepkg/entrypoints.py", _entry_calling("leaf.capability()"))
    verdict = verify_chain(fixture_pkg, _ENTRY_TO_LEAF)
    assert verdict.status == "wired"
    assert verdict.call_sites == ("fixturepkg/entrypoints.py:7",)


def test_a_callee_that_does_not_exist_does_not_verify(fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    _write(fixture_pkg, "fixturepkg/entrypoints.py", _entry_calling("leaf.missing()"))
    verdict = verify_chain(fixture_pkg, ("fixturepkg.entrypoints.do_the_thing", "fixturepkg.leaf.missing"))
    assert verdict.status == "isolated"
    _write(fixture_pkg, "fixturepkg/entrypoints.py", _entry_calling("import nowhere.at_all as gone\ngone.capability()"))
    assert (
        verify_chain(fixture_pkg, ("fixturepkg.entrypoints.do_the_thing", "nowhere.at_all.capability")).status
        == "isolated"
    )


def test_a_decorated_twin_in_an_uncalled_registrar_is_not_the_registered_tool(
    fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registration site is the registered callable's own first line, not any decorated same-named def."""
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    twin = (
        _entry_calling("return None")
        + "\n\ndef never_called(server):\n    @server.tool()\n    def do_the_thing() -> None:\n        leaf.capability()\n"
    )
    _write(fixture_pkg, "fixturepkg/entrypoints.py", twin)
    verdict = verify_chain(fixture_pkg, _ENTRY_TO_LEAF)
    assert verdict.status == "isolated"
    assert verdict.first_unverified_hop == " -> ".join(_ENTRY_TO_LEAF)


@pytest.mark.parametrize("chain", [(None,), ("a.b.c", None), "fixturepkg.leaf.capability", ("a..b", "c.d"), (3,)])
def test_a_malformed_chain_is_isolated_never_raises(fixture_pkg: Path, chain: object) -> None:
    verdict = verify_chain(fixture_pkg, chain, tools={})  # type: ignore[arg-type]
    assert verdict.status == "isolated"
    assert verdict.reason


# ── sol core-320-s1 r2 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("unused = (leaf.capability() for _ in [1])", "a call inside an unconsumed generator expression"),
        ("match object():\n    case leaf:\n        leaf.capability()", "a match capture shadows the import"),
        ("match {}:\n    case {**leaf}:\n        leaf.capability()", "a mapping-rest capture shadows the import"),
        ("leaf.capability = print\nleaf.capability()", "the callee attribute is reassigned in the caller"),
    ],
    ids=["generator", "match-as", "match-rest", "attribute-rebound"],
)
def test_r2_a_call_that_may_not_reach_the_callee_does_not_verify(
    fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch, body: str, why: str
) -> None:
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    _write(fixture_pkg, "fixturepkg/entrypoints.py", _entry_calling(body))
    assert verify_chain(fixture_pkg, _ENTRY_TO_LEAF).status == "isolated", why


def test_a_callee_rebound_after_its_definition_does_not_verify(
    fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    _write(fixture_pkg, "fixturepkg/entrypoints.py", _entry_calling("leaf.capability()"))
    _write(fixture_pkg, "fixturepkg/leaf.py", "def capability() -> None:\n    return None\n\n\ncapability = print\n")
    assert verify_chain(fixture_pkg, _ENTRY_TO_LEAF).status == "isolated"


def test_a_list_comprehension_call_runs_immediately_and_verifies(
    fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: unlike a generator expression, a list comprehension runs where it is written."""
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    _write(fixture_pkg, "fixturepkg/entrypoints.py", _entry_calling("_ = [leaf.capability() for _ in [1]]"))
    assert verify_chain(fixture_pkg, _ENTRY_TO_LEAF).status == "wired"


@pytest.mark.parametrize(
    "body",
    [
        "setattr(leaf, 'capability', print)\nleaf.capability()",
        "delattr(leaf, 'capability')\nleaf.capability()",
        "assert leaf.capability() is None",
    ],
    ids=["setattr", "delattr", "assert-stripped-under-O"],
)
def test_a_dynamically_replaced_or_optimised_away_call_does_not_verify(
    fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    _write(fixture_pkg, "fixturepkg/entrypoints.py", _entry_calling(body))
    assert verify_chain(fixture_pkg, _ENTRY_TO_LEAF).status == "isolated"


# ── worker-3 review, P2 a/b/c: each written as a failing test first, beside a positive control ──


@pytest.mark.parametrize(
    "rebind_line",
    ["    setattr(leaf, 'capability', print)", "    leaf.capability = print"],
    ids=["setattr", "attribute-store"],
)
def test_p2a_a_rebind_in_an_enclosing_function_scope_blocks_verification(
    fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch, rebind_line: str
) -> None:
    """A rebind in ``register`` (the caller's ENCLOSING scope, not the caller itself) must count too."""
    _patch_entry_point(monkeypatch, {"do_the_thing": ("fixturepkg.entrypoints", 7)})
    _write(
        fixture_pkg,
        "fixturepkg/entrypoints.py",
        "import fixturepkg.leaf as leaf\n\n\n"
        "def register(server):\n"
        f"{rebind_line}\n\n"
        "    @server.tool()\n"
        "    def do_the_thing() -> None:\n"
        "        leaf.capability()\n",
    )
    verdict = verify_chain(fixture_pkg, _ENTRY_TO_LEAF)
    assert verdict.status == "isolated"


def test_p2a_positive_control_no_enclosing_rebind_verifies(fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    _write(fixture_pkg, "fixturepkg/entrypoints.py", _entry_calling("leaf.capability()"))
    assert verify_chain(fixture_pkg, _ENTRY_TO_LEAF).status == "wired"


def test_p2b_a_decorated_callee_is_isolated_ambiguous(fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A decorated callee may be wrapped or replaced; the decorator's name is named in the reason."""
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    _write(
        fixture_pkg,
        "fixturepkg/middle.py",
        "import fixturepkg.leaf as leaf_module\n\n\n"
        "def _deco(fn):\n    return fn\n\n\n"
        "@_deco\ndef helper() -> None:\n    leaf_module.capability()\n",
    )
    verdict = verify_chain(fixture_pkg, _CHAIN)
    assert verdict.status == "isolated"
    assert "decorat" in verdict.reason
    assert "_deco" in verdict.reason


def test_p2b_positive_control_undecorated_callee_verifies(fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    assert verify_chain(fixture_pkg, _CHAIN).status == "wired"


def test_p2b_a_generator_callee_is_isolated_even_when_called_directly(
    fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling a generator function only builds an iterator; the body never runs at the call site."""
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    _write(fixture_pkg, "fixturepkg/leaf.py", "def capability():\n    yield None\n")
    verdict = verify_chain(fixture_pkg, _CHAIN)
    assert verdict.status == "isolated"
    assert "generator" in verdict.reason


def test_p2b_positive_control_non_generator_callee_verifies(fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    assert verify_chain(fixture_pkg, _CHAIN).status == "wired"


def test_p2b_a_yield_inside_an_assert_still_marks_a_generator_callee(
    fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_own_nodes`` skips assert bodies (never-runs-under-`-O`); generator detection must look inside anyway.

    ``yield`` anywhere in a function's body -- even one that never executes,
    such as an assert's test -- sets the code object's generator flag at
    compile time (worker-3 review, P2 row (b)).
    """
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    _write(fixture_pkg, "fixturepkg/leaf.py", "def capability():\n    assert (yield None)\n")
    verdict = verify_chain(fixture_pkg, _CHAIN)
    assert verdict.status == "isolated"
    assert "generator" in verdict.reason


def test_p2c_an_unawaited_async_callee_does_not_verify(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unawaited async call constructs a coroutine but never runs the callee's body."""
    _write(tmp_path, "asyncpkg/__init__.py", "")
    _write(
        tmp_path,
        "asyncpkg/entry.py",
        "import asyncpkg.leaf as leaf\n\n\n"
        "def register(server):\n    @server.tool()\n    def entrypoint() -> None:\n        leaf.capability()\n",
    )
    _write(tmp_path, "asyncpkg/leaf.py", "async def capability() -> None:\n    return None\n")
    _patch_entry_point(monkeypatch, {"entrypoint": ("asyncpkg.entry", 5)})
    verdict = verify_chain(tmp_path, ("asyncpkg.entry.entrypoint", "asyncpkg.leaf.capability"))
    assert verdict.status == "isolated"


def test_p2c_positive_control_a_directly_awaited_async_callee_verifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "asyncpkg2/__init__.py", "")
    _write(
        tmp_path,
        "asyncpkg2/entry.py",
        "import asyncpkg2.leaf as leaf\n\n\n"
        "def register(server):\n    @server.tool()\n    async def entrypoint() -> None:\n        await leaf.capability()\n",
    )
    _write(tmp_path, "asyncpkg2/leaf.py", "async def capability() -> None:\n    return None\n")
    _patch_entry_point(monkeypatch, {"entrypoint": ("asyncpkg2.entry", 5)})
    verdict = verify_chain(tmp_path, ("asyncpkg2.entry.entrypoint", "asyncpkg2.leaf.capability"))
    assert verdict.status == "wired"


# ── PRD-CORE-320 FR08: declared_chains parses a PRD's traceability "Call chain" column ──


def test_declared_chains_parses_backticked_hops_keyed_by_fr_id() -> None:
    prd = "| Requirement | Call chain | Test |\n|---|---|---|\n| FR02 | `a.b.c` -> `d.e.f` | t.py |\n"
    assert declared_chains(prd) == {"FR02": ChainDeclaration(chain=("a.b.c", "d.e.f"))}


def test_declared_chains_accepts_a_hook_entry() -> None:
    prd = "| Requirement | Call chain |\n|---|---|\n| FR04 | `hook:session-start` -> `a.b.c` |\n"
    assert declared_chains(prd) == {"FR04": ChainDeclaration(chain=("hook:session-start", "a.b.c"))}


@pytest.mark.parametrize("cell", ["", "  ", "—", "-", "(planned)"])
def test_declared_chains_omits_no_claim_rows(cell: str) -> None:
    prd = f"| Requirement | Call chain | Test |\n|---|---|---|\n| FR01 | {cell} | t.py |\n"
    assert declared_chains(prd) == {}


@pytest.mark.parametrize(
    "cell",
    ["`a.b.c` -> `d.e.f", "`` -> `a.b.c`", "`a-b.c`"],
    ids=["unbalanced-backticks", "empty-hop", "non-identifier-symbol"],
)
def test_declared_chains_reports_malformed_never_omits(cell: str) -> None:
    prd = f"| Requirement | Call chain | Test |\n|---|---|---|\n| FR05 | {cell} | t.py |\n"
    result = declared_chains(prd)
    assert result["FR05"].malformed is True
    assert result["FR05"].chain == ()


def test_declared_chains_no_call_chain_column_gives_empty_mapping() -> None:
    prd = "| Requirement | Test |\n|---|---|\n| FR01 | t.py |\n"
    assert declared_chains(prd) == {}


def test_declared_chains_ignores_non_table_prose() -> None:
    prd = "Just some prose with a | pipe | in it, no table here.\n"
    assert declared_chains(prd) == {}


# ── sol core-320-s2 r1 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cell",
    ["`a.b.entry` -> missing.capability", "`a.b.entry` `c.d.cap`", "`a.b.entry` -> -> `c.d.cap`", "see `a.b.entry`"],
    ids=["unbackticked-hop", "no-arrow", "double-arrow", "prose-around"],
)
def test_a_cell_with_anything_but_backticked_hops_and_arrows_is_malformed(cell: str) -> None:
    """A hop outside backticks was silently dropped, leaving a shorter chain that could verify."""
    prd = f"| Requirement | Call chain |\n|---|---|\n| FR01 | {cell} |\n"
    assert declared_chains(prd) == {"FR01": ChainDeclaration(malformed=True)}


def test_a_single_symbol_chain_is_isolated(fixture_pkg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An entry alone names no capability, so there is no edge to verify: never wired."""
    _patch_entry_point(monkeypatch, _DO_THE_THING)
    verdict = verify_chain(fixture_pkg, ("fixturepkg.entrypoints.do_the_thing",))
    assert verdict.status == "isolated"
    assert verdict.reason

"""PRD-CORE-317-FR04: census of the five features the 2026-07-28 spec marks Deprecated.

Scans ``trw-mcp/src`` and ``trw-memory/src`` for ACTIVE use (in code, not prose)
of: Roots, Sampling, Logging via ``logging/setLevel``, HTTP+SSE transport, and
OAuth Dynamic Client Registration. Any hit routes to an Open Question naming
the spec's twelve-month deprecation window before removal (PRD-CORE-317
section 11). The census passes today (no active use of any of the five) and
is written so a future introduction of one fails loudly, not silently.

A mention in a comment or docstring is not use. Rather than a full AST walk
(the five patterns mix string literals and bare identifiers in ways an AST
pass would need per-node-kind branching for no real precision gain here),
each source line is stripped of comments and any line inside a triple-quoted
docstring is skipped before pattern matching -- the same toggle-based
docstring/comment exclusion this package's own effective-LOC gate uses
(``.claude/rules/trw-mcp-python.md`` "Module Size Gate").

NFR01: this module imports only stdlib (``pathlib``, ``re``) at test time --
never ``trw_mcp.server`` or ``trw_memory.daemon`` production modules -- so it
adds no runtime cost to either server's boot path.
"""

from __future__ import annotations

import ast
import functools
import io
import re
import tokenize
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests import _source_index as source_index

_TESTS_DIR = Path(__file__).resolve().parent
_TRW_MCP_SRC = _TESTS_DIR.parent / "src"
_TRW_MEMORY_SRC = _TESTS_DIR.parent.parent / "trw-memory" / "src"


@dataclass(frozen=True)
class _Pattern:
    regex: str
    #: why a match on this pattern means active use, not a coincidental substring.
    reason: str
    #: one line of syntactically valid Python guaranteed to match ``regex``,
    #: used by the planted-violation (non-vacuity) test below.
    example: str


#: One entry per feature the 2026-07-28 spec marks Deprecated. Patterns cover
#: both the wire-protocol method-name string and the SDK-level handler name a
#: real registration would use, so renaming one call site doesn't blind the
#: census to the other.
_FEATURE_PATTERNS: dict[str, list[_Pattern]] = {
    "Roots": [
        _Pattern(
            regex=r'["\']roots/list["\']',
            reason="the JSON-RPC method name for the deprecated roots/list request",
            example='ROOTS_METHOD = "roots/list"',
        ),
        _Pattern(
            regex=r"\blist_roots\b",
            reason="a handler function/registration named list_roots (e.g. @server.list_roots() or list_roots=...)",
            example="def list_roots():\n    pass",
        ),
    ],
    "Sampling": [
        _Pattern(
            regex=r"sampling/createMessage",
            reason="the JSON-RPC method name for the deprecated sampling/createMessage request",
            example='SAMPLING_METHOD = "sampling/createMessage"',
        ),
        _Pattern(
            regex=r"\bcreate_message\b",
            reason="a handler function/registration named create_message implementing sampling",
            example="def create_message():\n    pass",
        ),
    ],
    "Logging via logging/setLevel": [
        _Pattern(
            regex=r"logging/setLevel",
            reason="the JSON-RPC method name for the deprecated logging/setLevel request",
            example='LOGGING_METHOD = "logging/setLevel"',
        ),
        _Pattern(
            regex=r"\bset_logging_level\b",
            reason="a handler function/registration named set_logging_level",
            example="def set_logging_level():\n    pass",
        ),
    ],
    "HTTP+SSE transport": [
        _Pattern(
            regex=r"\bSseServerTransport\b",
            reason="the mcp SDK's SSE transport class",
            example="from mcp.server.sse import SseServerTransport",
        ),
        _Pattern(
            regex=r"\bsse_app\b",
            reason="FastMCP's SSE ASGI app factory",
            example="app = server.sse_app()",
        ),
        _Pattern(
            regex=r"""transport\s*=\s*["']sse["']""",
            reason="an explicit transport='sse' selection",
            example='server.run(transport="sse")',
        ),
    ],
    "OAuth Dynamic Client Registration": [
        _Pattern(
            regex=r"\bregister_client\b",
            reason="a Dynamic Client Registration handler/method named register_client",
            example="def register_client():\n    pass",
        ),
        _Pattern(
            regex=r'["\']\/register["\']',
            reason="a DCR /register endpoint path literal",
            example='DCR_PATH = "/register"',
        ),
    ],
}

_SOURCE_ROOTS = (_TRW_MCP_SRC, _TRW_MEMORY_SRC)


def _iter_code_lines(path: Path) -> Iterator[tuple[int, str]]:
    """Yield ``(line_number, code_without_comments)`` for *path*, skipping docstrings.

    Docstrings are located by the AST (the first string statement of a module, class or function) and
    comments by the tokenizer, so a closing triple quote is never mistaken for an opening one (sol r1 P2:
    a line-based skipper hid ``server.list_roots()`` after a multi-line string). A prose mention in a
    docstring or comment never counts as use; a string that is not a docstring is still scanned.
    """
    text = path.read_text(encoding="utf-8")
    doc_lines: set[int] = set()
    for node in ast.walk(ast.parse(text, filename=str(path))):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and body:
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                doc_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    comment_col = {
        tok.start[0]: tok.start[1]
        for tok in tokenize.generate_tokens(io.StringIO(text).readline)
        if tok.type == tokenize.COMMENT
    }
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        if lineno not in doc_lines:
            yield lineno, raw_line[: comment_col[lineno]] if lineno in comment_col else raw_line


def _code_lines_under(root: Path) -> list[tuple[str, int, str]]:
    """Every non-blank ``(file, line, code)`` under *root*, docstrings and comments removed."""
    if not root.is_dir():
        return []
    return [
        (str(py_file), lineno, code.strip())
        for py_file in sorted(root.rglob("*.py"))
        for lineno, code in _iter_code_lines(py_file)
        if code.strip()
    ]


def _match(lines: list[tuple[str, int, str]], pattern: _Pattern) -> list[str]:
    return [f"{path}:{lineno}: {code}" for path, lineno, code in lines if re.search(pattern.regex, code)]


def _scan_for_pattern(root: Path, pattern: _Pattern) -> list[str]:
    """Return ``"file:line: code"`` hits for *pattern* under *root*, else ``[]``."""
    return _match(_code_lines_under(root), pattern)


@functools.cache
def _source_code_lines() -> tuple[list[tuple[str, int, str]], ...]:
    """The two source trees, tokenized once per worker and shared by every feature case.

    Each case used to re-read and re-tokenize both trees once per pattern (~5-7 s a case).
    Read-only after the first call, so sharing it cannot leak state between tests.
    """
    return tuple(_code_lines_under(root) for root in _SOURCE_ROOTS)


def _scan_feature(feature: str) -> list[str]:
    return [
        hit
        for pattern in _FEATURE_PATTERNS[feature]
        for lines in _source_code_lines()
        for hit in _match(lines, pattern)
    ]


@pytest.mark.parametrize("feature", sorted(_FEATURE_PATTERNS))
def test_no_deprecated_feature_usage(feature: str) -> None:
    """The five 2026-07-28-deprecated features have no active call site today."""
    hits = _scan_feature(feature)
    reasons = "; ".join(f"{p.regex!r} ({p.reason})" for p in _FEATURE_PATTERNS[feature])
    assert not hits, (
        f"active use of deprecated feature {feature!r} found under trw-mcp/src or "
        f"trw-memory/src (PRD-CORE-317-FR04; patterns checked: {reasons}). Any hit "
        f"routes to an Open Question naming the twelve-month deprecation window "
        f"before removal. Hits:\n" + "\n".join(hits)
    )


@pytest.mark.parametrize("feature", sorted(_FEATURE_PATTERNS))
def test_scanner_flags_a_planted_use(feature: str, tmp_path: Path) -> None:
    """Non-vacuity: plant one pattern per feature and assert the scanner catches it."""
    pattern = _FEATURE_PATTERNS[feature][0]
    planted = tmp_path / "planted_use.py"
    planted.write_text(pattern.example + "\n", encoding="utf-8")

    hits = _scan_for_pattern(tmp_path, pattern)

    assert hits, f"planted example {pattern.example!r} for feature {feature!r} was not detected by the scanner"
    assert str(planted) in hits[0]


@pytest.mark.parametrize("feature", sorted(_FEATURE_PATTERNS))
def test_scanner_ignores_comment_and_docstring_mentions(feature: str, tmp_path: Path) -> None:
    """A comment/docstring mention of the same text must NOT be flagged as use."""
    pattern = _FEATURE_PATTERNS[feature][0]
    planted = tmp_path / "prose_only.py"
    # repr() keeps the docstring valid Python whatever quotes or newlines the example carries.
    one_line = pattern.example.replace("\n", " ")
    planted.write_text(
        f"{('Module docstring mentioning: ' + pattern.example)!r}\n"
        f"# comment mentioning: {one_line}\n"
        "x = 1  # unrelated code\n",
        encoding="utf-8",
    )

    hits = _scan_for_pattern(tmp_path, pattern)

    assert not hits, f"comment/docstring-only mention of {pattern.example!r} was wrongly flagged: {hits}"


#: This module's own basename plus its FR01/FR02 siblings -- NFR01 requires
#: none of the three guard/census test modules be imported from production.
_GUARD_MODULE_NAMES: frozenset[str] = frozenset(
    {
        "test_mcp_protocol_version_ceiling",
        "test_mcp_spec_2026_07_28_compat_matrix",
        "test_mcp_deprecated_feature_census",
    }
)


def _imported_module_names(py_file: Path) -> set[str]:
    """Return every dotted module name *py_file* imports, via AST (cheap and precise here).

    Unlike the regex-based feature scan above, "is this name imported" is
    exactly what ``ast.Import``/``ast.ImportFrom`` nodes represent, so there is
    no comment/docstring ambiguity to resolve by hand.
    """
    # A source file that does not parse fails the census loudly rather than being skipped as "no imports".
    tree = source_index.tree(py_file)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # ``from tests import test_x`` imports the module ``test_x``: check the imported names too, and
            # relative imports (no module) by name alone (sol r1 P2).
            if node.module:
                names.add(node.module)
            prefix = f"{node.module}." if node.module else ""
            names.update(f"{prefix}{alias.name}" for alias in node.names)
    return names


def _scan_for_guard_import(root: Path, guard_names: frozenset[str]) -> list[str]:
    """Return ``"file: imports 'dotted.module'"`` hits where the leaf name is a guard module."""
    if not root.is_dir():
        return []
    hits: list[str] = []
    for py_file in sorted(root.rglob("*.py")):
        for imported in _imported_module_names(py_file):
            if imported.rsplit(".", 1)[-1] in guard_names:
                hits.append(f"{py_file}: imports {imported!r}")
    return hits


def test_guard_tests_not_imported_from_production() -> None:
    """PRD-CORE-317-NFR01: no production module imports a guard/census test module.

    The two guard tests (FR01, FR04) -- and this matrix-loader test module
    itself -- must stay test-only so neither adds runtime cost to
    ``trw_mcp.server`` or ``trw_memory.daemon``'s boot path.
    """
    hits: list[str] = []
    for root in _SOURCE_ROOTS:
        hits.extend(_scan_for_guard_import(root, _GUARD_MODULE_NAMES))
    assert not hits, (
        "PRD-CORE-317-NFR01 violation: a production module under trw-mcp/src or "
        f"trw-memory/src imports a guard/census test module: {hits}"
    )


def test_guard_import_scan_flags_a_planted_import(tmp_path: Path) -> None:
    """Non-vacuity: a planted production import of a guard test module must be caught."""
    planted = tmp_path / "would_be_prod.py"
    planted.write_text(
        "from tests.test_mcp_protocol_version_ceiling import SUPPORTED_PROTOCOL_VERSIONS\n",
        encoding="utf-8",
    )

    hits = _scan_for_guard_import(tmp_path, _GUARD_MODULE_NAMES)

    assert hits, "planted production import of a guard test module was not detected by the scan"
    assert str(planted) in hits[0]


def test_a_call_after_a_multiline_string_is_still_scanned(tmp_path: Path) -> None:
    """Sol r1 P2: the closing triple quote of a non-docstring string must not hide the next line's code."""
    planted = tmp_path / "after_string.py"
    planted.write_text('DESCRIPTION = """Some text\n"""\nserver.list_roots()\n', encoding="utf-8")

    hits = [code for _line, code in _iter_code_lines(planted) if "list_roots" in code]

    assert hits == ["server.list_roots()"]


@pytest.mark.parametrize(
    "statement",
    [
        "from tests import test_mcp_protocol_version_ceiling",
        "from . import test_mcp_deprecated_feature_census",
        "import tests.test_mcp_spec_2026_07_28_compat_matrix",
    ],
)
def test_guard_import_scan_catches_every_import_form(statement: str, tmp_path: Path) -> None:
    """Sol r1 P2: ``from pkg import module`` and relative imports name the module in ``ImportFrom.names``."""
    (tmp_path / "prod.py").write_text(statement + "\n", encoding="utf-8")

    assert _scan_for_guard_import(tmp_path, _GUARD_MODULE_NAMES), statement


# --- PRD-CORE-313 FR02 / FR05: what the published wheel contains --------------------------------

_PACKAGE_ROOT = _TESTS_DIR.parent

#: FR05 keep decision, asserted rather than implied: an enrolled user project runs these from its
#: own ``.pre-commit-config.yaml`` (``python3 -m trw_mcp.security.intent_contract.pre_commit_check``),
#: and ``retro_compensator.scan_recent_history`` is the public entry a project may call from its own
#: test suite (this repository's standing test does; no shipped template wires it, UF-MCP-03), so they
#: are runtime surface of the shipped PRD-SEC-013 feature, not development-only gates.
_KEPT_INTENT_CONTRACT_MODULES = tuple(
    f"trw_mcp/security/intent_contract/{name}.py"
    for name in ("pre_commit_check", "pre_push_check", "weaken_edit_detector", "retro_compensator")
)


@pytest.fixture(scope="module")
def built_wheel_names(tmp_path_factory: pytest.TempPathFactory) -> frozenset[str]:
    """Build the real wheel once (``python -m build --wheel --no-isolation``) and list its members.

    ``build`` and ``hatchling`` are pinned in the ``dev`` extra for exactly this kind of fixture, so
    a missing one is a broken dev environment and fails the test rather than skipping it.
    """
    import subprocess
    import sys
    import zipfile

    out_dir = tmp_path_factory.mktemp("wheel")
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(out_dir), str(_PACKAGE_ROOT)],
        capture_output=True,
        text=True,
        timeout=110,
        check=False,
    )
    assert result.returncode == 0, f"wheel build failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    wheels = sorted(out_dir.glob("trw_mcp-*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as archive:
        return frozenset(archive.namelist())


@pytest.mark.slow
@pytest.mark.xdist_group(name="core313_built_wheel")
def test_the_delivery_io_tracer_is_not_in_the_built_wheel(built_wheel_names: frozenset[str]) -> None:
    """PRD-CORE-313-FR02: the test-only durable-write tracer lives in tests/support, not the wheel."""
    # Non-vacuity: the listing is a real trw-mcp wheel, including the tracer's production neighbour.
    assert "trw_mcp/tools/_delivery_tracer.py" in built_wheel_names
    leaked = sorted(name for name in built_wheel_names if name.endswith("_delivery_io_tracer.py"))
    assert leaked == []


@pytest.mark.slow
@pytest.mark.xdist_group(name="core313_built_wheel")
def test_no_dev_gate_module_ships_in_the_wheel(built_wheel_names: frozenset[str]) -> None:
    """PRD-CORE-313-FR05: the wiring gate and the client-profile docs renderer are out of the wheel."""
    leaked = sorted(
        name
        for name in built_wheel_names
        if name.startswith("trw_mcp/wiring/") or name == "trw_mcp/client_profiles/markdown.py"
    )
    assert leaked == []
    # The runtime pieces the move had to leave behind still ship: the AST helpers the delivery
    # call-chain verifier imports, and the intent-contract hooks enrolled projects execute.
    assert "trw_mcp/state/validation/_source_ast.py" in built_wheel_names
    assert "trw_mcp/client_profiles/catalog.py" in built_wheel_names
    missing = [name for name in _KEPT_INTENT_CONTRACT_MODULES if name not in built_wheel_names]
    assert missing == []

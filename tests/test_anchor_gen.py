"""Tests for anchor generation — regex-based symbol extraction."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.anchor_generation import generate_anchors


class TestPythonExtraction:
    def test_python_def(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text("def my_func(x: int) -> str:\n    return str(x)\n")
        result = generate_anchors([str(f)], {})
        assert len(result) == 1
        assert result[0]["symbol_name"] == "my_func"
        assert result[0]["symbol_type"] == "function"

    def test_python_class(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text("class MyClass:\n    pass\n")
        result = generate_anchors([str(f)], {})
        assert len(result) == 1
        assert result[0]["symbol_name"] == "MyClass"
        assert result[0]["symbol_type"] == "class"

    def test_python_async_def(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text("async def async_handler(): pass\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "async_handler"

    def test_python_method_indentation(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text("class Foo:\n    def bar(self): pass\n")
        result = generate_anchors([str(f)], {})
        # Should extract the class first (it appears first in patterns)
        assert result[0]["symbol_name"] == "Foo"

    def test_python_anchor_has_required_keys(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text("def my_func(): pass\n")
        result = generate_anchors([str(f)], {})
        anchor = result[0]
        assert "file" in anchor
        assert "symbol_name" in anchor
        assert "symbol_type" in anchor
        assert "signature" in anchor
        assert "line_range" in anchor

    def test_python_signature_content(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text("def process(x: int, y: str) -> bool:\n    return True\n")
        result = generate_anchors([str(f)], {})
        assert "process" in result[0]["signature"]

    def test_python_line_range(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text("# header\ndef my_func(): pass\n")
        result = generate_anchors([str(f)], {})
        line_range = result[0]["line_range"]
        assert isinstance(line_range, tuple)
        assert line_range[0] == 2  # second line


class TestTypeScriptExtraction:
    def test_ts_function(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.ts"
        f.write_text("export function handleRequest(req: Request): Response {\n}\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "handleRequest"

    def test_ts_const_typed(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.ts"
        f.write_text("export const API_URL: string = 'https://example.com'\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "API_URL"

    def test_ts_export_default(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.ts"
        f.write_text("export default class AppController {\n}\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "AppController"

    def test_tsx_file(self, tmp_path: Path) -> None:
        f = tmp_path / "Component.tsx"
        f.write_text("export function MyComponent(): JSX.Element {\n  return <div />\n}\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "MyComponent"

    def test_jsx_file(self, tmp_path: Path) -> None:
        f = tmp_path / "Component.jsx"
        f.write_text("export function Widget() {\n  return null\n}\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "Widget"

    def test_js_class(self, tmp_path: Path) -> None:
        """FR02 (:241): JavaScript class definitions are recognized."""
        f = tmp_path / "svc.js"
        f.write_text("class EventBus {\n  constructor() {}\n}\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "EventBus"
        assert result[0]["symbol_type"] == "class"


class TestGoExtraction:
    def test_go_func(self, tmp_path: Path) -> None:
        f = tmp_path / "main.go"
        f.write_text("func HandleHTTP(w http.ResponseWriter, r *http.Request) {\n}\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "HandleHTTP"

    def test_go_method_receiver(self, tmp_path: Path) -> None:
        f = tmp_path / "main.go"
        f.write_text("func (s *Server) Start() error {\n}\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "Start"


class TestRustExtraction:
    def test_rust_fn(self, tmp_path: Path) -> None:
        f = tmp_path / "lib.rs"
        f.write_text("fn process_data(input: &[u8]) -> Result<Vec<u8>, Error> {\n}\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "process_data"

    def test_rust_pub_fn(self, tmp_path: Path) -> None:
        f = tmp_path / "lib.rs"
        f.write_text("pub fn new() -> Self {\n}\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "new"

    def test_rust_impl_generic(self, tmp_path: Path) -> None:
        f = tmp_path / "lib.rs"
        f.write_text("impl<T: Clone> MyStruct<T> {\n}\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "MyStruct"

    def test_rust_impl_trait(self, tmp_path: Path) -> None:
        f = tmp_path / "lib.rs"
        f.write_text("impl Display for MyType {\n}\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "MyType"

    def test_generate_anchors_rust_struct(self, tmp_path: Path) -> None:
        """FR02 (:242-243, PRD :449): Rust struct extracts with symbol_type='class'."""
        f = tmp_path / "lib.rs"
        f.write_text("pub struct Config {\n    name: String,\n}\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "Config"
        assert result[0]["symbol_type"] == "class"

    def test_rust_pub_crate_fn(self, tmp_path: Path) -> None:
        """FR02 (:243): pub(crate) fn is recognized."""
        f = tmp_path / "lib.rs"
        f.write_text("pub(crate) fn internal_helper() {\n}\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["symbol_name"] == "internal_helper"
        assert result[0]["symbol_type"] == "function"


class TestEdgeCases:
    def test_binary_returns_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "data.py"
        f.write_bytes(b"def foo():\n\x00binary\n")
        result = generate_anchors([str(f)], {})
        assert result == []

    def test_unsupported_lang_returns_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "data.csv"
        f.write_text("a,b,c\n1,2,3\n")
        result = generate_anchors([str(f)], {})
        assert result == []

    def test_max_3_anchors(self, tmp_path: Path) -> None:
        for i in range(5):
            f = tmp_path / f"mod{i}.py"
            f.write_text(f"def func_{i}(): pass\n")
        files = [str(tmp_path / f"mod{i}.py") for i in range(5)]
        result = generate_anchors(files, {})
        assert len(result) == 3

    def test_empty_files_returns_empty(self) -> None:
        result = generate_anchors([], {})
        assert result == []

    def test_nonexistent_file_skipped(self) -> None:
        result = generate_anchors(["/nonexistent/path.py"], {})
        assert result == []

    def test_empty_py_file_skipped(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.py"
        f.write_text("")
        result = generate_anchors([str(f)], {})
        assert result == []

    def test_ranges_for_unrelated_file_ignored(self, tmp_path: Path) -> None:
        # Ranges keyed to a DIFFERENT file must not apply here; with no ranges
        # for this file the fallback (first symbol) is used and nothing raises.
        f = tmp_path / "mod.py"
        f.write_text("def hello(): pass\n")
        result = generate_anchors([str(f)], {"/some/other/file.py": [(1, 5)]})
        assert len(result) == 1
        assert result[0]["symbol_name"] == "hello"

    def test_file_path_in_anchor(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text("def func(): pass\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["file"] == str(f)

    def test_no_ranges_falls_back_to_first_symbol(self, tmp_path: Path) -> None:
        """FR02 fallback: with no changed ranges, the first symbol is emitted.

        Preserves behaviour for callers that supply no line ranges (e.g. git
        diff unavailable). A multi-symbol file yields exactly one anchor — the
        first symbol by line order.
        """
        f = tmp_path / "multi.py"
        f.write_text("def func_a(): pass\ndef func_b(): pass\ndef func_c(): pass\n")
        result = generate_anchors([str(f)], {})
        assert len(result) == 1
        assert result[0]["symbol_name"] == "func_a"


class TestNearestSymbolSelection:
    """FR02: nearest-symbol-to-changed-range selection (PRD :245-249, :259)."""

    def test_two_changed_functions_yield_two_anchors(self, tmp_path: Path) -> None:
        """FR02 acceptance (:259): a file with 2 changed functions yields 2 anchors.

        Each anchor is bound to the correct (nearest at-or-before) function.
        """
        f = tmp_path / "mod.py"
        # foo at line 1, bar at line 4
        f.write_text("def foo(x):\n    return x\n\ndef bar(y):\n    return y\n")
        # Changed line 2 (inside foo) and line 5 (inside bar).
        ranges = {str(f): [(2, 2), (5, 5)]}
        result = generate_anchors([str(f)], ranges)
        assert len(result) == 2
        names = {a["symbol_name"] for a in result}
        assert names == {"foo", "bar"}
        # Verify each anchor maps to the nearest-at-or-before definition.
        by_name = {a["symbol_name"]: a for a in result}
        assert by_name["foo"]["line_range"] == (1, 1)
        assert by_name["bar"]["line_range"] == (4, 4)

    def test_range_selects_nearest_at_or_before(self, tmp_path: Path) -> None:
        """A single changed range picks the nearest definition at-or-before it."""
        f = tmp_path / "multi.py"
        f.write_text("def func_a():\n    pass\ndef func_b():\n    pass\ndef func_c():\n    pass\n")
        # Change on line 4 — inside func_b (defined line 3).
        result = generate_anchors([str(f)], {str(f): [(4, 4)]})
        assert len(result) == 1
        assert result[0]["symbol_name"] == "func_b"

    def test_change_above_first_symbol_uses_first(self, tmp_path: Path) -> None:
        """A change before any definition still anchors to the first symbol."""
        f = tmp_path / "mod.py"
        f.write_text("# header comment\n# another\ndef only_fn():\n    pass\n")
        result = generate_anchors([str(f)], {str(f): [(1, 1)]})
        assert len(result) == 1
        assert result[0]["symbol_name"] == "only_fn"

    def test_per_file_capped_at_three(self, tmp_path: Path) -> None:
        """Even with 5 changed ranges in one file, at most 3 anchors emit."""
        f = tmp_path / "many.py"
        f.write_text("".join(f"def fn_{i}():\n    pass\n" for i in range(5)))
        # 5 ranges, each on a distinct def line (lines 1,3,5,7,9).
        ranges = {str(f): [(1, 1), (3, 3), (5, 5), (7, 7), (9, 9)]}
        result = generate_anchors([str(f)], ranges)
        assert len(result) == 3

    def test_duplicate_ranges_same_symbol_dedup(self, tmp_path: Path) -> None:
        """Multiple ranges resolving to the same symbol yield a single anchor."""
        f = tmp_path / "mod.py"
        f.write_text("def foo():\n    a = 1\n    b = 2\n    return a + b\n")
        # Two changed ranges both inside foo.
        result = generate_anchors([str(f)], {str(f): [(2, 2), (4, 4)]})
        assert len(result) == 1
        assert result[0]["symbol_name"] == "foo"

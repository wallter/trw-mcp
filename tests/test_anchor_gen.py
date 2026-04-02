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

    def test_symbol_context_unused_no_error(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text("def hello(): pass\n")
        # symbol_context is reserved; passing arbitrary data must not raise
        result = generate_anchors([str(f)], {"key": "value", "nested": [1, 2]})
        assert result[0]["symbol_name"] == "hello"

    def test_file_path_in_anchor(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text("def func(): pass\n")
        result = generate_anchors([str(f)], {})
        assert result[0]["file"] == str(f)

    def test_one_anchor_per_file(self, tmp_path: Path) -> None:
        """Only one anchor extracted per file for diversity across files."""
        f = tmp_path / "multi.py"
        f.write_text("def func_a(): pass\ndef func_b(): pass\ndef func_c(): pass\n")
        result = generate_anchors([str(f)], {})
        # Only one anchor per file
        assert len(result) == 1

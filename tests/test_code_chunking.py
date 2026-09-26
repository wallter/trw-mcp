from __future__ import annotations

from pathlib import Path

from trw_mcp.code_index.chunking import chunk_source


def test_python_chunking_extracts_deterministic_ast_symbols_with_required_metadata(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "sample.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        'class Greeter:\n    """Say hello."""\n    def greet(self) -> str:\n        return "hello"\n\n'
        'def helper(value: str) -> str:\n    """Normalize a value."""\n    return value.strip()\n',
        encoding="utf-8",
    )

    first = chunk_source(
        source.relative_to(tmp_path).as_posix(), source.read_text(encoding="utf-8"), file_sha256="a" * 64
    )
    second = chunk_source(
        source.relative_to(tmp_path).as_posix(), source.read_text(encoding="utf-8"), file_sha256="a" * 64
    )

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
    assert {(chunk.symbol_kind, chunk.symbol_name) for chunk in first} == {
        ("class", "Greeter"),
        ("function", "helper"),
    }
    helper = next(chunk for chunk in first if chunk.symbol_name == "helper")
    assert helper.path == "pkg/sample.py"
    assert helper.language == "python"
    assert helper.start_line == 6
    assert helper.end_line == 8
    assert helper.signature == "def helper(value: str) -> str:"
    assert helper.docstring_summary == "Normalize a value."
    assert helper.embedding_id is None
    assert helper.ast_available is True
    assert len(helper.text_hash) == 64
    assert "return value.strip()" in helper.text


def test_non_python_chunking_uses_bounded_fallback_chunks_without_ast(tmp_path: Path) -> None:
    source = tmp_path / "web" / "app.ts"
    source.parent.mkdir(parents=True)
    source.write_text("\n".join(f"const value{i} = {i};" for i in range(95)), encoding="utf-8")

    chunks = chunk_source(
        source.relative_to(tmp_path).as_posix(), source.read_text(encoding="utf-8"), file_sha256="b" * 64
    )

    assert len(chunks) == 2
    assert all(chunk.ast_available is False for chunk in chunks)
    assert all(chunk.symbol_kind == "fallback" for chunk in chunks)
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 80
    assert chunks[1].start_line == 81
    assert chunks[1].end_line == 95
    assert all(len(chunk.text.splitlines()) <= 80 for chunk in chunks)


def _covered(chunks: tuple[object, ...]) -> set[int]:
    return {line for chunk in chunks for line in range(chunk.start_line, chunk.end_line + 1)}  # type: ignore[attr-defined]


def test_a_script_with_constants_keeps_its_main_block_searchable(tmp_path: Path) -> None:
    """C12 (7.0.0 rc2): indexing constants suppressed the whole-file fallback, dropping the rest of the script."""
    source = tmp_path / "deploy.py"
    source.write_text(
        'import sys\nENABLED = True\n\nif __name__ == "__main__":\n    sys.exit(run_deploy_command())\n',
        encoding="utf-8",
    )

    chunks = chunk_source(
        source.relative_to(tmp_path).as_posix(), source.read_text(encoding="utf-8"), file_sha256="c" * 64
    )

    assert _covered(chunks) >= {1, 2, 4, 5}
    assert any("run_deploy_command" in chunk.text and chunk.symbol_kind == "module" for chunk in chunks)
    assert any(chunk.symbol_name == "ENABLED" and chunk.symbol_kind == "constant" for chunk in chunks)


def test_statements_between_definitions_are_indexed_as_module_runs(tmp_path: Path) -> None:
    source = tmp_path / "tool.py"
    source.write_text(
        "import os\nimport re\n\ndef helper():\n    return 1\n\nregister(helper)\nmain_entry_point()\n",
        encoding="utf-8",
    )

    chunks = chunk_source(
        source.relative_to(tmp_path).as_posix(), source.read_text(encoding="utf-8"), file_sha256="d" * 64
    )

    runs = [(c.start_line, c.end_line) for c in chunks if c.symbol_kind == "module"]
    assert runs == [(1, 2), (7, 8)], "each run of loose statements is one chunk, in order"
    assert [c.symbol_name for c in chunks if c.symbol_kind == "function"] == ["helper"]


def test_a_script_with_no_symbol_still_uses_the_whole_file_fallback(tmp_path: Path) -> None:
    source = tmp_path / "run.py"
    source.write_text('import sys\nprint("hello")\n', encoding="utf-8")

    chunks = chunk_source(
        source.relative_to(tmp_path).as_posix(), source.read_text(encoding="utf-8"), file_sha256="e" * 64
    )

    assert [c.symbol_kind for c in chunks] == ["fallback"]

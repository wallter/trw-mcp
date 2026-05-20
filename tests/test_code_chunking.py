from __future__ import annotations

from pathlib import Path

from trw_mcp.code_index.chunking import chunk_source_file


def test_python_chunking_extracts_deterministic_ast_symbols_with_required_metadata(tmp_path: Path) -> None:
    source = tmp_path / "pkg" / "sample.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        'class Greeter:\n    """Say hello."""\n    def greet(self) -> str:\n        return "hello"\n\n'
        'def helper(value: str) -> str:\n    """Normalize a value."""\n    return value.strip()\n',
        encoding="utf-8",
    )

    first = chunk_source_file(tmp_path, source, file_sha256="a" * 64)
    second = chunk_source_file(tmp_path, source, file_sha256="a" * 64)

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

    chunks = chunk_source_file(tmp_path, source, file_sha256="b" * 64)

    assert len(chunks) == 2
    assert all(chunk.ast_available is False for chunk in chunks)
    assert all(chunk.symbol_kind == "fallback" for chunk in chunks)
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 80
    assert chunks[1].start_line == 81
    assert chunks[1].end_line == 95
    assert all(len(chunk.text.splitlines()) <= 80 for chunk in chunks)

"""Tests for DRY check duplication detection (PRD-QUAL-039)."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.dry_check import (
    BlockLocation,
    DuplicatedBlock,
    _is_ignorable_line,
    find_duplicated_blocks,
    format_dry_report,
)


class TestIsIgnorableLine:
    """Lines that should be skipped during analysis."""

    def test_blank_line(self) -> None:
        assert _is_ignorable_line("") is True
        assert _is_ignorable_line("   ") is True

    def test_python_comment(self) -> None:
        assert _is_ignorable_line("# This is a comment") is True
        assert _is_ignorable_line("  # Indented comment") is True

    def test_js_comment(self) -> None:
        assert _is_ignorable_line("// JS comment") is True

    def test_block_comment(self) -> None:
        assert _is_ignorable_line("/* start") is True
        assert _is_ignorable_line(" * continuation") is True

    def test_code_line(self) -> None:
        assert _is_ignorable_line("x = 1") is False
        assert _is_ignorable_line("    return result") is False


class TestFindDuplicatedBlocks:
    """FR-1: Duplication detection function."""

    def test_detects_exact_duplicate_blocks(self, tmp_path: Path) -> None:
        """Two files with identical 5-line blocks should be detected."""
        block = "def helper():\n    a = 1\n    b = 2\n    c = a + b\n    return c\n"
        file1 = tmp_path / "file1.py"
        file2 = tmp_path / "file2.py"
        file1.write_text(f"# File 1\n{block}\ndef other1():\n    pass\n")
        file2.write_text(f"# File 2\n{block}\ndef other2():\n    pass\n")

        results = find_duplicated_blocks(
            [str(file1), str(file2)],
            min_block_size=5,
        )
        assert len(results) >= 1
        assert any(len(b.locations) >= 2 for b in results)

    def test_no_duplicates_in_unique_files(self, tmp_path: Path) -> None:
        """Files with completely different content should return no duplicates."""
        file1 = tmp_path / "unique1.py"
        file2 = tmp_path / "unique2.py"
        file1.write_text(
            "def foo():\n    x = 1\n    y = 2\n    z = 3\n    w = 4\n    return x\n",
        )
        file2.write_text(
            "def bar():\n    a = 10\n    b = 20\n    c = 30\n    d = 40\n    return a\n",
        )

        results = find_duplicated_blocks(
            [str(file1), str(file2)],
            min_block_size=5,
        )
        assert len(results) == 0

    def test_ignores_blank_lines_and_comments(self, tmp_path: Path) -> None:
        """Blank lines and comments should not count toward block size."""
        file1 = tmp_path / "blanks.py"
        file1.write_text("# comment\n\n# another comment\n\n# more\n")

        results = find_duplicated_blocks([str(file1)], min_block_size=3)
        assert len(results) == 0

    def test_respects_min_block_size(self, tmp_path: Path) -> None:
        """Blocks smaller than min_block_size should not be reported."""
        block = "a = 1\nb = 2\nc = 3\n"
        file1 = tmp_path / "small1.py"
        file2 = tmp_path / "small2.py"
        file1.write_text(block)
        file2.write_text(block)

        # min_block_size=5 should not catch 3-line blocks
        results = find_duplicated_blocks(
            [str(file1), str(file2)],
            min_block_size=5,
        )
        assert len(results) == 0

        # min_block_size=3 should catch them
        results = find_duplicated_blocks(
            [str(file1), str(file2)],
            min_block_size=3,
        )
        assert len(results) >= 1

    def test_detects_intra_file_duplication(self, tmp_path: Path) -> None:
        """Duplication within a single file should also be detected."""
        block = "result = process(data)\nvalidate(result)\nstore(result)\nlog_success(result)\nreturn result\n"
        file1 = tmp_path / "intra.py"
        file1.write_text(
            f"def func1():\n{block}\ndef func2():\n{block}\n",
        )

        results = find_duplicated_blocks([str(file1)], min_block_size=5)
        assert len(results) >= 1

    def test_handles_nonexistent_files(self, tmp_path: Path) -> None:
        """Non-existent files should be silently skipped."""
        results = find_duplicated_blocks(
            [str(tmp_path / "nonexistent.py")],
            min_block_size=5,
        )
        assert len(results) == 0

    def test_handles_empty_file_list(self) -> None:
        """Empty file list should return no results."""
        results = find_duplicated_blocks([], min_block_size=5)
        assert len(results) == 0

    def test_ignores_import_only_blocks(self, tmp_path: Path) -> None:
        """Blocks consisting entirely of imports should be ignored."""
        imports = "from pathlib import Path\nimport os\nimport sys\nimport re\nimport json\n"
        file1 = tmp_path / "imp1.py"
        file2 = tmp_path / "imp2.py"
        file1.write_text(imports + "\ndef func1():\n    pass\n")
        file2.write_text(imports + "\ndef func2():\n    pass\n")

        results = find_duplicated_blocks(
            [str(file1), str(file2)],
            min_block_size=5,
        )
        assert len(results) == 0

    def test_reports_file_paths_and_line_ranges(self, tmp_path: Path) -> None:
        """FR-1: Results must include file paths, line ranges, and content."""
        block = "x = compute()\ny = transform(x)\nz = validate(y)\nw = persist(z)\nreturn w\n"
        file1 = tmp_path / "a.py"
        file2 = tmp_path / "b.py"
        file1.write_text(block)
        file2.write_text(block)

        results = find_duplicated_blocks(
            [str(file1), str(file2)],
            min_block_size=5,
        )
        assert len(results) >= 1
        dup = results[0]
        assert dup.content  # content is non-empty
        assert dup.block_hash  # hash is non-empty
        assert len(dup.locations) >= 2
        for loc in dup.locations:
            assert loc.file_path  # file path reported
            assert loc.start_line >= 1
            assert loc.end_line >= loc.start_line

    def test_custom_ignore_patterns(self, tmp_path: Path) -> None:
        """Custom ignore patterns should filter out matching lines."""
        # All lines match the custom pattern
        block = "LOG.debug('a')\nLOG.debug('b')\nLOG.debug('c')\nLOG.debug('d')\nLOG.debug('e')\n"
        file1 = tmp_path / "log1.py"
        file2 = tmp_path / "log2.py"
        file1.write_text(block)
        file2.write_text(block)

        results = find_duplicated_blocks(
            [str(file1), str(file2)],
            min_block_size=5,
            ignore_patterns=[r"^LOG\.debug"],
        )
        assert len(results) == 0


class TestFormatDryReport:
    """Report formatting."""

    def test_no_blocks_message(self) -> None:
        report = format_dry_report([])
        assert "No duplicated blocks found" in report

    def test_formats_blocks_with_locations(self) -> None:
        block = DuplicatedBlock(
            content="x = 1\ny = 2\nz = 3\nw = 4\nv = 5",
            block_hash="abc123",
            locations=[
                BlockLocation(
                    file_path="file1.py",
                    start_line=10,
                    end_line=14,
                ),
                BlockLocation(
                    file_path="file2.py",
                    start_line=20,
                    end_line=24,
                ),
            ],
        )
        report = format_dry_report([block])
        assert "2 occurrences" in report
        assert "file1.py" in report
        assert "file2.py" in report
        assert "abc123" in report

    def test_respects_max_blocks(self) -> None:
        blocks = [
            DuplicatedBlock(
                content=f"block {i}",
                block_hash=f"hash{i}",
                locations=[
                    BlockLocation(
                        file_path="f.py",
                        start_line=i,
                        end_line=i + 4,
                    ),
                    BlockLocation(
                        file_path="g.py",
                        start_line=i,
                        end_line=i + 4,
                    ),
                ],
            )
            for i in range(20)
        ]
        report = format_dry_report(blocks, max_blocks=3)
        assert "17 more blocks" in report

    def test_includes_code_block_in_report(self) -> None:
        block = DuplicatedBlock(
            content="x = 1\ny = 2",
            block_hash="deadbeef",
            locations=[
                BlockLocation(file_path="a.py", start_line=1, end_line=2),
                BlockLocation(file_path="b.py", start_line=1, end_line=2),
            ],
        )
        report = format_dry_report([block])
        assert "```" in report
        assert "x = 1" in report

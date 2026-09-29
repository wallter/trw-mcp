"""PRD-CORE-316 FR04 -- the B71-108 r1-r4 sol-review edge cases, first-class against the module.

B71-108's fix (already shipped in 7.0.1) took five separate review rounds because there was no
single module whose contract each edge case could be checked against, and every prior test proved
them only indirectly through ``update_code_index``. These four tests are the DoD this PRD names:
each is written to fail if its corresponding guard in ``discover_indexable_files``/``_Walk`` is
reverted (verified in review by reverting each guard one at a time and confirming the matching test
red) and to pass on HEAD without any guard reverted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.code_index.bounds import CodeIndexBounds, Deadline, IndexBoundExceeded
from trw_mcp.code_index.discovery import discover_indexable_files


def _write(path: Path, body: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, bytes):
        path.write_bytes(body)
    else:
        path.write_text(body, encoding="utf-8")


def test_b71_108_r1_out_of_scope_rows_are_rediscovered_through_filters_and_budgets(tmp_path: Path) -> None:
    """r1: an out-of-scope manifest row (``files=``) gets every walk filter and budget applied
    directly, not only observed indirectly through ``update_code_index``."""
    _write(tmp_path / "included.py", "kept\n")
    _write(tmp_path / "wrong_ext.txt", "print('py')\n")  # extension filter
    _write(tmp_path / "binary.py", b"abc\x00def")  # binary sniff
    _write(tmp_path / "nested" / ".git", "gitdir: ../modules/nested\n")
    _write(tmp_path / "nested" / "inner.py", "inside a nested checkout\n")  # nested-checkout marker
    _write(tmp_path / "node_modules" / "file.py", "excluded by dirname\n")  # excluded dir

    result = discover_indexable_files(
        tmp_path,
        paths=["included.py"],
        files=("wrong_ext.txt", "binary.py", "nested/inner.py", "node_modules/file.py"),
        include_extensions=frozenset({".py"}),
    )

    relative = {path.relative_to(tmp_path).as_posix() for path in result.files}
    assert relative == {"included.py"}

    # The entry-count budget applies to out-of-scope rows too: each `files=` entry is charged once.
    with pytest.raises(IndexBoundExceeded):
        discover_indexable_files(
            tmp_path,
            paths=["included.py"],
            files=("wrong_ext.txt", "binary.py", "nested/inner.py", "node_modules/file.py"),
            bounds=CodeIndexBounds(build_max_entries=1),
        )

    # The indexed-file-count budget applies too: two eligible out-of-scope files exceed a cap of one.
    _write(tmp_path / "second.py", "also kept\n")
    with pytest.raises(IndexBoundExceeded):
        discover_indexable_files(
            tmp_path,
            paths=[],
            files=("included.py", "second.py"),
            bounds=CodeIndexBounds(build_max_files=1),
        )

    # And the source-byte budget: two out-of-scope files together cross a tiny byte cap.
    with pytest.raises(IndexBoundExceeded):
        discover_indexable_files(
            tmp_path,
            paths=[],
            files=("included.py", "second.py"),
            bounds=CodeIndexBounds(build_max_source_bytes=1),
        )


def test_b71_108_r2_a_non_file_out_of_scope_row_is_dropped_not_walked(tmp_path: Path) -> None:
    """r2: an out-of-scope row whose path is now a directory is dropped (counted, never walked)."""
    was_a_file = tmp_path / "was_a_file"
    was_a_file.mkdir()
    _write(was_a_file / "inner.py", "should never appear via the swapped row\n")

    result = discover_indexable_files(tmp_path, paths=[], files=("was_a_file",))

    assert result.files == ()

    # Still charged to the entry budget (dropped, not free): a second out-of-scope row crosses a cap of one.
    with pytest.raises(IndexBoundExceeded):
        discover_indexable_files(
            tmp_path, paths=[], files=("was_a_file", "was_a_file"), bounds=CodeIndexBounds(build_max_entries=1)
        )


def test_b71_108_r3_a_file_to_directory_swap_is_never_walked(tmp_path: Path) -> None:
    """r3: a file->directory swap between the pre-check and discovery never widens a scoped update
    past its named path -- the swapped entry is a file-only limit under the entry budget and
    deadline, never walked as a directory, even when it now contains real, indexable source files.
    """
    swapped = tmp_path / "was_file_now_dir"
    swapped.mkdir()
    _write(swapped / "a.py", "one\n")
    _write(swapped / "b.py", "two\n")
    _write(swapped / "c.py", "three\n")

    result = discover_indexable_files(tmp_path, paths=[], files=("was_file_now_dir",))

    assert result.files == (), "none of a.py/b.py/c.py may leak in through the swapped directory"

    # The deadline applies to a file-only limit exactly as it does to the walk: an already-expired
    # deadline refuses even this single-entry lookup, never silently widening past it instead.
    expired = Deadline(0.0, "build_timeout_seconds")
    with pytest.raises(IndexBoundExceeded):
        discover_indexable_files(tmp_path, paths=[], files=("was_file_now_dir",), deadline=expired)


def test_b71_108_r4_a_manifest_row_with_whitespace_resolves_verbatim(tmp_path: Path) -> None:
    """r4: a manifest row with a leading/trailing space is looked up by its exact bytes, never
    stripped or otherwise normalized before the file-only lookup."""
    verbatim_name = " a.py"
    _write(tmp_path / verbatim_name, "found via the exact row bytes\n")
    stripped_name = "a.py"  # if the row were incorrectly stripped, THIS file would be found instead
    _write(tmp_path / stripped_name, "must never be reached through the whitespace row\n")

    result = discover_indexable_files(tmp_path, paths=[], files=(verbatim_name,))

    relative = {path.relative_to(tmp_path).as_posix() for path in result.files}
    assert relative == {verbatim_name}
    assert stripped_name not in relative


def test_a_file_only_limit_is_never_walked_as_a_directory(tmp_path: Path) -> None:
    """PRD-CORE-316 NFR01 cross-reference: proof that an explicit path limit never widens beyond
    the caller's named scope, independent of whether the on-disk entry is a file or a directory.
    """
    directory_limit = tmp_path / "scope_target"
    directory_limit.mkdir()
    _write(directory_limit / "one.py", "one\n")
    _write(directory_limit / "two.py", "two\n")

    result = discover_indexable_files(tmp_path, paths=[], files=("scope_target",))

    assert result.files == ()

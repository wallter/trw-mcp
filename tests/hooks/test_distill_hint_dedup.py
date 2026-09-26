"""PRD-CORE-301 cut 2: session-scoped identical-hint dedup.

Both distill-hint libraries (Claude Code CC-03 and Cursor CUR-06) ship the same
``_distill_hint_already_seen`` shell helper: it dedups on the HINT'S CONTENT,
independent of the per-file time debounce those libraries already enforce. A
hint byte-identical to the last one recorded for a file this session is a
duplicate; a changed hint, or the first hint for a file, is not.

Driven as a real ``sh`` subprocess sourcing the shipped library — nothing here
reimplements the helper's logic.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_DATA = Path(__file__).resolve().parents[2] / "src" / "trw_mcp" / "data"
_CLAUDE_LIB = _DATA / "claude_code" / "hooks" / "lib-distill-hint.sh"
_CURSOR_LIB = _DATA / "hooks" / "cursor" / "lib-distill-hint.sh"
_CLAUDE_HOOK = _DATA / "claude_code" / "hooks" / "pre-tool-distill-hint.sh"
_CURSOR_HOOK = _DATA / "hooks" / "cursor" / "trw-before-edit-hint.sh"

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="sh unavailable")


def _dedup_check(lib_path: Path, project: Path, file_path: str, text: str) -> int:
    """Return the helper's exit code for one call: 0 = duplicate, 1 = new/changed."""
    driver = project / "driver.sh"
    driver.write_text(
        f'. "{lib_path}"\n_distill_hint_already_seen "{project}" "{file_path}" "{text}"\nexit $?\n',
        encoding="utf-8",
    )
    result = subprocess.run(["sh", str(driver)], capture_output=True, text=True, timeout=5, check=False)
    return result.returncode


@pytest.mark.parametrize("lib_path", [_CLAUDE_LIB, _CURSOR_LIB], ids=["claude_code", "cursor"])
def test_first_hint_for_a_file_is_never_a_duplicate(tmp_path: Path, lib_path: Path) -> None:
    rc = _dedup_check(lib_path, tmp_path, "src/a.py", "hotspot warning: high churn")
    assert rc == 1, "nothing recorded yet, so the first hint must not be suppressed"


@pytest.mark.parametrize("lib_path", [_CLAUDE_LIB, _CURSOR_LIB], ids=["claude_code", "cursor"])
def test_identical_hint_repeated_for_the_same_file_is_a_duplicate(tmp_path: Path, lib_path: Path) -> None:
    text = "hotspot warning: high churn"
    first = _dedup_check(lib_path, tmp_path, "src/a.py", text)
    second = _dedup_check(lib_path, tmp_path, "src/a.py", text)
    assert (first, second) == (1, 0)


@pytest.mark.parametrize("lib_path", [_CLAUDE_LIB, _CURSOR_LIB], ids=["claude_code", "cursor"])
def test_a_changed_hint_for_the_same_file_is_never_suppressed(tmp_path: Path, lib_path: Path) -> None:
    first = _dedup_check(lib_path, tmp_path, "src/a.py", "hotspot warning: high churn")
    second = _dedup_check(lib_path, tmp_path, "src/a.py", "hotspot warning: LOW churn now")
    assert (first, second) == (1, 1), "a changed hint must always fire, even for the same file"


@pytest.mark.parametrize("lib_path", [_CLAUDE_LIB, _CURSOR_LIB], ids=["claude_code", "cursor"])
def test_dedup_state_is_keyed_per_file_not_globally(tmp_path: Path, lib_path: Path) -> None:
    text = "hotspot warning: high churn"
    _dedup_check(lib_path, tmp_path, "src/a.py", text)
    other_file_first_hint = _dedup_check(lib_path, tmp_path, "src/b.py", text)
    assert other_file_first_hint == 1, "a different file's first hint must not be treated as a duplicate"


@pytest.mark.parametrize(
    "hook_path",
    [_CLAUDE_HOOK, _CURSOR_HOOK],
    ids=["claude_code_pre_tool_hook", "cursor_before_edit_hook"],
)
def test_hook_wires_the_dedup_helper_before_emitting(hook_path: Path) -> None:
    """Wiring guard: the shipped hook must actually call the helper, not just
    ship it in the library unused."""
    content = hook_path.read_text(encoding="utf-8")
    assert "_distill_hint_already_seen" in content

"""T29: no bundled hook extracts a JSON field with grep/sed.

A ``grep -o '"key"[[:space:]]*:[[:space:]]*"[^"]*"' | sed ...`` pipeline reads
the first matching pair anywhere in the payload, whatever object it sits in and
whether the text parses at all, so its answer is a guess presented as a value.
The hooks read JSON with jq only; with jq absent each one logs a
``jq_unavailable=1`` diagnostic, and the edit-evidence writer records
``change_evidence_unknown`` so the deliver gate blocks rather than counting zero.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

import trw_mcp.tools._delivery_helpers  # noqa: F401  (import-cycle order guard)
from tests._layout import PACKAGE_ROOT

_DATA = PACKAGE_ROOT / "src" / "trw_mcp" / "data"
#: A grep that pulls a quoted key's quoted value out of JSON text.
_EXTRACTOR = re.compile(r"""grep\s+-[A-Za-z]*o[A-Za-z]*\s+['"][^'"\n]*"[A-Za-z_]+"\[\[:space:\]\]\*:""")


def _bundled_scripts() -> list[Path]:
    return sorted(p for p in _DATA.rglob("*.sh") if p.is_file())


def test_the_extractor_pattern_recognises_the_removed_shape() -> None:
    """Non-vacuity: the scan below would have caught the parser this change deleted."""
    removed = """_x=$(printf '%s' "$_payload" | grep -o '"file_path"[[:space:]]*:[[:space:]]*"[^"]*"' | head -1)"""
    assert _EXTRACTOR.search(removed)


def test_no_bundled_hook_parses_json_with_grep() -> None:
    offenders = [
        f"{path.relative_to(_DATA)}:{number}"
        for path in _bundled_scripts()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if not line.lstrip().startswith("#") and _EXTRACTOR.search(line)
    ]
    assert offenders == [], f"shell JSON parsers remain (read with jq instead): {offenders}"


def _jq_free_path(tmp_path: Path) -> str:
    bin_dir = tmp_path / "bin-nojq"
    bin_dir.mkdir()
    for tool in ("sh", "cat", "date", "dirname", "pwd", "printf", "mkdir", "tr", "wc", "cut", "sed", "grep", "head"):
        resolved = shutil.which(tool)
        if resolved is not None:
            (bin_dir / tool).symlink_to(resolved)
    assert not (bin_dir / "jq").exists()
    return str(bin_dir)


@pytest.mark.parametrize("context_dir_exists", [True, False], ids=["context-present", "fresh-checkout"])
def test_with_jq_off_path_an_edit_reaches_the_gate_as_unknown_and_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, context_dir_exists: bool
) -> None:
    """End to end: the REAL hook on a jq-less PATH, then the REAL delivery decision.

    ``fresh-checkout`` has no ``.trw/context`` yet; the marker must still land there,
    or the gate reads the missing stream as zero changes.
    """
    from trw_mcp.tools._deliver_gate_dispatch import evaluate_delivery_gates

    root = tmp_path / "project"
    (root / ".trw").mkdir(parents=True)
    if context_dir_exists:
        (root / ".trw" / "context").mkdir()
    payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(root / "src/a.py")}, "session_id": "h"})
    completed = subprocess.run(
        ["sh", str(_DATA / "hooks" / "post-tool-event.sh")],
        input=payload,
        capture_output=True,
        text=True,
        env={"PATH": _jq_free_path(tmp_path), "CLAUDE_PROJECT_DIR": str(root), "TRW_SESSION_ID": "sess-1"},
        check=False,
    )
    assert completed.returncode == 0

    rows = [json.loads(line) for line in (root / ".trw/context/session-events.jsonl").read_text().splitlines()]
    assert [row["event"] for row in rows] == ["change_evidence_unknown"], "no guessed file_modified row"

    (root / ".trw" / "context" / "ceremony-state.json").write_text(
        json.dumps({"session_started": True, "session_build_results": {}}), encoding="utf-8"
    )
    monkeypatch.setenv("TRW_SESSION_ID", "sess-1")
    results: dict[str, Any] = {}
    assert evaluate_delivery_gates({}, cast("Any", results), [], None, root / ".trw", False, "") is True
    assert "could not be read" in str(results["delivery_blocked"])

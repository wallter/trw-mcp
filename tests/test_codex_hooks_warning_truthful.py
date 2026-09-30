"""E2E-CODEX-INIT-ARTIFACTS item 2 (INC-016): the codex hooks warning describes the files TRW wrote.

``init --ide codex`` warned "Codex hooks are enabled with [features].hooks ... approve or disable the 5
TRW-managed hooks" while the managed ``[features]`` table was empty and ``.codex/hooks.json`` held 2 hooks:
the count came from the full ceremony payload (written only when ``[features].hooks`` is on) and the
"enabled" claim was unconditional. It now counts the TRW hooks in the real ``hooks.json`` and names
``[features].hooks`` only when that flag is set.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from trw_mcp.bootstrap import init_project

pytestmark = [pytest.mark.usefixtures("no_memory_daemon"), pytest.mark.integration]


def _hooks_warning(result: dict[str, object]) -> str:
    warnings = [w for w in result.get("warnings", []) if isinstance(w, str) and "/hooks" in w]  # type: ignore[union-attr]
    assert len(warnings) == 1, warnings
    return warnings[0]


def _trw_hooks_in_file(project: Path) -> int:
    data = json.loads((project / ".codex" / "hooks.json").read_text(encoding="utf-8"))
    return sum(
        len(group.get("hooks", []))
        for groups in data.get("hooks", {}).values()
        for group in groups
        if str(group.get("description", "")).startswith("TRW managed:")
    )


def test_the_warning_counts_the_hooks_in_the_file_and_does_not_claim_the_flag(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    result = init_project(tmp_path, ide="codex")

    warning = _hooks_warning(result)
    count = int(re.search(r"(\d+) TRW-managed hook", warning).group(1))  # type: ignore[union-attr]
    assert count == _trw_hooks_in_file(tmp_path)
    assert "enabled with [features].hooks" not in warning

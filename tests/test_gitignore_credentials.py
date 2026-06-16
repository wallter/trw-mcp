"""PRD-SEC-005-FR02: bundled gitignore ignores credentials.yaml.

The bundled ``data/gitignore.txt`` (deployed to ``.trw/.gitignore``) MUST
list ``credentials.yaml`` so the credential file is never tracked.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from trw_mcp.bootstrap import _DATA_FILE_MAP

_GITIGNORE = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data" / "gitignore.txt"


def test_bundled_gitignore_lists_credentials() -> None:
    text = _GITIGNORE.read_text(encoding="utf-8")
    lines = {ln.strip() for ln in text.splitlines()}
    assert "credentials.yaml" in lines


def test_gitignore_is_deployed_to_trw_dir() -> None:
    """FR02: the bundled gitignore is mapped to .trw/.gitignore by bootstrap."""
    mappings = dict(_DATA_FILE_MAP)
    assert mappings.get("gitignore.txt") == ".trw/.gitignore"


def test_git_check_ignore_matches_credentials(tmp_path: Path) -> None:
    """End-to-end: a repo seeded with the bundled gitignore ignores the file."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / ".gitignore").write_text(_GITIGNORE.read_text(encoding="utf-8"), encoding="utf-8")
    (trw_dir / "credentials.yaml").write_text('platform_api_key: "x"\n', encoding="utf-8")

    result = subprocess.run(
        ["git", "check-ignore", ".trw/credentials.yaml"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert ".trw/credentials.yaml" in result.stdout

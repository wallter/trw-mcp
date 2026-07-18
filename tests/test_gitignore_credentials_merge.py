"""PRD-SEC-005 round-2: update-project ensures credentials.yaml is ignored.

The bundled ``gitignore.txt`` is only deployed to ``.trw/.gitignore`` on
INIT (``_DATA_FILE_MAP``); ``update-project`` never refreshed it, so existing
installs that pre-date SEC-005 kept a custom ``.trw/.gitignore`` WITHOUT a
``credentials.yaml`` rule — the SEC-005 migration then wrote the credential
into an unignored location.

These tests cover the MERGE-ensure fix: ``_ensure_credentials_gitignored``
appends the credentials.yaml rule to an existing custom ``.trw/.gitignore``
WITHOUT discarding the user's other ignores, and is idempotent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from trw_mcp.bootstrap._template_updater import _ensure_credentials_gitignored

# The legacy custom .trw/.gitignore format shipped before SEC-005 — it has NO
# credentials.yaml rule (the exact format present in older real installs).
_OLD_CUSTOM_GITIGNORE = """# TRW self-learning layer gitignore
# Track: config, learnings, scripts, patterns
# Ignore: reflections, event streams, locks, databases, runtime, context
reflections/
logs/
*.jsonl
*.lock
knowledge.db

# User's own custom ignore
my-private-notes/
"""


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_appends_credentials_rule_to_old_custom_gitignore(tmp_path: Path) -> None:
    """A custom .trw/.gitignore missing the rule gains it WITHOUT losing content."""
    trw = tmp_path / ".trw"
    trw.mkdir()
    gi = trw / ".gitignore"
    gi.write_text(_OLD_CUSTOM_GITIGNORE, encoding="utf-8")

    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
    _ensure_credentials_gitignored(tmp_path, result, dry_run=False)

    text = _read(gi)
    lines = {ln.strip() for ln in text.splitlines()}
    # Rule added.
    assert "credentials.yaml" in lines
    # User's custom content preserved (NOT discarded by a blind overwrite).
    assert "my-private-notes/" in lines
    assert "reflections/" in lines
    assert "knowledge.db" in lines
    assert str(gi) in result["updated"]


def test_idempotent_when_rule_already_present(tmp_path: Path) -> None:
    """If credentials.yaml is already ignored, the file is left untouched."""
    trw = tmp_path / ".trw"
    trw.mkdir()
    gi = trw / ".gitignore"
    gi.write_text("credentials.yaml\nreflections/\n", encoding="utf-8")
    before = _read(gi)

    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
    _ensure_credentials_gitignored(tmp_path, result, dry_run=False)

    assert _read(gi) == before  # no duplicate append, no mutation
    assert str(gi) not in result["updated"]


def test_creates_gitignore_when_absent(tmp_path: Path) -> None:
    """An install that has .trw/ but no .gitignore gets one with the rule."""
    trw = tmp_path / ".trw"
    trw.mkdir()
    gi = trw / ".gitignore"
    assert not gi.exists()

    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
    _ensure_credentials_gitignored(tmp_path, result, dry_run=False)

    assert gi.exists()
    assert "credentials.yaml" in {ln.strip() for ln in _read(gi).splitlines()}
    assert str(gi) in result["created"]


def test_dry_run_does_not_write(tmp_path: Path) -> None:
    trw = tmp_path / ".trw"
    trw.mkdir()
    gi = trw / ".gitignore"
    gi.write_text(_OLD_CUSTOM_GITIGNORE, encoding="utf-8")
    before = _read(gi)

    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
    _ensure_credentials_gitignored(tmp_path, result, dry_run=True)

    assert _read(gi) == before
    assert any("credentials.yaml" in entry for entry in result["updated"])


def test_real_git_check_ignore_after_merge(tmp_path: Path) -> None:
    """End-to-end: after the merge, real ``git check-ignore`` ignores the file."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    trw = tmp_path / ".trw"
    trw.mkdir()
    (trw / ".gitignore").write_text(_OLD_CUSTOM_GITIGNORE, encoding="utf-8")
    (trw / "credentials.yaml").write_text('platform_api_key: "x"\n', encoding="utf-8")

    # Before the merge the legacy gitignore does NOT cover credentials.yaml.
    pre = subprocess.run(
        ["git", "check-ignore", ".trw/credentials.yaml"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert pre.returncode != 0  # not ignored yet

    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
    _ensure_credentials_gitignored(tmp_path, result, dry_run=False)

    post = subprocess.run(
        ["git", "check-ignore", ".trw/credentials.yaml"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert post.returncode == 0
    assert ".trw/credentials.yaml" in post.stdout


def test_noop_when_no_trw_dir(tmp_path: Path) -> None:
    """Fail-open: no .trw/ directory means nothing to do, no error."""
    result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
    _ensure_credentials_gitignored(tmp_path, result, dry_run=False)
    assert result["errors"] == []
    assert not (tmp_path / ".trw" / ".gitignore").exists()

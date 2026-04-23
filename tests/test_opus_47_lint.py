"""Lint guard against Opus 4.7 removed sampling params (PRD-QUAL-072 FR05).

Scans YAML frontmatter of every agent and skill markdown file under the
canonical source trees and asserts NO occurrence of the keys removed by
the Opus 4.7 API:

* ``budget_tokens`` (extended thinking budgets — removed)
* ``temperature``  (sampling knob — removed)
* ``top_p``        (sampling knob — removed)
* ``top_k``        (sampling knob — removed)

Only the leading ``---``-fenced frontmatter block is parsed (prose bodies
that mention the keys are ignored). Violations produce a clear error
naming the file path and the offending key.
"""

from __future__ import annotations

import textwrap
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

SCAN_GLOBS: tuple[tuple[Path, str], ...] = (
    (REPO_ROOT / ".claude" / "agents", "*.md"),
    (REPO_ROOT / ".claude" / "skills", "**/SKILL.md"),
    (REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "agents", "*.md"),
    (REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "skills", "**/SKILL.md"),
)

REMOVED_KEYS: frozenset[str] = frozenset({"budget_tokens", "temperature", "top_p", "top_k"})


def _extract_frontmatter(path: Path) -> dict[str, Any] | None:
    """Return the parsed YAML frontmatter dict, or None if the file has none.

    Parses ONLY the block between leading ``---`` fences. Returns ``None``
    if the file does not start with ``---`` or if the block is not a mapping.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return None
    # Strip the leading fence and split on the next fence.
    body = text[3:].lstrip("\n")
    end = body.find("\n---")
    if end == -1:
        return None
    block = body[:end]
    try:
        parsed = yaml.safe_load(block)
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def _iter_scan_files() -> list[Path]:
    """Collect every markdown file matching the scan globs."""
    files: list[Path] = []
    for base, pattern in SCAN_GLOBS:
        if not base.exists():
            continue
        files.extend(sorted(base.glob(pattern)))
    return files


def _violations(frontmatter: dict[str, Any]) -> list[str]:
    """Return the list of removed-key names present at the top level."""
    return sorted(k for k in frontmatter if k in REMOVED_KEYS)


# ---------------------------------------------------------------------------
# FR05 — positive guard: the repo is clean today
# ---------------------------------------------------------------------------


def test_no_removed_sampling_params_in_frontmatter() -> None:
    """FR05: zero occurrences of removed Opus 4.7 keys in any frontmatter."""
    offenders: list[str] = []
    for path in _iter_scan_files():
        frontmatter = _extract_frontmatter(path)
        if frontmatter is None:
            continue
        bad = _violations(frontmatter)
        if bad:
            rel = path.relative_to(REPO_ROOT)
            offenders.append(f"{rel}: {', '.join(bad)}")
    if offenders:
        pytest.fail(
            "Opus 4.7 removed sampling params found in frontmatter "
            "(delete them — they return HTTP 400 on 4.7):\n  " + "\n  ".join(offenders)
        )


def test_lint_scans_both_mirror_trees() -> None:
    """FR05: both .claude/ and trw-mcp/src/trw_mcp/data/ are inspected."""
    files = _iter_scan_files()
    assert any(".claude/agents" in str(p) for p in files), "missing .claude/agents/ in scan set"
    assert any(
        "trw_mcp/data/agents" in str(p) for p in files
    ), "missing bundled trw-mcp data/agents/ in scan set"


# ---------------------------------------------------------------------------
# FR05 — NFR: lint must complete quickly
# ---------------------------------------------------------------------------


def test_lint_runtime_under_2s() -> None:
    """NFR01: full frontmatter scan runs in under 2 seconds."""
    t0 = time.perf_counter()
    for path in _iter_scan_files():
        _extract_frontmatter(path)
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, f"lint scan took {elapsed:.3f}s (budget: 2.0s)"


# ---------------------------------------------------------------------------
# FR05 — negative fixtures: verify the lint logic DETECTS injected violations
# ---------------------------------------------------------------------------


def _write_fixture(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "agent.md"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_lint_catches_injected_temperature(tmp_path: Path) -> None:
    """FR05 negative: a fixture with ``temperature:`` is flagged."""
    path = _write_fixture(
        tmp_path,
        """\
        ---
        name: evil
        model: opus
        temperature: 0.5
        ---
        body
        """,
    )
    fm = _extract_frontmatter(path)
    assert fm is not None
    assert "temperature" in _violations(fm)


def test_lint_catches_injected_budget_tokens(tmp_path: Path) -> None:
    """FR05 negative: a fixture with ``budget_tokens:`` is flagged."""
    path = _write_fixture(
        tmp_path,
        """\
        ---
        name: evil
        model: opus
        budget_tokens: 8192
        ---
        body
        """,
    )
    fm = _extract_frontmatter(path)
    assert fm is not None
    assert "budget_tokens" in _violations(fm)


def test_lint_ignores_prose_mentions(tmp_path: Path) -> None:
    """Prose bodies mentioning the keys (e.g. migration docs) are ignored."""
    path = _write_fixture(
        tmp_path,
        """\
        ---
        name: ok
        model: opus
        ---
        # Docs body
        Opus 4.7 removed `temperature`, `top_p`, and `top_k` — use effort instead.
        """,
    )
    fm = _extract_frontmatter(path)
    assert fm is not None
    assert _violations(fm) == []

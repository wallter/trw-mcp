"""Lint test: every public TRW skill has a 'Use when:' line near the top (PRD-QUAL-074 FR08).

A public skill is one with ``user-invocable: true`` in its SKILL.md
frontmatter. We check the first 10 non-blank, non-frontmatter lines
for the literal substring ``Use when:``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Body-trigger lint covers the canonical and Claude surfaces it was designed for.
_SKILL_DIRS: tuple[Path, ...] = (
    _REPO_ROOT / ".claude" / "skills",
    _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "skills",
)

# Invocation/name parity covers canonical, packaged-client, generated-plugin,
# and tracked client mirrors.
_USAGE_SKILL_DIRS: tuple[Path, ...] = (
    _REPO_ROOT / ".agents" / "skills",
    _REPO_ROOT / ".claude" / "skills",
    _REPO_ROOT / ".cursor" / "skills",
    _REPO_ROOT / ".github" / "skills",
    _REPO_ROOT / "build" / "trw-plugin" / "skills",
    _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "skills",
    _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "codex" / "skills",
    _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "copilot" / "skills",
)

# Skills excluded from the "Use when:" in body rule with justification.
# trw-skills-guide is invoked as a slash command and has no SKILL.md body
# below its H1 describing a trigger (it IS the directory of triggers).
BODY_USE_WHEN_ALLOWLIST: dict[str, str] = {
    "trw-delegate": "Body opens with the equivalent 'Use for a bias-breaking review' trigger.",
}

LOOK_AHEAD = 10


def _parse_frontmatter_and_body(path: Path) -> tuple[dict[str, str], list[str]]:
    """Return (frontmatter_map, body_lines). Very small parser — keys only, no nesting."""
    raw = path.read_text(encoding="utf-8").splitlines()
    if not raw or raw[0].strip() != "---":
        return {}, raw
    fm: dict[str, str] = {}
    i = 1
    while i < len(raw) and raw[i].strip() != "---":
        line = raw[i]
        if ":" in line and not line.lstrip().startswith("#"):
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip().rstrip(">")
        i += 1
    body = raw[i + 1 :] if i < len(raw) else []
    return fm, body


def _is_public(fm: dict[str, str]) -> bool:
    val = fm.get("user-invocable", "").lower()
    return val in {"true", "yes", "1"}


def _public_skill_names_from_inventory() -> set[str] | None:
    """Return the set of public skill names from build/inventory.json, or None.

    The inventory is the canonical PRD-QUAL-074 public-skill registry: it
    filters to skills with ``user_invocable=True``. When the inventory is
    missing (fresh checkout), fall back to the frontmatter-scan set.
    """
    import json

    inv = _REPO_ROOT / "build" / "inventory.json"
    if not inv.is_file():
        return None
    try:
        data = json.loads(inv.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    names: set[str] = set()
    for sk in data.get("skills", []):
        if sk.get("user_invocable") is True and isinstance(sk.get("name"), str):
            names.add(sk["name"])
    return names or None


def _iter_public_skills(skill_dirs: tuple[Path, ...] = _SKILL_DIRS) -> list[tuple[str, Path]]:
    """Return (skill_name, SKILL.md path) for every public skill in inventory.

    When build/inventory.json exists, only its ``user_invocable: true`` set
    is enforced. Otherwise, fall back to frontmatter scanning (kept for
    fresh checkouts pre-``make inventory``).
    """
    inventory = _public_skill_names_from_inventory()
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for base in skill_dirs:
        if not base.is_dir():
            continue
        for skill_md in base.glob("*/SKILL.md"):
            name = skill_md.parent.name
            key = str(skill_md.resolve())
            if key in seen:
                continue
            seen.add(key)
            if inventory is not None:
                if name in inventory:
                    out.append((name, skill_md))
                continue
            fm, _body = _parse_frontmatter_and_body(skill_md)
            if _is_public(fm):
                out.append((name, skill_md))
    return out


def _has_use_when_near_top(body: list[str]) -> bool:
    seen = 0
    for line in body:
        if not line.strip():
            continue
        if seen >= LOOK_AHEAD:
            break
        if "Use when:" in line:
            return True
        seen += 1
    return False


def test_public_skills_have_use_when() -> None:
    offenders: list[str] = []
    for name, path in _iter_public_skills():
        if name in BODY_USE_WHEN_ALLOWLIST:
            continue
        _fm, body = _parse_frontmatter_and_body(path)
        if not _has_use_when_near_top(body):
            offenders.append(f"{path.relative_to(_REPO_ROOT)}: no 'Use when:' in first {LOOK_AHEAD} body lines")
    assert not offenders, "Public skills missing 'Use when:' trigger:\n  " + "\n  ".join(offenders)


def test_missing_use_when_fails(tmp_path: Path) -> None:
    """Negative control: body without 'Use when:' is flagged."""
    fake = tmp_path / "SKILL.md"
    fake.write_text(
        "---\nname: fake\nuser-invocable: true\n---\n\n# Fake\n\nSome prose without the trigger.\n",
        encoding="utf-8",
    )
    _fm, body = _parse_frontmatter_and_body(fake)
    assert not _has_use_when_near_top(body)


_USE_INVOCATION_RE = re.compile(r"\bUse:\s*/([a-z0-9][a-z0-9-]*)")


def _load_yaml_frontmatter(path: Path) -> dict[str, object]:
    raw = path.read_text(encoding="utf-8").splitlines()
    if not raw or raw[0].strip() != "---":
        return {}
    try:
        end = raw.index("---", 1)
    except ValueError:
        return {}
    parsed = yaml.safe_load("\n".join(raw[1:end])) or {}
    return parsed if isinstance(parsed, dict) else {}


def _frontmatter_usage_matches_name(path: Path) -> bool:
    fm = _load_yaml_frontmatter(path)
    match = _USE_INVOCATION_RE.search(str(fm.get("description", "")))
    return match is None or match.group(1) == str(fm.get("name", ""))


def test_public_skill_usage_matches_frontmatter_name() -> None:
    offenders = [
        str(path.relative_to(_REPO_ROOT))
        for _name, path in _iter_public_skills(_USAGE_SKILL_DIRS)
        if not _frontmatter_usage_matches_name(path)
    ]
    assert not offenders, "Public skill descriptions advertise a different command name:\n  " + "\n  ".join(offenders)


def test_mismatched_usage_name_fails(tmp_path: Path) -> None:
    fake = tmp_path / "SKILL.md"
    fake.write_text(
        "---\nname: trw-foo\ndescription: 'Use: /foo'\nuser-invocable: true\n---\n# Fake\n",
        encoding="utf-8",
    )
    assert not _frontmatter_usage_matches_name(fake)

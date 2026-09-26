"""Every client's skills, rendered from the one canonical ``data/skills`` corpus (PRD-CORE-291-FR04).

The codex, copilot and opencode skill trees used to be hand-authored forks of
``data/skills`` that drifted from it (copilot's ``trw-reflect`` ran 77 lines
behind; opencode's stubs were a fraction of the canonical text). They are gone.
A client now differs from the canonical corpus in exactly two declared ways:

* **membership** -- which canonical skills it ships. Codex ships all of them
  (its fork had silently lagged three behind); copilot and opencode keep the
  curated subsets their forks shipped;
* **frontmatter** -- which top-level frontmatter keys it keeps. Codex reads only
  ``name``/``description``; opencode also honours ``user-invocable`` and
  ``argument-hint``; copilot takes the canonical frontmatter unchanged.

The body is the canonical body, byte for byte. Codex and opencode also fold the
readiness phases into ``trw-prd-ready`` as ``<phase>-contract.md`` supporting
files (``PRD_READY_CONTRACTS``); :func:`skill_files` returns them with that
skill, so the installers and the manifest recorders read one list and uninstall
removes exactly what was written. Conditional skills
(``_optional_skills.CONDITIONAL_SKILLS``) stay with the installers, which add
them only while their flag is on.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["PRD_READY_CONTRACTS", "canonical_skills_dir", "render_skill_md", "skill_files", "skill_names"]

#: Top-level frontmatter keys each client keeps; ``None`` keeps them all.
_FRONTMATTER_KEYS: dict[str, tuple[str, ...] | None] = {
    "codex": ("name", "description"),
    "copilot": None,
    "opencode": ("name", "description", "user-invocable", "argument-hint"),
}

#: Copilot's curated subset (its fork's membership at deletion); opencode's set is
#: its skills_inventory.yaml.
_COPILOT_SKILLS = frozenset(
    {
        "trw-audit",
        "trw-ceremony-guide",
        "trw-delegate",
        "trw-deliver",
        "trw-dry-check",
        "trw-feedback",
        "trw-learn",
        "trw-prd-new",
        "trw-project-health",
        "trw-reflect",
        "trw-security-check",
        "trw-sprint-init",
    }
)
#: Readiness phases a client ships inside ``trw-prd-ready`` as ``<phase>-contract.md``
#: instead of as skills of their own. Codex renders each contract like a SKILL.md;
#: opencode ships the canonical bytes. ``trw-prd-ready`` is never its own contract:
#: that file would be a byte copy of the ``SKILL.md`` beside it that nothing names.
PRD_READY_CONTRACTS: dict[str, tuple[str, ...]] = {
    "codex": ("trw-prd-groom", "trw-prd-review", "trw-exec-plan"),
    "opencode": ("trw-prd-groom", "trw-prd-review", "trw-exec-plan"),
}
_KEY_LINE = re.compile(r"^([A-Za-z][\w-]*):")


def canonical_skills_dir() -> Path:
    """The canonical skill corpus every client renders from."""
    from ._utils import _DATA_DIR

    return _DATA_DIR / "skills"


def skill_names(client: str) -> list[str]:
    """The canonical skills *client* ships, sorted; conditional skills excluded."""
    from ._optional_skills import CONDITIONAL_SKILLS

    root = canonical_skills_dir()
    names = {d.name for d in root.iterdir() if (d / "SKILL.md").is_file()} if root.is_dir() else set()
    names -= set(CONDITIONAL_SKILLS)
    if client == "copilot":
        names &= _COPILOT_SKILLS
    elif client == "opencode":
        from ._opencode import load_opencode_skill_inventory

        inventory = load_opencode_skill_inventory()
        names &= {name for name, cfg in inventory.items() if cfg.get("disposition") != "exclude"}
    return sorted(names)


def render_skill_md(text: str, client: str) -> str:
    """*text* (a canonical SKILL.md) with its frontmatter reduced to *client*'s keys."""
    keys = _FRONTMATTER_KEYS.get(client)
    if keys is None or not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end < 0:
        return text
    kept: list[str] = []
    keep = False
    for line in text[4:end].split("\n"):
        match = _KEY_LINE.match(line)
        if match:  # a top-level key starts; its indented continuation lines follow it
            keep = match.group(1) in keys
        if keep:
            kept.append(line)
    return "---\n" + "\n".join(kept) + text[end:]


def skill_files(client: str, name: str, *, root: Path | None = None) -> list[tuple[str, bytes]]:
    """``(filename, bytes)`` for every file of canonical skill *name*, rendered for *client*."""
    files: list[tuple[str, bytes]] = []
    corpus = root or canonical_skills_dir()
    skill_dir = corpus / name
    for path in sorted(skill_dir.iterdir()) if skill_dir.is_dir() else []:
        if not path.is_file():
            continue
        if path.name == "SKILL.md":
            files.append((path.name, render_skill_md(path.read_text(encoding="utf-8"), client).encode("utf-8")))
        else:
            files.append((path.name, path.read_bytes()))
    for phase in PRD_READY_CONTRACTS.get(client, ()) if name == "trw-prd-ready" else ():
        source = corpus / phase / "SKILL.md"
        data = (
            render_skill_md(source.read_text(encoding="utf-8"), client).encode()
            if client == "codex"
            else source.read_bytes()
        )
        files.append((f"{phase}-contract.md", data))
    return files

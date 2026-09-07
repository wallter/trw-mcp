"""Brief rendering — the manifest becomes the member's starting instructions (FR06).

Plain string substitution over a fixed placeholder set, into a template bundled
in the package data directory. No template engine, no evaluation, no shell.

WHY SUBSTITUTION AND NOT A TEMPLATE LANGUAGE (NFR03). Manifest content is data,
never instruction. A template engine would let a manifest value reach an
expression evaluator; a format string would let a value carrying ``{}`` reach
``str.format``'s attribute access. Both are avoided by replacing each fixed
``{{TOKEN}}`` exactly once, from a table this module owns, with a value that is
first stripped of control characters and backticks and then wrapped in a code
span. A member declaration that reads like an instruction therefore RENDERS as
quoted data, which is what the FR06 test asserts.

The three prose bodies in ``docs/documentation/agent-briefs/`` are unchanged and
remain the role-specific text a brief composes; this replaces the manual
placeholder-filling, not the prose.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from trw_mcp.formation._manifest import FormationError, FormationManifest

logger = structlog.get_logger(__name__)

__all__ = ["PLACEHOLDERS", "render_brief", "template_path"]

_TEMPLATE_NAME = "formation-brief-template.md"
_PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")
#: Characters removed from every substituted value before it is wrapped in a
#: code span: control characters would let a value forge document structure, and
#: a backtick would let it escape the span it is quoted in.
_UNSAFE_RE = re.compile(r"[`\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

#: The fixed placeholder set. Rendering asserts every token here is consumed and
#: that no ``{{...}}`` survives, so adding a token to the template without adding
#: it here fails loudly instead of shipping a brief with a literal placeholder.
PLACEHOLDERS: tuple[str, ...] = (
    "MEMBER_ID",
    "FORMATION_ID",
    "CLIENT",
    "ROLE",
    "REPO_ROOT",
    "RUN_ROOT",
    "ORCHESTRATOR_RUN_ROOT",
    "MANIFEST_PATH",
    "STATUS",
    "OWNED_PATHS",
    "TEST_OWNED_PATHS",
    "PRD_IDS",
    "SHARED_TREE_RULES",
    "PROCESS_ADDENDUM",
)

#: The shared-tree rules every member receives. Stated here rather than read
#: from the repository so a member on any client gets them even when the project
#: ships no addendum of its own.
_SHARED_TREE_RULES = (
    "- Commit only the paths you own, with "
    '`scripts/git-commit-scoped.sh "<subject>" "<why>" -- <paths>`.\n'
    "- Never `git add -A`, `stash`, `reset`, `checkout --`, `clean`, or `rebase`: "
    "other members share this working tree and those commands destroy their work.\n"
    "- On an unexpected diff in a file you own, leave it and re-establish ownership "
    "with the orchestrator rather than committing it.\n"
    "- Ownership is enforced at the commit boundary on every client; the editor hook "
    "only warns, so the commit is the surface that decides."
)


def template_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / _TEMPLATE_NAME


def _quote(value: str) -> str:
    cleaned = _UNSAFE_RE.sub("", value.replace("\n", " ").replace("\r", " ")).strip()
    return f"`{cleaned}`" if cleaned else "_(not set)_"


def _bullets(values: list[str], empty: str) -> str:
    return "\n".join(f"- {_quote(item)}" for item in values) if values else f"_{empty}_"


def render_brief(
    manifest: FormationManifest,
    member_id: str,
    *,
    project_root: Path,
    process_addendum: str = "",
) -> str:
    """Render *member_id*'s brief, or raise when a placeholder is unresolved."""
    member = manifest.member(member_id)
    addendum = process_addendum.strip()
    if not addendum and manifest.shared_rules_ref:
        addendum = _read_addendum(project_root / manifest.shared_rules_ref)
    values = {
        "MEMBER_ID": _quote(member.member_id),
        "FORMATION_ID": _quote(manifest.formation_id),
        "CLIENT": _quote(member.client),
        "ROLE": _quote(member.role),
        "REPO_ROOT": _quote(str(project_root)),
        "RUN_ROOT": _quote(member.run_path or "not joined yet"),
        "ORCHESTRATOR_RUN_ROOT": _quote(manifest.orchestrator_run_path),
        "MANIFEST_PATH": _quote(str(manifest_location(manifest))),
        "STATUS": _quote(str(member.status)),
        "OWNED_PATHS": _bullets(member.owned_paths, "no owned paths declared"),
        "TEST_OWNED_PATHS": _bullets(member.test_owned_paths, "no test paths declared"),
        "PRD_IDS": _bullets(member.prd_ids, "no PRD identifiers allocated"),
        "SHARED_TREE_RULES": _SHARED_TREE_RULES,
        "PROCESS_ADDENDUM": addendum or "_(no process addendum declared for this formation)_",
    }
    path = template_path()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FormationError(f"brief template {path} is unreadable: {exc}") from exc
    for token in PLACEHOLDERS:
        text = text.replace("{{" + token + "}}", values[token])
    leftover = _PLACEHOLDER_RE.search(text)
    if leftover:
        raise FormationError(f"brief template {path} carries an unsubstituted placeholder {leftover.group(0)!r}")
    return text


def manifest_location(manifest: FormationManifest) -> Path:
    from trw_mcp.formation._store import manifest_path_for_run

    return manifest_path_for_run(Path(manifest.orchestrator_run_path))


def _read_addendum(path: Path) -> str:
    """Read the shared process addendum as DATA.

    A read failure yields the empty string and the brief says the addendum is
    not declared: the alternative — silently emitting the raw path, or the
    template's own token — would put an unresolved reference in front of a
    member as though it were content.
    """
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        # The orchestrator DECLARED shared_rules_ref, so an unreadable file here
        # is worth a maintainer's attention even though the brief degrades to
        # "not declared" rather than failing the render.
        logger.info("formation_addendum_unreadable", path=str(path), reason=str(exc))
        return ""

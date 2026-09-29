"""PRD-CORE-301-FR08: the three protected rule blocks stay byte-for-byte.

Every context cut this PRD makes removes text AROUND three blocks and never
inside them: the deliver gate, the MCP transport-loss retry protocol and the
reviewer-role bound. This module is the checker the FR08 tests in
``test_instruction_single_block.py`` drive; pytest is its only runtime caller,
and the production code it exercises is the real install path
(``init_project`` and ``update_project`` -> the per-client instruction
renderers -> ``render_deliver_gate_statement`` / ``render_transport_loss_guidance``).

Soundness scope
---------------
PROVES, for every client in ``SUPPORTED_IDES`` under the default config:

* each file ``init_project`` writes, and each file a following
  ``update_project`` writes (``.trw/backups/`` excluded — those are copies of the
  previous file, not a surface), carries each renderable block (deliver gate,
  transport loss) byte-identical to its current canonical render, exactly once,
  in exactly the pinned set of files (:data:`CARRIERS`) — a block that moved to,
  vanished from or was reworded in any surface fails;
* the canonical sources are unchanged: the ``## Deliver Gate`` section of the
  bundled ``tool-lifecycle.md`` and the rendered transport-loss protocol match a
  pinned sha256, and in the monorepo so do ``docs/CONSTITUTION.md`` §1.a and the
  hand-authored reviewer-role sections of ``AGENTS.md`` and
  ``docs/CLIENT-PROFILES.md`` (:func:`pinned_sources`). The digest is over the
  section's RAW bytes, read with ``read_bytes`` and split with each line's own
  terminator kept, so a line-ending (LF->CRLF) change is caught too; a surface is
  read the same way (:func:`read_surfaces` decodes bytes, never text mode).

DOES NOT PROVE:

* that the pinned text is correct, or that a client actually loads the file it
  sits in (injection is the harness's question, not this one);
* anything about surfaces other paths produce — ``trw-mcp instructions sync``,
  this repository's own checked-in instruction files, skill and agent bodies,
  hook output, the ``trw_session_start`` payload, the MCP server instructions;
* non-default configs (light profiles, disabled surfaces, custom client
  profiles);
* a reworded copy placed in a surface WITHOUT the block's anchor line — the
  anchor is how a surface is recognised as carrying the block at all;
* that the reviewer-role bound reaches an installed project: no client surface
  renders it, so only its two hand-authored monorepo copies are pinned.

Updating a pin is a deliberate rule edit, never part of a cut: it needs the
approving PRD cited next to the new digest.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

GATE = "deliver_gate"
TRANSPORT = "transport_loss"
#: The line that marks a surface as carrying the block, reworded or not.
GATE_ANCHOR = "## Deliver Gate"
TRANSPORT_ANCHOR = "## MCP transport-loss retry protocol"
GATE_SECTION_HEADING = "## Deliver Gate (v26.2)"

#: sha256 of the ``## Deliver Gate`` section of the bundled tool-lifecycle.md.
GATE_SECTION_SHA256 = "4dab2f1910ba229daf06db7552ee83224359041e5c9185d5b0613b9208feed51"
#: sha256 of ``render_transport_loss_guidance()``.
TRANSPORT_SHA256 = "1dc9aebcb8b11d175112d0b82973b7c2f07b93a50fb75f2ae01841f6ea901809"

#: (repo-relative path, exact heading line) -> sha256 of that section. Monorepo only.
_REVIEWER = "## Reviewer role (read-only dispatched lanes)"
MONOREPO_SECTION_SHA256: dict[tuple[str, str], str] = {
    ("docs/CONSTITUTION.md", "#### 1.a The Deliver Gate Rule (no fourth path)"): (
        "ee612e930be2c0e82d9846a86a2a5441f3a6a4b7867f1ae910c3a4b8cc00ec87"
    ),
    # The two reviewer-bound copies are worded differently today (AGENTS.md is the
    # shorter one); each is pinned as it stands rather than one declared canonical.
    ("AGENTS.md", _REVIEWER): "b237920ade2637fdd8d346703a6d8b056ab68fc02061fdfaf177351765b8bc8f",
    ("docs/CLIENT-PROFILES.md", _REVIEWER): "a9cfbfef82839caa419eea2bb05854153af7dd80b1953ab34a75820b9bb2ae02",
}

#: Client -> file -> protected blocks that file carries after ``init_project``.
_G = frozenset({GATE})
_GT = frozenset({GATE, TRANSPORT})
CARRIERS: dict[str, dict[str, frozenset[str]]] = {
    # PRD-CORE-341: Claude Code reads AGENTS.md natively and expands its ``@.trw/INSTRUCTIONS.md``
    # import; the block lives in that TRW-owned file and AGENTS.md holds only the link.
    "claude-code": {".trw/INSTRUCTIONS.md": _GT},
    # PRD-CORE-301-FR02: opencode renders the shared block, which carries transport loss (PRD-CORE-215-FR06).
    "opencode": {".opencode/INSTRUCTIONS.md": _GT},
    "cursor-ide": {".cursor/rules/trw-ceremony.mdc": _GT},
    # PRD-CORE-301-FR14 / PRD-CORE-341: cursor-cli's AGENTS.md names .trw/INSTRUCTIONS.md, which holds the protocol.
    "cursor-cli": {".trw/INSTRUCTIONS.md": _GT},
    "codex": {".codex/INSTRUCTIONS.md": _GT},
    "copilot": {".github/copilot-instructions.md": _G, ".github/instructions/trw-ceremony.instructions.md": _GT},
    "antigravity-cli": {".agents/rules/trw-ceremony.md": _G, "ANTIGRAVITY.md": _G},
    "grok": {".trw/INSTRUCTIONS.md": _GT},
}

#: Files ``update_project`` adds on top of :data:`CARRIERS`, for every client.
UPDATE_ADDS: dict[str, frozenset[str]] = {".trw/context/behavioral_protocol.md": _G}


@dataclass(frozen=True)
class Block:
    """A protected block: its canonical text and the line that identifies a carrier."""

    name: str
    canonical: str
    anchor: str


def section(data: bytes, heading: str) -> bytes:
    """The raw bytes of the Markdown section opened by the line *heading*, up to the next heading at its level or above.

    Lines keep their own terminators and nothing is decoded, so a CRLF, a BOM
    or any other byte-level change inside the section reaches the digest: an
    LF->CRLF rewrite changes no visible character, and a text-mode read or a
    ``splitlines`` join would have normalized it away. Trailing ``\n`` bytes
    are dropped (the pins were minted that way); a ``\r`` never is.

    Raises :class:`LookupError` when *heading* is absent: a section that is not
    there must never hash as an empty string that a pin could be minted from.
    """
    lines = data.splitlines(keepends=True)
    wanted = heading.encode("utf-8")
    bare = [line.rstrip(b"\r\n") for line in lines]
    if wanted not in bare:
        raise LookupError(f"heading {heading!r} not found — was the protected section renamed or removed?")
    start = bare.index(wanted)
    level = len(heading) - len(heading.lstrip("#"))
    stop = re.compile(rb"^#{1,%d}\s" % level)
    end = next((i for i in range(start + 1, len(lines)) if stop.match(lines[i])), len(lines))
    return b"".join(lines[start:end]).rstrip(b"\n")


def surface_violations(
    surfaces: Mapping[str, str], blocks: tuple[Block, ...], carriers: Mapping[str, frozenset[str]]
) -> list[str]:
    """Every way *surfaces* (path -> text) departs from *carriers* for *blocks*; empty means intact.

    A pinned carrier must hold each of its blocks byte-identical exactly once
    with exactly one anchor line; any other surface must hold neither the block
    nor its anchor.
    """
    violations: list[str] = []
    for path in sorted(set(surfaces) | set(carriers)):
        expected = carriers.get(path, frozenset())
        text = surfaces.get(path)
        if text is None:
            violations.extend(f"{path}: {name} carrier was not written" for name in sorted(expected))
            continue
        for block in blocks:
            identical = text.count(block.canonical)
            anchored = sum(1 for line in text.splitlines() if line.startswith(block.anchor))
            if block.name in expected and (identical, anchored) != (1, 1):
                violations.append(
                    f"{path}: {block.name} must appear once byte-identical to its canonical render; "
                    f"found {identical} identical and {anchored} anchor line(s)"
                )
            elif block.name not in expected and (identical or anchored):
                violations.append(f"{path}: {block.name} appears in a file that is not one of its pinned carriers")
    return violations


def digest_violations(texts: Mapping[str, bytes], pins: Mapping[str, str]) -> list[str]:
    """Every *texts* entry (raw bytes) whose sha256 differs from its pin in *pins*; empty means unchanged."""
    violations: list[str] = []
    for name, pin in sorted(pins.items()):
        data = texts.get(name)
        if data is None:
            violations.append(f"{name}: no text to hash — the protected source is gone")
        elif (actual := sha256(data)) != pin:
            violations.append(f"{name}: sha256 {actual} != pinned {pin}; a cut must not edit a protected block")
    return violations


def read_surfaces(root: Path) -> dict[str, str]:
    """Every text file under *root* except ``.trw/backups/``, keyed by POSIX path relative to it.

    A file holding a NUL byte is binary (SQLite stores, indexes) and skipped;
    any other file must decode as UTF-8 or this raises — a surface the check
    cannot read is not a surface it may pass.
    """
    surfaces: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or rel.startswith(".trw/backups/"):
            continue
        data = path.read_bytes()
        if b"\0" not in data:
            surfaces[rel] = data.decode("utf-8")
    return surfaces


def rendered_blocks() -> tuple[Block, ...]:
    """The renderable protected blocks as the production renderers emit them today."""
    from trw_mcp.bootstrap._client_integrations import render_transport_loss_guidance
    from trw_mcp.state.claude_md.sections._tool_lifecycle import render_deliver_gate_statement

    return (
        Block(GATE, render_deliver_gate_statement().strip("\n"), GATE_ANCHOR),
        Block(TRANSPORT, render_transport_loss_guidance().strip("\n"), TRANSPORT_ANCHOR),
    )


def pinned_sources(monorepo_root: Path | None) -> tuple[dict[str, bytes], dict[str, str]]:
    """(source name -> current raw bytes, source name -> pinned sha256) for every canonical source.

    Every source is read with ``read_bytes`` — never ``read_text``, whose
    universal-newline translation turns CRLF into LF before hashing.
    """
    from importlib.resources import files

    from trw_mcp.bootstrap._client_integrations import render_transport_loss_guidance

    lifecycle = files("trw_mcp").joinpath("data/surfaces/tool-lifecycle.md").read_bytes()
    texts = {
        "tool-lifecycle.md deliver gate": section(lifecycle, GATE_SECTION_HEADING),
        "render_transport_loss_guidance()": render_transport_loss_guidance().encode("utf-8"),
    }
    pins = {"tool-lifecycle.md deliver gate": GATE_SECTION_SHA256, "render_transport_loss_guidance()": TRANSPORT_SHA256}
    if monorepo_root is not None:
        for (rel, heading), pin in MONOREPO_SECTION_SHA256.items():
            name = f"{rel} {heading}"
            texts[name] = section((monorepo_root / rel).read_bytes(), heading)
            pins[name] = pin
    return texts, pins


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

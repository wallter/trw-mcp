"""PRD-QUAL-143-FR01 / PRD-CORE-341: one instruction block, one writer.

The externalization carrier once moved the TRW block into a ``.trw`` sidecar
behind an ``@`` import that a second writer also fed, so the sidecar went
unregenerated and an agent read a deliver gate that no longer matched the
bundled one. PRD-CORE-341 makes ``.trw/INSTRUCTIONS.md`` the one TRW-owned file
every AGENTS.md writer renders the block into, with AGENTS.md holding only the
link. These tests pin that single-writer contract: the block carries memory
routing, feedback reporting and the offline table once each, the deliver gate
occurs once, and the sidecar-era plumbing stays removed. The replacement
behaviour (link body, migration, refusal, no retirement) is covered by
``test_agents_md_link.py``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest

from tests import _protected_blocks as pb
from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

_MEMORY = "### Memory Routing"
_OFFLINE = "### Troubleshooting: the MCP surface is absent"
_GATE = "## Deliver Gate"
_DELEGATION = "not to verify your own work"
_STALE_GATE = "build_check_result=pass"
_FEEDBACK = re.compile(r"^### Reporting Issues to TRW$|^TRW issues: see ", re.MULTILINE)

#: Sidecar headings that do not survive verbatim in the AGENTS.md block, each
#: with the surface that now carries the statement.
_DISPOSITIONS = {
    "## TRW Behavioral Protocol (Auto-Generated)": "the block's Start/Accept/Verify list; full table in trw_session_start",
    "### Session Boundaries": "the block's closing paragraph (render_deliver_gate_statement)",
    # PRD-CORE-301-FR13: the full policy moved behind the block's Memory Routing pointer.
    "#### Project vs user tier": "memory-routing.md, served by the trw://framework/memory-routing resource",
    "#### Feedback semantics": "memory-routing.md, served by the trw://framework/memory-routing resource",
}


def _agents_bodies() -> dict[str, Callable[[], str]]:
    from trw_mcp.state.claude_md._static_sections import render_agents_trw_section, render_minimal_protocol

    return {"minimal": render_minimal_protocol, "full": render_agents_trw_section}


@pytest.mark.parametrize("body_name", ["minimal", "full"])
def test_agents_block_states_each_section_once(body_name: str) -> None:
    body = _agents_bodies()[body_name]()

    assert body.count(_MEMORY) == 1
    assert body.count(_OFFLINE) == 1
    assert body.count(_GATE) == 1
    assert len(_FEEDBACK.findall(body)) == 1
    assert body.replace("\n", " ").count(_DELEGATION) == 1
    assert _STALE_GATE not in body


def test_every_sidecar_heading_survives_or_has_a_home() -> None:
    """Diff the retired sidecar's headings into the post-change closure."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state.claude_md._static_sections import (
        render_ceremony_quick_ref,
        render_closing_reminder,
        render_imperative_opener,
        render_memory_harmonization,
        render_minimal_protocol,
    )
    from trw_mcp.state.claude_md.sections._feedback import render_feedback_reporting

    sidecar = "".join(
        (
            render_imperative_opener(),
            render_ceremony_quick_ref(),
            render_memory_harmonization(),
            render_feedback_reporting(get_config().client_profile),
            render_closing_reminder(),
        )
    )
    closure = render_minimal_protocol()
    missing = [
        heading
        for heading in re.findall(r"^#{2,4} .+$", sidecar, re.MULTILINE)
        if heading not in closure and heading not in _DISPOSITIONS
    ]
    assert missing == []
    assert _DELEGATION in render_imperative_opener().replace("\n", " ")
    assert _DELEGATION in closure.replace("\n", " ")


def test_sidecar_plumbing_is_gone() -> None:
    from trw_mcp.models.config import TRWConfig

    src = PACKAGE_ROOT / "src/trw_mcp"
    sync_hash = (src / "state/claude_md/_sync_hash.py").read_text(encoding="utf-8")
    assert "instruction_externalize" not in sync_hash
    assert "instruction_external_filename" not in sync_hash
    assert not (src / "state/claude_md/_carrier_externalize.py").exists()
    assert "instruction_externalize" not in TRWConfig.model_fields
    assert "instruction_external_filename" not in TRWConfig.model_fields


# ── PRD-CORE-301-FR08: the three protected blocks stay byte-for-byte ──────────
# Checker, pins and soundness scope: tests/_protected_blocks.py.


def _supported_ides() -> list[str]:
    from trw_mcp.bootstrap._utils import SUPPORTED_IDES

    return list(SUPPORTED_IDES)


def test_every_installable_client_has_a_pinned_carrier_map() -> None:
    """A new client without a pin, or a retired one still pinned, fails here rather than going unguarded."""
    assert set(pb.CARRIERS) == set(_supported_ides())


@pytest.mark.parametrize("ide", _supported_ides())
def test_protected_blocks_are_byte_identical(ide: str, tmp_path: Path) -> None:
    """Every surface install and update write carries each block verbatim, once, exactly where it was."""
    from trw_mcp.bootstrap import init_project, update_project

    (tmp_path / ".git").mkdir()
    blocks = pb.rendered_blocks()
    assert pb.CARRIERS.get(ide), f"{ide} has no pinned carrier map"

    assert init_project(tmp_path, ide=ide)["errors"] == []
    assert pb.surface_violations(pb.read_surfaces(tmp_path), blocks, pb.CARRIERS[ide]) == []

    assert update_project(tmp_path, ide=ide)["errors"] == []
    after_update = {**pb.CARRIERS[ide], **pb.UPDATE_ADDS}
    assert pb.surface_violations(pb.read_surfaces(tmp_path), blocks, after_update) == []


def test_protected_canonical_sources_are_pinned() -> None:
    texts, pins = pb.pinned_sources(MONOREPO_ROOT)

    assert len(pins) == (2 + len(pb.MONOREPO_SECTION_SHA256) if MONOREPO_ROOT else 2)
    assert pb.digest_violations(texts, pins) == []


_LIFECYCLE = "data/surfaces/tool-lifecycle.md"
_CRLF_SOURCES = [_LIFECYCLE] + ([rel for rel, _ in pb.MONOREPO_SECTION_SHA256] if MONOREPO_ROOT else [])


@pytest.mark.parametrize("converted", sorted(set(_CRLF_SOURCES)))
def test_a_crlf_conversion_of_a_canonical_source_is_caught(
    converted: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An LF->CRLF rewrite keeps every visible character, so only a raw-bytes digest can see it."""
    from importlib.resources import files

    package, repo = tmp_path / "pkg", tmp_path / "repo" if MONOREPO_ROOT else None
    copies = {package / _LIFECYCLE: files("trw_mcp").joinpath(_LIFECYCLE).read_bytes()}
    if repo is not None and MONOREPO_ROOT is not None:
        copies.update({repo / rel: (MONOREPO_ROOT / rel).read_bytes() for rel, _ in pb.MONOREPO_SECTION_SHA256})
    for path, data in copies.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    monkeypatch.setattr("importlib.resources.files", lambda _name: package)
    assert pb.digest_violations(*pb.pinned_sources(repo)) == [], "the unconverted copies must match their pins"

    target = package / _LIFECYCLE if converted == _LIFECYCLE else tmp_path / "repo" / converted
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))

    violations = pb.digest_violations(*pb.pinned_sources(repo))
    assert violations, f"a CRLF conversion of {converted} kept its pinned digest"
    assert all("sha256" in v for v in violations)


def _plant_surfaces(blocks: tuple[pb.Block, ...]) -> dict[str, str]:
    body = "\n\n".join(block.canonical for block in blocks)
    return {"CARRIER.md": f"# Project\n\nUser prose.\n\n{body}\n\nMore prose.\n", "OTHER.md": "# Unrelated\n"}


def _reword(text: str, block: pb.Block) -> str:
    middle = len(block.canonical) // 2
    changed = (
        block.canonical[:middle] + ("X" if block.canonical[middle] != "X" else "Y") + block.canonical[middle + 1 :]
    )
    return text.replace(block.canonical, changed)


_PLANTS: dict[str, Callable[[dict[str, str], pb.Block], object]] = {
    "one byte changed inside the block": lambda s, b: s.update({"CARRIER.md": _reword(s["CARRIER.md"], b)}),
    "block deleted from its carrier": lambda s, b: s.update({"CARRIER.md": s["CARRIER.md"].replace(b.canonical, "")}),
    "block stated twice": lambda s, b: s.update({"CARRIER.md": s["CARRIER.md"] + "\n" + b.canonical + "\n"}),
    "block copied into a non-carrier": lambda s, b: s.update({"OTHER.md": s["OTHER.md"] + b.canonical + "\n"}),
    "carrier file not written": lambda s, b: s.pop("CARRIER.md"),
}


@pytest.mark.parametrize("plant", sorted(_PLANTS))
@pytest.mark.parametrize("block_name", [pb.GATE, pb.TRANSPORT])
def test_a_planted_change_to_a_protected_block_is_caught(plant: str, block_name: str) -> None:
    blocks = pb.rendered_blocks()
    carriers = {"CARRIER.md": frozenset({pb.GATE, pb.TRANSPORT})}
    surfaces = _plant_surfaces(blocks)
    assert pb.surface_violations(surfaces, blocks, carriers) == [], "the unplanted fixture must be clean"

    _PLANTS[plant](surfaces, next(b for b in blocks if b.name == block_name))

    violations = pb.surface_violations(surfaces, blocks, carriers)
    assert violations, f"{plant} in {block_name} went undetected"
    assert all(block_name in v for v in violations if plant != "carrier file not written")


@pytest.mark.parametrize(
    ("text", "expected"),
    [(b"abc", []), (b"abd", ["source: sha256"]), (None, ["source: no text"])],
    ids=["unchanged", "one-byte-edit", "source-missing"],
)
def test_a_changed_canonical_source_is_caught(text: bytes | None, expected: list[str]) -> None:
    texts = {} if text is None else {"source": text}

    violations = pb.digest_violations(texts, {"source": pb.sha256(b"abc")})

    assert [v[: len(e)] for v, e in zip(violations, expected, strict=True)] == expected


def test_a_section_runs_to_the_next_heading_at_its_level_and_a_missing_one_is_refused() -> None:
    text = b"# T\n\n## A\n\nbody\n\n### A.1\n\nnested\n\n## B\n\nother\n"

    assert pb.section(text, "## A") == b"## A\n\nbody\n\n### A.1\n\nnested"
    # Only trailing \n bytes are dropped, so a CRLF section keeps its \r and hashes differently.
    assert pb.section(text.replace(b"\n", b"\r\n"), "## A") == b"## A\r\n\r\nbody\r\n\r\n### A.1\r\n\r\nnested\r\n\r"
    with pytest.raises(LookupError, match="## C"):
        pb.section(text, "## C")


# ── PRD-CORE-301-FR02: one renderer for every client instruction mirror ──────
# The claude-code block (``render_agents_trw_section``) is the shared body; a
# mirror may add client framing around it but may not restate the protocol in
# a renderer of its own. Profile-gated fragments are the only allowed variance:
# the light-ceremony feedback one-liner and the ``include_delegation`` block.

_MIRRORS = {
    "codex": ".codex/INSTRUCTIONS.md",
    "cursor-ide": ".cursor/rules/trw-ceremony.mdc",
    "opencode": ".opencode/INSTRUCTIONS.md",
}
_FEEDBACK_BLOCK = re.compile(r"<!-- BEGIN: feedback-reporting -->.*?<!-- END: feedback-reporting -->\n", re.DOTALL)
#: PRD-CORE-301-FR02 measured target: a mirror is at most this multiple of the claude-code block.
_MAX_RATIO = 1.25


def _install(ide: str, root: Path) -> None:
    from trw_mcp.bootstrap import init_project

    (root / ".git").mkdir(parents=True)
    assert init_project(root, ide=ide)["errors"] == []


def _claude_code_block(root: Path) -> str:
    """The claude-code block: the body of ``.trw/INSTRUCTIONS.md`` after its generated header line (PRD-CORE-341-FR02)."""
    _install("claude-code", root)
    text = (root / ".trw/INSTRUCTIONS.md").read_text(encoding="utf-8")
    header, _, body = text.partition("\n")
    assert header.startswith("<!-- TRW AUTO-GENERATED")
    return body


def _shared_chunks(block: str, *, with_delegation: bool) -> list[str]:
    """The claude-code block cut at its profile-gated fragments into chunks every mirror must hold verbatim.

    The gated fragments are the full-mode feedback block and, since
    PRD-CORE-301-FR13, the one-line delegation-guide pointer.
    """
    from trw_mcp.state.claude_md.sections._delegation import DELEGATION_GUIDE_POINTER

    pieces = _FEEDBACK_BLOCK.split(block)
    chunks = [c.strip("\n") for piece in pieces for c in piece.split(DELEGATION_GUIDE_POINTER)]
    if with_delegation:
        chunks.append(DELEGATION_GUIDE_POINTER.strip("\n"))
    return [c for c in chunks if c]


def _tokens(text: str) -> int:
    """chars/4, the unit ``scripts/measure_context_cost.py`` reports instruction_files in."""
    return -(-len(text) // 4)


@pytest.mark.parametrize("ide", sorted(_MIRRORS))
def test_every_client_mirror_renders_from_one_renderer(ide: str, tmp_path: Path) -> None:
    """Each installed mirror carries the claude-code block verbatim."""
    from trw_mcp.models.config._profiles import resolve_client_profile

    block = _claude_code_block(tmp_path / "claude-code")
    _install(ide, tmp_path / ide)
    mirror = (tmp_path / ide / _MIRRORS[ide]).read_text(encoding="utf-8")

    chunks = _shared_chunks(block, with_delegation=resolve_client_profile(ide).include_delegation)
    missing = [chunk.splitlines()[0] for chunk in chunks if chunk not in mirror]
    assert missing == [], f"{ide} does not render these claude-code block chunks verbatim"


#: PRD-CORE-301-FR13 shrank the shared block to ~1,700 tokens; cursor-ide's fixed
#: IDE appendix (trigger phrases, verification pass, drift, planning, pre-compaction:
#: ~590 tokens) now exceeds 0.25x of it. FR02's cursor target stays open (see the
#: PRD); strict, so meeting it turns this red and the mark must come off.
_CURSOR_RATIO_OPEN = pytest.mark.xfail(strict=True, reason="PRD-CORE-301-FR02 cursor target open after FR13")


@pytest.mark.parametrize(
    "ide", [pytest.param(ide, marks=_CURSOR_RATIO_OPEN) if ide == "cursor-ide" else ide for ide in sorted(_MIRRORS)]
)
def test_every_client_mirror_stays_within_the_ratio(ide: str, tmp_path: Path) -> None:
    """PRD-CORE-301-FR02 measured target: a mirror is at most 1.25x the claude-code block."""
    block = _claude_code_block(tmp_path / "claude-code")
    _install(ide, tmp_path / ide)
    mirror = (tmp_path / ide / _MIRRORS[ide]).read_text(encoding="utf-8")

    assert _tokens(mirror) <= _MAX_RATIO * _tokens(block), (
        f"{ide}: {_tokens(mirror)} tokens > {_MAX_RATIO} x the claude-code block ({_tokens(block)})"
    )


def test_a_mirror_with_its_own_protocol_text_is_caught(tmp_path: Path) -> None:
    """Negative: a mirror that paraphrases one shared chunk fails the verbatim check."""
    block = _claude_code_block(tmp_path)
    chunks = _shared_chunks(block, with_delegation=True)
    paraphrased = block.replace("## Workflow\n", "## Codex Workflow\n", 1)

    assert [chunk for chunk in chunks if chunk not in paraphrased] != []
    assert [chunk for chunk in chunks if chunk not in block] == []

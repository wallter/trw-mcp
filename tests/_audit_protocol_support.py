"""Loaders and parsers for the audit-protocol contract tests (PRD-QUAL-128).

The point of this module is that the tests read the *protocol* rather than the
*prose*: Section G's report schema is parsed as YAML, Section B's taxonomy and
Section E's verdict criteria are parsed as tables. A reword then costs nothing
and a schema change costs a failing test — which is the trade PRD-QUAL-128 buys.

Belongs to ``test_audit_protocol_contracts.py`` and
``test_audit_protocol_single_source.py``. Not a test module itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA = REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data"

FRAMEWORK_PATH = _DATA / "skills" / "trw-audit" / "audit-framework.md"
AUDITOR_PATH = _DATA / "agents" / "trw-auditor.md"
ADAPTER_PATH = _DATA / "agents" / "trw-adversarial-auditor.md"
IMPLEMENTER_PATH = _DATA / "agents" / "trw-implementer.md"
SKILL_PATH = _DATA / "skills" / "trw-audit" / "SKILL.md"

#: All seven authored ``trw-audit/SKILL.md`` projections. Four were guarded
#: before PRD-QUAL-128; ``.cursor``, ``.github`` and ``.agents`` were not, and
#: all three carried the drifted 10-row NFR table.
SKILL_PROJECTIONS: tuple[Path, ...] = (
    _DATA / "skills" / "trw-audit" / "SKILL.md",
    _DATA / "codex" / "skills" / "trw-audit" / "SKILL.md",
    _DATA / "copilot" / "skills" / "trw-audit" / "SKILL.md",
    REPO_ROOT / ".claude" / "skills" / "trw-audit" / "SKILL.md",
    REPO_ROOT / ".cursor" / "skills" / "trw-audit" / "SKILL.md",
    REPO_ROOT / ".github" / "skills" / "trw-audit" / "SKILL.md",
    REPO_ROOT / ".agents" / "skills" / "trw-audit" / "SKILL.md",
)

BUNDLED_AGENTS_DIR = _DATA / "agents"

#: Sentinel-delimited shared policy blocks are injected identically into every
#: agent by ``scripts/agent_fragments.py``. Strip them before measuring an
#: agent's *authored* content, or an agent gets charged for policy it does not own.
_FRAGMENT_BLOCK = re.compile(r"(?ms)^<!-- trw:[a-z-]+:start -->.*?^<!-- trw:[a-z-]+:end -->\n?")
_MARKER = re.compile(r"\{tool:(trw_\w+)\}")


def expand_markers(text: str) -> str:
    """Expand ``{tool:trw_x}`` placeholders so bundled and mirror copies compare."""
    return _MARKER.sub(lambda m: m.group(1), text)


def strip_fragments(text: str) -> str:
    return _FRAGMENT_BLOCK.sub("", text)


def level_two_headings(text: str) -> set[str]:
    """Level-2 headings in *text*, sentinel fragments removed first."""
    return {line.strip() for line in strip_fragments(text).splitlines() if line.startswith("## ")}


def section(framework: str, letter: str) -> str:
    """Return the body of ``## Section <letter>.`` up to the next section."""
    parts = framework.split(f"\n## Section {letter}.")
    if len(parts) < 2:
        raise AssertionError(f"audit-framework.md has no Section {letter}")
    return parts[1].split("\n## Section ")[0]


def fenced_yaml(text: str) -> list[dict[str, Any]]:
    """Every fenced ``yaml`` block in *text*, parsed."""
    blocks = []
    for raw in re.findall(r"```yaml\n(.*?)```", text, re.DOTALL):
        loaded = yaml.safe_load(raw)
        if isinstance(loaded, dict):
            blocks.append(loaded)
    return blocks


def tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """Every markdown table in *text* as ``(header_cells, body_rows)``."""
    found: list[tuple[list[str], list[list[str]]]] = []
    run: list[str] = []
    for line in [*text.splitlines(), ""]:
        if line.lstrip().startswith("|"):
            run.append(line)
            continue
        if len(run) >= 3:
            found.append(([_cell(c) for c in _split(run[0])], [[_cell(c) for c in _split(r)] for r in run[2:]]))
        run = []
    return found


def _split(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return stripped.split("|")


def _cell(text: str) -> str:
    return text.replace("**", "").replace("`", "").strip()


def table_with_header(text: str, *required: str) -> tuple[list[str], list[list[str]]]:
    """The one table whose header carries every *required* column name."""
    wanted = {r.casefold() for r in required}
    matches = [t for t in tables(text) if wanted <= {c.casefold() for c in t[0]}]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one table with columns {sorted(wanted)}, found {len(matches)}")
    return matches[0]


@dataclass(frozen=True)
class Protocol:
    """The audit-protocol surfaces as text, so a fixture can plant a regression."""

    framework: str
    auditor: str
    adapter: str
    implementer: str
    skill: str
    surfaces: dict[str, str] = field(default_factory=dict)

    def _rewrite(self, field_name: str, patched: str) -> Protocol:
        """Rewrite one surface AND its entry in the 18-surface scan set.

        Patching only the named attribute would leave the scan set holding the
        unpatched text, so a planted violation would never reach the assertion
        that reads ``surfaces`` — the fixture would prove nothing.
        """
        previous: str = getattr(self, field_name)
        surfaces = {k: (patched if v == previous else v) for k, v in self.surfaces.items()}
        return Protocol(
            framework=patched if field_name == "framework" else self.framework,
            auditor=patched if field_name == "auditor" else self.auditor,
            adapter=patched if field_name == "adapter" else self.adapter,
            implementer=patched if field_name == "implementer" else self.implementer,
            skill=patched if field_name == "skill" else self.skill,
            surfaces=surfaces,
        )

    def with_framework(self, old: str, new: str, *, count: int = 1) -> Protocol:
        return self._rewrite("framework", _sub(self.framework, old, new, "audit-framework.md", count))

    def with_section_g(self, old: str, new: str) -> Protocol:
        """Rewrite inside Section G only — Section E carries a similar schema."""
        head, marker, tail = self.framework.partition("\n## Section G.")
        if not marker:
            raise LookupError("audit-framework.md has no Section G")
        return self._rewrite("framework", head + marker + _sub(tail, old, new, "Section G", 1))

    def with_auditor(self, old: str, new: str) -> Protocol:
        return self._rewrite("auditor", _sub(self.auditor, old, new, "trw-auditor.md", 1))

    def with_adapter(self, old: str, new: str, *, count: int = 1) -> Protocol:
        return self._rewrite("adapter", _sub(self.adapter, old, new, "trw-adversarial-auditor.md", count))

    def with_implementer(self, old: str, new: str) -> Protocol:
        return self._rewrite("implementer", _sub(self.implementer, old, new, "trw-implementer.md", 1))

    def with_skill(self, old: str, new: str) -> Protocol:
        return self._rewrite("skill", _sub(self.skill, old, new, "trw-audit/SKILL.md", 1))

    def with_surface(self, name: str, text: str) -> Protocol:
        if name not in self.surfaces:
            raise LookupError(f"unknown surface {name!r}")
        return replace(self, surfaces={**self.surfaces, name: text})


def _sub(text: str, old: str, new: str, label: str, count: int) -> str:
    """Replace *old* with *new*, refusing to plant a fixture that matches nothing.

    Raises ``LookupError``, deliberately NOT ``AssertionError``: a negative
    fixture whose planted text never landed would otherwise satisfy its own
    ``pytest.raises(AssertionError)`` and report a fail-closed proof it never
    performed — the loosest possible green check.
    """
    if text.count(old) < count:
        raise LookupError(f"fixture anchor appears {text.count(old)}x (need {count}) in {label}: {old!r}")
    return text.replace(old, new, count)


def load_protocol() -> Protocol:
    """Read every surface from disk once."""
    surfaces = {
        f"agent:{p.name}": expand_markers(p.read_text(encoding="utf-8"))
        for p in sorted(BUNDLED_AGENTS_DIR.glob("*.md"))
    }
    surfaces.update(
        {
            f"skill:{p.relative_to(REPO_ROOT)}": p.read_text(encoding="utf-8")
            for p in SKILL_PROJECTIONS
        }
    )
    return Protocol(
        framework=FRAMEWORK_PATH.read_text(encoding="utf-8"),
        auditor=expand_markers(AUDITOR_PATH.read_text(encoding="utf-8")),
        adapter=expand_markers(ADAPTER_PATH.read_text(encoding="utf-8")),
        implementer=expand_markers(IMPLEMENTER_PATH.read_text(encoding="utf-8")),
        skill=SKILL_PATH.read_text(encoding="utf-8"),
        surfaces=surfaces,
    )


def report_schema(protocol: Protocol) -> dict[str, Any]:
    """The Section G report schema, parsed."""
    blocks = fenced_yaml(section(protocol.framework, "G"))
    if len(blocks) != 1:
        raise AssertionError(f"Section G must hold exactly one YAML report schema, found {len(blocks)}")
    return blocks[0]


def config_max_audit_cycles() -> int:
    """The declared default of ``TRWConfig.max_audit_cycles`` (its real owner)."""
    from trw_mcp.models.config import TRWConfig

    default = TRWConfig.model_fields["max_audit_cycles"].default
    assert isinstance(default, int)
    return default

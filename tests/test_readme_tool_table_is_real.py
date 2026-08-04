"""Every tool the public README advertises must actually be registered.

``pyproject.toml`` sets ``readme = "README.md"``, so this file IS the PyPI
long_description — the page a prospective user reads before installing. When
``trw_entity_risk_map`` was removed (UF-011), the sweep updated the changelog,
the inventory, the bundled agents and this repo's own Claude Code agent, and
missed the README. The package was then published with its front page listing a
tool the wheel does not contain.

The sting is that the removal's own changelog entry justifies the deletion on
the grounds that the tool "could never return data to anyone" yet was
"advertised to every calling LLM". The release removed a tool for being falsely
advertised and shipped a README that falsely advertised it.

Nothing could have caught it: ``make inventory`` syncs the ``<!-- inv:tools -->``
COUNT in the heading, not the tool NAMES in the table below it, so the count
stayed truthful while the list went stale. This closes that gap — the names are
now checked against the same registry ``scripts/check_agent_contracts.py`` uses
for agent grants.
"""

from __future__ import annotations

import re
from pathlib import Path

_README = Path(__file__).resolve().parents[1] / "README.md"

#: The tool table lives under this heading and ends at the next ``## ``.
_TOOLS_HEADING = re.compile(r"^## MCP Tools\b", re.MULTILINE)

#: ``| **Category** | `a`, `b` | Purpose |`` — column 2 holds the tool names.
_TABLE_ROW = re.compile(r"^\|[^|]*\|([^|]*)\|")

#: Names inside the table that are prose or links, not tools. Keep this EMPTY
#: unless a real exception appears: an exclusion set with no members is a
#: stronger claim than one that has quietly grown.
_NOT_TOOLS: frozenset[str] = frozenset()


def _readme_tool_names() -> list[str]:
    """Bare tool names (no ``trw_`` prefix) from the README's MCP Tools table."""
    text = _README.read_text(encoding="utf-8")
    start = _TOOLS_HEADING.search(text)
    assert start is not None, "README no longer has an '## MCP Tools' section"
    body = text[start.end() :]
    end = body.find("\n## ")
    if end != -1:
        body = body[:end]

    names: list[str] = []
    for line in body.splitlines():
        if not line.startswith("|") or line.startswith("|---") or "| Category " in line:
            continue
        cell = _TABLE_ROW.match(line)
        if cell is None:
            continue
        names.extend(re.findall(r"`([a-z0-9_]+)`", cell.group(1)))
    return [n for n in names if n not in _NOT_TOOLS]


def test_the_table_is_found_and_non_empty() -> None:
    """Non-vacuity control.

    A parser that silently matched nothing would make the assertion below pass
    forever — which is the same defect class (a check that cannot tell "found
    nothing wrong" from "never looked") that the removal itself was about.
    """
    names = _readme_tool_names()

    assert len(names) >= 15, f"README tool table parsed as {len(names)} names; the parser is broken"
    assert "session_start" in names, "the parser lost a name that is definitely in the table"


#: ``def trw_x(`` in the tool package is the registration form. Same predicate
#: ``scripts/check_agent_contracts.py`` uses to catch a dead agent grant; kept
#: local so this test does not depend on a sibling script's import path.
_TOOL_DEF = re.compile(r"^\s*(?:async )?def (trw_\w+)\(", re.MULTILINE)
_TOOLS_PKG = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "tools"


def _registered_tool_names() -> set[str]:
    return {
        match
        for path in _TOOLS_PKG.rglob("*.py")
        for match in _TOOL_DEF.findall(path.read_text(encoding="utf-8", errors="replace"))
    }


#: Tools named individually rather than counted. A COUNT here would be census
#: data — a literal standing in for the cardinality of a population that grows
#: and shrinks elsewhere, which is what `make census-check` exists to reject (it
#: rejected the first draft of this line). Naming three tools the protocol itself
#: mandates is a stronger floor anyway: a scan returning the right NUMBER of
#: wrong names would satisfy a count and fails this.
_MUST_BE_FOUND = frozenset({"trw_session_start", "trw_deliver", "trw_build_check"})


def test_the_registry_scan_finds_the_real_tool_surface() -> None:
    """Second non-vacuity control, and the reason this test exists at all.

    An empty registry would make the subset assertion below pass trivially. The
    first draft of that assertion resolved the surface by ``dir()`` over
    ``server._tools``, got an empty set, and *skipped* — a green run proving
    nothing. Assert the floor instead of skipping.
    """
    registered = _registered_tool_names()

    missing = sorted(_MUST_BE_FOUND - registered)
    assert not missing, f"tool scan missed mandated tools {missing}; the predicate is broken"


def test_every_readme_tool_is_registered() -> None:
    """The advertised surface must be a subset of the real one.

    A name here that no ``def trw_x(`` defines is a promise the wheel cannot
    keep, printed on the project's PyPI page.
    """
    registered = _registered_tool_names()

    missing = sorted(n for n in _readme_tool_names() if f"trw_{n}" not in registered)

    assert not missing, (
        "README.md advertises tools that are not registered. It is the PyPI "
        "long_description, so this ships to every visitor: " + repr(missing)
    )

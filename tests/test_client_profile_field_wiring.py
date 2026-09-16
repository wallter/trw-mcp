"""Derived wiring audit for ``ClientProfile`` / ``WriteTargets`` fields.

A profile field nobody reads is a configuration knob an operator can set with
no effect — this repository's dominant defect class. ``scripts/
check_config_field_consumers.py`` already ratchets that property for
``TRWConfig`` fields, but it never covered ``ClientProfile``, and its
name-keyed AST scan cannot distinguish ``config.hooks_enabled`` from
``profile.hooks_enabled`` anyway.

So this module DERIVES the answer from the model and the production tree
instead of asserting a hand-written list:

* ``model_fields`` supplies the field set, so a newly added field is audited
  the moment it exists. A hand-maintained list cannot notice a field it was
  never told about.
* the reader scan only counts an attribute/``getattr`` access whose BASE
  expression names a profile or a write-targets holder, so constructing a
  profile in ``_profiles.py`` (``scoring_weights=...``) is not mistaken for
  reading one.
* two distinct verdicts, because they have different severities:
  ``UNREAD`` (no production reader at all) and ``DOC_PROJECTION_ONLY`` (the
  only readers are inside ``trw_mcp/client_profiles/``, the package that
  renders ``docs/client-profiles/matrix.md``). The second is the shape where
  the generated matrix DECLARES a capability and nothing enforces it — the
  doc's own generator is its only consumer.

Both verdict sets are pinned exactly. A field that gains a reader fails just
as loudly as a new field that has none, because a stale waiver is how a fixed
defect gets re-introduced unnoticed.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp"

# Public-mirror guard mirrors test_client_profile_docs.py: the bundled-data and
# docs assertions below are monorepo invariants.
if not (_REPO_ROOT / "scripts").is_dir():
    pytest.skip(
        "monorepo-only invariant (repo-root scripts/ absent in standalone mirror)",
        allow_module_level=True,
    )

from trw_mcp.models.config._client_profile import ClientProfile, WriteTargets

#: The model's own module (a declaration is not a reader) and package
#: ``__init__`` files (a re-export is not a reader).
_MODEL_MODULE = _SRC / "models" / "config" / "_client_profile.py"

#: Package whose only product is generated documentation. A reader here proves
#: the value reaches a doc table, not that any behavior branches on it.
_DOC_RENDERER_PKG = _SRC / "client_profiles"

#: Substrings that mark an attribute base as a profile / write-targets holder:
#: ``profile``, ``client_profile``, ``self.client_profile``,
#: ``resolve_client_profile("codex")``, ``cfg.client_profile``,
#: ``profile.write_targets``, and the local ``targets`` alias catalog.py uses.
#: Deliberately does NOT include ``row`` — ``ClientProfileDocRow`` is a copy
#: made FOR the doc renderer, and counting it as a reader would launder the
#: doc-projection-only verdict this module exists to expose.
_PROFILE_BASE_MARKERS = ("profile", "target")

#: Lower bound on the scanned tree. A scan that silently walked an empty or
#: relocated source root would report every field unread (or, with the exact
#: assertions below, fail for the wrong reason); this makes vacuity explicit.
_MIN_SCANNED_MODULES = 400

#: Field whose wiring is not in question, asserted separately so a predicate
#: regression cannot quietly turn the whole scan into "nothing is read".
_CANARY_FIELD = "ceremony_mode"


def _production_modules() -> list[Path]:
    return [
        path
        for path in sorted(_SRC.rglob("*.py"))
        if "__pycache__" not in path.parts and path.name != "__init__.py" and path != _MODEL_MODULE
    ]


def _base_is_profile_holder(node: ast.expr) -> bool:
    text = ast.unparse(node).lower()
    return any(marker in text for marker in _PROFILE_BASE_MARKERS)


def _module_readers(tree: ast.AST, fields: frozenset[str]) -> set[str]:
    """Field names this module READS off a profile/write-targets holder."""
    found: set[str] = set()
    dynamic_base = False
    dynamic_literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in fields and _base_is_profile_holder(node.value):
            found.add(node.attr)
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and node.args
                and _base_is_profile_holder(node.args[0])
            ):
                # getattr(profile.write_targets, flag, False): the flag names
                # themselves are string constants elsewhere in the module.
                dynamic_base = True
                if (
                    len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                ):
                    dynamic_literals.add(node.args[1].value)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in fields:
            dynamic_literals.add(node.value)
    if dynamic_base:
        found |= dynamic_literals & fields
    return found


def _scan() -> tuple[dict[str, set[Path]], int]:
    """Return ``(field -> modules that read it, modules scanned)``."""
    fields = frozenset(ClientProfile.model_fields) | frozenset(WriteTargets.model_fields)
    readers: dict[str, set[Path]] = {name: set() for name in fields}
    modules = _production_modules()
    for path in modules:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a syntactically broken tree fails elsewhere
            continue
        for name in _module_readers(tree, fields):
            readers[name].add(path)
    return readers, len(modules)


# ---------------------------------------------------------------------------
# Waivers. Each entry is a KNOWN defect with a decision attached, not a
# permission slip: the exact-equality assertions below fail when an entry is
# fixed (stale waiver) as well as when a new one appears.
# ---------------------------------------------------------------------------

#: Fields with no production reader anywhere in ``trw_mcp/``. Audited
#: 2026-09-12; see docs/CLIENT-PROFILES.md §What will bite you.
_UNREAD_WAIVERS: dict[str, str] = {
    # Per-profile value diverges (light profiles and cursor-cli set their own
    # list) yet resolve_task_profile takes mandatory_phases from the
    # ceremony-depth contract instead: models/task_profile.py:183.
    "mandatory_phases": "superseded by get_ceremony_depth_contract(); wire or remove",
    # Eval dimension weights. trw-eval carries its own weights and never reads
    # this field, so 'scoring calibration' is a designed contract only.
    "scoring_weights": "no consumer in trw-mcp or trw-eval",
    # No sub-instruction writer consults it; instruction_max_lines is the only
    # budget any carrier enforces.
    "sub_instruction_max_lines": "no writer consults it",
    # PRD-INTENT-002 FR05b, superseded 2026-09-07 by best-effort list_changed.
    "on_transition": "documented as accepted-and-inert legacy compatibility",
    # cursor-cli's .cursor/cli.json is written unconditionally by
    # generate_cursor_cli_config; the flag gates nothing. Same class as
    # agents_md_primary, removed 2026-07-28 for exactly this reason.
    "cli_config": "generate_cursor_cli_config is dispatched by client id, not by this flag",
}

#: Fields whose only readers are the doc-rendering package. The generated
#: matrix column exists; the behavior it implies does not.
_DOC_PROJECTION_WAIVERS: dict[str, str] = {
    "learning_recall_enabled": "matrix 'Recall' column; no recall path branches on it",
    "mcp_instructions_enabled": "matrix 'MCP Instructions' column; no renderer branches on it",
    "cursor_rules": "_write_target_label only; the cursor writer is dispatched by client id",
    "copilot_instructions": "_write_target_label only; the copilot writer is dispatched by client id",
    "antigravitycli_md": "_write_target_label + uninstall surface; no writer branches on it",
}


_CLIENT_PROFILES_DOC = _REPO_ROOT / "docs" / "CLIENT-PROFILES.md"
_OPENCODE_DATA = _SRC / "data" / "opencode"


def _doc_bullet_names(prefix: str) -> set[str]:
    """Names backticked on the single OpenCode bullet line starting with *prefix*.

    The bullet spells one artifact as a full path and the rest as bare skill
    names ("- `.opencode/commands/trw-deliver.md` — plus `trw-prd-ready`, …"),
    so every backticked token is reduced to its ``trw-*`` name. Only that one
    line is read, keeping the licence-gated distill commands named in a later
    paragraph out of the comparison.
    """
    names: set[str] = set()
    for line in _CLIENT_PROFILES_DOC.read_text(encoding="utf-8").splitlines():
        if not line.startswith(prefix):
            continue
        for token in line.split("`")[1::2]:
            stem = token.split("/")[-1] if "/" not in token or not token.endswith("SKILL.md") else token.split("/")[-2]
            names.add(stem.removesuffix(".md"))
    return names


@pytest.mark.unit
def test_opencode_doc_names_every_bundled_command_and_skill() -> None:
    """Derived doc/disk parity for the OpenCode managed set.

    The neighbouring assertion in ``test_client_profile_docs.py`` checks four
    exemplar bullets, which cannot notice a command or skill added to
    ``data/opencode/`` — the exact shape that once shipped a client without a
    skill the framework told its agents to use. This compares the doc's named
    set against the bundled directories in both directions.
    """
    on_disk_commands = {path.stem for path in (_OPENCODE_DATA / "commands").glob("*.md")}
    on_disk_skills = {path.name for path in (_OPENCODE_DATA / "skills").iterdir() if (path / "SKILL.md").is_file()}
    assert on_disk_commands, "no bundled opencode commands found; data path moved?"
    assert on_disk_skills, "no bundled opencode skills found; data path moved?"

    documented_commands = _doc_bullet_names("- `.opencode/commands/")
    documented_skills = _doc_bullet_names("- `.opencode/skills/")
    assert documented_commands, "OpenCode commands bullet not found in docs/CLIENT-PROFILES.md"
    assert documented_skills, "OpenCode skills bullet not found in docs/CLIENT-PROFILES.md"

    assert documented_commands == on_disk_commands, (
        f"undocumented commands: {sorted(on_disk_commands - documented_commands)}; "
        f"documented but absent: {sorted(documented_commands - on_disk_commands)}"
    )
    assert documented_skills == on_disk_skills, (
        f"undocumented skills: {sorted(on_disk_skills - documented_skills)}; "
        f"documented but absent: {sorted(documented_skills - on_disk_skills)}"
    )


@pytest.mark.unit
def test_reader_scan_is_not_vacuous() -> None:
    """The scan must actually see the tree, and its predicate must still match."""
    readers, scanned = _scan()
    assert scanned >= _MIN_SCANNED_MODULES, f"only {scanned} modules scanned; source root moved?"
    behavioral = {path for path in readers[_CANARY_FIELD] if _DOC_RENDERER_PKG not in path.parents}
    assert behavioral, f"canary field {_CANARY_FIELD!r} lost every behavioral reader"


@pytest.mark.unit
def test_every_client_profile_field_has_a_production_reader() -> None:
    """Derived ratchet: the unread set equals the documented waiver set."""
    readers, _ = _scan()
    unread = {name for name, paths in readers.items() if not paths}
    assert unread == set(_UNREAD_WAIVERS), (
        "ClientProfile/WriteTargets unread-field set drifted.\n"
        f"  newly unread (wire it or remove it): {sorted(unread - set(_UNREAD_WAIVERS))}\n"
        f"  stale waiver (now read; drop the waiver): {sorted(set(_UNREAD_WAIVERS) - unread)}"
    )


@pytest.mark.unit
def test_declared_surface_flags_are_not_doc_projections_only() -> None:
    """A field read only by the doc renderer declares a capability nothing enforces."""
    readers, _ = _scan()
    doc_only = {
        name
        for name, paths in readers.items()
        if paths and all(_DOC_RENDERER_PKG in path.parents or path == _DOC_RENDERER_PKG for path in paths)
    }
    assert doc_only == set(_DOC_PROJECTION_WAIVERS), (
        "Doc-projection-only field set drifted.\n"
        f"  newly doc-only (the matrix declares it; nothing enforces it): {sorted(doc_only - set(_DOC_PROJECTION_WAIVERS))}\n"
        f"  stale waiver (now has a behavioral reader): {sorted(set(_DOC_PROJECTION_WAIVERS) - doc_only)}"
    )

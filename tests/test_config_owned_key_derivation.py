"""The externally-owned key allowlist must be DERIVED, not hand-copied.

PRD-QUAL-131-FR04 warns when ``.trw/config.yaml`` names a key ``TRWConfig`` does
not define. ``_retired_keys.externally_owned_config_keys()`` exempts the keys
other subsystems legitimately keep in that file. The exemption list shipped
hand-written and was incomplete the day it landed: it named the two
``cli/auth.py`` keys and missed both keys the **bundled shell hooks** read, so

    TRW: WARNING - .trw/config.yaml sets 'cc03_hook_enabled', which has no
    effect: TRWConfig does not define it; check for a typo.

printed on every single ``trw-mcp`` invocation in this repo, about a key that
three bundled hook libraries read as their HIGHEST-priority enable path. An
operator who believed it and deleted the key would have silently turned the
CC-03 hint hook off.

That is wiring-defect pattern P11 (``docs/documentation/wiring-defect-patterns.md``):
a module-local subset of a closed set, with no derivation and no exclusion set,
correct the day it was written and wrong the moment the set grew. The doc's own
prescription is the fix shape here - *"derive from the model, never a parallel
list"* - so this module rescans the shipped hooks and fails when the map has
fallen behind, instead of waiting for an operator to be told a working knob is
a typo.

Kept at test time rather than runtime on purpose: the runtime map must be data
in the wheel (hooks are shell, unimportable), so what a test can add is proof
that the data is *complete*.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

#: Bundled hook tree. Every ``.sh`` TRW installs into a user project lives here.
_DATA_DIR = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"

#: ``_read_trw_config_field <key> <default>`` - the documented top-level reader.
#: Nested reads (``_read_trw_nested_config_field channels cc03_hook_enabled``)
#: are deliberately NOT collected: a nested key is not a top-level key, so it is
#: not something the top-level warning can fire on.
_HELPER_READ = re.compile(r'_read_trw_config_field\s+"([a-z0-9_]+)"')

#: Hand-rolled reads: ``grep '^key:' "$cfg"``. Only counted when ``$cfg`` is a
#: variable this file assigned the config path to - otherwise the same pattern
#: over a run-state JSON file (``phase:``, ``tests_passed:``, ``wave:`` ...)
#: would be collected as config keys, which it is not.
_GREP_READ = re.compile(r"""grep\s+(?:-\w+\s+)*['"]\^?([a-z0-9_]+):['"]\s+"\$\{?(\w+)""")

#: ``_cfg="${TRW_PROJECT_DIR:-$(pwd)}/.trw/config.yaml"`` and friends.
_CONFIG_VAR = re.compile(r"^\s*(\w+)=.*\.trw/config\.yaml")

#: The published installer is the THIRD subsystem that owns keys in this file,
#: after the shell hooks and ``cli/auth.py`` — and the first version of this
#: module missed it, so ``sqlite_vec_enabled`` became the fourth instance of the
#: same false warning while a guard written to prevent exactly that sat one
#: directory away. A guard scoped to one owner is the P11 defect applied to its
#: own fix. The three shapes ``install-trw.template.py`` actually uses:
#: ``out.append(f"key: ...")`` and ``out.append('key: ...')`` to write,
#: ``s.startswith("key:")`` to rewrite in place, and ``flat["key"]`` to read back.
_INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install-trw.template.py"
_INSTALLER_KEY = re.compile(
    r"""out\.append\(f?["']([a-z0-9_]+):|s\.startswith\(["']([a-z0-9_]+):["']\)|flat\[["']([a-z0-9_]+)["']\]"""
)


def _config_keys_owned_by_the_installer() -> dict[str, set[str]]:
    """``{config key: {"install-trw.template.py"}}`` for keys the installer writes."""
    if not _INSTALLER.is_file():
        return {}
    found: dict[str, set[str]] = {}
    for match in _INSTALLER_KEY.finditer(_INSTALLER.read_text(encoding="utf-8", errors="replace")):
        key = next(group for group in match.groups() if group)
        found.setdefault(key, set()).add(_INSTALLER.name)
    return found


def _config_keys_read_by_bundled_hooks() -> dict[str, set[str]]:
    """``{config key: {hook files that read it}}`` for top-level reads.

    Comment lines are stripped first. The prose in these files documents the
    precedence order key-by-key, so counting comments would report keys that no
    line of shell actually reads.
    """
    found: dict[str, set[str]] = {}
    for script in sorted(_DATA_DIR.rglob("*.sh")):
        rel = str(script.relative_to(_DATA_DIR))
        config_vars: set[str] = set()
        lines = script.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines:
            match = _CONFIG_VAR.match(line)
            if match:
                config_vars.add(match.group(1))
        for line in lines:
            if line.lstrip().startswith("#"):
                continue
            for match in _HELPER_READ.finditer(line):
                found.setdefault(match.group(1), set()).add(rel)
            for match in _GREP_READ.finditer(line):
                key, var = match.group(1), match.group(2)
                if var in config_vars:
                    found.setdefault(key, set()).add(rel)
    return found


def test_the_scanner_sees_the_two_known_hook_knobs() -> None:
    """Non-vacuity control, and it comes first for a reason.

    A scanner that silently matched nothing would make the real assertion below
    pass forever. These two are the keys whose absence from the map caused the
    false warning, and they exercise both read shapes: ``cc03_hook_enabled``
    through the ``_read_trw_config_field`` helper, ``stop_deliver_window_minutes``
    through a hand-rolled ``grep`` against a resolved config path.
    """
    found = _config_keys_read_by_bundled_hooks()

    assert "cc03_hook_enabled" in found, "the helper-call read shape is no longer detected"
    assert "stop_deliver_window_minutes" in found, "the grep read shape is no longer detected"


def test_the_scanner_does_not_collect_run_state_keys() -> None:
    """Precision control. The hooks grep run-state files with the same syntax.

    ``phase:``, ``tests_passed:``, ``wave:`` and ``status:`` are fields of run
    artifacts, not of ``.trw/config.yaml``. Collecting them would demand
    exemption entries for keys no operator can set, which is how an over-broad
    guard gets suppressed instead of fixed.
    """
    found = _config_keys_read_by_bundled_hooks()

    leaked = sorted({"phase", "wave", "status", "tests_passed", "timed_out"} & set(found))
    assert leaked == [], f"scanner collected run-state fields as config keys: {leaked}"


def test_the_scanner_sees_the_installer_written_keys() -> None:
    """Non-vacuity control for the installer half.

    ``sqlite_vec_enabled`` is the key this scanner was added for;
    ``platform_telemetry_enabled`` is a control that IS a TRWConfig field, so it
    proves the scanner reads real keys rather than only the exempted one.
    """
    found = _config_keys_owned_by_the_installer()

    assert "sqlite_vec_enabled" in found, "the installer write-site shape is no longer detected"
    assert "platform_telemetry_enabled" in found, "the installer scanner is matching too narrowly"


def test_every_config_key_an_owning_subsystem_uses_is_recognised() -> None:
    """The derivation, over EVERY subsystem that owns keys in ``.trw/config.yaml``.

    A knob TRWConfig does not define MUST be exempted, or the operator who sets
    it - following the owning subsystem's own documentation - is told on every
    invocation that their working knob is a typo, and the honest response to that
    advice breaks their install.

    Scoped to the bundled hooks alone, this test passed while
    ``sqlite_vec_enabled`` - written and read back by the PUBLISHED installer, on
    the primary install path - produced that warning in every project installed
    with sqlite-vec enabled. Deleting the key on that advice loses the
    installer's memory of the choice, and a later non-interactive reinstall then
    silently drops sqlite-vec. A guard that covers one of three owners is the
    same subset-with-no-derivation defect it exists to catch.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._retired_keys import externally_owned_config_keys

    owners: dict[str, set[str]] = {}
    for source in (_config_keys_read_by_bundled_hooks(), _config_keys_owned_by_the_installer()):
        for key, files in source.items():
            owners.setdefault(key, set()).update(files)

    recognised = set(TRWConfig.model_fields) | set(externally_owned_config_keys())
    unrecognised = {key: sorted(files) for key, files in sorted(owners.items()) if key not in recognised}

    assert not unrecognised, (
        "a subsystem reads or writes config keys that TRWConfig does not define and "
        "config-retired-keys.json does not exempt. Setting one produces a false "
        "'check for a typo' warning on every invocation. Add each to owned_elsewhere "
        "with its read site: " + repr(unrecognised)
    )


@pytest.mark.parametrize(
    ("key", "expected_owner_fragment"),
    [("cc03_hook_enabled", "hook"), ("stop_deliver_window_minutes", "hook")],
)
def test_the_hook_owned_keys_name_a_hook_as_their_owner(key: str, expected_owner_fragment: str) -> None:
    """An exemption is only checkable if it says where the read is.

    ``test_the_owner_of_each_externally_owned_key_is_recorded`` already asserts
    the owner slot is non-empty; a non-empty string is not yet an attribution.
    This pins that these two point at the shell hooks, so the next reader can
    verify the claim rather than take it.
    """
    from trw_mcp.models.config._retired_keys import externally_owned_config_keys

    owner = externally_owned_config_keys().get(key, "")

    assert expected_owner_fragment in owner.lower(), f"{key} does not name its owning subsystem: {owner!r}"


def test_a_key_no_hook_reads_is_still_warned_about() -> None:
    """End-to-end non-vacuity: the exemption widened, it did not go silent."""
    from trw_mcp.models.config._retired_keys import (
        _reset_warned_keys,
        warn_unrecognised_config_keys,
    )

    _reset_warned_keys()
    try:
        warned = warn_unrecognised_config_keys(
            ["cc03_hook_enabled", "stop_deliver_window_minutes", "cc03_hook_enbaled"],
            defined=["trw_dir"],
        )
    finally:
        _reset_warned_keys()

    assert warned == ["cc03_hook_enbaled"], "the real typo must still be reported"

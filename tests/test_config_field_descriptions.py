"""E2E-INC-051 (a): every configuration field is described, and the reference lists the memory engine's variables too.

The descriptions live with the model (``Field(description=...)`` or ``models/config/_field_descriptions.py``), never in
the generated reference, so a new field with no description fails here instead of shipping as an empty table cell.
"""

from __future__ import annotations

import argparse

import pytest


def test_every_trwconfig_field_has_a_description() -> None:
    from trw_mcp.models.config import TRWConfig

    missing = sorted(n for n, f in TRWConfig.model_fields.items() if not (f.description or "").strip())

    assert missing == [], (
        f"{len(missing)} TRWConfig field(s) have no description; add `Field(description=...)` or one line in "
        f"models/config/_field_descriptions.py: {missing[:10]}"
    )


def test_every_memory_config_field_has_a_description() -> None:
    from trw_memory.models.config import MemoryConfig

    missing = sorted(n for n, f in MemoryConfig.model_fields.items() if not (f.description or "").strip())

    assert missing == [], f"MemoryConfig field(s) without a description: {missing}"


def test_the_description_table_names_no_field_that_does_not_exist() -> None:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._field_descriptions import FIELD_DESCRIPTIONS

    assert sorted(set(FIELD_DESCRIPTIONS) - set(TRWConfig.model_fields)) == []


def test_the_reference_has_no_empty_description_and_lists_the_memory_variables(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from trw_mcp.server._subcommands_misc import _run_config_reference

    _run_config_reference(argparse.Namespace())
    rows = [line for line in capsys.readouterr().out.splitlines() if line.startswith("| `")]

    assert [r for r in rows if r.rstrip().endswith("|  |")] == []
    assert any(r.startswith("| `MEMORY_STORAGE_BACKEND`") for r in rows)
    assert any(r.startswith("| `TRW_TASK_ROOT`") for r in rows)

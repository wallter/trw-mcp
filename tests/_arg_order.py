"""Explicit test arguments are regrouped so every directory is collected in one visit (COMMS-CONFTEST-ORDER).

pytest 9 matches a conftest's fixtures to the conftest's Package node object. Arguments that leave a
directory and return to it (``tests/comms/a.py tests/b.py tests/comms/c.py``) get a second Package
node for that directory, and the later files lose its conftest: fixtures "not found", autouse fixtures
silently skipped. Regrouping the arguments as a tree -- directories in first-seen order, files in the
order given within their directory -- collects each directory once. Registered from tests/conftest.py.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import pytest


def group_by_directory(args: Sequence[str], root: Path) -> list[str]:
    """ARGS reordered so the arguments under each directory are contiguous; otherwise as given."""
    first_seen: dict[tuple[str, ...], int] = {}
    keys: list[tuple[int, ...]] = []
    for arg in args:
        parts = Path(os.path.normpath(root / arg.split("::", 1)[0])).parts
        prefixes = [parts[: i + 1] for i in range(len(parts))]
        for prefix in prefixes:
            first_seen.setdefault(prefix, len(first_seen))
        keys.append(tuple(first_seen[prefix] for prefix in prefixes))
    order = sorted(range(len(args)), key=lambda k: keys[k])
    return [args[k] for k in order]


def pytest_configure(config: pytest.Config) -> None:
    if len(config.args) > 1 and not config.option.pyargs:
        config.args[:] = group_by_directory(config.args, config.invocation_params.dir)

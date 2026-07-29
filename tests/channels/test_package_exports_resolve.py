"""Every `__all__` entry in the channel packages must actually resolve.

PRD-CORE-239 FR01 deleted 12 channel renderers and pruned the imports from each
client package's `__init__.py` — but not the corresponding `__all__` entries.
That left 24 names exported from six packages with nothing behind them, so
`from trw_mcp.channels.<pkg> import *` raised `AttributeError` for every one of
them while `import trw_mcp.channels.<pkg>` still succeeded. Ordinary imports and
the whole test suite stayed green; only a star-import or a tooling pass that
reads `__all__` would have surfaced it.

Found by an independent reviewer after the removal, not by the removal's own
validation, which is the point of this file: a dangling export is invisible to
every check that imports symbols by name.
"""

from __future__ import annotations

import importlib

import pytest

_PACKAGES = (
    "antigravity",
    "claude_code",
    "codex",
    "copilot",
    "cursor",
    "opencode",
)


@pytest.mark.parametrize("package", _PACKAGES)
def test_every_exported_name_resolves(package: str) -> None:
    module = importlib.import_module(f"trw_mcp.channels.{package}")
    # `__all__` must be DEFINED, but may legitimately be empty: after FR01 the
    # cursor package has no modules left, and an empty export list is the
    # truthful statement for it. Asserting truthiness instead would have forced
    # a package to invent an export to satisfy a test.
    assert hasattr(module, "__all__"), f"trw_mcp.channels.{package} defines no __all__"
    exported = module.__all__

    dangling = [name for name in exported if not hasattr(module, name)]
    assert not dangling, (
        f"trw_mcp.channels.{package}.__all__ exports names that do not exist, so `from ... import *` raises: {dangling}"
    )


def test_the_shared_channels_package_also_resolves() -> None:
    """The parent facade lost `_cleanup`, `_conflict` and `_ttl` re-exports too."""
    module = importlib.import_module("trw_mcp.channels")
    assert hasattr(module, "__all__"), "trw_mcp.channels defines no __all__"
    exported = module.__all__
    assert exported, "the parent facade should still export the shared helpers"

    dangling = [name for name in exported if not hasattr(module, name)]
    assert not dangling, f"trw_mcp.channels.__all__ has dangling entries: {dangling}"

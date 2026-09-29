"""The payload source a directly built platform sender reads from, in tests.

A sender's whole send policy (the contact switch and the consent flags) is read from the ``.trw``
its payload came from (``state._platform_trust.send_policy``). A test that builds a sender by hand
names that ``.trw`` with :func:`payload_trw_dir`: the isolated project's own. The
``governing_project`` fixture makes it a project whose policy allows sending.
"""

from __future__ import annotations

from pathlib import Path

from tests._path_isolation import current_root


def payload_trw_dir() -> Path:
    """The isolated project's ``.trw``: where a hand-built sender's payload is read from."""
    return current_root() / ".trw"

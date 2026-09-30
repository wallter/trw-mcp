"""Which clients report what they actually ran, and where (CODEX-P0-A S2).

Belongs to the dispatch client registry. A separate table rather than a ``ClientSpec`` field because
the registry modules are at the 350 effective-LOC gate; the dispatch client-literal census exempts
this file by name, as it does the other registry-adjacent tables.
"""

from __future__ import annotations

from typing import Literal

__all__ = ["OBSERVATION_SOURCES", "ObservationSource"]

ObservationSource = Literal["codex-rollout"]

#: client id -> where its own record of the model and effort it ran lives.
OBSERVATION_SOURCES: dict[str, ObservationSource] = {"codex": "codex-rollout"}

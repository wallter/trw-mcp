"""The public TemporalFence contract and its conformance check (PRD-CORE-296-FR03, RC-011).

The endpoint lease conforms on its epoch-second clock. The checker itself is
proven non-vacuous: each rule it states is shown to catch a fence that breaks it,
because a conformance check that passes everything is what trw-swarm would be
importing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _Fence:
    expires_at: float | None
    released: bool = False
    inclusive: bool = False

    def is_live(self, now: float) -> bool:
        if self.released:
            return False
        if self.expires_at is None:
            return True
        return now <= self.expires_at if self.inclusive else now < self.expires_at


def test_the_comms_endpoint_conforms_on_its_own_clock() -> None:
    from trw_mcp.comms._endpoints import Endpoint
    from trw_mcp.comms.temporal_fence import TemporalFence, check_temporal_fence

    def make(expires_at: float | None) -> TemporalFence[float]:
        assert expires_at is not None, "an endpoint lease always expires"
        return Endpoint("g", "m", "s", Path("run"), 0.0, 0.0, expires_at)

    check_temporal_fence(make, before=99.0, expiry=100.0, after=101.0)


def test_a_conforming_fence_passes_every_rule() -> None:
    from trw_mcp.comms.temporal_fence import check_temporal_fence

    check_temporal_fence(
        _Fence,
        before=1.0,
        expiry=2.0,
        after=3.0,
        release=lambda f: _Fence(f.expires_at, released=True),  # type: ignore[attr-defined]
        open_ended=True,
    )


@pytest.mark.parametrize(
    ("make", "release", "open_ended", "rule"),
    [
        pytest.param(lambda e: _Fence(e - 1.5), None, False, "live before its expiry", id="dead-before-expiry"),
        pytest.param(lambda e: _Fence(e + 5.0), None, False, "not live after its expiry", id="live-after-expiry"),
        pytest.param(lambda e: _Fence(e, inclusive=True), None, False, "AT its expiry", id="inclusive-expiry"),
        pytest.param(_Fence, lambda f: f, False, "once released", id="release-ignored"),
        pytest.param(lambda e: _Fence(0.0 if e is None else e), None, True, "never expires", id="open-end-expires"),
    ],
)
def test_the_check_names_the_rule_a_fence_breaks(make: object, release: object, open_ended: bool, rule: str) -> None:
    from trw_mcp.comms.temporal_fence import check_temporal_fence

    with pytest.raises(AssertionError, match=rule):
        check_temporal_fence(make, before=1.0, expiry=2.0, after=3.0, release=release, open_ended=open_ended)  # type: ignore[arg-type]

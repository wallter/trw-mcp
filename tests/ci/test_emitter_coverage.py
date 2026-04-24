"""CI emitter-coverage gate — PRD-HPO-MEAS-001 FR-3 + FR-10 / NFR-8.

This test gates merge to ``main``. Every declared HPO event subclass must
be registered in ``EVENT_TYPE_REGISTRY``, and every production emitter
capability named in FR-10 §1-10 must have an observable wiring path.

For Wave 2b the gate enforces:
- Schema parity: every subclass in ``event_base`` is in the registry.
- Reachability scaffolding: the 10 FR-10 emitter capabilities are at
  least *named* in the codebase (import-resolvable module or wired
  helper). Full production-dispatch reachability is a Wave 2c gate
  (``test_emitter_reachability.py``) once emitter retrofits ship.
"""

from __future__ import annotations

import importlib
from typing import Final

import pytest

from trw_mcp.telemetry.event_base import EVENT_TYPE_REGISTRY, HPOTelemetryEvent

#: FR-10 §1-10 capabilities + import-resolvable scaffolding paths.
#: When a capability's module lands, add its import path here so this
#: gate starts asserting the symbol exists.
_FR10_CAPABILITIES: Final[tuple[tuple[str, str, str], ...]] = (
    ("ceremony",         "trw_mcp.telemetry.event_base",          "CeremonyEvent"),
    ("contract",         "trw_mcp.telemetry.event_base",          "ContractEvent"),
    ("phase_exposure",   "trw_mcp.telemetry.event_base",          "PhaseExposureEvent"),
    ("observer",         "trw_mcp.telemetry.event_base",          "ObserverEvent"),
    ("mcp_security",     "trw_mcp.telemetry.event_base",          "MCPSecurityEvent"),
    ("meta_tune",        "trw_mcp.telemetry.event_base",          "MetaTuneEvent"),
    ("thrashing",        "trw_mcp.telemetry.event_base",          "ThrashingEvent"),
    ("artifact_registry","trw_mcp.telemetry.artifact_registry",   "SurfaceRegistry"),
    ("surface_manifest", "trw_mcp.telemetry.surface_manifest",    "stamp_session"),
    ("tool_call_timing", "trw_mcp.telemetry.tool_call_timing",    "wrap_tool"),
)


class TestEventTypeRegistryParity:
    """NFR-3 schema rigor: every subclass is registered."""

    def test_registry_is_non_empty(self) -> None:
        assert len(EVENT_TYPE_REGISTRY) >= 12

    def test_every_registry_value_is_subclass(self) -> None:
        for event_type, cls in EVENT_TYPE_REGISTRY.items():
            assert issubclass(cls, HPOTelemetryEvent), f"{event_type} → {cls} not a subclass"

    def test_every_registry_key_matches_class_default(self) -> None:
        for event_type, cls in EVENT_TYPE_REGISTRY.items():
            default = cls.model_fields["event_type"].default
            assert default == event_type, f"registry key {event_type!r} ≠ class default {default!r}"

    def test_no_orphan_subclasses(self) -> None:
        # Walk HPOTelemetryEvent subclasses; every one must appear in the registry.
        def _all_subclasses(cls: type) -> set[type]:
            result: set[type] = set()
            for sub in cls.__subclasses__():
                result.add(sub)
                result.update(_all_subclasses(sub))
            return result

        registered = set(EVENT_TYPE_REGISTRY.values())
        # `H1ObserveModeWarning` subclasses `ObserverEvent` — both must appear
        # in the registry (which they do via their distinct event_type strings).
        missing: list[str] = []
        for sub in _all_subclasses(HPOTelemetryEvent):
            if sub not in registered:
                missing.append(sub.__name__)
        assert not missing, (
            f"HPOTelemetryEvent subclasses missing from EVENT_TYPE_REGISTRY: {missing}. "
            "Every subclass must register via its event_type literal or be explicitly "
            "marked as abstract in the PRD."
        )


class TestFR10CapabilityResolvability:
    """FR-10 §1-10: each declared emitter capability must be import-resolvable.

    This is a scaffolding gate — does not prove production dispatch. The
    deeper reachability test in Wave 2c exercises real tool invocations.
    """

    @pytest.mark.parametrize(("capability", "module_path", "symbol_name"), _FR10_CAPABILITIES)
    def test_capability_symbol_importable(
        self, capability: str, module_path: str, symbol_name: str
    ) -> None:
        try:
            mod = importlib.import_module(module_path)
        except ImportError as exc:
            pytest.fail(
                f"FR-10 capability {capability!r} — module {module_path!r} not importable: {exc}"
            )
        assert hasattr(mod, symbol_name), (
            f"FR-10 capability {capability!r} — symbol {symbol_name!r} missing from "
            f"{module_path}. Declared capability has no wiring."
        )


class TestEventTypeLiteralsAreStable:
    """Guard against accidental event_type renames that break jsonl consumers."""

    _EXPECTED_LITERALS: Final[frozenset[str]] = frozenset(
        {
            "ceremony",
            "contract",
            "phase_exposure",
            "observer",
            "mcp_security",
            "meta_tune",
            "thrashing",
            "llm_call",
            "tool_call",
            "session_start",
            "session_end",
            "ceremony_compliance",
            "h1_observe_mode_warning",
        }
    )

    def test_registry_matches_expected_literals(self) -> None:
        assert set(EVENT_TYPE_REGISTRY.keys()) == self._EXPECTED_LITERALS, (
            "EVENT_TYPE_REGISTRY keys drifted from expected set. If you added a "
            "subclass, update both EVENT_TYPE_REGISTRY AND this test's "
            "_EXPECTED_LITERALS, and bump the PRD-HPO-MEAS-001 FR-3 schema."
        )

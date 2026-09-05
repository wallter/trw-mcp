"""Nudge engine tunables: pool routing, urgency, budget, cooldowns.

Domain mixin. Split from ``_fields_ceremony.py`` because that file sat against
the 200-raw-line domain-mixin ceiling
(``tests/test_config_fields.py::test_domain_mixin_files_under_200_lines``), and
because these knobs are one subject: how the nudge engine picks, throttles,
and labels the guidance surfaced to an agent (as opposed to the surrounding
ceremony/compliance/enforcement config that stayed in ``_fields_ceremony.py``).

``NudgeMessengerLiteral`` stays declared in ``_fields_ceremony.py`` -- it is
imported there by ``_main.py``'s TYPE_CHECKING re-declaration and by
``tests/test_nudge_messengers.py`` -- and is imported here rather than
duplicated.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from trw_mcp.models.config._fields_ceremony import NudgeMessengerLiteral


class _NudgeFields:
    """Nudge-engine domain mixin -- mixed into _TRWConfigFields via MI."""

    nudge_enabled: bool | None = None
    nudge_budget_chars: int = Field(default=600, ge=100, le=2000)
    nudge_messenger: NudgeMessengerLiteral | None = None

    # ``nudge_urgency_mode`` and ``nudge_dedup_enabled`` were removed in 2.0.0
    # (WD-02). Both were PRD-CORE-125 surface flags whose ONLY path out of this
    # model was ``TRWConfig.surfaces``, and that projection's only reader --
    # ``state/surface_resolver.py::resolve_surface`` -- had no production call
    # site, so setting either in ``.trw/config.yaml`` was a no-op that looked
    # like a working control.
    #
    # They are DELETED rather than wired because the live emission path already
    # implements both behaviours, unconditionally and by other means:
    # ``_nudge_messages._compute_urgency`` derives urgency from the run's own
    # nudge counts (i.e. the ``"adaptive"`` the field defaulted to; the other
    # three members named modes that were never implemented anywhere), and
    # ``_ceremony_nudge_selectors`` suppresses a repeat within a phase via
    # ``state.nudge_history[...]["phases_shown"]``, logging ``reason="phase_dedup"``.
    # Wiring the knobs would therefore not have restored a lost control -- it
    # would have been NEW behaviour (three unbuilt urgency modes, and an
    # off-switch for dedup whose only effect is to re-show nudges an agent has
    # already been shown this phase).

    # Live A/B arm label stamped onto nudge surface events for real-traffic
    # comparison. Routing stays via ``nudge_messenger``; this only labels the
    # arm (e.g. "control", "structural-v2"). None => unlabelled.
    nudge_variant: str | None = None

    nudge_density: Literal["low", "medium", "high"] | None = Field(
        default=None,
        description="Nudge injection density: low=less frequent, high=more frequent; None defers to profile default.",
    )

    # The four flat nudge_pool_weight_* fields were removed 2026-07-28
    # (PRD-QUAL-131-FR05). The nudge pool is LIVE and its weights ARE read --
    # from the client profile. 85490eb73c (2026-06-09) made
    # ``client_profile.nudge_pool_weights`` the authority (read at
    # tools/_ceremony_status_pool.py) and left these four behind, so setting one
    # was not a no-op that looked like a no-op: it was a no-op that looked like a
    # working control, and the profile silently won. The two cooldowns below are
    # read from TRWConfig by the SAME function -- two neighbours in one config
    # family wired, four not, which is what made this the sharpest case in the
    # census. Five trw-eval ablation arms rode on these and were retired first.
    nudge_pool_cooldown_after: int = Field(default=3, ge=1, le=20)
    nudge_pool_cooldown_calls: int = Field(default=10, ge=1, le=100)
    nudge_pool_cooldown_wall_clock_max_hours: int = Field(
        default=24, ge=1, le=720, description="Max wall-clock hours a nudge pool stays cooled down before re-engaging."
    )

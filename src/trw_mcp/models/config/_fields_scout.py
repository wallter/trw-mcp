"""Cognitive-scaling Scout config fields — PRD-SCALE-001 (FR01/FR14).

Mixed into ``_TRWConfigFields`` via multiple inheritance. Kept as its own
small mixin (well under the 200-raw-line domain-mixin gate enforced by
``tests/test_config_fields.py``).

``scout_enabled`` is the FR14 kill switch (default ``True`` — Scout is
advisory + fail-open, so even enabled it only ever downgrades ceremony on
clearly-low-blast tasks and degrades to DIRECT on any signal failure). The
``scout_*_threshold`` knobs are the FR01 hand-tuned signal thresholds (v1;
meta-tune is PRD-HPO-MTPROP-001 scope, NG4). ``scout_max_mode3_rate`` is the
FR14 anti-inflation cap on TRIANGULATED_WITH_PROBE escalation.
"""

from __future__ import annotations


class _ScoutFields:
    """Cognitive-scaling Scout domain mixin — mixed into _TRWConfigFields."""

    # -- Cognitive Scaling Scout (PRD-SCALE-001 SCALE-001) --

    #: FR14 kill switch. When False, Scout never classifies and every session
    #: stays on the static config-driven ceremony (no session overlay written).
    scout_enabled: bool = True

    #: FR01 blast-radius threshold: symbol fan-out count at/above which the
    #: blast_radius signal counts as a "hit".
    scout_blast_radius_threshold: int = 10

    #: FR01 churn threshold: 6-month commit count at/above which the churn
    #: signal counts as a "hit".
    scout_churn_commit_threshold: int = 8

    #: FR14 anti-inflation cap: rolling-window TRIANGULATED_WITH_PROBE
    #: escalation rate above which thresholds auto-tighten + a warning emits.
    scout_max_mode3_rate: float = 0.15

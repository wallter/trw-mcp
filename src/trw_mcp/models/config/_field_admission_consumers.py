"""An admission may not name the config model as its own consumer. FR03.

Belongs to the ``_field_admission`` facade. Re-exported there.

PRD-QUAL-131-FR03. Every public ``TRWConfig`` field carries an admission record
naming its ``consumer``, and the gate that enforces the slot is
``assert getattr(record, slot)`` -- a NON-EMPTY check. 370 of 383 admissions
satisfy it with the exact string ``TRWConfig``: the config model naming itself as
the thing that consumes it. That is how 115 fields with no reader anywhere kept
passing a contract test whose whole purpose was to require one.

The falsification needs BOTH signals, which is why this module exists rather than
a blanket ban on the string:

- **Self-referential AND unread** -> rejected. The record claims a consumer, no
  consumer exists, and the claim is the only thing standing between the field and
  the gate. This is the case worth failing a build over.
- **Self-referential WITH a reader** -> warned, and counted in a shrink-only
  ratchet. The reader is real, so nothing is broken; the prose is just lazy.
  Failing here would fail 298 times on day one, and a gate that does that gets
  suppressed within a week -- the same reasoning that made
  ``check_config_field_consumers`` a ratchet rather than a zero-tolerance gate.

The unread half is grandfathered against a CURATED set -- the dated
``classifications`` map in ``.trw/compliance/config-field-consumers-baseline.json``
-- so the fields already recorded as debt do not fail twice for one defect, while a
new field that copies the pattern IS rejected.

**That guarantee used to be false, and the way it was false is worth recording.**
``grandfathered`` defaulted to ``unread``: the same set the rejection subtracts.
``(self_referential & unread) - unread`` is empty for every possible input, so
``rejected`` could not be non-empty on the default path, and the only live caller
took that path. The forward-looking guarantee this docstring advertised was
delivered by an expression that had no reachable failing branch (wiring-defect
pattern P4). Aggravating it, ``--write-baseline`` regenerates the published unread
set together with the ratchet, so even a hand-passed live measurement would have
absorbed each new offender on the next regeneration.

The parameter is therefore REQUIRED now. A grandfather set has to be something a
person decided and dated; deriving it from the measurement it is supposed to
constrain is what made the gate unfalsifiable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict

from trw_mcp.models.config._field_admission_registry import ConfigAdmission

#: The value an admission must not use as its own ``consumer``. Named as data so
#: a rename of the config model does not silently defeat the check.
CONFIG_MODEL_NAME = "TRWConfig"

#: Admissions that name the config model as their consumer while the field DOES
#: have a production reader. Measured 2026-07-28 after PRD-QUAL-131-FR01 removed
#: 41 fields. RATCHET: may only shrink. A rise means a new field copied the
#: pattern, which is how the 115-field backlog accumulated in the first place.
SELF_REFERENTIAL_WITH_READER_CEILING = 298


class ConsumerClaimReport(BaseModel):
    """Which admissions name the config model as their own consumer."""

    model_config = ConfigDict(frozen=True)

    #: Self-referential AND unread — a claim with nothing behind it.
    rejected: tuple[str, ...]
    #: Self-referential but genuinely read — a documentation defect, not a wiring one.
    warned: tuple[str, ...]
    ok: bool
    message: str


def verify_consumer_claims(
    admissions: Mapping[str, ConfigAdmission],
    *,
    unread: Iterable[str],
    grandfathered: Iterable[str],
) -> ConsumerClaimReport:
    """Split self-referential consumer claims into rejections and warnings.

    Args:
        admissions: The live admission records, keyed by field name.
        unread: Field names with no production reader.
        grandfathered: Field names whose unread state is already recorded as
            debt elsewhere -- in practice the dated ``classifications`` map from
            the compliance baseline. REQUIRED, and it must not be derived from
            *unread*: passing the same set makes the rejection set empty for
            every possible input. See the module docstring.

    Returns:
        A report whose ``ok`` is False when any admission claims the config model
        as its consumer for a field nothing reads and that is not grandfathered.
    """
    unread_set = set(unread)
    exempt = set(grandfathered)
    self_referential = {name for name, record in admissions.items() if record.consumer.strip() == CONFIG_MODEL_NAME}
    rejected = tuple(sorted((self_referential & unread_set) - exempt))
    warned = tuple(sorted(self_referential - unread_set))

    if rejected:
        message = (
            f"{len(rejected)} admission(s) name {CONFIG_MODEL_NAME} as their own consumer for a field "
            f"NOTHING reads: {list(rejected)}. A config model is not a consumer of its own field — the "
            "slot is meant to name the code that acts on the value. Name the real reader, or drop the "
            "field. This is the exact shape that let 115 fields pass a contract test requiring a consumer."
        )
    else:
        message = (
            f"0 unread fields claim {CONFIG_MODEL_NAME} as their consumer outside the recorded "
            f"grandfather set. {len(warned)} field(s) carry the self-referential string but DO have a "
            f"reader (documentation defect; ratchet ceiling {SELF_REFERENTIAL_WITH_READER_CEILING})."
        )

    return ConsumerClaimReport(rejected=rejected, warned=warned, ok=not rejected, message=message)

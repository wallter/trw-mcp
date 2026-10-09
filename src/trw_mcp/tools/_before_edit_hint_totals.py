"""The totals behind a before-edit hint's capped lists (trw-distill 0.12.0), mirrored field for field.

Belongs to ``_before_edit_hint_core.BeforeYouEditHintPayload``, which inherits it. The four fields must keep the
source's schema (``scripts/check-schema-mirror-parity.py``), so their default is the source's 0; what is OUTPUT is
decided here.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, SerializerFunctionWrapHandler, model_serializer

_CAPPED_LISTS = ("importers", "inferred_tests", "doc_references", "co_change_neighbors")


class CappedListTotals(BaseModel):
    """Distinct entries behind each capped list of the hint; equal to the list's length when nothing was cut."""

    importers_total: int = Field(default=0, ge=0)
    inferred_tests_total: int = Field(default=0, ge=0)
    doc_references_total: int = Field(default=0, ge=0)
    co_change_neighbors_total: int = Field(default=0, ge=0)

    @model_serializer(mode="wrap")
    def _only_totals_that_say_something(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        """A total is output only when it exceeds its list, i.e. when the list was cut.

        A sidecar written before trw-distill 0.12.0 carries no totals; the default 0 beside a non-empty list
        would be a false count. A total equal to the list's length adds nothing. Either way it is left out.
        """
        data: dict[str, object] = handler(self)
        for name in _CAPPED_LISTS:
            listed = data.get(name)
            total = data.get(f"{name}_total")
            if not (isinstance(total, int) and isinstance(listed, list) and total > len(listed)):
                data.pop(f"{name}_total", None)
        return data


__all__ = ["CappedListTotals"]

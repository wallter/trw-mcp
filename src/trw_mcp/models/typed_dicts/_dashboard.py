"""Dashboard trend TypedDicts (state/dashboard.py)."""

from __future__ import annotations

from typing_extensions import TypedDict

# "pass" is a Python keyword so this TypedDict uses the functional form.
ReviewTrendResult = TypedDict(
    "ReviewTrendResult",
    {
        "block": int,
        "warn": int,
        "pass": int,
        "total": int,
    },
)
ReviewTrendResult.__doc__ = "Return shape of ``compute_review_trend()``."

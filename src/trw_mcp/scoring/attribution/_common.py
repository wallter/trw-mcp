"""Shared utilities for the attribution sub-package.

Extracted to avoid duplication between ips.py and pipeline.py.
"""

from __future__ import annotations


def map_estimate_to_category(estimate: float) -> str:
    """Map a numeric estimate to an outcome correlation category.

    Categories:
    - estimate >= 0.75: "strong_positive"
    - estimate >= 0.5: "positive"
    - estimate <= -0.5: "negative"
    - otherwise: "neutral"
    """
    if estimate >= 0.75:
        return "strong_positive"
    if estimate >= 0.5:
        return "positive"
    if estimate <= -0.5:
        return "negative"
    return "neutral"

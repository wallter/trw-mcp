"""Reliability math — system error, supermajority, effective-n, BFT tolerance.

PRD-QUAL-005-FR08/FR09/FR12: Formal consensus math for adaptive gate evaluation.
P_sys = sum_{k=ceil(n*q)}^{n} C(n,k) * p^k * (1-p)^{n-k}
"""

from __future__ import annotations

import math


def _validate_n(n: int) -> None:
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")


def compute_system_error(n: int, p: float, quorum: float = 0.67) -> float:
    """Binomial CDF for system error: P(>= ceil(n*quorum) judges wrong).

    P_sys = sum_{k=ceil(n*q)}^{n} C(n,k) * p^k * (1-p)^{n-k}

    Raises ValueError if n < 1 or p not in (0, 1).
    """
    _validate_n(n)
    if not (0.0 < p < 1.0):
        raise ValueError(f"p must be in (0, 1), got {p}")

    threshold = math.ceil(n * quorum)
    return sum(
        math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))
        for k in range(threshold, n + 1)
    )


def compute_system_error_supermajority(
    n: int,
    p: float,
    quorum: float = 0.75,
) -> float:
    """Convenience wrapper with supermajority default (75%) for CRITIC gates."""
    return compute_system_error(n, p, quorum)


def compute_effective_n(n: int, correlation: float = 0.0) -> float:
    """Effective independent judges: n_eff = n / (1 + (n-1) * correlation).

    Raises ValueError if correlation not in [0, 1] or n < 1.
    """
    _validate_n(n)
    if not (0.0 <= correlation <= 1.0):
        raise ValueError(f"correlation must be in [0, 1], got {correlation}")

    if n == 1:
        return 1.0

    return n / (1.0 + (n - 1) * correlation)


def compute_bft_tolerance(n: int, quorum: float = 0.67) -> int:
    """Maximum Byzantine faults tolerated: n - ceil(n * quorum).

    Raises ValueError if n < 1.
    """
    _validate_n(n)
    return n - math.ceil(n * quorum)

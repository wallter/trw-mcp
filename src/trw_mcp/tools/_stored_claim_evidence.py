"""Pure response qualification of existing observations; never verifies or persists."""

from __future__ import annotations

from datetime import datetime, timezone


def _observation(result: object, checked_at: object, ttl: float, now: datetime) -> dict[str, object]:
    stamp = checked_at.isoformat() if isinstance(checked_at, datetime) else checked_at
    observation = "unknown"
    freshness = "unknown"
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            age = (now - parsed.astimezone(timezone.utc)).total_seconds()
            if age >= 0 and isinstance(result, bool):
                observation = "pass" if result else "failure"
                if ttl > 0:
                    freshness = "fresh" if age < ttl else "expired"
    except (ValueError, TypeError, OverflowError):
        # observation and freshness are pre-seeded to the literal "unknown" above and
        # stay there, so an unparseable stamp is REPORTED as unknown rather than
        # defaulting to pass/fresh. The typed-unknown pattern, not a swallowed error.
        # trw-fail-silent-allow: an unparseable stamp is reported as "unknown", never defaulted to pass/fresh
        pass
    return {"observation": observation, "checked_at": stamp, "freshness": freshness}


def stored_claim_evidence(
    entry: dict[str, object], *, ttl_seconds: float, now: datetime | None = None
) -> tuple[dict[str, object], float]:
    """Return qualified last-known observations and dated assertion failure fraction.

    TTL qualifies observation age, not current-tree truth. Aggregate dates never
    repair assertion dates. Invalid/future/naive dates cannot create a penalty.
    """
    moment = now or datetime.now(timezone.utc)
    raw = entry.get("assertions")
    assertions = raw if isinstance(raw, list) else []
    evidence = []
    for assertion in assertions:
        row = assertion if isinstance(assertion, dict) else {}
        item = _observation(row.get("last_result"), row.get("last_verified_at"), ttl_seconds, moment)
        item["last_evidence"] = row.get("last_evidence", "")
        evidence.append(item)
    failures = sum(item["observation"] == "failure" for item in evidence)
    aggregate_result = {"verified": True, "stale": False}.get(str(entry.get("verification_status")))
    aggregate = _observation(aggregate_result, entry.get("verification_checked_at"), ttl_seconds, moment)
    observations = [item["observation"] for item in evidence]
    if evidence:
        state = "failure" if failures else "pass" if all(value == "pass" for value in observations) else "unknown"
    else:
        state = str(aggregate["observation"])
    return {
        "observation": state,
        "assertions": evidence,
        "aggregate": aggregate,
        "current_tree_verified": False,
    }, failures / len(assertions) if assertions else 0.0

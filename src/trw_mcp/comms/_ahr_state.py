"""The pure AHR lifecycle replay (PRD-CORE-349 FR03-FR06): one handoff's event log, one event at a time.

A port of the AHR 1.0-rc.1 reference checker ``specs/handoff/tools/ahr_lifecycle.py``: the reference
loop body is :meth:`AhrLog.step`, with its rules, order and messages kept, so the vendored lifecycle
vectors (``tests/handoff/vectors/lifecycle``) are this module's regression suite. Only where a ``ref``
comes from differs: the caller resolves it and passes the target, because the store reads its own
stored bytes and the vector test reads a scenario directory.

Pure: no I/O, no clock, no database. The store (``_ahr_events``) rebuilds the log from its stored
rows, then steps the event it proposes; any violation refuses that event and the rebuilt log is
discarded. Record content is data (R-SEC-1): it is compared and parsed, never followed.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from trw_mcp.comms._ahr_arms import AHR_MEDIA, ARMS, TIER_RANK, Step, check_doc
from trw_mcp.handoff import AhrInputError, digest
from trw_mcp.handoff._rules import instant

JsonDoc = dict[str, Any]
#: A resolved ``ref``: the target (an AHR record as a dict, or raw bytes) and its digest.
Target = tuple[object, str]

TERMINAL = frozenset({"declined", "completed", "withdrawn", "superseded", "expired"})


def _looks_ahr(raw: bytes) -> bool:
    try:
        parsed = json.loads(raw)
    except ValueError:  # trw-fail-silent-allow: bytes that are not JSON are not an AHR record; False IS the answer
        return False
    return isinstance(parsed, dict) and "ahr" in parsed and parsed.get("type") in ("handoff", "readback", "event")


@dataclass
class AhrLog:
    """The replay state of one handoff. Build with :func:`start`; feed events with :meth:`step`."""

    handoff: JsonDoc
    record_digest: str
    state: str | None = None
    receiver: str | None = None
    prev_at: datetime | None = None
    prev_event: JsonDoc | None = None
    #: author -> (read-back digest, read-back, stored at)
    latest_rb: dict[str, tuple[str, JsonDoc, datetime]] = field(default_factory=dict)
    #: read-back digest -> latest verification
    verdict: dict[str, JsonDoc] = field(default_factory=dict)
    verifiers: set[str] = field(default_factory=set)
    any_fail: bool = False
    seen_ids: dict[str, str] = field(default_factory=dict)
    rb_ids: dict[str, str] = field(default_factory=dict)
    seen_rb_digests: set[str] = field(default_factory=set)
    count: int = 0

    @property
    def tier(self) -> str:
        return str(self.handoff["tier"])

    @property
    def sender(self) -> str:
        return str(self.handoff["from"]["id"])

    @property
    def addressed(self) -> bool:
        return bool(self.handoff["to"].get("kind") != "unaddressed")

    @property
    def completer(self) -> str | None:
        """The principal that may complete: the sender of an addressed record, else ``to.completer`` (R-LC-5)."""
        if self.addressed:
            return self.sender
        designated = self.handoff["to"].get("completer")
        return str(designated["id"]) if isinstance(designated, dict) else None

    def expires_at(self) -> datetime | None:
        value = self.handoff.get("expires_at")
        return instant(value) if isinstance(value, str) else None

    def step(self, ev: object, target: Target | None) -> list[str]:
        """Admit one stored event (the reference loop body); returns its violations, empty when conforming.

        Mutates exactly as the reference does, so a replay continues past a violation the way the
        reference does. *target* is the resolved ``ref`` (``None`` when the event has none, or when
        the caller could not resolve it).
        """
        handoff = self.handoff
        n = self.count
        self.count += 1
        if not isinstance(ev, dict):
            return [f"event line {n + 1}: R-DOC-1 an event must be a JSON object"]
        tag = f"event seq={ev.get('seq')} {ev.get('event')}"
        bad = check_doc(ev)
        if bad:
            return [f"{tag}: {e}" for e in bad]
        tier, sender, to = handoff["tier"], handoff["from"]["id"], handoff["to"]
        kinds = {handoff["from"]["id"]: handoff["from"]["kind"]}
        if to.get("id"):
            kinds[to["id"]] = to["kind"]
        if to.get("completer"):
            kinds.setdefault(to["completer"]["id"], to["completer"]["kind"])
        if handoff.get("readback", {}).get("verifier"):
            kinds[handoff["readback"]["verifier"]["id"]] = handoff["readback"]["verifier"]["kind"]
        addressed = self.addressed
        completer = self.completer
        verifier_id = handoff.get("readback", {}).get("verifier", {}).get("id")
        verifier_kind = handoff.get("readback", {}).get("verifier", {}).get("kind")
        kind, actor, at = ev["event"], ev["actor"]["id"], instant(ev["at"])
        if actor in kinds and kinds[actor] != ev["actor"]["kind"]:
            return [f"R-ROLE-2 {tag}: actor kind differs from the kind this handoff records for {actor!r}"]
        if ev["event_id"] in self.seen_ids:
            rule = "X-15" if self.seen_ids[ev["event_id"]] == actor else "R-LC-1"
            return [
                f"{rule} {tag}: duplicate event_id; a retry must return the stored event,"
                " and another actor's reuse is a conflict"
            ]
        self.seen_ids[ev["event_id"]] = actor
        errs: list[str] = []
        if "prev_event_digest" in ev and (
            self.prev_event is None or ev["prev_event_digest"] != digest(self.prev_event)
        ):
            errs.append(f"R-LC-13 {tag}: prev_event_digest does not match the previous stored event")
        self.prev_event = ev

        def fail(rule: str, msg: str) -> list[str]:
            errs.append(f"{rule} {tag}: {msg}")
            return errs

        if ev["handoff_id"] != handoff["handoff_id"]:
            return fail("X-15", "event names another handoff")
        if ev["seq"] != n + 1:
            fail("X-15", f"seq must be {n + 1} (dense from 1 in append order)")
        if self.prev_at and at < self.prev_at:
            fail("X-16", "stored time decreases along seq")
        if at < instant(handoff["created_at"]):
            fail("X-16", "event is stored before the handoff was created")
        self.prev_at = at
        if ev["record_digest"] != self.record_digest:
            fail("R-LC-1", "record_digest is not the digest of handoff.json")
        if self.state in TERMINAL:
            return fail("R-LC-2", f"no event may follow terminal state {self.state!r}")
        resolved: Any = None
        if "ref" in ev:
            if target is None:
                return fail("R-INT-7", "ref cannot be resolved")
            resolved, got = target
            if ev["ref"].get("media_type") != AHR_MEDIA and isinstance(resolved, bytes) and _looks_ahr(resolved):
                return fail("R-INT-7", "ref targets an AHR record but lacks media_type application/vnd.ahr+json")
            if ev["ref"].get("digest") and ev["ref"]["digest"] != got:
                return fail("R-INT-6", "ref.digest does not match the referenced content")
        may_receive = (actor == to.get("id")) if addressed else (actor != sender)
        expires = self.expires_at()
        if self.state == "offered" and expires is not None and at >= expires and kind != "expired":
            return fail(
                "R-TIME-2", f"{kind} on an offered record at or after expires_at; the store appends expired first"
            )

        arm = ARMS.get(kind)
        if arm is not None:  # readback_verified, declined, reported, superseded: see _ahr_arms
            step = Step(
                ev,
                resolved,
                actor,
                tier,
                sender,
                addressed,
                completer,
                verifier_id,
                verifier_kind,
                handoff,
                may_receive,
                fail,
            )
            arm(self, step)
        elif kind == "offered":
            if self.state is not None or actor != sender:
                fail("R-LC-2", "offered must be the first event and come from the sender")
            if expires is not None and at >= expires:
                return fail("R-TIME-2", "offered is stored at or after expires_at")
            self.state = "offered"
        elif kind == "read_back":
            if (
                self.state not in ("offered", "accepted")
                or not may_receive
                or (self.receiver and actor != self.receiver)
            ):
                return fail("R-LC-2", "read_back must come from the (prospective) receiver while offered or accepted")
            if (
                ev["ref"].get("media_type") != AHR_MEDIA
                or not isinstance(resolved, dict)
                or resolved.get("type") != "readback"
            ):
                return fail(
                    "R-LC-2", "read_back ref must be an AHR read-back record (media_type application/vnd.ahr+json)"
                )
            raw_by = resolved.get("by")
            by: JsonDoc = raw_by if isinstance(raw_by, dict) else {}
            if by.get("id") != actor or by.get("kind") != ev["actor"]["kind"]:
                fail("R-LC-2", "read-back author differs from event actor")
            if not addressed and (actor in self.verifiers or actor == verifier_id):
                return fail("R-RB-5", "a principal that verified this unaddressed record cannot also read it back")
            try:
                problems = check_doc(resolved, handoff)
            except (AhrInputError, KeyError, TypeError) as exc:
                problems = [f"R-DOC-1 read-back unusable: {exc}"]
            if problems:
                errs.extend(f"{tag}: readback {p}" for p in problems)
                return errs
            if instant(resolved["at"]) > at:
                fail("X-16", "read-back is dated after the event that records it")
            prior = self.rb_ids.setdefault(resolved["readback_id"], ev["ref"]["digest"])
            if prior != ev["ref"]["digest"]:
                return fail("R-DOC-2", "a second read-back reuses an existing readback_id with a different digest")
            if ev["ref"]["digest"] in self.seen_rb_digests or (
                actor in self.latest_rb and instant(resolved["at"]) <= instant(self.latest_rb[actor][1]["at"])
            ):
                return fail(
                    "R-LC-2", "a read_back must reference a new read-back, dated after the author's previous one"
                )
            self.seen_rb_digests.add(ev["ref"]["digest"])
            self.latest_rb[actor] = (ev["ref"]["digest"], resolved, at)
        elif kind == "answered":
            if actor != sender or self.state not in ("offered", "accepted"):
                fail("R-LC-8", "answered must come from the sender while offered or accepted")
            elif isinstance(resolved, dict):
                fail("R-DOC-2", "an answered ref carries explanation, never an AHR record; supersede instead")
        elif kind == "accepted":
            if self.state != "offered" or not may_receive:
                return fail("R-LC-2", "accepted must follow offered and come from a permitted receiver")
            if not addressed and verifier_id and actor == verifier_id:
                return fail("R-RB-5", "the designated verifier cannot accept an unaddressed record")
            if not addressed and actor == completer:
                return fail("R-LC-9", "the designated completer cannot accept (it could never complete)")
            if not addressed and self.latest_rb and actor not in self.latest_rb:
                fail("R-LC-9", "another principal read back this unaddressed record first")
            if actor in self.latest_rb and any(
                r["result"] == "contradicted" for r in self.latest_rb[actor][1]["reverified"]
            ):
                return fail("R-RB-4", "the receiver's latest read-back lists a contradicted claim")
            if actor in self.latest_rb:
                last_v = self.verdict.get(self.latest_rb[actor][0])
                if last_v and last_v["outcome"] == "fail":
                    return fail("R-LC-15", "the latest verdict on the receiver's latest read-back is fail")
            if tier in ("standard", "critical") or handoff.get("readback", {}).get("required"):
                mine = self.latest_rb.get(actor)
                if mine is None:
                    return fail("R-LC-11", "a read-back by the receiver must precede accepted")
                d, rb, _ = mine
                if rb["disposition"] != "ready" or any(r["result"] == "contradicted" for r in rb["reverified"]):
                    return fail("R-LC-11", "the receiver's latest read-back has open questions or contradictions")
                if tier == "critical":
                    v = self.verdict.get(d)
                    needs_human = self.any_fail or any(r["result"] == "not_checkable" for r in rb["reverified"])
                    ok = (
                        v
                        and v["outcome"] in ("pass", "pass_with_flags")
                        and v["method"] in ("judge", "human")
                        and not (v.get("confidence") == "low" and v["method"] != "human")
                        and not (needs_human and v["method"] != "human")
                    )
                    if not ok:
                        rule = "R-RB-9" if (v and needs_human and v["method"] != "human") else "R-LC-10"
                        return fail(
                            rule,
                            "critical: the latest verdict on the receiver's latest read-back"
                            " is not an admissible passing verdict",
                        )
            if instant(ev["effective_at"]) != instant(ev["at"]):
                return fail("R-LC-6", "the store sets effective_at equal to the stored at of the accepted event")
            self.state, self.receiver = "accepted", actor
        elif kind == "completed":
            if self.state != "reported":
                fail("R-LC-2", "completed must follow reported")
            elif completer is None or actor != completer:
                fail("R-LC-5", "only the completer may complete (unaddressed records need to.completer)")
            elif actor == self.receiver:
                fail("R-LC-5", "the receiver cannot complete its own handoff")
            self.state = "completed"
        elif kind == "withdrawn":
            if actor != sender or self.state != "offered":
                return fail("R-LC-2", "withdrawn must come from the sender while offered")
            self.state = "withdrawn"
        elif kind == "expired":
            if self.state != "offered" or expires is None or at < expires or ev["actor"]["kind"] != "service":
                return fail("R-LC-2", "expired applies only to an offered record at or after expires_at, by the store")
            self.state = "expired"
        return errs


def start(handoff: JsonDoc) -> tuple[AhrLog | None, list[str]]:
    """A fresh log for a valid *handoff*, or ``(None, findings)`` when the record itself fails L1."""
    errs = [f"handoff: {e}" for e in check_doc(handoff)]
    if errs:
        return None, errs
    return AhrLog(handoff=handoff, record_digest=digest(handoff)), []


def replay(handoff: JsonDoc, events: Iterable[tuple[object, Callable[[], Target | None]]]) -> tuple[str, list[str]]:
    """The reference ``replay``: the final state and every violation of one handoff's event log.

    Each event comes with a thunk that resolves its ``ref``; a thunk that raises ``ValueError`` or
    ``OSError`` leaves the ref unresolved, which :meth:`AhrLog.step` reports as R-INT-7 at the
    point the reference does.
    """
    log, errs = start(handoff)
    if log is None:
        return "invalid", errs
    for ev, resolve in events:
        target: Target | None = None
        if isinstance(ev, dict) and "ref" in ev:
            try:
                target = resolve()
            except (OSError, ValueError):  # trw-fail-silent-allow: an unresolved ref is reported by step as R-INT-7
                target = None
        errs += log.step(ev, target)
    if handoff["tier"] != "minimal" and log.state is None and not errs:
        errs.append("R-LC-2 a standard or critical handoff needs at least an offered event")
    return log.state or "none", errs


__all__ = ["AHR_MEDIA", "TERMINAL", "TIER_RANK", "AhrLog", "Target", "check_doc", "replay", "start"]

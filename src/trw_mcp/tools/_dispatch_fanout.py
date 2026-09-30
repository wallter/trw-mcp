"""Fan-out helpers for ``trw_dispatch`` (PUBLIC, BSL-1.1).

Belongs to the :mod:`trw_mcp.tools.dispatch` facade. Collaborators are looked up
on the facade module at call time so tests that monkeypatch
``trw_mcp.tools.dispatch.dispatch`` (etc.) reach the fan-out path too.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from trw_mcp.dispatch._resolve import DispatchResolutionError
from trw_mcp.dispatch._targets import Target, compact_result
from trw_mcp.dispatch._types import DispatchRequest


def _f() -> Any:
    """The facade module, resolved per call so monkeypatches on it apply."""
    from trw_mcp.tools import dispatch

    return dispatch


def status_many(job_ids: list[str]) -> dict[str, object]:
    """Compact status for a fan-out: one lane per job, terminal when all are."""
    lanes: list[dict[str, object]] = []
    for jid in job_ids:
        st = _f()._status(jid, verbose=False)
        if "error" in st:
            lanes.append({"job_id": jid, "ok": False, "error": st["error"], "terminal": True})
            continue
        result = st.get("result")
        if isinstance(result, dict):
            lane = compact_result(str(result.get("client", jid)), result)
        else:
            lane = {"target": jid, "ok": None, "status": st.get("status")}
        lane["job_id"] = jid
        lane["terminal"] = st.get("terminal")
        lanes.append(lane)
    return {"terminal": all(lane.get("terminal") for lane in lanes), "results": lanes}


def _run_fanout(reqs: list[tuple[str, DispatchRequest]]) -> list[dict[str, object]]:
    """Run every request concurrently and return compact lanes in input order."""
    from concurrent.futures import ThreadPoolExecutor

    def _one(label: str, req: DispatchRequest) -> dict[str, object]:
        child_id = f"sync-{uuid.uuid4().hex}"
        _f().record_dispatch_policy(req, child_id)
        try:
            result = _f().dispatch(req)
        except Exception as exc:  # justified: one lane's crash must not lose the others
            _f().logger.warning("dispatch_fanout_lane_failed", target=label, exc_info=True)
            return {"target": label, "ok": False, "error": f"{type(exc).__name__}: {exc}", "reason": "launch_error"}
        _f().record_child_usage(result, child_id=child_id)
        return compact_result(label, _f()._result_payload_capped(result))

    with ThreadPoolExecutor(max_workers=len(reqs)) as pool:
        futures = [pool.submit(_one, label, req) for label, req in reqs]
        return [f.result() for f in futures]


def launch_fanout(
    targets: list[Target],
    resolve: Callable[[Target], DispatchRequest],
    *,
    wait: bool,
) -> dict[str, object]:
    """Launch one lane per target; a lane that fails to resolve is reported, not fatal."""
    lanes: list[dict[str, object]] = []
    reqs: list[tuple[str, DispatchRequest]] = []
    for t in targets:
        try:
            req = resolve(t)
        except DispatchResolutionError as err:
            lanes.append({"target": t.label, "ok": False, "error": str(err), "reason": "resolution_error"})
            continue
        if wait and req.timeout_s > _f()._MAX_WAIT_TIMEOUT_S:
            lanes.append(
                {
                    "target": t.label,
                    "ok": False,
                    "error": f"timeout_s>{_f()._MAX_WAIT_TIMEOUT_S} needs wait=False; poll the returned job ids with action='status'",
                    "reason": "resolution_error",
                }
            )
            continue
        reqs.append((t.label, req))
    if not wait:
        jobs = []
        for label, req in reqs:
            job = _f().start_background(req)
            _f().record_dispatch_policy(req, job.job_id)
            jobs.append({"target": label, "job_id": job.job_id, "status": job.status})
        ids = ",".join(str(j["job_id"]) for j in jobs)
        return {"jobs": jobs, "failed": lanes, "poll": f"trw_dispatch(action='status', target='{ids}')"}
    results = _run_fanout(reqs) if reqs else []
    order = {t.label: i for i, t in enumerate(targets)}
    merged = sorted([*results, *lanes], key=lambda lane: order.get(str(lane.get("target")), 0))
    ok = sum(1 for lane in merged if lane.get("ok"))
    return {"status": f"{ok}/{len(merged)} succeeded", "results": merged}

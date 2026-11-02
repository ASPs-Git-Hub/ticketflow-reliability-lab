"""First-response time, resolution time, backlog and breach rate from case events."""
import statistics

from . import sla
from .common import DEFAULT_TARGETS, get_tz


def _status_at(case, now):
    evs = sorted((e for e in case["status_events"] if e[0] <= now), key=lambda e: e[0])
    return evs[-1][1] if evs else None


def _summary(values):
    if not values:
        return {"n": 0, "mean": None, "median": None}
    return {"n": len(values), "mean": statistics.fmean(values), "median": statistics.median(values)}


DECIDED = ("met", "met_late", "breached")
LATE = ("met_late", "breached")


def compute(cases, now, targets=None):
    """Breach rate is computed per clock and summed: a clock is decided once it has met, met late
    or breached. A case whose first response is met but whose resolution clock is still running
    contributes one decided, unbreached clock, not a decided case, so the rate is not hidden by
    cases that are still open. `per_clock` shows each clock on its own."""
    targets = targets or DEFAULT_TARGETS
    fr, res, backlog = [], [], 0
    per = {c: {"decided": 0, "breached": 0} for c in ("first_response", "resolution")}
    for case in cases:
        if case["created_at"] > now:
            continue
        ev = sla.evaluate(case, now, targets)
        status = _status_at(case, now)
        if status != "resolved":
            backlog += 1
        if case["first_response_at"] is not None and case["first_response_at"] <= now:
            fr.append(ev["first_response"]["elapsed"])
        if status == "resolved":
            res.append(ev["resolution"]["elapsed"])
        for clock in per:
            state = ev[clock]["state"]
            if state in DECIDED:
                per[clock]["decided"] += 1
                if state in LATE:
                    per[clock]["breached"] += 1
    for c in per.values():
        c["breach_rate"] = (c["breached"] / c["decided"]) if c["decided"] else None
    decided = sum(c["decided"] for c in per.values())
    breached = sum(c["breached"] for c in per.values())
    return {"first_response_minutes": _summary(fr), "resolution_minutes": _summary(res),
            "backlog": backlog, "decided": decided, "breached": breached,
            "breach_rate": (breached / decided) if decided else None, "per_clock": per}

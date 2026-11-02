"""SLA clocks in business hours per timezone (Mon-Fri 09:00-17:00 local), DST safe.

All differences are taken between UTC instants. Subtracting two aware datetimes that share
one zoneinfo tzinfo ignores DST and is a classic bug, so every comparison goes through UTC.
"""
from datetime import datetime, time, timedelta

from .common import AT_RISK_RATIO, DEFAULT_TARGETS, UTC, get_tz

OPEN_HOUR = 9
CLOSE_HOUR = 17
WORKDAYS = (0, 1, 2, 3, 4)
MAX_DAYS = 3650


def _window(day, tz):
    start = datetime.combine(day, time(OPEN_HOUR), tzinfo=tz).astimezone(UTC)
    end = datetime.combine(day, time(CLOSE_HOUR), tzinfo=tz).astimezone(UTC)
    return start, end


def business_minutes_between(start, end, tz):
    if end <= start:
        return 0.0
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    day = start.astimezone(tz).date()
    last = end.astimezone(tz).date()
    total = 0.0
    while day <= last:
        if day.weekday() in WORKDAYS:
            ws, we = _window(day, tz)
            lo, hi = max(ws, start), min(we, end)
            if hi > lo:
                total += (hi - lo).total_seconds() / 60.0
        day += timedelta(days=1)
    return total


def add_business_minutes(start, minutes, tz):
    """UTC instant at which `minutes` business minutes have elapsed after `start`."""
    start = start.astimezone(UTC)
    if minutes <= 0:
        return start
    remaining = float(minutes)
    day = start.astimezone(tz).date()
    for _ in range(MAX_DAYS):
        if day.weekday() in WORKDAYS:
            ws, we = _window(day, tz)
            lo = max(ws, start)
            if we > lo:
                cap = (we - lo).total_seconds() / 60.0
                if cap >= remaining:
                    return lo + timedelta(minutes=remaining)
                remaining -= cap
        day += timedelta(days=1)
    raise ValueError("business-minute horizon exceeded")


def _state(elapsed, target, done):
    if done:
        return "met" if elapsed <= target else "met_late"
    if elapsed > target:
        return "breached"
    if elapsed >= AT_RISK_RATIO * target:
        return "at_risk"
    return "ok"


def _running_intervals(events, now):
    """Intervals where the status is 'open', from events sorted by time (out-of-order safe)."""
    evs = sorted((e for e in events if e[0] <= now), key=lambda e: e[0])
    out = []
    for i, (at, status) in enumerate(evs):
        if status != "open":
            continue
        end = evs[i + 1][0] if i + 1 < len(evs) else now
        out.append((at, end))
    return out


def resolution_elapsed(case, now):
    tz = get_tz(case["tz"])
    return sum(business_minutes_between(a, b, tz) for a, b in _running_intervals(case["status_events"], now))


def evaluate(case, now, targets=None):
    targets = targets or DEFAULT_TARGETS
    fr_target, res_target = targets[case["priority"]]
    tz = get_tz(case["tz"])
    responded = case["first_response_at"] is not None and case["first_response_at"] <= now
    fr_end = case["first_response_at"] if responded else now
    fr_elapsed = business_minutes_between(case["created_at"], fr_end, tz)
    evs = sorted((e for e in case["status_events"] if e[0] <= now), key=lambda e: e[0])
    resolved = bool(evs) and evs[-1][1] == "resolved"
    res_elapsed = resolution_elapsed(case, now)
    return {
        "first_response": {"elapsed": fr_elapsed, "target": fr_target,
                           "state": _state(fr_elapsed, fr_target, responded),
                           "due_at": add_business_minutes(case["created_at"], fr_target, tz)},
        "resolution": {"elapsed": res_elapsed, "target": res_target,
                       "state": _state(res_elapsed, res_target, resolved)},
        "paused": bool(evs) and evs[-1][1] == "waiting_on_customer",
    }

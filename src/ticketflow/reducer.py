"""Deterministic status reducer.

Every status change is an event {at, status, rank, seq}. The case status, `resolved_at` and the
`status_events` list (used by the SLA clock) are always derived from the whole event log, sorted
by (event time, rank, sequence), never from arrival order. Arrival order therefore cannot change
the answer, except for two agent-set events with the same timestamp and different statuses,
where the later source sequence wins (a genuine conflict, documented).

rank: 0 = agent-set status, 1 = a customer message. At the same instant a customer message sorts
after an agent action, so a customer reply stamped at the same time as a resolve reopens the case.
"""

RANK_AGENT = 0
RANK_CUSTOMER = 1


def add_event(case, at, status, rank, seq):
    """Append an event unless an identical (time, status, rank) one exists, then re-derive state.
    Returns True when the log changed."""
    for e in case["event_log"]:
        if e["at"] == at and e["status"] == status and e["rank"] == rank:
            return False
    case["event_log"].append({"at": at, "status": status, "rank": rank, "seq": seq})
    reduce_case(case)
    return True


def reduce_case(case):
    log = sorted(case["event_log"], key=lambda e: (e["at"], e["rank"], e["seq"]))
    case["status_events"] = [(e["at"], e["status"]) for e in log]
    last = log[-1]
    case["status"] = last["status"]
    case["resolved_at"] = last["at"] if last["status"] == "resolved" else None

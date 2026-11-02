"""Seeded simulation with an INDEPENDENT audit.

The audit reads public state only (flow.cases, flow.alerts, flow.sla) plus the notifications
returned by flow.tick(), and judges them against an oracle computed here from the simulator's own
ground truth. The oracle shares no code and no constants with the implementation:

  * business minutes come from a per-minute flag table (a minute counts when its local weekday is
    Monday to Friday and its local hour is 9 to 16), not from sla.py;
  * targets, the at-risk ratio and the cooldown are copied below, so a changed constant in
    common.py is detected rather than followed;
  * the expected status timeline is rebuilt from the generated events (agent events, then customer
    replies, sorted by time), not read back from the flow.

Because of that, a flow whose tick() returns nothing, whose cooldown is zero, whose clock is frozen,
or that never indexes the agent's outbound mail, fails the audit. Each of those is a must-fire
control in tests/test_sim.py.
"""
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
START = datetime(2026, 3, 2, 8, 0, tzinfo=UTC)  # a week that contains the US DST change
DAYS = 9
TICK_DAYS = DAYS + 4
KNOWN_QUEUES = {"billing", "technical", "accounts", "security", "general"}
TZS = ["UTC", "America/New_York", "America/Los_Angeles", "Europe/London", "Asia/Kolkata", "Australia/Sydney"]
SUBJECTS = ["Invoice question", "Refund for duplicate charge", "API returns 429", "Cannot login after password change",
            "Suspicious email received", "Question about my setup", "Feature request", "Hello"]
PRIORITIES = ("urgent", "high", "normal", "low")
SECURITY_SUBJECT = "Suspicious email received"
CATEGORIES = ("duplicate_cases", "unassigned_cases", "alert_twice", "alert_missing", "alert_spurious",
              "sla_elapsed_wrong", "clock_running_while_paused", "bounce_or_auto_opened_case",
              "reply_not_threaded")

# Oracle constants, deliberately NOT imported from the implementation.
ORACLE_TARGETS = {"urgent": (60, 240), "high": (120, 480), "normal": (240, 960), "low": (480, 2400)}
ORACLE_AT_RISK = 0.8
ORACLE_COOLDOWN = timedelta(minutes=120)
HORIZON_MIN = (TICK_DAYS + 3) * 24 * 60

_CACHE = {}


def generate(seed, n_cases=60, days=DAYS):
    key = (seed, n_cases, days)
    if key in _CACHE:
        return _CACHE[key]
    rng = random.Random(seed)
    items, seq = [], 0

    def add(at, kind, **kw):
        nonlocal seq
        seq += 1
        items.append({"at": at, "seq": seq, "kind": kind, **kw})

    truth = {"seed": seed, "cases": {}, "timeline": {}, "agent_reply": {}, "waits": [], "replies": [],
             "noise": [], "ticks": []}
    for i in range(n_cases):
        at = START + timedelta(minutes=rng.randrange(0, days * 24 * 60 // 2))
        mid = "m%d@cust.example" % i
        tz = rng.choice(TZS)
        priority = rng.choice(PRIORITIES)
        subject = rng.choice(SUBJECTS)
        expected_priority = priority
        if subject == SECURITY_SUBJECT and priority in ("normal", "low"):
            expected_priority = "high"  # spec: a security report is never below high
        email = {"message_id": "<%s>" % mid, "from": "user%d@cust.example" % rng.randrange(40),
                 "subject": subject, "tz": tz, "priority": priority,
                 "body": "Hi team,\nplease help with this.\n\n-- \nSent by user", "date": at.isoformat()}
        add(at, "email", email=email, mid=mid)
        truth["cases"][mid] = {"at": at, "tz": tz, "priority": expected_priority}
        timeline = truth["timeline"][mid] = [(at, "open", 0)]
        if rng.random() < 0.15:
            add(at + timedelta(minutes=rng.randrange(1, 120)), "email", email=dict(email), mid=mid)
        waits = rng.random() < 0.3
        ar = None
        if rng.random() < 0.7:
            ar = at + timedelta(minutes=rng.randrange(10, 1500))
            add(ar, "agent_reply", mid=mid, out_id="out%d@northwind.example" % i)
            truth["agent_reply"][mid] = ar
            if waits:
                ws = ar + timedelta(minutes=rng.randrange(5, 60))
                we = ws + timedelta(minutes=rng.randrange(120, 2000))
                add(ws, "status", mid=mid, status="waiting_on_customer")
                add(we, "status", mid=mid, status="open")
                timeline += [(ws, "waiting_on_customer", 0), (we, "open", 0)]
                truth["waits"].append((mid, ws, we))
                if rng.random() < 0.7:
                    done = we + timedelta(minutes=rng.randrange(30, 600))
                    add(done, "status", mid=mid, status="resolved")
                    timeline.append((done, "resolved", 0))
            elif rng.random() < 0.6:
                done = ar + timedelta(minutes=rng.randrange(30, 3000))
                add(done, "status", mid=mid, status="resolved")
                timeline.append((done, "resolved", 0))
        if rng.random() < 0.3:
            rid = "r%d@cust.example" % i
            r = at + timedelta(minutes=rng.randrange(60, 3000))
            via_outbound = ar is not None and r > ar and rng.random() < 0.5
            parent = "<out%d@northwind.example>" % i if via_outbound else email["message_id"]
            rep = {"message_id": rid, "from": email["from"], "subject": "Re: " + email["subject"],
                   "body": "Thanks.\n\n> Hi team,\n> please help", "in_reply_to": parent, "date": r.isoformat()}
            add(r, "email", email=rep, mid=rid)
            truth["replies"].append((rid, mid, r))
            timeline.append((r, "open", 1))
        if rng.random() < 0.1:
            bid = "b%d@mail.example" % i
            when = at + timedelta(minutes=rng.randrange(1, 600))
            add(when, "email", email={"message_id": bid, "from": "mailer-daemon@mail.example",
                                      "subject": "Undelivered Mail Returned to Sender", "body": "failed",
                                      "date": when.isoformat()}, mid=bid)
            truth["noise"].append(bid)
        if rng.random() < 0.1:
            aid = "a%d@cust.example" % i
            when = at + timedelta(minutes=rng.randrange(1, 600))
            add(when, "email", email={"message_id": aid, "from": "user%d@cust.example" % i,
                                      "subject": "Automatic reply: out of office", "body": "I am away",
                                      "headers": {"Auto-Submitted": "auto-replied"},
                                      "date": when.isoformat()}, mid=aid)
            truth["noise"].append(aid)
    t = START
    while t <= START + timedelta(days=TICK_DAYS):
        add(t, "tick")
        truth["ticks"].append(t)
        t += timedelta(hours=1)
    items.sort(key=lambda x: (x["at"], 1 if x["kind"] == "tick" else 0, x["seq"]))
    _CACHE[key] = (items, truth)
    return items, truth


def run(factory, seed):
    flow = factory()
    items, truth = generate(seed)
    case_of, notes = {}, []
    for it in items:
        k = it["kind"]
        if k == "email":
            res = flow.ingest(it["email"], it["at"])
            if res.get("case_id") and it["mid"] in truth["cases"] and it["mid"] not in case_of:
                case_of[it["mid"]] = res["case_id"]
        elif k == "tick":
            notes.extend(flow.tick(it["at"]) or [])
        elif it["mid"] in case_of:
            if k == "agent_reply":
                flow.agent_reply(case_of[it["mid"]], it["at"], message_id=it["out_id"])
            else:
                flow.set_status(case_of[it["mid"]], it["status"], it["at"])
    return audit(flow, truth, case_of, notes)


# ---------------------------------------------------------------- oracle
_FLAGS = {}


def _prefix(tzname):
    pre = _FLAGS.get(tzname)
    if pre is None:
        tz = ZoneInfo(tzname)
        pre, acc = [0], 0
        for m in range(HORIZON_MIN):
            local = (START + timedelta(minutes=m)).astimezone(tz)
            if local.weekday() < 5 and 9 <= local.hour < 17:
                acc += 1
            pre.append(acc)
        _FLAGS[tzname] = pre
    return pre


def _idx(t):
    return max(0, min(HORIZON_MIN, int((t - START).total_seconds() // 60)))


def _bm(tzname, a, b):
    if b <= a:
        return 0
    pre = _prefix(tzname)
    return pre[_idx(b)] - pre[_idx(a)]


def _state(elapsed, target, done):
    if done:
        return "met" if elapsed <= target else "met_late"
    if elapsed > target:
        return "breached"
    if elapsed >= ORACLE_AT_RISK * target:
        return "at_risk"
    return "ok"


def oracle_at(truth, mid, now):
    """(first response elapsed, resolution elapsed, set of rules that must be active) at `now`."""
    info = truth["cases"][mid]
    tz, fr_t, res_t = info["tz"], *ORACLE_TARGETS[info["priority"]]
    reply = truth["agent_reply"].get(mid)
    responded = reply is not None and reply <= now
    fr_el = _bm(tz, info["at"], reply if responded else now)
    evs = sorted((e for e in truth["timeline"][mid] if e[0] <= now), key=lambda e: (e[0], e[2]))
    res_el = 0
    for i, (at, status, _) in enumerate(evs):
        if status == "open":
            res_el += _bm(tz, at, evs[i + 1][0] if i + 1 < len(evs) else now)
    done = bool(evs) and evs[-1][1] == "resolved"
    active = set()
    for clock, el, target, fin in (("first_response", fr_el, fr_t, responded), ("resolution", res_el, res_t, done)):
        st = _state(el, target, fin)
        if st in ("at_risk", "breached"):
            active.add("%s_%s" % (clock, st))
    return fr_el, res_el, active


def _oracle_ticks(truth):
    cached = truth.get("_oracle")
    if cached is None:
        cached = {}
        for mid, info in truth["cases"].items():
            for t in truth["ticks"]:
                if t >= info["at"]:
                    cached[(mid, t)] = oracle_at(truth, mid, t)[2]
        truth["_oracle"] = cached
    return cached


# ---------------------------------------------------------------- audit
def audit(flow, truth, case_of, notes=None):
    v = dict.fromkeys(CATEGORIES, 0)
    notes = list(notes or [])
    msg_count, cases_with = Counter(), {}
    for cid, case in flow.cases.items():
        for m in case["messages"]:
            msg_count[m["message_id"]] += 1
            cases_with.setdefault(m["message_id"], set()).add(cid)
        if case["queue"] not in KNOWN_QUEUES:
            v["unassigned_cases"] += 1
    v["duplicate_cases"] = sum(c - 1 for c in msg_count.values() if c > 1)

    # alerts: notifications returned by tick() against the oracle's expected active rules
    mid_of = {cid: mid for mid, cid in case_of.items()}
    expected = _oracle_ticks(truth)
    by_key = {}
    for n in notes:
        by_key.setdefault((n["case_id"], n["rule"]), []).append(n["at"])
        mid = mid_of.get(n["case_id"])
        if mid is None or n["rule"] not in expected.get((mid, n["at"]), ()):
            v["alert_spurious"] += 1
    for (mid, t), rules in expected.items():
        cid = case_of.get(mid)
        for rule in rules:
            recent = [x for x in by_key.get((cid, rule), ()) if t - ORACLE_COOLDOWN < x <= t]
            if cid is None or not recent:
                v["alert_missing"] += 1
    for times in by_key.values():
        times.sort()
        v["alert_twice"] += sum(1 for a, b in zip(times, times[1:]) if b - a < ORACLE_COOLDOWN)
    records = Counter((a["case_id"], a["rule"]) for a in flow.alerts)
    v["alert_twice"] += sum(c - 1 for c in records.values() if c > 1)

    # SLA clocks against the oracle at three checkpoints
    ticks = truth["ticks"]
    for t in (ticks[len(ticks) * 4 // 10], ticks[len(ticks) * 7 // 10], ticks[-1]):
        for mid, cid in case_of.items():
            if mid not in truth["cases"] or cid not in flow.cases or truth["cases"][mid]["at"] > t:
                continue
            fr_el, res_el, _ = oracle_at(truth, mid, t)
            got = flow.sla(cid, t)
            if abs(got["first_response"]["elapsed"] - fr_el) > 1e-6:
                v["sla_elapsed_wrong"] += 1
            if abs(got["resolution"]["elapsed"] - res_el) > 1e-6:
                v["sla_elapsed_wrong"] += 1

    # a clock must not move while a case is waiting (waits without a customer reply inside)
    for mid, ws, we in truth["waits"]:
        cid = case_of.get(mid)
        if cid is None or cid not in flow.cases:
            continue
        if any(m == mid and ws <= r <= we for _, m, r in truth["replies"]):
            continue
        t1, t2 = ws + timedelta(minutes=10), we - timedelta(minutes=1)
        if t2 > t1:
            e1 = flow.sla(cid, t1)["resolution"]["elapsed"]
            e2 = flow.sla(cid, t2)["resolution"]["elapsed"]
            if e2 > e1 + 1e-6:
                v["clock_running_while_paused"] += 1
    v["bounce_or_auto_opened_case"] = sum(1 for b in truth["noise"] if b in cases_with)
    for rid, parent, _ in truth["replies"]:
        if not (cases_with.get(rid, set()) & cases_with.get(parent, {None})):
            v["reply_not_threaded"] += 1
    v["_cases"] = len(flow.cases)
    return v

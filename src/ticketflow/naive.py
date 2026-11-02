"""NaiveTicketFlow: the usual first implementation, with the usual defects. Same interface as
TicketFlow so the contract tests can run against both. DO NOT use as a model.

Defects, deliberately present:
  1. no idempotency: a resubmitted message-id opens another case
  2. no threading: every reply opens a new case
  3. bounces and auto-replies open cases
  4. quoted text and signatures are kept
  5. SLA in wall-clock time, ignoring business hours and timezone
  6. no pause while waiting on customer
  7. alerts re-fire on every tick, without dedupe or cooldown
  8. cases that match no keyword are left unassigned (queue None)
  9. malformed input crashes with KeyError / AttributeError
"""
from datetime import datetime, timezone

from .common import DEFAULT_TARGETS

KEYWORDS = {"billing": ("invoice", "refund", "charge"), "technical": ("error", "api", "429"),
            "accounts": ("password", "login", "2fa"), "security": ("phishing", "suspicious")}


def _dt(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _mins(a, b):
    return max(0.0, (b - a).total_seconds() / 60.0)


class NaiveTicketFlow:
    def __init__(self, targets=None, cooldown_minutes=None):
        self.targets = targets or DEFAULT_TARGETS
        self.cases = {}
        self.alerts = []
        self.events = []
        self._n = 0

    def ingest(self, email, now=None):
        mid = email["message_id"]
        subject = email["subject"]
        body = email["body"]
        at = _dt(email["date"])
        text = (subject + " " + body).lower()
        queue = None
        for name, words in KEYWORDS.items():
            if any(w in text for w in words):
                queue = name
                break
        self._n += 1
        cid = "TF-%06d" % self._n
        priority = email.get("priority") if email.get("priority") in self.targets else "normal"
        self.cases[cid] = {
            "id": cid, "subject": subject, "requester": email["from"], "created_at": at,
            "tz": "UTC", "priority": priority, "queue": queue, "status": "open",
            "messages": [{"message_id": mid, "at": at, "from": email["from"], "text": body,
                          "direction": "customer"}],
            "status_events": [(at, "open")], "first_response_at": None, "resolved_at": None,
            "empty_body": not body,
        }
        return {"action": "created", "case_id": cid, "reason": "new"}

    def agent_reply(self, case_id, at, message_id=None):
        case = self.cases[case_id]
        case["first_response_at"] = at
        return True

    def set_status(self, case_id, status, at, seq=None):
        case = self.cases[case_id]
        case["status_events"].append((at, status))
        case["status"] = status
        case["resolved_at"] = at if status == "resolved" else None
        return True

    def sla(self, case_id, now):
        case = self.cases[case_id]
        fr_t, res_t = self.targets[case["priority"]]
        responded = case["first_response_at"] is not None
        fr = _mins(case["created_at"], case["first_response_at"] if responded else now)
        done = case["resolved_at"] is not None
        res = _mins(case["created_at"], case["resolved_at"] if done else now)

        def state(el, t, fin):
            if fin:
                return "met" if el <= t else "met_late"
            return "breached" if el > t else ("at_risk" if el >= 0.8 * t else "ok")

        return {"first_response": {"elapsed": fr, "target": fr_t, "state": state(fr, fr_t, responded)},
                "resolution": {"elapsed": res, "target": res_t, "state": state(res, res_t, done)},
                "paused": False}

    def tick(self, now):
        out = []
        for cid in sorted(self.cases):
            ev = self.sla(cid, now)
            for clock in ("first_response", "resolution"):
                for st, sev in (("at_risk", "warning"), ("breached", "critical")):
                    if ev[clock]["state"] == st:
                        rule = "%s_%s" % (clock, st)
                        alert = {"id": "AL-%05d" % (len(self.alerts) + 1), "case_id": cid, "rule": rule,
                                 "severity": sev, "opened_at": now, "notify_count": 1, "active": True}
                        self.alerts.append(alert)
                        out.append({"alert_id": alert["id"], "kind": "new", "case_id": cid,
                                    "rule": rule, "severity": sev, "at": now})
        return out

    def metrics(self, now):
        fr = [_mins(c["created_at"], c["first_response_at"]) for c in self.cases.values()
              if c["first_response_at"] is not None]
        res = [_mins(c["created_at"], c["resolved_at"]) for c in self.cases.values()
               if c["resolved_at"] is not None]
        backlog = sum(1 for c in self.cases.values() if c["status"] != "resolved")
        mean = lambda v: (sum(v) / len(v)) if v else None
        return {"first_response_minutes": {"n": len(fr), "mean": mean(fr), "median": None},
                "resolution_minutes": {"n": len(res), "mean": mean(res), "median": None},
                "backlog": backlog, "decided": 0, "breached": 0, "breach_rate": None}

"""TicketFlow: the fixed implementation, composing ingest, routing, sla, escalation, metrics."""
from . import metrics as metrics_mod
from . import sla
from .common import DEFAULT_TARGETS, STATUSES, is_aware
from .escalation import RULES, Escalator
from .ingest import Ingestor
from .reducer import RANK_AGENT, add_event
from .routing import Router


class TicketFlow:
    def __init__(self, targets=None, cooldown_minutes=None, tz_by_queue=None):
        self.targets = targets or DEFAULT_TARGETS
        self.router = Router()
        self.ingestor = Ingestor(self.router, tz_by_queue=tz_by_queue)
        self.escalator = Escalator() if cooldown_minutes is None else Escalator(cooldown_minutes)
        self.events = []

    @property
    def cases(self):
        return self.ingestor.cases

    @property
    def alerts(self):
        return self.escalator.alerts

    def ingest(self, email, now=None):
        result = self.ingestor.ingest(email)
        for gone in result.get("merged", ()):  # an absorbed case has no clock of its own any more
            for rule, (_clock, _state, severity) in RULES.items():
                self.escalator.observe(gone, rule, severity, False, result["at"])
        if result["action"] in ("created", "threaded"):
            kind = "created" if result["action"] == "created" else "customer_reply"
            self.events.append({"at": result["at"], "case_id": result["case_id"], "kind": kind})
        return result

    def _valid_event(self, case_id, at):
        """Case for an agent-side event, or None: unknown case, naive time, or time before creation."""
        case = self.cases.get(self.ingestor.resolve(case_id))
        if case is None or not is_aware(at) or at < case["created_at"]:
            return None
        return case

    def agent_reply(self, case_id, at, message_id=None):
        """Record an agent reply. The outbound message-id (generated when not given) is indexed so
        a customer reply to the agent's own mail threads into this case."""
        case = self._valid_event(case_id, at)
        if case is None:
            return False
        case_id = case["id"]
        if case["first_response_at"] is None or at < case["first_response_at"]:
            case["first_response_at"] = at
        if message_id is None:
            message_id = "tf-%s-%d@tickets.northwind.example" % (case_id.lower(), len(case["outbound_ids"]) + 1)
        self.ingestor.register_outbound(case_id, message_id)
        self.events.append({"at": at, "case_id": case_id, "kind": "agent_reply"})
        return True

    def set_status(self, case_id, status, at, seq=None):
        """Record a status change. State is derived by the reducer from event time, then source
        sequence, never from arrival order."""
        case = self._valid_event(case_id, at)
        if case is None or status not in STATUSES:
            return False
        case_id = case["id"]
        if add_event(case, at, status, RANK_AGENT, self.ingestor.next_seq() if seq is None else seq):
            self.events.append({"at": at, "case_id": case_id, "kind": "status:" + status})
        return True

    def sla(self, case_id, now):
        return sla.evaluate(self.cases[self.ingestor.resolve(case_id)], now, self.targets)

    def tick(self, now):
        out = []
        for case_id in sorted(self.cases):
            ev = self.sla(case_id, now)
            for rule, (clock, state, severity) in RULES.items():
                note = self.escalator.observe(case_id, rule, severity, ev[clock]["state"] == state, now)
                if note:
                    out.append(note)
                    self.events.append({"at": now, "case_id": case_id, "kind": "alert:" + rule})
        return out

    def metrics(self, now):
        return metrics_mod.compute(list(self.cases.values()), now, self.targets)

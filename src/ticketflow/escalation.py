"""Alerts: one alert per (case, rule). Any further notification, including one after the alert
clears and becomes active again, waits for the cooldown since the last notification."""
from datetime import timedelta

from .common import ESCALATION_COOLDOWN_MINUTES

RULES = {
    "first_response_at_risk": ("first_response", "at_risk", "warning"),
    "first_response_breached": ("first_response", "breached", "critical"),
    "resolution_at_risk": ("resolution", "at_risk", "warning"),
    "resolution_breached": ("resolution", "breached", "critical"),
}


class Escalator:
    def __init__(self, cooldown_minutes=ESCALATION_COOLDOWN_MINUTES):
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self._alerts = {}
        self._order = []

    @property
    def alerts(self):
        return [dict(self._alerts[k]) for k in self._order]

    def observe(self, case_id, rule, severity, active, now):
        """Feed the current truth for one (case, rule). Returns a notification dict or None."""
        key = (case_id, rule)
        alert = self._alerts.get(key)
        if not active:
            if alert and alert["active"]:
                alert["active"] = False
                alert["cleared_at"] = now
            return None
        if alert is None:
            alert = {"id": "AL-%05d" % (len(self._order) + 1), "case_id": case_id, "rule": rule,
                     "severity": severity, "opened_at": now, "last_notified_at": now,
                     "notify_count": 1, "active": True, "cleared_at": None}
            self._alerts[key] = alert
            self._order.append(key)
            return {"alert_id": alert["id"], "kind": "new", "case_id": case_id, "rule": rule,
                    "severity": severity, "at": now}
        if not alert["active"]:
            alert.update(active=True, cleared_at=None)
            if now - alert["last_notified_at"] < self.cooldown:
                return None  # flapping inside the cooldown stays silent
            alert["last_notified_at"] = now
            alert["notify_count"] += 1
            return {"alert_id": alert["id"], "kind": "reactivated", "case_id": case_id,
                    "rule": rule, "severity": severity, "at": now}
        if now - alert["last_notified_at"] >= self.cooldown:
            alert["last_notified_at"] = now
            alert["notify_count"] += 1
            return {"alert_id": alert["id"], "kind": "renotify", "case_id": case_id,
                    "rule": rule, "severity": severity, "at": now}
        return None

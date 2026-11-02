import unittest
from datetime import timedelta

import helpers  # noqa: F401  (sets sys.path)
from helpers import D, mail
from ticketflow import TicketFlow, metrics, sla
from ticketflow.common import MalformedEmail, get_tz, parse_dt
from ticketflow.escalation import RULES, Escalator
from ticketflow.ingest import clean_message_id, normalise, strip_quoted
from ticketflow.routing import Router, infer_priority

NY = get_tz("America/New_York")
UTCZ = get_tz("UTC")


class IngestTests(unittest.TestCase):
    def test_message_id_is_normalised(self):
        f = TicketFlow()
        f.ingest(mail("<ABC@x>"))
        r = f.ingest(mail(" abc@x "))
        self.assertEqual(r["action"], "duplicate")
        self.assertEqual(len(f.cases), 1)

    def test_duplicate_points_at_original_case(self):
        f = TicketFlow()
        first = f.ingest(mail("<a@x>"))
        again = f.ingest(mail("<a@x>"))
        self.assertEqual(again["case_id"], first["case_id"])

    def test_strip_on_wrote_block(self):
        self.assertEqual(strip_quoted("Yes please.\nOn Tue, 3 Mar 2026 at 10:00, Dana <d@x.example> wrote:\n> hi"), "Yes please.")

    def test_strip_original_message_marker(self):
        self.assertEqual(strip_quoted("Fixed now\n-----Original Message-----\nFrom: a@b.example"), "Fixed now")

    def test_strip_signature_delimiter(self):
        self.assertEqual(strip_quoted("Please advise\n-- \nAlex Smith\nNorthwind"), "Please advise")

    def test_strip_sent_from_phone(self):
        self.assertEqual(strip_quoted("ok\nSent from my iPhone"), "ok")

    def test_inline_quote_lines_dropped_but_text_kept(self):
        self.assertEqual(strip_quoted("> quoted\nmine\n> quoted again"), "mine")

    def test_empty_body_makes_case_flagged(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>", body=""))["case_id"]
        self.assertTrue(f.cases[cid]["empty_body"])
        self.assertEqual(f.cases[cid]["messages"][0]["text"], "")

    def test_rejections(self):
        f = TicketFlow()
        for bad in ({}, {"message_id": "<a@x>"}, mail("<a@x>", **{"from": "nobody"}),
                    mail("<a@x>", date="not a date"), "text", None, mail("  ")):
            self.assertEqual(f.ingest(bad)["action"], "rejected", bad)
        self.assertEqual(len(f.cases), 0)

    def test_normalise_raises_on_non_dict(self):
        with self.assertRaises(MalformedEmail):
            normalise(["x"])

    def test_references_header_threads(self):
        f = TicketFlow()
        f.ingest(mail("<a@x>"))
        r = f.ingest(mail("<c@x>", references="<zzz@x> <a@x>", at=D(2026, 3, 2, 11)))
        self.assertEqual(r["action"], "threaded")

    def test_unknown_case_reference_makes_new_case(self):
        f = TicketFlow()
        r = f.ingest(mail("<a@x>", subject="Re: [TF-999999] hello"))
        self.assertEqual(r["action"], "created")

    def test_reply_reopens_resolved_case(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        f.set_status(cid, "resolved", D(2026, 3, 2, 10))
        self.assertEqual(f.cases[cid]["status"], "resolved")
        f.ingest(mail("<b@x>", in_reply_to="<a@x>", at=D(2026, 3, 2, 11)))
        self.assertEqual(f.cases[cid]["status"], "open")
        self.assertIsNone(f.cases[cid]["resolved_at"])

    def test_customer_reply_resumes_clock(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        f.set_status(cid, "waiting_on_customer", D(2026, 3, 2, 10))
        f.ingest(mail("<b@x>", in_reply_to="<a@x>", at=D(2026, 3, 2, 12)))
        self.assertAlmostEqual(f.sla(cid, D(2026, 3, 2, 13))["resolution"]["elapsed"], 120.0)

    def test_auto_reply_and_bounce_variants_with_strong_evidence_are_ignored(self):
        f = TicketFlow()
        variants = [mail("<1@x>", subject="Out of office", headers={"Auto-Submitted": "auto-replied"}),
                    mail("<2@x>", headers={"Precedence": "bulk"}),
                    mail("<3@x>", subject="Mail delivery failed", **{"from": "mailer-daemon@x.example"}),
                    mail("<4@x>", **{"from": "postmaster@x.example"}),
                    mail("<5@x>", subject="Automatic reply: away", headers={"X-Auto-Response-Suppress": "All"}),
                    mail("<6@x>", headers={"Content-Type": "multipart/report; report-type=delivery-status"}),
                    mail("<7@x>", **{"from": "Mail Delivery System <MAILER-DAEMON@mx.example>"})]
        for v in variants:
            self.assertEqual(f.ingest(v)["action"], "ignored", v["message_id"])
        self.assertEqual(len(f.cases), 0)
        self.assertEqual(len(f.ingestor.ignored), 7)

    def test_auto_submitted_no_is_human(self):
        f = TicketFlow()
        self.assertEqual(f.ingest(mail("<a@x>", headers={"Auto-Submitted": "no"}))["action"], "created")

    def test_invalid_timezone_falls_back_to_utc(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>", tz="Mars/Olympus"))["case_id"]
        self.assertEqual(f.cases[cid]["tz"], "UTC")

    def test_clean_message_id_and_parse_dt(self):
        self.assertEqual(clean_message_id(" <A@B> "), "a@b")
        self.assertIsNone(clean_message_id(5))
        self.assertEqual(parse_dt("2026-03-02T09:00:00Z"), D(2026, 3, 2, 9))
        self.assertEqual(parse_dt("2026-03-02T09:00:00"), D(2026, 3, 2, 9))


class RoutingTests(unittest.TestCase):
    def test_keyword_routing(self):
        self.assertEqual(Router().assign("Refund please", "charge twice")[0], "billing")

    def test_fallback_queue(self):
        self.assertEqual(Router().assign("Hello", "nothing special")[0], "general")

    def test_score_beats_order(self):
        self.assertEqual(Router().assign("error api crash", "invoice")[0], "technical")

    def test_tie_goes_to_first_sorted_queue(self):
        for _ in range(3):
            self.assertEqual(Router().assign("invoice", "api")[0], "billing")

    def test_priority_inference(self):
        self.assertEqual(infer_priority("Outage", ""), "urgent")
        self.assertEqual(infer_priority("login", "I cannot sign in"), "high")
        self.assertEqual(infer_priority("question", "how"), "normal")

    def test_explicit_priority_wins_but_invalid_is_ignored(self):
        self.assertEqual(infer_priority("outage", "", "low"), "low")
        self.assertEqual(infer_priority("question", "", "bogus"), "normal")

    def test_security_queue_raises_priority(self):
        self.assertEqual(Router().assign("Suspicious email", "fyi")[1], "high")

    def test_none_inputs_still_assign(self):
        self.assertEqual(Router().assign(None, None), ("general", "normal"))


class SlaTests(unittest.TestCase):
    def test_same_day_minutes(self):
        self.assertEqual(sla.business_minutes_between(D(2026, 3, 2, 10), D(2026, 3, 2, 11), UTCZ), 60)

    def test_weekend_skipped(self):
        self.assertEqual(sla.business_minutes_between(D(2026, 3, 6, 16), D(2026, 3, 9, 10), UTCZ), 120)

    def test_outside_hours_is_zero(self):
        self.assertEqual(sla.business_minutes_between(D(2026, 3, 7, 10), D(2026, 3, 7, 15), UTCZ), 0)
        self.assertEqual(sla.business_minutes_between(D(2026, 3, 2, 18), D(2026, 3, 2, 23), UTCZ), 0)

    def test_end_before_start_is_zero(self):
        self.assertEqual(sla.business_minutes_between(D(2026, 3, 2, 12), D(2026, 3, 2, 10), UTCZ), 0)

    def test_dst_fall_back_week_has_two_full_days(self):
        # Fri 2026-10-30 09:00 EDT (13:00Z) to Mon 2026-11-02 17:00 EST (22:00Z): two business days
        self.assertEqual(sla.business_minutes_between(D(2026, 10, 30, 13), D(2026, 11, 2, 22), NY), 960)

    def test_dst_spring_forward_weekend_crossing(self):
        self.assertEqual(sla.business_minutes_between(D(2026, 3, 6, 21, 30), D(2026, 3, 9, 13, 30), NY), 60)

    def test_add_business_minutes_across_weekend(self):
        self.assertEqual(sla.add_business_minutes(D(2026, 3, 6, 16, 30), 60, UTCZ), D(2026, 3, 9, 9, 30))

    def test_add_business_minutes_across_dst(self):
        self.assertEqual(sla.add_business_minutes(D(2026, 3, 6, 21, 30), 60, NY), D(2026, 3, 9, 13, 30))

    def test_add_zero_minutes_returns_start(self):
        self.assertEqual(sla.add_business_minutes(D(2026, 3, 7, 12), 0, UTCZ), D(2026, 3, 7, 12))

    def test_add_minutes_starting_outside_hours_waits_for_opening(self):
        self.assertEqual(sla.add_business_minutes(D(2026, 3, 2, 6), 30, UTCZ), D(2026, 3, 2, 9, 30))

    def test_sydney_clock(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>", tz="Australia/Sydney", at=D(2026, 3, 2, 22)))["case_id"]
        self.assertAlmostEqual(f.sla(cid, D(2026, 3, 2, 23))["first_response"]["elapsed"], 60.0)

    def _urgent(self):
        f = TicketFlow()
        return f, f.ingest(mail("<a@x>", priority="urgent", at=D(2026, 3, 2, 9)))["case_id"]

    def test_state_progression_ok_at_risk_breached(self):
        f, cid = self._urgent()
        self.assertEqual(f.sla(cid, D(2026, 3, 2, 9, 30))["first_response"]["state"], "ok")
        self.assertEqual(f.sla(cid, D(2026, 3, 2, 9, 50))["first_response"]["state"], "at_risk")
        self.assertEqual(f.sla(cid, D(2026, 3, 2, 10, 1))["first_response"]["state"], "breached")
        self.assertEqual(f.sla(cid, D(2026, 3, 2, 10, 0))["first_response"]["state"], "at_risk")

    def test_met_and_met_late(self):
        f, cid = self._urgent()
        f.agent_reply(cid, D(2026, 3, 2, 9, 40))
        self.assertEqual(f.sla(cid, D(2026, 3, 3, 9))["first_response"]["state"], "met")
        g, cid2 = self._urgent()
        g.agent_reply(cid2, D(2026, 3, 2, 11))
        self.assertEqual(g.sla(cid2, D(2026, 3, 3, 9))["first_response"]["state"], "met_late")

    def test_resolution_states_and_paused_flag(self):
        f, cid = self._urgent()
        f.set_status(cid, "waiting_on_customer", D(2026, 3, 2, 10))
        self.assertTrue(f.sla(cid, D(2026, 3, 2, 11))["paused"])
        f.set_status(cid, "open", D(2026, 3, 2, 12))
        f.set_status(cid, "resolved", D(2026, 3, 2, 12, 30))
        r = f.sla(cid, D(2026, 3, 3, 9))["resolution"]
        self.assertEqual(r["state"], "met")
        self.assertAlmostEqual(r["elapsed"], 90.0)

    def test_resolution_breach_while_open(self):
        f, cid = self._urgent()
        self.assertEqual(f.sla(cid, D(2026, 3, 2, 13))["resolution"]["state"], "at_risk")
        self.assertEqual(f.sla(cid, D(2026, 3, 2, 13, 1))["resolution"]["state"], "breached")

    def test_due_at(self):
        f, cid = self._urgent()
        self.assertEqual(f.sla(cid, D(2026, 3, 2, 9))["first_response"]["due_at"], D(2026, 3, 2, 10))

    def test_future_events_are_ignored(self):
        f, cid = self._urgent()
        f.agent_reply(cid, D(2026, 3, 2, 9, 30))
        self.assertEqual(f.sla(cid, D(2026, 3, 2, 9, 10))["first_response"]["state"], "ok")


class EscalationTests(unittest.TestCase):
    def test_single_alert_with_severity(self):
        e = Escalator(60)
        n = e.observe("TF-1", "resolution_breached", "critical", True, D(2026, 3, 2, 9))
        self.assertEqual(n["kind"], "new")
        self.assertEqual(e.alerts[0]["severity"], "critical")
        self.assertIsNone(e.observe("TF-1", "resolution_breached", "critical", True, D(2026, 3, 2, 9, 1)))
        self.assertEqual(len(e.alerts), 1)

    def test_cooldown_boundary(self):
        e = Escalator(60)
        e.observe("TF-1", "r", "warning", True, D(2026, 3, 2, 9))
        self.assertIsNone(e.observe("TF-1", "r", "warning", True, D(2026, 3, 2, 9, 59)))
        n = e.observe("TF-1", "r", "warning", True, D(2026, 3, 2, 10))
        self.assertEqual(n["kind"], "renotify")
        self.assertEqual(e.alerts[0]["notify_count"], 2)
        self.assertIsNone(e.observe("TF-1", "r", "warning", True, D(2026, 3, 2, 10, 30)))

    def test_clear_then_reactivate_after_cooldown_keeps_one_alert(self):
        e = Escalator(60)
        e.observe("TF-1", "r", "warning", True, D(2026, 3, 2, 9))
        self.assertIsNone(e.observe("TF-1", "r", "warning", False, D(2026, 3, 2, 9, 5)))
        self.assertFalse(e.alerts[0]["active"])
        n = e.observe("TF-1", "r", "warning", True, D(2026, 3, 2, 10, 10))
        self.assertEqual(n["kind"], "reactivated")
        self.assertEqual(len(e.alerts), 1)
        self.assertTrue(e.alerts[0]["active"])

    def test_reactivation_inside_cooldown_is_silent(self):
        e = Escalator(120)
        e.observe("TF-1", "r", "warning", True, D(2026, 3, 2, 9))
        e.observe("TF-1", "r", "warning", False, D(2026, 3, 2, 9, 1))
        self.assertIsNone(e.observe("TF-1", "r", "warning", True, D(2026, 3, 2, 9, 2)))
        self.assertTrue(e.alerts[0]["active"])
        self.assertEqual(e.alerts[0]["notify_count"], 1)
        self.assertEqual(e.observe("TF-1", "r", "warning", True, D(2026, 3, 2, 11))["kind"], "renotify")

    def test_inactive_never_creates_alert(self):
        e = Escalator(60)
        self.assertIsNone(e.observe("TF-1", "r", "warning", False, D(2026, 3, 2, 9)))
        self.assertEqual(e.alerts, [])

    def test_rules_have_expected_severities(self):
        self.assertEqual({r: v[2] for r, v in RULES.items()},
                         {"first_response_at_risk": "warning", "first_response_breached": "critical",
                          "resolution_at_risk": "warning", "resolution_breached": "critical"})

    def test_flow_clears_alert_when_case_resolved(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>", priority="urgent"))["case_id"]
        f.tick(D(2026, 3, 2, 10, 30))
        self.assertTrue(any(a["active"] for a in f.alerts))
        f.agent_reply(cid, D(2026, 3, 2, 10, 31))
        f.set_status(cid, "resolved", D(2026, 3, 2, 10, 32))
        f.tick(D(2026, 3, 2, 10, 40))
        self.assertFalse(any(a["active"] for a in f.alerts if a["rule"] == "first_response_breached"))

    def test_flow_renotifies_after_cooldown_without_new_alert(self):
        f = TicketFlow(cooldown_minutes=60)
        f.ingest(mail("<a@x>", priority="urgent"))
        f.tick(D(2026, 3, 2, 10, 30))
        n = f.tick(D(2026, 3, 2, 11, 30))
        self.assertTrue(any(x["kind"] == "renotify" for x in n))
        self.assertEqual(len([a for a in f.alerts if a["rule"] == "first_response_breached"]), 1)


class FlowAndMetricsTests(unittest.TestCase):
    def test_unknown_case_or_status_returns_false(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        self.assertFalse(f.set_status("TF-999999", "open", D(2026, 3, 2, 10)))
        self.assertFalse(f.set_status(cid, "bogus", D(2026, 3, 2, 10)))
        self.assertFalse(f.agent_reply("TF-999999", D(2026, 3, 2, 10)))

    def test_earliest_agent_reply_wins(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        f.agent_reply(cid, D(2026, 3, 2, 11))
        f.agent_reply(cid, D(2026, 3, 2, 10))
        self.assertEqual(f.cases[cid]["first_response_at"], D(2026, 3, 2, 10))

    def test_set_status_is_idempotent(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        f.set_status(cid, "waiting_on_customer", D(2026, 3, 2, 10))
        f.set_status(cid, "waiting_on_customer", D(2026, 3, 2, 10))
        self.assertEqual(len(f.cases[cid]["status_events"]), 2)

    def test_metrics_empty(self):
        m = metrics.compute([], D(2026, 3, 2, 12))
        self.assertEqual((m["backlog"], m["breach_rate"], m["first_response_minutes"]["n"]), (0, None, 0))

    def test_backlog_and_resolution_time_exclude_pause(self):
        f = TicketFlow()
        a = f.ingest(mail("<a@x>"))["case_id"]
        f.ingest(mail("<b@x>"))
        f.ingest(mail("<c@x>"))
        f.set_status(a, "waiting_on_customer", D(2026, 3, 2, 10))
        f.set_status(a, "open", D(2026, 3, 2, 14))
        f.set_status(a, "resolved", D(2026, 3, 2, 15))
        m = f.metrics(D(2026, 3, 2, 16))
        self.assertEqual(m["backlog"], 2)
        self.assertAlmostEqual(m["resolution_minutes"]["mean"], 120.0)

    def test_cases_created_after_now_are_not_counted(self):
        f = TicketFlow()
        f.ingest(mail("<a@x>", at=D(2026, 3, 2, 15)))
        self.assertEqual(f.metrics(D(2026, 3, 2, 12))["backlog"], 0)

    def test_breach_rate(self):
        f = TicketFlow()
        a = f.ingest(mail("<a@x>", priority="urgent", at=D(2026, 3, 2, 9)))["case_id"]
        b = f.ingest(mail("<b@x>", priority="urgent", at=D(2026, 3, 2, 9)))["case_id"]
        f.agent_reply(a, D(2026, 3, 2, 9, 30))
        f.agent_reply(b, D(2026, 3, 2, 11))
        m = f.metrics(D(2026, 3, 2, 11, 30))
        self.assertEqual((m["decided"], m["breached"]), (2, 1))
        self.assertAlmostEqual(m["breach_rate"], 0.5)


class BoundaryTests(unittest.TestCase):
    def _urgent(self):
        f = TicketFlow()
        return f, f.ingest(mail("<a@x>", priority="urgent", at=D(2026, 3, 2, 9)))["case_id"]

    def test_add_minutes_exact_capacity(self):
        self.assertEqual(sla.add_business_minutes(D(2026, 3, 6, 16), 60, UTCZ), D(2026, 3, 6, 17))

    def test_met_exactly_at_target(self):
        f, cid = self._urgent()
        f.agent_reply(cid, D(2026, 3, 2, 10))
        self.assertEqual(f.sla(cid, D(2026, 3, 3, 9))["first_response"]["state"], "met")

    def test_at_risk_starts_at_eighty_percent(self):
        f, cid = self._urgent()
        self.assertEqual(f.sla(cid, D(2026, 3, 2, 9, 47))["first_response"]["state"], "ok")
        self.assertEqual(f.sla(cid, D(2026, 3, 2, 9, 48))["first_response"]["state"], "at_risk")

    def test_reactivation_after_cooldown_restarts_cooldown(self):
        e = Escalator(60)
        e.observe("TF-1", "r", "warning", True, D(2026, 3, 2, 9))
        e.observe("TF-1", "r", "warning", False, D(2026, 3, 2, 9, 5))
        self.assertEqual(e.observe("TF-1", "r", "warning", True, D(2026, 3, 2, 10, 10))["kind"], "reactivated")
        self.assertIsNone(e.observe("TF-1", "r", "warning", True, D(2026, 3, 2, 11, 5)))
        self.assertEqual(e.observe("TF-1", "r", "warning", True, D(2026, 3, 2, 11, 10))["kind"], "renotify")

    def test_two_rules_same_severity_are_two_alerts(self):
        e = Escalator(60)
        e.observe("TF-1", "first_response_breached", "critical", True, D(2026, 3, 2, 9))
        e.observe("TF-1", "resolution_breached", "critical", True, D(2026, 3, 2, 9))
        self.assertEqual(len(e.alerts), 2)

    def test_resolved_at_and_status_follow_event_time_not_arrival(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        f.set_status(cid, "open", D(2026, 3, 2, 12))
        f.set_status(cid, "waiting_on_customer", D(2026, 3, 2, 10))
        self.assertEqual(f.cases[cid]["status"], "open")
        f.set_status(cid, "resolved", D(2026, 3, 2, 13))
        self.assertEqual(f.cases[cid]["resolved_at"], D(2026, 3, 2, 13))
        self.assertEqual([e[0] for e in f.cases[cid]["status_events"]], sorted(e[0] for e in f.cases[cid]["status_events"]))

    def test_default_cooldown_is_two_hours(self):
        f = TicketFlow()
        f.ingest(mail("<a@x>", priority="urgent", at=D(2026, 3, 2, 9)))
        f.tick(D(2026, 3, 2, 10, 30))
        self.assertEqual([n for n in f.tick(D(2026, 3, 2, 12, 29)) if n["rule"] == "first_response_breached"], [])
        self.assertEqual(len([n for n in f.tick(D(2026, 3, 2, 12, 30)) if n["rule"] == "first_response_breached"]), 1)

    def test_default_targets(self):
        from ticketflow.common import DEFAULT_TARGETS
        self.assertEqual(DEFAULT_TARGETS["urgent"], (60, 240))
        self.assertEqual(DEFAULT_TARGETS["normal"], (240, 960))


if __name__ == "__main__":
    unittest.main()

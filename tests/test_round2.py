"""Tests added after review round 2. Each class names the finding it pins."""
import unittest
from datetime import timedelta

import helpers  # noqa: F401
from helpers import D, mail
from ticketflow import TicketFlow, metrics, sla
from ticketflow.common import DEFAULT_TARGETS, MalformedEmail, get_tz, parse_dt
from ticketflow.ingest import classify, normalise, strip_quoted
from ticketflow.routing import Router, infer_priority


class AuthenticatedThreading(unittest.TestCase):
    """A subject token alone must not attach mail from an unrelated sender to somebody's case."""

    def test_unrelated_sender_with_case_token_gets_own_flagged_case(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        r = f.ingest(mail("<m@x>", subject="[%s] hi" % cid, at=D(2026, 3, 2, 10), **{"from": "mallory@other.example"}))
        self.assertEqual(r["action"], "created")
        self.assertNotEqual(r["case_id"], cid)
        self.assertEqual(len(f.cases[cid]["messages"]), 1)
        self.assertIn("unauthorized_reference:" + cid, f.cases[r["case_id"]]["flags"])
        self.assertEqual(f.cases[cid]["status"], "open")

    def test_unrelated_sender_cannot_reopen_a_resolved_case(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        f.set_status(cid, "resolved", D(2026, 3, 2, 10))
        f.ingest(mail("<m@x>", subject="[%s]" % cid, at=D(2026, 3, 2, 11), **{"from": "mallory@other.example"}))
        self.assertEqual(f.cases[cid]["status"], "resolved")

    def test_requester_with_display_name_still_threads_by_token(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        r = f.ingest(mail("<b@x>", subject="Re: [%s] hi" % cid, at=D(2026, 3, 2, 10), **{"from": "Alex <ALEX@cust.example>"}))
        self.assertEqual((r["action"], r["case_id"]), ("threaded", cid))

    def test_prior_participant_can_use_the_token(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        f.ingest(mail("<c@x>", in_reply_to="<a@x>", at=D(2026, 3, 2, 10), **{"from": "boss@cust.example"}))
        r = f.ingest(mail("<d@x>", subject="[%s]" % cid, at=D(2026, 3, 2, 11), **{"from": "boss@cust.example"}))
        self.assertEqual(r["case_id"], cid)

    def test_message_id_threading_does_not_need_the_sender(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        r = f.ingest(mail("<c@x>", in_reply_to="<a@x>", at=D(2026, 3, 2, 10), **{"from": "colleague@cust.example"}))
        self.assertEqual(r["case_id"], cid)
        self.assertIn("colleague@cust.example", f.cases[cid]["participants"])


class OutboundThreading(unittest.TestCase):
    """A customer replying to the agent's own mail must land in the same case."""

    def test_reply_to_generated_outbound_id_threads(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        f.agent_reply(cid, D(2026, 3, 2, 10))
        out = f.cases[cid]["outbound_ids"][0]
        r = f.ingest(mail("<b@x>", in_reply_to="<%s>" % out, at=D(2026, 3, 2, 11)))
        self.assertEqual((r["action"], r["case_id"]), ("threaded", cid))

    def test_reply_to_explicit_outbound_id_and_references(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        f.agent_reply(cid, D(2026, 3, 2, 10), message_id="<Out-1@northwind.example>")
        r = f.ingest(mail("<b@x>", references="<zzz@x> <out-1@northwind.example>", at=D(2026, 3, 2, 11)))
        self.assertEqual(r["case_id"], cid)

    def test_outbound_ids_are_per_reply_and_unique(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        f.agent_reply(cid, D(2026, 3, 2, 10))
        f.agent_reply(cid, D(2026, 3, 2, 11))
        ids = f.cases[cid]["outbound_ids"]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

    def test_unregistered_outbound_id_does_not_thread(self):
        f = TicketFlow()
        f.ingest(mail("<a@x>"))
        r = f.ingest(mail("<b@x>", in_reply_to="<nobody-sent-this@x>", subject="Question", at=D(2026, 3, 2, 11)))
        self.assertEqual(r["action"], "created")


class LateParent(unittest.TestCase):
    def test_child_before_parent_ends_up_in_one_case(self):
        f = TicketFlow()
        child = f.ingest(mail("<child@x>", in_reply_to="<parent@x>", at=D(2026, 3, 2, 11)))
        parent = f.ingest(mail("<parent@x>", at=D(2026, 3, 2, 9)))
        self.assertEqual(len(f.cases), 1)
        self.assertEqual(parent["case_id"], child["case_id"])
        case = f.cases[child["case_id"]]
        self.assertEqual(case["created_at"], D(2026, 3, 2, 9))
        self.assertEqual([m["message_id"] for m in case["messages"]], ["parent@x", "child@x"])
        self.assertAlmostEqual(f.sla(case["id"], D(2026, 3, 2, 12))["first_response"]["elapsed"], 180.0)


class StatusReducer(unittest.TestCase):
    """Status and clocks come from event time, never from arrival order."""

    def test_late_reply_before_resolution_does_not_reopen(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>", at=D(2026, 3, 2, 9)))["case_id"]
        f.set_status(cid, "resolved", D(2026, 3, 2, 12))
        f.ingest(mail("<b@x>", in_reply_to="<a@x>", at=D(2026, 3, 2, 11)))
        self.assertEqual(f.cases[cid]["status"], "resolved")
        self.assertEqual(f.sla(cid, D(2026, 3, 2, 13))["resolution"]["state"], "met")

    def test_late_reply_after_waiting_started_resumes_in_event_order(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>", at=D(2026, 3, 2, 9)))["case_id"]
        f.set_status(cid, "waiting_on_customer", D(2026, 3, 2, 10))
        f.set_status(cid, "open", D(2026, 3, 2, 14))  # agent resumed by hand
        f.ingest(mail("<b@x>", in_reply_to="<a@x>", at=D(2026, 3, 2, 12)))  # arrives late
        self.assertEqual(f.cases[cid]["status"], "open")
        # open 9-10, waiting 10-12, open from the reply at 12: 60 + 180 minutes by 15:00
        self.assertAlmostEqual(f.sla(cid, D(2026, 3, 2, 15))["resolution"]["elapsed"], 60 + 180)

    def test_arrival_order_does_not_change_the_outcome(self):
        def run(order):
            f = TicketFlow()
            cid = f.ingest(mail("<a@x>", at=D(2026, 3, 2, 9)))["case_id"]
            steps = {"wait": lambda: f.set_status(cid, "waiting_on_customer", D(2026, 3, 2, 10)),
                     "reply": lambda: f.ingest(mail("<b@x>", in_reply_to="<a@x>", at=D(2026, 3, 2, 11))),
                     "resolve": lambda: f.set_status(cid, "resolved", D(2026, 3, 2, 13))}
            for name in order:
                steps[name]()
            return f.cases[cid]["status"], f.sla(cid, D(2026, 3, 2, 16))["resolution"]["elapsed"]
        outcomes = {run(o) for o in (("wait", "reply", "resolve"), ("resolve", "reply", "wait"),
                                       ("reply", "wait", "resolve"), ("reply", "resolve", "wait"))}
        self.assertEqual(len(outcomes), 1, outcomes)

    def test_customer_reply_at_same_instant_as_resolve_reopens(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>", at=D(2026, 3, 2, 9)))["case_id"]
        f.ingest(mail("<b@x>", in_reply_to="<a@x>", at=D(2026, 3, 2, 12)))
        f.set_status(cid, "resolved", D(2026, 3, 2, 12))
        self.assertEqual(f.cases[cid]["status"], "open")

    def test_same_instant_agent_conflict_uses_source_sequence_not_arrival(self):
        def run(first, second):
            f = TicketFlow()
            cid = f.ingest(mail("<a@x>", at=D(2026, 3, 2, 9)))["case_id"]
            f.set_status(cid, first[0], D(2026, 3, 2, 10), seq=first[1])
            f.set_status(cid, second[0], D(2026, 3, 2, 10), seq=second[1])
            return f.sla(cid, D(2026, 3, 2, 13))["resolution"]["elapsed"]
        a = run(("waiting_on_customer", 1), ("open", 2))
        b = run(("open", 2), ("waiting_on_customer", 1))
        self.assertEqual(a, b)
        self.assertAlmostEqual(a, 240.0)

    def test_every_reply_is_recorded_as_an_event(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>", at=D(2026, 3, 2, 9)))["case_id"]
        f.ingest(mail("<b@x>", in_reply_to="<a@x>", at=D(2026, 3, 2, 10)))
        self.assertEqual(len(f.cases[cid]["event_log"]), 2)

    def test_event_apis_reject_pre_creation_and_naive_times(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>", at=D(2026, 3, 2, 9)))["case_id"]
        from datetime import datetime
        self.assertFalse(f.agent_reply(cid, D(2026, 3, 2, 8)))
        self.assertFalse(f.set_status(cid, "resolved", D(2026, 3, 2, 8)))
        self.assertFalse(f.agent_reply(cid, datetime(2026, 3, 2, 10)))
        self.assertFalse(f.set_status(cid, "resolved", "2026-03-02T10:00:00+00:00"))
        self.assertIsNone(f.cases[cid]["first_response_at"])
        self.assertEqual(f.sla(cid, D(2026, 3, 2, 9, 30))["first_response"]["state"], "ok")

    def test_flow_events_carry_kind_and_time(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>", at=D(2026, 3, 2, 9)))["case_id"]
        f.ingest(mail("<b@x>", in_reply_to="<a@x>", at=D(2026, 3, 2, 10)))
        f.agent_reply(cid, D(2026, 3, 2, 11))
        f.set_status(cid, "resolved", D(2026, 3, 2, 12))
        self.assertEqual([(e["kind"], e["at"]) for e in f.events],
                         [("created", D(2026, 3, 2, 9)), ("customer_reply", D(2026, 3, 2, 10)),
                          ("agent_reply", D(2026, 3, 2, 11)), ("status:resolved", D(2026, 3, 2, 12))])


class MalformedInput(unittest.TestCase):
    def test_nested_and_header_types_raise_malformed_email(self):
        for bad in (dict(mail("<a@x>"), headers=[1]), dict(mail("<a@x>"), headers="x"),
                    dict(mail("<a@x>"), references=1), dict(mail("<a@x>"), references=[1, 2]),
                    dict(mail("<a@x>"), references={"a": 1}), dict(mail("<a@x>"), in_reply_to=5),
                    dict(mail("<a@x>"), subject=["x"]), dict(mail("<a@x>"), body=b"bytes"),
                    dict(mail("<a@x>"), date=None), dict(mail("<a@x>"), date=[1]),
                    dict(mail("<a@x>"), **{"from": ["a@x"]}), dict(mail("<a@x>"), **{"from": "a@@x"})):
            with self.assertRaises(MalformedEmail, msg=repr(bad)):
                normalise(bad)

    def test_ingest_never_raises_on_weird_values(self):
        f = TicketFlow()
        weird = [None, 0, 1.5, "", "x", [], {}, [1], {"a": 1}, b"b", True, object()]
        for i, w in enumerate(weird):
            for key in ("headers", "references", "in_reply_to", "subject", "body", "date", "tz", "priority",
                        "from", "message_id", "content_type"):
                email = dict(mail("<w%d%s@x>" % (i, key)), **{key: w})
                result = f.ingest(email)
                self.assertIn(result["action"], ("created", "threaded", "rejected", "duplicate", "ignored"))

    def test_rfc2822_date_header_is_accepted(self):
        self.assertEqual(parse_dt("Mon, 2 Mar 2026 09:00:00 +0000"), D(2026, 3, 2, 9))
        self.assertEqual(parse_dt("Mon, 2 Mar 2026 10:00:00 +0100"), D(2026, 3, 2, 9))
        r = TicketFlow().ingest(mail("<a@x>", date="Mon, 2 Mar 2026 09:00:00 +0000"))
        self.assertEqual(r["action"], "created")


class BounceAndAutoReplyEvidence(unittest.TestCase):
    def kind(self, **kw):
        e = normalise(mail("<a@x>", **kw))
        return e["kind"], e["warnings"]

    def test_human_mail_with_bounce_like_subject_is_kept_and_flagged(self):
        f = TicketFlow()
        r = f.ingest(mail("<a@x>", subject="Cannot fix delivery failure"))
        self.assertEqual(r["action"], "created")
        self.assertEqual(f.ingestor.flagged[0]["flags"], ["weak_bounce_subject"])
        self.assertEqual(f.ingestor.ignored, [])
        self.assertEqual(self.kind(subject="Returned mail order arrived damaged")[0], "human")

    def test_suppress_header_alone_is_a_preference_not_proof(self):
        self.assertEqual(self.kind(headers={"X-Auto-Response-Suppress": "None"}),
                         ("human", ["auto_response_suppress_preference"]))
        self.assertEqual(self.kind(headers={"X-Auto-Response-Suppress": "All"})[0], "human")

    def test_suppress_header_plus_auto_subject_is_auto(self):
        self.assertEqual(self.kind(subject="Automatic reply: away", headers={"X-Auto-Response-Suppress": "All"})[0], "auto_reply")

    def test_auto_subject_alone_is_weak(self):
        self.assertEqual(self.kind(subject="Out of office")[0], "human")

    def test_auto_submitted_is_parsed_per_rfc_3834(self):
        for value, kind in (("no", "human"), ("No", "human"), ("no; owner-email=x@cust.example", "human"),
                            ("no (not automatic)", "human"), ("auto-replied", "auto_reply"),
                            ("auto-generated; type=x", "auto_reply"), ("auto-notified", "auto_reply"),
                            ("", "human")):
            self.assertEqual(self.kind(headers={"Auto-Submitted": value})[0], kind, value)

    def test_delivery_status_report_is_a_bounce_without_english_subject(self):
        self.assertEqual(self.kind(subject="Zustellung fehlgeschlagen",
                                   headers={"Content-Type": 'multipart/report; report-type="delivery-status"; boundary=x'})[0], "bounce")
        self.assertEqual(self.kind(subject="x", content_type="multipart/report; report-type=delivery-status")[0], "bounce")
        self.assertEqual(self.kind(headers={"X-Failed-Recipients": "a@x"})[0], "bounce")
        self.assertEqual(self.kind(headers={"Content-Type": "multipart/report; report-type=disposition-notification"})[0], "human")

    def test_sender_is_parsed_as_an_address(self):
        self.assertEqual(self.kind(**{"from": "Mail Delivery System <MAILER-DAEMON@mx.example>"})[0], "bounce")
        f = TicketFlow()
        a = f.ingest(mail("<a@x>", **{"from": "Alex <alex@cust.example>"}))
        b = f.ingest(mail("<b@x>", at=D(2026, 3, 2, 10), subject="Other", **{"from": "alex@cust.example"}))
        self.assertEqual(f.cases[a["case_id"]]["requester"], f.cases[b["case_id"]]["requester"])
        self.assertEqual(f.cases[a["case_id"]]["requester"], "alex@cust.example")

    def test_classify_returns_tuple(self):
        self.assertEqual(classify("a@x", "hello", {}), ("human", []))


class QuotedTextStripping(unittest.TestCase):
    def test_interleaved_reply_keeps_the_answers(self):
        body = "On Mon, 2 Mar 2026, Dana wrote:\n> Which plan?\nPro plan.\n> Which region?\nEU."
        self.assertEqual(strip_quoted(body), "Pro plan.\nEU.")

    def test_wrapped_attribution_line_is_dropped(self):
        body = "Yes.\nOn Mon, Mar 2, 2026 at 10:00 AM Dana Support <dana@northwind.example>\nwrote:\n> hi"
        self.assertEqual(strip_quoted(body), "Yes.")

    def test_from_line_without_header_block_is_customer_text(self):
        self.assertEqual(strip_quoted("From: a@cust.example\nAPI is down"), "From: a@cust.example\nAPI is down")

    def test_outlook_header_block_ends_the_new_text(self):
        body = "Fixed.\nFrom: dana@northwind.example\nSent: Monday\nTo: a@x\nSubject: Re: hi\nold stuff"
        self.assertEqual(strip_quoted(body), "Fixed.")

    def test_indented_signature_delimiter_still_ends_the_text(self):
        self.assertEqual(strip_quoted("Thanks\n  -- \nAlex\nbilling team api"), "Thanks")

    def test_interleaved_body_is_not_empty_and_routes(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>", subject="Re: help", body="On Mon, 2 Mar 2026, Dana wrote:\n> q?\nThe invoice is wrong."))["case_id"]
        self.assertEqual(f.cases[cid]["messages"][0]["text"], "The invoice is wrong.")
        self.assertEqual(f.cases[cid]["queue"], "billing")

    def test_only_quoted_text_is_flagged_and_original_is_kept(self):
        f = TicketFlow()
        cid = f.ingest(mail("<a@x>", body="> only a quote"))["case_id"]
        self.assertIn("no_new_text_after_stripping", f.cases[cid]["flags"])
        self.assertEqual(f.cases[cid]["messages"][0]["raw"], "> only a quote")


class RoutingBoundaries(unittest.TestCase):
    def test_keywords_match_whole_words(self):
        r = Router()
        self.assertEqual(r.assign("Capital letters", "in my name")[0], "general")
        self.assertEqual(r.assign("Budget approval", "for a rapid rollout")[0], "general")
        self.assertEqual(r.assign("Accessory", "discharge question")[0], "general")
        self.assertEqual(r.assign("API question", "")[0], "technical")
        self.assertEqual(r.assign("two invoices", "")[0], "billing")
        self.assertEqual(r.assign("charged twice", "")[0], "billing")

    def test_security_report_outranks_account_keyword_counts(self):
        q, p = Router().assign("Account hacked", "my password and login and access are gone, I am locked out")
        self.assertEqual((q, p), ("security", "high"))

    def test_precedence_can_be_switched_off(self):
        r = Router(precedence=())
        self.assertEqual(r.assign("hacked", "password login access locked sign in")[0], "accounts")

    def test_negated_urgency_is_not_urgent(self):
        self.assertEqual(infer_priority("This is not urgent", ""), "normal")
        self.assertEqual(infer_priority("non-urgent question", ""), "normal")
        self.assertEqual(infer_priority("urgent: site down", ""), "urgent")
        self.assertEqual(infer_priority("Not urgent but I cannot sign in", ""), "high")


class SlaDueTimesAndMetrics(unittest.TestCase):
    def test_first_response_due_at_matches_each_priority(self):
        f = TicketFlow()
        ids = {p: f.ingest(mail("<%s@x>" % p, priority=p, at=D(2026, 3, 2, 9)))["case_id"] for p in DEFAULT_TARGETS}
        for p, (fr, _res) in DEFAULT_TARGETS.items():
            self.assertEqual(f.sla(ids[p], D(2026, 3, 2, 9))["first_response"]["due_at"], D(2026, 3, 2, 9) + timedelta(minutes=fr), p)
            self.assertEqual(f.sla(ids[p], D(2026, 3, 2, 9))["first_response"]["target"], fr)
            self.assertEqual(f.sla(ids[p], D(2026, 3, 2, 9))["resolution"]["target"], _res)

    def test_breach_rate_is_not_hidden_by_open_cases(self):
        f = TicketFlow()
        a = f.ingest(mail("<a@x>", priority="urgent", at=D(2026, 3, 2, 9)))["case_id"]
        f.agent_reply(a, D(2026, 3, 2, 11))  # first response late, resolution clock still running
        m = f.metrics(D(2026, 3, 2, 11, 30))
        self.assertEqual(m["per_clock"]["first_response"], {"decided": 1, "breached": 1, "breach_rate": 1.0})
        self.assertEqual(m["per_clock"]["resolution"]["decided"], 0)
        f.agent_reply(f.ingest(mail("<b@x>", priority="urgent", at=D(2026, 3, 2, 9)))["case_id"], D(2026, 3, 2, 9, 30))
        m = f.metrics(D(2026, 3, 2, 11, 30))
        self.assertEqual((m["decided"], m["breached"]), (2, 1))

    def test_resolved_late_counts_on_the_resolution_clock(self):
        f = TicketFlow()
        a = f.ingest(mail("<a@x>", priority="urgent", at=D(2026, 3, 2, 9)))["case_id"]
        f.agent_reply(a, D(2026, 3, 2, 9, 10))
        f.set_status(a, "resolved", D(2026, 3, 2, 16))
        m = f.metrics(D(2026, 3, 2, 17))
        self.assertEqual(m["per_clock"]["resolution"], {"decided": 1, "breached": 1, "breach_rate": 1.0})
        self.assertEqual(m["per_clock"]["first_response"], {"decided": 1, "breached": 0, "breach_rate": 0.0})


class QueueCalendar(unittest.TestCase):
    def test_queue_calendar_overrides_the_email_tz(self):
        f = TicketFlow(tz_by_queue={"billing": "Asia/Kolkata"})
        cid = f.ingest(mail("<a@x>", tz="Pacific/Auckland", at=D(2026, 3, 2, 22)))["case_id"]
        self.assertEqual(f.cases[cid]["tz"], "Asia/Kolkata")
        g = TicketFlow(tz_by_queue={"technical": "Asia/Kolkata"})
        cid = g.ingest(mail("<a@x>", tz="Europe/London"))["case_id"]
        self.assertEqual(g.cases[cid]["tz"], "Europe/London")


if __name__ == "__main__":
    unittest.main()

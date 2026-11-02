"""Contract tests: each one targets a defect of the naive implementation.

The same test bodies run twice. Against TicketFlow they must pass. Against NaiveTicketFlow every
one of them must fail (AssertionError or a crash), which proves the test can detect its defect.
Monday 2026-03-02 is a Monday; 2026-03-08 is the US DST change."""
import unittest

import helpers
from helpers import D, mail
from ticketflow import NaiveTicketFlow, TicketFlow


class ContractBase:
    def make(self):
        raise NotImplementedError

    def test_duplicate_message_id_gives_one_case(self):
        f = self.make()
        e = mail("<a@x>")
        f.ingest(e)
        f.ingest(dict(e))
        self.assertEqual(len(f.cases), 1)

    def test_reply_by_in_reply_to_threads(self):
        f = self.make()
        f.ingest(mail("<a@x>"))
        f.ingest(mail("<b@x>", subject="Re: Invoice question", in_reply_to="<a@x>", at=D(2026, 3, 2, 10)))
        self.assertEqual(len(f.cases), 1)

    def test_reply_by_case_reference_threads(self):
        f = self.make()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        f.ingest(mail("<b@x>", subject="Re: [%s] Invoice question" % cid, at=D(2026, 3, 2, 10)))
        self.assertEqual(len(f.cases), 1)

    def test_bounce_opens_no_case(self):
        f = self.make()
        f.ingest(mail("<b@x>", subject="Undelivered Mail Returned", **{"from": "mailer-daemon@mail.example"}))
        self.assertEqual(len(f.cases), 0)

    def test_auto_reply_opens_no_case(self):
        f = self.make()
        f.ingest(mail("<b@x>", subject="Automatic reply: away", headers={"Auto-Submitted": "auto-replied"}))
        self.assertEqual(len(f.cases), 0)

    def test_no_case_left_unassigned(self):
        f = self.make()
        cid = f.ingest(mail("<a@x>", subject="Hello", body="just saying hi"))["case_id"]
        self.assertIn(f.cases[cid]["queue"], {"billing", "technical", "accounts", "security", "general"})

    def test_weekend_is_not_sla_time(self):
        f = self.make()
        cid = f.ingest(mail("<a@x>", priority="urgent", at=D(2026, 3, 6, 16, 30)))["case_id"]
        fr = f.sla(cid, D(2026, 3, 9, 9, 30))["first_response"]
        self.assertAlmostEqual(fr["elapsed"], 60.0)
        self.assertNotEqual(fr["state"], "breached")

    def test_clock_starts_at_local_opening(self):
        f = self.make()
        cid = f.ingest(mail("<a@x>", tz="Asia/Kolkata", at=D(2026, 3, 2, 22)))["case_id"]
        self.assertAlmostEqual(f.sla(cid, D(2026, 3, 3, 4, 30))["first_response"]["elapsed"], 60.0)

    def test_dst_week_uses_local_clock(self):
        f = self.make()
        cid = f.ingest(mail("<a@x>", tz="America/New_York", priority="urgent", at=D(2026, 3, 6, 21, 30)))["case_id"]
        self.assertAlmostEqual(f.sla(cid, D(2026, 3, 9, 13, 30))["first_response"]["elapsed"], 60.0)

    def test_waiting_on_customer_pauses_resolution_clock(self):
        f = self.make()
        cid = f.ingest(mail("<a@x>", at=D(2026, 3, 2, 9)))["case_id"]
        f.set_status(cid, "waiting_on_customer", D(2026, 3, 2, 10))
        f.set_status(cid, "open", D(2026, 3, 2, 14))
        self.assertAlmostEqual(f.sla(cid, D(2026, 3, 2, 15))["resolution"]["elapsed"], 120.0)

    def test_out_of_order_status_events(self):
        f = self.make()
        cid = f.ingest(mail("<a@x>", at=D(2026, 3, 2, 9)))["case_id"]
        f.set_status(cid, "open", D(2026, 3, 2, 12))
        f.set_status(cid, "waiting_on_customer", D(2026, 3, 2, 10))
        self.assertAlmostEqual(f.sla(cid, D(2026, 3, 2, 15))["resolution"]["elapsed"], 240.0)

    def test_alert_fires_once_per_case_and_rule(self):
        f = self.make()
        f.ingest(mail("<a@x>", priority="urgent", at=D(2026, 3, 2, 9)))
        f.tick(D(2026, 3, 2, 10, 30))
        f.tick(D(2026, 3, 2, 10, 31))
        rules = [a["rule"] for a in f.alerts]
        self.assertEqual(rules.count("first_response_breached"), 1)

    def test_no_renotification_inside_cooldown(self):
        f = self.make()
        f.ingest(mail("<a@x>", priority="urgent", at=D(2026, 3, 2, 9)))
        f.tick(D(2026, 3, 2, 10, 30))
        self.assertEqual(f.tick(D(2026, 3, 2, 11, 0)), [])

    def test_quoted_text_and_signature_stripped(self):
        f = self.make()
        body = "Thanks!\n\n> old text\nOn Mon, 2 Mar 2026, Dana wrote:\n> more\n-- \nAlex"
        cid = f.ingest(mail("<a@x>", body=body))["case_id"]
        self.assertEqual(f.cases[cid]["messages"][0]["text"], "Thanks!")

    def test_malformed_email_is_rejected_not_fatal(self):
        f = self.make()
        f.ingest({"subject": "no id, no sender"})
        self.assertEqual(len(f.cases), 0)

    def test_reply_to_the_agents_own_mail_threads(self):
        f = self.make()
        cid = f.ingest(mail("<a@x>"))["case_id"]
        f.agent_reply(cid, D(2026, 3, 2, 10), message_id="<out-1@northwind.example>")
        f.ingest(mail("<b@x>", subject="Re: Invoice question", in_reply_to="<out-1@northwind.example>",
                      at=D(2026, 3, 2, 11)))
        self.assertEqual(len(f.cases), 1)

    def test_interleaved_reply_keeps_the_answers(self):
        f = self.make()
        body = "On Mon, 2 Mar 2026, Dana wrote:\n> Which plan?\nPro plan.\n> Which region?\nEU."
        cid = f.ingest(mail("<a@x>", body=body))["case_id"]
        self.assertEqual(f.cases[cid]["messages"][0]["text"], "Pro plan.\nEU.")

    def test_metrics_use_business_minutes(self):
        f = self.make()
        cid = f.ingest(mail("<a@x>", at=D(2026, 3, 6, 16, 30)))["case_id"]
        f.agent_reply(cid, D(2026, 3, 9, 9, 30))
        m = f.metrics(D(2026, 3, 9, 12))
        self.assertAlmostEqual(m["first_response_minutes"]["mean"], 60.0)


class FixedContract(ContractBase, unittest.TestCase):
    def make(self):
        return TicketFlow()


class NaiveContract(unittest.TestCase):
    """Every contract test must fail on the naive implementation."""

    def make(self):
        return NaiveTicketFlow()


CRASH_IS_THE_DEFECT = {"test_malformed_email_is_rejected_not_fatal"}  # naive KeyError is the documented defect


def run_inverted(name, make):
    """Run contract test `name` against `make()` and return (detected, reason).

    Detected means an AssertionError, or (only for the malformed-input test, where a crash IS the
    defect) a KeyError/AttributeError/TypeError. Any other exception, for example a RuntimeError from
    a broken constructor, is not a detected defect: it is a broken harness and counts as failure."""
    class Probe(unittest.TestCase):
        pass
    probe = Probe()
    probe.make = make
    try:
        getattr(ContractBase, name)(probe)
    except AssertionError as exc:
        return True, "AssertionError: %s" % exc
    except (KeyError, AttributeError, TypeError) as exc:
        if name in CRASH_IS_THE_DEFECT:
            return True, "%s: %s" % (type(exc).__name__, exc)
        return False, "unexpected crash %s: %s" % (type(exc).__name__, exc)
    except Exception as exc:
        return False, "unexpected crash %s: %s" % (type(exc).__name__, exc)
    return False, "passed"


def _inverted(name):
    def test(self):
        detected, reason = run_inverted(name, self.make)
        self.assertTrue(detected, "naive implementation was not caught by %s: %s" % (name, reason))
    test.__name__ = name
    return test


CONTRACT_TESTS = sorted(n for n in dir(ContractBase) if n.startswith("test_"))
for _n in CONTRACT_TESTS:
    setattr(NaiveContract, _n, _inverted(_n))


class ContractShape(unittest.TestCase):
    def test_contract_has_at_least_eighteen_tests(self):
        self.assertGreaterEqual(len(CONTRACT_TESTS), 18)

    def test_a_broken_constructor_does_not_count_as_catching_a_defect(self):
        """Review round 2: a naive flow whose constructor raises RuntimeError used to satisfy every
        inverted contract. It must now be reported as a broken harness for every test."""
        def broken():
            raise RuntimeError("constructor is broken")
        for name in CONTRACT_TESTS:
            detected, reason = run_inverted(name, broken)
            self.assertFalse(detected, name)
            self.assertIn("RuntimeError", reason)

    def test_naive_failures_have_the_expected_kind(self):
        kinds = {}
        for name in CONTRACT_TESTS:
            detected, reason = run_inverted(name, NaiveTicketFlow)
            self.assertTrue(detected, (name, reason))
            kinds[name] = reason.split(":")[0]
        self.assertEqual({n for n, k in kinds.items() if k != "AssertionError"}, CRASH_IS_THE_DEFECT)


if __name__ == "__main__":
    unittest.main()

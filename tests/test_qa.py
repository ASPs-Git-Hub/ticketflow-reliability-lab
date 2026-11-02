import json
import os
import unittest

import helpers  # noqa: F401
from ticketflow import qa

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


def item(topic, *turns, labels=None):
    return {"id": "t", "topic": topic, "turns": [["agent" if r == "a" else "customer", t] for r, t in turns],
            "labels": labels}


GOOD = item("password_reset",
            ("a", "Hello, thanks for contacting Northwind support."),
            ("c", "I forgot my password."),
            ("a", "I understand. I've sent a reset link; it is valid for 15 minutes."),
            ("c", "Thanks"),
            ("a", "Anything else I can help with? Have a great day."))


class CriterionTests(unittest.TestCase):
    def test_perfect_transcript_scores_100(self):
        r = qa.score_transcript(GOOD)
        self.assertEqual(r["total"], 100)
        self.assertEqual(r["verdict"], "pass")

    def test_weights_sum_to_100(self):
        self.assertEqual(sum(qa.WEIGHTS.values()), 100)

    def test_greeting_only_counts_first_agent_turn(self):
        self.assertTrue(qa.check_greeting(item("invoice", ("a", "Hi there"))))
        self.assertFalse(qa.check_greeting(item("invoice", ("a", "What now?"), ("c", "x"), ("a", "Hello"))))
        self.assertFalse(qa.check_greeting(item("invoice")))

    def test_greeting_ignores_customer_hello(self):
        self.assertFalse(qa.check_greeting(item("invoice", ("c", "Hello"), ("a", "State your issue"))))

    def test_identification(self):
        self.assertTrue(qa.check_identification(item("invoice", ("a", "To confirm, you need an invoice."))))
        self.assertTrue(qa.check_identification(item("invoice", ("a", "Could you share the month?"))))
        self.assertFalse(qa.check_identification(item("invoice", ("a", "Open billing."))))

    def test_correct_info_required_and_forbidden(self):
        ok = item("invoice", ("a", "Open Billing and download the PDF."))
        self.assertTrue(qa.check_correct_info(ok))
        self.assertFalse(qa.check_correct_info(item("invoice", ("a", "Open Billing and export the CSV PDF."))))
        self.assertFalse(qa.check_correct_info(item("invoice", ("a", "Open Billing."))))

    def test_correct_info_unknown_topic_fails(self):
        self.assertFalse(qa.check_correct_info(item("weather", ("a", "billing pdf"))))

    def test_correct_info_all_groups_needed(self):
        self.assertFalse(qa.check_correct_info(item("password_reset", ("a", "I sent a reset link."))))
        self.assertTrue(qa.check_correct_info(item("password_reset", ("a", "The reset link lasts 15 minutes."))))
        self.assertFalse(qa.check_correct_info(item("password_reset", ("a", "The reset link lasts 15 minutes, or 24 hours."))))

    def test_tone_needs_courtesy_and_no_rudeness(self):
        self.assertTrue(qa.check_tone(item("invoice", ("a", "Sorry about that."))))
        self.assertFalse(qa.check_tone(item("invoice", ("a", "Open billing."))))
        self.assertFalse(qa.check_tone(item("invoice", ("a", "Please calm down."))))

    def test_tone_penalises_shouting(self):
        self.assertFalse(qa.check_tone(item("invoice", ("a", "PLEASE CHECK THE BILLING PAGE NOW"))))
        self.assertTrue(qa.check_tone(item("invoice", ("a", "Please check the API page, the FAQ helps."))))

    def test_resolution_action_and_instruction(self):
        self.assertTrue(qa.check_resolution(item("invoice", ("a", "Hi"), ("a", "I've emailed the PDF."))))
        self.assertTrue(qa.check_resolution(item("invoice", ("a", "Hi"), ("a", "Please open the Billing page."))))

    def test_resolution_ignores_first_turn_and_deflections(self):
        self.assertFalse(qa.check_resolution(item("invoice", ("a", "I've sent it"))))
        self.assertFalse(qa.check_resolution(item("invoice", ("a", "Hi"), ("a", "Please try again later."))))
        self.assertTrue(qa.check_resolution(item("invoice", ("a", "Hi"), ("a", "I've sent it. I'll get back to you."))))

    def test_closing_needs_a_second_turn(self):
        self.assertFalse(qa.check_closing(item("invoice", ("a", "Anything else?"))))
        self.assertTrue(qa.check_closing(item("invoice", ("a", "Hi"), ("a", "Have a good day."))))
        self.assertFalse(qa.check_closing(item("invoice", ("a", "Hi"), ("a", "Done."))))


class CriticalTests(unittest.TestCase):
    def test_asking_for_password_is_critical(self):
        self.assertTrue(qa.check_critical(item("two_factor", ("a", "Please send me your password."))))

    def test_card_number_request_is_critical(self):
        self.assertTrue(qa.check_critical(item("cancel", ("a", "Can you tell me your card number?"))))

    def test_negated_mention_is_not_critical(self):
        self.assertFalse(qa.check_critical(item("phishing", ("a", "Please do not share your password with anyone."))))
        self.assertFalse(qa.check_critical(item("phishing", ("a", "We never ask you to send me your password."))))

    def test_abuse_is_critical(self):
        self.assertTrue(qa.check_critical(item("invoice", ("a", "Only an idiot would do that."))))

    def test_customer_words_do_not_trigger(self):
        self.assertFalse(qa.check_critical(item("invoice", ("c", "You idiot, send me your password"), ("a", "Sorry."))))

    def test_critical_overrides_total(self):
        bad = item("password_reset", *[(r, t) for r, t in [
            ("a", "Hello, thanks for contacting Northwind support."), ("c", "x"),
            ("a", "I understand. Send me your password. I've sent a reset link, valid 15 minutes."),
            ("c", "y"), ("a", "Anything else? Have a great day.")]])
        r = qa.score_transcript(bad)
        self.assertEqual(r["critical_fail"], 1)
        self.assertEqual(r["total"], 0)
        self.assertEqual(r["verdict"], "fail")
        self.assertEqual(r["criteria"]["greeting"], 1)

    def test_pass_mark_boundary(self):
        flags = {"greeting": 1, "identification": 1, "correct_info": 1, "tone": 0, "resolution": 1, "closing": 0}
        self.assertEqual(qa.total_from(flags, 0), 75)
        flags["resolution"] = 0
        self.assertEqual(qa.total_from(flags, 0), 55)
        self.assertEqual(qa.total_from({"greeting": 1, "identification": 1, "correct_info": 1, "tone": 1}, 0), 70)

    def test_pass_mark_is_inclusive_at_70(self):
        labels = {"greeting": 1, "identification": 1, "correct_info": 1, "tone": 1, "resolution": 0,
                  "closing": 0, "critical_fail": 0}
        self.assertEqual(qa.human_verdict(labels), "pass")
        self.assertEqual(qa.human_verdict(dict(labels, tone=0)), "fail")

    def test_exactly_three_shouted_words_fail_tone(self):
        self.assertFalse(qa.check_tone(item("invoice", ("a", "PLEASE CHECK NOW"))))
        self.assertTrue(qa.check_tone(item("invoice", ("a", "PLEASE CHECK now"))))


class StatsTests(unittest.TestCase):
    def test_wilson_known_value(self):
        lo, hi = qa.wilson(8, 10)
        self.assertAlmostEqual(lo, 0.490, places=3)
        self.assertAlmostEqual(hi, 0.943, places=3)

    def test_wilson_edges(self):
        self.assertIsNone(qa.wilson(0, 0))
        lo, hi = qa.wilson(30, 30)
        self.assertAlmostEqual(hi, 1.0)
        self.assertAlmostEqual(lo, 0.886, places=3)
        lo, hi = qa.wilson(0, 30)
        self.assertAlmostEqual(lo, 0.0)

    def test_kappa_values(self):
        self.assertAlmostEqual(qa.cohen_kappa([1, 1, 0, 0], [1, 0, 0, 0]), 0.5)
        self.assertAlmostEqual(qa.cohen_kappa([1, 0, 1, 0], [1, 0, 1, 0]), 1.0)
        self.assertAlmostEqual(qa.cohen_kappa([1, 1, 0, 0], [1, 0, 1, 0]), 0.0)
        self.assertAlmostEqual(qa.cohen_kappa([1, 1, 0, 0], [0, 0, 1, 1]), -1.0)
        self.assertAlmostEqual(qa.cohen_kappa([1, 1, 1, 1, 0], [1, 1, 1, 0, 0]), 0.24 / 0.44)

    def test_kappa_undefined_and_bad_input(self):
        self.assertIsNone(qa.cohen_kappa([1, 1, 1], [1, 1, 1]))
        with self.assertRaises(ValueError):
            qa.cohen_kappa([1], [1, 0])
        with self.assertRaises(ValueError):
            qa.cohen_kappa([], [])

    def test_human_verdict(self):
        labels = {"greeting": 1, "identification": 1, "correct_info": 1, "tone": 1, "resolution": 1,
                  "closing": 1, "critical_fail": 0}
        self.assertEqual(qa.human_verdict(labels), "pass")
        self.assertEqual(qa.human_verdict(dict(labels, critical_fail=1)), "fail")
        self.assertEqual(qa.human_verdict(dict(labels, correct_info=0, resolution=0)), "fail")

    def test_calibrate_small_set(self):
        good = dict(GOOD, id="g", labels=dict(greeting=1, identification=1, correct_info=1, tone=1,
                                              resolution=1, closing=1, critical_fail=0))
        wrong = dict(GOOD, id="w", labels=dict(greeting=1, identification=1, correct_info=1, tone=1,
                                               resolution=1, closing=0, critical_fail=0))
        rep = qa.calibrate([good, wrong])
        self.assertEqual(rep["criteria"]["closing"]["agree"], 1)
        self.assertEqual(rep["criteria"]["greeting"]["agree"], 2)
        self.assertEqual(rep["disagreements"], [{"id": "w", "criterion": "closing", "auto": 1, "human": 0}])
        self.assertEqual(rep["verdict"]["agree"], 2)


class LabelFileTests(unittest.TestCase):
    def load(self, name):
        with open(os.path.join(DATA, "qa_labels_%s.json" % name)) as fh:
            return json.load(fh)

    def test_label_sets_are_complete(self):
        for name in ("a", "b", "c"):
            d = self.load(name)
            self.assertGreaterEqual(len(d["items"]), 30)
            ids = [i["id"] for i in d["items"]]
            self.assertEqual(len(ids), len(set(ids)))
            for it in d["items"]:
                self.assertEqual(set(it["labels"]), set(qa.CRITERIA))
                self.assertIn(it["topic"], qa.KB)

    def test_label_sets_are_disjoint(self):
        ids = [{i["id"] for i in self.load(n)["items"]} for n in "abc"]
        self.assertFalse(ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2])

    def _agent_turns(self, name):
        return {t for it in self.load(name)["items"] for s, t in it["turns"] if s == "agent"}

    def test_set_c_wording_is_independent_of_a_and_b(self):
        c = self._agent_turns("c")
        self.assertEqual(c & self._agent_turns("a"), set())
        self.assertEqual(c & self._agent_turns("b"), set())

    def test_set_b_overlap_with_a_is_a_known_flaw(self):
        """Set B is frozen and 14 of its agent turns repeat set A turns verbatim (review round 2).
        This pins the fact so the README claim 'B is not unseen' stays true to the file."""
        b_turns = [t for it in self.load("b")["items"] for s, t in it["turns"] if s == "agent"]
        a_turns = self._agent_turns("a")
        self.assertEqual(sum(1 for t in b_turns if t in a_turns), 14)
        self.assertEqual(len(b_turns), 90)

    def test_set_a_agreement_is_pinned_set_b_and_c_only_have_floors(self):
        """A is the tuning set, so its counts are pinned. B was re-measured after the round 2 fix pass
        and C is never to be tuned against: both only carry a loose regression floor, on purpose."""
        rep = qa.calibrate(self.load("a")["items"])
        self.assertEqual({k: v["agree"] for k, v in rep["criteria"].items()},
                         {"greeting": 30, "identification": 29, "correct_info": 30, "tone": 30,
                          "resolution": 26, "closing": 30, "critical_fail": 30})
        self.assertEqual(rep["verdict"]["agree"], 29)
        for name in ("b", "c"):
            rep = qa.calibrate(self.load(name)["items"])
            for crit, r in rep["criteria"].items():
                self.assertGreaterEqual(r["agreement"], 0.7, (name, crit))
            self.assertGreaterEqual(rep["verdict"]["agreement"], 0.8, name)
            self.assertEqual(set(rep["critical"]), {"tp", "fp", "fn", "precision", "recall"})

    def test_scorer_still_misses_what_the_readme_says_it_misses(self):
        """Honest failure cases, not to be patched against the held-out sets."""
        wrong = {(d["id"], d["criterion"]) for d in qa.calibrate(self.load("b")["items"])["disagreements"]}
        self.assertIn(("B16", "critical_fail"), wrong)
        self.assertIn(("B11", "greeting"), wrong)
        wrong_c = {(d["id"], d["criterion"]) for d in qa.calibrate(self.load("c")["items"])["disagreements"]}
        self.assertIn(("C05", "critical_fail"), wrong_c)


class RoundTwoCriticalTests(unittest.TestCase):
    def crit(self, *turns):
        return qa.check_critical(item("two_factor", *[("a", t) for t in turns]))

    def test_instruction_about_a_page_is_not_a_secret_request(self):
        self.assertFalse(self.crit("Please enter your password on the sign-in page afterwards."))
        self.assertFalse(self.crit("Type your password on the login screen, then your code."))
        r = qa.score_transcript(item("password_reset",
            ("a", "Hello, thanks for contacting Northwind support."), ("c", "x"),
            ("a", "I understand. I've sent a reset link, valid for 15 minutes. Please enter your password on the sign-in page afterwards."),
            ("c", "y"), ("a", "Anything else? Have a great day.")))
        self.assertEqual((r["critical_fail"], r["total"]), (0, 100))

    def test_unrelated_negation_does_not_hide_a_real_request(self):
        self.assertTrue(self.crit("I never received your request, please send me your password."))
        self.assertTrue(self.crit("We don't see the order. Please share with us your password."))

    def test_negation_in_the_same_clause_still_excuses(self):
        self.assertFalse(self.crit("Please do not send me your password."))
        self.assertFalse(self.crit("Never tell me your card number, we do not need it."))

    def test_requests_aimed_at_the_agent_are_critical(self):
        for text in ("What is your password?", "Please type your PIN here.", "Reply with your security code.",
                     "Send me your CVV."):
            self.assertTrue(self.crit(text), text)

    def test_abuse_needs_a_target_for_mild_words(self):
        self.assertFalse(self.crit("That is a dumb limitation on our side."))
        self.assertTrue(self.crit("You are being stupid about this."))
        self.assertTrue(self.crit("Shut up."))

    def test_acronyms_are_not_shouting_but_real_shouting_is(self):
        self.assertTrue(qa.check_tone(item("api_limit", ("a", "The API returned HTTP 429, see the FAQ and the PDF, thanks."))))
        self.assertFalse(qa.check_tone(item("api_limit", ("a", "PLEASE CHECK DOCS AGAIN"))))

    def test_negated_courtesy_is_not_courtesy(self):
        self.assertFalse(qa.check_tone(item("invoice", ("a", "I don't understand why you keep asking."))))
        self.assertTrue(qa.check_tone(item("invoice", ("a", "I understand, thanks for waiting."))))

    def test_calibrate_reports_critical_fail_precision_and_recall(self):
        good = dict(GOOD, id="g", labels=dict(greeting=1, identification=1, correct_info=1, tone=1,
                                              resolution=1, closing=1, critical_fail=0))
        bad = dict(GOOD, id="b", turns=GOOD["turns"] + [["agent", "Send me your password."]],
                   labels=dict(greeting=1, identification=1, correct_info=1, tone=1, resolution=1, closing=1,
                               critical_fail=1))
        miss = dict(GOOD, id="m", labels=dict(greeting=1, identification=1, correct_info=1, tone=1,
                                              resolution=1, closing=1, critical_fail=1))
        bad2 = dict(bad, id="b2")
        false_alarm = dict(bad, id="f", labels=dict(bad["labels"], critical_fail=0))
        rep = qa.calibrate([good, bad, bad2, miss, false_alarm])
        self.assertEqual((rep["critical"]["tp"], rep["critical"]["fp"], rep["critical"]["fn"]), (2, 1, 1))
        self.assertAlmostEqual(rep["critical"]["recall"], 2 / 3)
        self.assertAlmostEqual(rep["critical"]["precision"], 2 / 3)


if __name__ == "__main__":
    unittest.main()

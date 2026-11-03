"""Pending cases that turn out to belong together must be merged (found by the real-data run).

Before the fix, two replies that arrived before their shared parent each opened a case, and the
parent joined only one of them. These tests pin the merge and the safety rules around it."""
import itertools
import unittest

from helpers import D, mail
from ticketflow import TicketFlow


def one_case_with(test, f, ids):
    test.assertEqual(len(f.cases), 1, sorted(f.cases))
    case = next(iter(f.cases.values()))
    test.assertEqual(sorted(m["message_id"] for m in case["messages"]), sorted(ids))
    return case


class SharedMissingParent(unittest.TestCase):
    def test_two_orphans_then_their_parent_end_in_one_case(self):
        f = TicketFlow()
        a = f.ingest(mail("<c1@x>", in_reply_to="<p@x>", references=["<p@x>"], at=D(2026, 3, 2, 10)))
        b = f.ingest(mail("<c2@x>", in_reply_to="<p@x>", references=["<p@x>"], at=D(2026, 3, 2, 11)))
        self.assertNotEqual(a["case_id"], b["case_id"])  # nothing connects them yet
        p = f.ingest(mail("<p@x>", at=D(2026, 3, 2, 9)))
        case = one_case_with(self, f, ["c1@x", "c2@x", "p@x"])
        self.assertEqual(p["case_id"], case["id"])
        self.assertEqual(case["created_at"], D(2026, 3, 2, 9))
        self.assertEqual([m["message_id"] for m in case["messages"]], ["p@x", "c1@x", "c2@x"])

    def test_surviving_case_is_the_oldest_id_and_the_other_id_still_resolves(self):
        f = TicketFlow()
        a = f.ingest(mail("<c1@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 10)))["case_id"]
        b = f.ingest(mail("<c2@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 11)))["case_id"]
        f.ingest(mail("<p@x>", at=D(2026, 3, 2, 9)))
        self.assertEqual(list(f.cases), [a])
        self.assertTrue(f.agent_reply(b, D(2026, 3, 2, 12)))  # old id of the merged case still works
        self.assertEqual(f.cases[a]["first_response_at"], D(2026, 3, 2, 12))
        self.assertIsNotNone(f.sla(b, D(2026, 3, 2, 13)))

    def test_three_orphans_one_parent(self):
        f = TicketFlow()
        for i in range(3):
            f.ingest(mail("<c%d@x>" % i, in_reply_to="<p@x>", at=D(2026, 3, 2, 10 + i)))
        f.ingest(mail("<p@x>", at=D(2026, 3, 2, 9)))
        one_case_with(self, f, ["c0@x", "c1@x", "c2@x", "p@x"])

    def test_orphans_waiting_for_different_parents_stay_apart(self):
        f = TicketFlow()
        f.ingest(mail("<c1@x>", in_reply_to="<p1@x>", at=D(2026, 3, 2, 10)))
        f.ingest(mail("<c2@x>", in_reply_to="<p2@x>", at=D(2026, 3, 2, 11)))
        f.ingest(mail("<p1@x>", at=D(2026, 3, 2, 9)))
        self.assertEqual(len(f.cases), 2)

    def test_orphans_sharing_a_parent_that_never_arrives_stay_apart(self):
        """Documented limit: only the arrival of the connecting message merges."""
        f = TicketFlow()
        f.ingest(mail("<c1@x>", in_reply_to="<gone@x>", at=D(2026, 3, 2, 10)))
        f.ingest(mail("<c2@x>", in_reply_to="<gone@x>", at=D(2026, 3, 2, 11)))
        self.assertEqual(len(f.cases), 2)


class CyclesAndChains(unittest.TestCase):
    def test_two_orphans_that_reference_each_other(self):
        f = TicketFlow()
        f.ingest(mail("<a@x>", in_reply_to="<b@x>", at=D(2026, 3, 2, 10)))
        f.ingest(mail("<b@x>", in_reply_to="<a@x>", at=D(2026, 3, 2, 11)))
        one_case_with(self, f, ["a@x", "b@x"])

    def test_chain_arriving_in_reverse(self):
        f = TicketFlow()
        refs = []
        mails = []
        prev = None
        for i in range(5):
            mid = "<m%d@x>" % i
            kw = {"in_reply_to": prev, "references": list(refs)} if prev else {}
            mails.append(mail(mid, at=D(2026, 3, 2, 9 + i), **kw))
            refs.append(mid)
            prev = mid
        for e in reversed(mails):
            f.ingest(e)
        one_case_with(self, f, ["m%d@x" % i for i in range(5)])

    def test_chain_with_only_in_reply_to_arriving_middle_out(self):
        f = TicketFlow()
        order = [2, 4, 0, 3, 1]
        for i in order:
            kw = {"in_reply_to": "<m%d@x>" % (i - 1)} if i else {}
            f.ingest(mail("<m%d@x>" % i, at=D(2026, 3, 2, 9 + i), **kw))
        one_case_with(self, f, ["m%d@x" % i for i in range(5)])

    def test_every_delivery_order_of_a_small_tree_gives_one_case(self):
        #        r
        #      /   \
        #     a     b
        #    / \     \
        #   c   d     e
        parents = {"r": None, "a": "r", "b": "r", "c": "a", "d": "a", "e": "b"}

        def chain(k):
            out = []
            while parents[k]:
                k = parents[k]
                out.append("<%s@x>" % k)
            return list(reversed(out))
        names = list(parents)
        for n, order in enumerate(itertools.permutations(names)):
            f = TicketFlow()
            for k in order:
                kw = {"in_reply_to": "<%s@x>" % parents[k], "references": chain(k)} if parents[k] else {}
                f.ingest(mail("<%s@x>" % k, at=D(2026, 3, 2, 9 + names.index(k)), **kw))
            self.assertEqual(len(f.cases), 1, order)
            self.assertEqual(len(next(iter(f.cases.values()))["messages"]), 6, order)


class ParentWithItsOwnConnection(unittest.TestCase):
    def test_parent_that_replies_to_an_indexed_case_joins_both(self):
        f = TicketFlow()
        root = f.ingest(mail("<root@x>", at=D(2026, 3, 2, 9)))["case_id"]
        o = f.ingest(mail("<kid@x>", in_reply_to="<mid@x>", at=D(2026, 3, 2, 12)))["case_id"]
        self.assertNotEqual(root, o)
        f.ingest(mail("<mid@x>", in_reply_to="<root@x>", at=D(2026, 3, 2, 10)))
        one_case_with(self, f, ["root@x", "mid@x", "kid@x"])

    def test_message_that_bridges_two_pending_parents(self):
        f = TicketFlow()
        f.ingest(mail("<k1@x>", in_reply_to="<p1@x>", at=D(2026, 3, 2, 10)))
        f.ingest(mail("<k2@x>", in_reply_to="<p2@x>", at=D(2026, 3, 2, 11)))
        f.ingest(mail("<p1@x>", in_reply_to="<p2@x>", at=D(2026, 3, 2, 9)))  # p1 points to p2, both waited for
        self.assertEqual(len(f.cases), 2)  # k1+p1 and k2 both wait for p2
        f.ingest(mail("<p2@x>", at=D(2026, 3, 2, 8)))
        one_case_with(self, f, ["k1@x", "k2@x", "p1@x", "p2@x"])


class SafetyAfterMerge(unittest.TestCase):
    def test_redelivery_of_the_merging_message_is_a_duplicate(self):
        f = TicketFlow()
        f.ingest(mail("<c1@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 10)))
        f.ingest(mail("<c2@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 11)))
        f.ingest(mail("<p@x>", at=D(2026, 3, 2, 9)))
        r = f.ingest(mail("<p@x>", at=D(2026, 3, 2, 9)))
        self.assertEqual(r["action"], "duplicate")
        self.assertEqual(r["case_id"], next(iter(f.cases)))
        r2 = f.ingest(mail("<c2@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 11)))  # c2 was in the absorbed case
        self.assertEqual((r2["action"], r2["case_id"]), ("duplicate", next(iter(f.cases))))
        case = one_case_with(self, f, ["c1@x", "c2@x", "p@x"])
        self.assertEqual(len(case["messages"]), 3)

    def test_forged_subject_token_still_does_not_merge_or_attach(self):
        f = TicketFlow()
        f.ingest(mail("<c1@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 10)))
        f.ingest(mail("<c2@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 11)))
        f.ingest(mail("<p@x>", at=D(2026, 3, 2, 9)))
        victim = next(iter(f.cases))
        r = f.ingest(mail("<evil@x>", subject="Re: [%s] hello" % victim, at=D(2026, 3, 2, 12),
                          **{"from": "mallory@other.example"}))
        self.assertEqual(r["action"], "created")
        self.assertEqual(len(f.cases), 2)
        self.assertNotIn("evil@x", [m["message_id"] for m in f.cases[victim]["messages"]])

    def test_token_of_a_merged_case_resolves_for_its_participants(self):
        f = TicketFlow()
        a = f.ingest(mail("<c1@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 10)))["case_id"]
        b = f.ingest(mail("<c2@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 11)))["case_id"]
        f.ingest(mail("<p@x>", at=D(2026, 3, 2, 9)))
        r = f.ingest(mail("<n@x>", subject="Re: [%s] more" % b, at=D(2026, 3, 2, 12)))
        self.assertEqual(r["case_id"], a)
        self.assertEqual(len(f.cases), 1)

    def test_agent_work_on_a_loser_case_survives_the_merge(self):
        f = TicketFlow()
        a = f.ingest(mail("<c1@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 10)))["case_id"]
        b = f.ingest(mail("<c2@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 11)))["case_id"]
        f.agent_reply(b, D(2026, 3, 2, 11, 30), message_id="<out@x>")
        f.set_status(b, "waiting_on_customer", D(2026, 3, 2, 11, 40))
        f.ingest(mail("<p@x>", at=D(2026, 3, 2, 9)))
        case = f.cases[a]
        self.assertEqual(case["first_response_at"], D(2026, 3, 2, 11, 30))
        self.assertEqual(case["status"], "waiting_on_customer")
        r = f.ingest(mail("<rr@x>", in_reply_to="<out@x>", at=D(2026, 3, 2, 12)))  # reply to the agent's mail
        self.assertEqual(r["case_id"], a)
        self.assertEqual(case["status"], "open")

    def test_alert_on_a_merged_case_is_cleared(self):
        f = TicketFlow()
        b = f.ingest(mail("<c2@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 9), priority="urgent"))["case_id"]
        f.ingest(mail("<c1@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 9, 5)))
        f.tick(D(2026, 3, 2, 16))
        self.assertTrue([a for a in f.alerts if a["case_id"] == b and a["active"]])
        f.ingest(mail("<p@x>", at=D(2026, 3, 2, 8)))
        self.assertEqual(len(f.cases), 1)
        survivor = next(iter(f.cases))
        for a in f.alerts:
            if a["case_id"] != survivor:
                self.assertFalse(a["active"], a)

    def test_no_wrong_attachment_between_unrelated_threads(self):
        f = TicketFlow()
        f.ingest(mail("<x1@x>", in_reply_to="<px@x>", at=D(2026, 3, 2, 10)))
        f.ingest(mail("<y1@x>", in_reply_to="<py@x>", at=D(2026, 3, 2, 10)))
        f.ingest(mail("<px@x>", at=D(2026, 3, 2, 9)))
        f.ingest(mail("<py@x>", at=D(2026, 3, 2, 9)))
        sets = sorted(sorted(m["message_id"] for m in c["messages"]) for c in f.cases.values())
        self.assertEqual(sets, [["px@x", "x1@x"], ["py@x", "y1@x"]])


class MergeBookkeeping(unittest.TestCase):
    def test_creation_time_is_the_earliest_of_the_merged_cases(self):
        f = TicketFlow()
        a = f.ingest(mail("<c1@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 11)))["case_id"]  # lower id, later date
        f.ingest(mail("<c2@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 10)))
        f.ingest(mail("<p@x>", at=D(2026, 3, 2, 12)))  # parent stamped after both children (clock skew)
        self.assertEqual(f.cases[a]["created_at"], D(2026, 3, 2, 10))
        self.assertEqual(f.cases[a]["status_events"][0], (D(2026, 3, 2, 10), "open"))

    def test_customer_mail_of_an_absorbed_case_reopens_after_an_earlier_resolve(self):
        f = TicketFlow()
        a = f.ingest(mail("<c1@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 10)))["case_id"]
        f.ingest(mail("<c2@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 11)))
        f.set_status(a, "resolved", D(2026, 3, 2, 10, 30))
        self.assertEqual(f.cases[a]["status"], "resolved")
        f.ingest(mail("<p@x>", at=D(2026, 3, 2, 9)))
        self.assertEqual(f.cases[a]["status"], "open")  # c2 wrote at 11:00, after the resolve

    def test_participants_of_both_cases_are_kept(self):
        f = TicketFlow()
        a = f.ingest(mail("<c1@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 10)))["case_id"]
        b = f.ingest(mail("<c2@x>", in_reply_to="<p@x>", at=D(2026, 3, 2, 11), **{"from": "sam@other.example"}))["case_id"]
        f.ingest(mail("<p@x>", at=D(2026, 3, 2, 9)))
        self.assertEqual(sorted(f.cases[a]["participants"]), ["alex@cust.example", "sam@other.example"])
        r = f.ingest(mail("<n@x>", subject="[%s] again" % b, at=D(2026, 3, 2, 12), **{"from": "sam@other.example"}))
        self.assertEqual(r["action"], "threaded")
        self.assertEqual(r["case_id"], a)

    def test_ids_absorbed_in_two_steps_still_resolve(self):
        f = TicketFlow()
        c1 = f.ingest(mail("<o1@x>", in_reply_to="<p1@x>", at=D(2026, 3, 2, 10)))["case_id"]
        c2 = f.ingest(mail("<o2@x>", in_reply_to="<p2@x>", references=["<p1@x>", "<p2@x>"], at=D(2026, 3, 2, 10)))["case_id"]
        c3 = f.ingest(mail("<o3@x>", in_reply_to="<p2@x>", at=D(2026, 3, 2, 10)))["case_id"]
        self.assertEqual(len({c1, c2, c3}), 3)
        f.ingest(mail("<p2@x>", at=D(2026, 3, 2, 9)))  # c3 goes into c2
        self.assertEqual(sorted(f.cases), [c1, c2])
        f.ingest(mail("<p1@x>", at=D(2026, 3, 2, 8)))  # c2 goes into c1
        self.assertEqual(list(f.cases), [c1])
        self.assertTrue(f.agent_reply(c3, D(2026, 3, 2, 13)))
        self.assertEqual(sorted(f.cases[c1]["merged_from"]), [c2, c3])
        self.assertEqual(len(f.cases[c1]["messages"]), 5)


if __name__ == "__main__":
    unittest.main()

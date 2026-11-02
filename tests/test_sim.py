import unittest

import helpers  # noqa: F401
from ticketflow import NaiveTicketFlow, TicketFlow, sim

SEEDS = range(1, 6)


class SimulationTests(unittest.TestCase):
    def test_generation_is_deterministic(self):
        a, ta = sim.generate(3)
        b, tb = sim.generate(3)
        self.assertEqual([(i["at"], i["kind"]) for i in a], [(i["at"], i["kind"]) for i in b])
        self.assertEqual(ta, tb)

    def test_different_seeds_differ(self):
        a, _ = sim.generate(1)
        b, _ = sim.generate(2)
        self.assertNotEqual([i["at"] for i in a], [i["at"] for i in b])

    def test_fixed_has_zero_violations_on_every_seed(self):
        for seed in SEEDS:
            v = sim.run(TicketFlow, seed)
            self.assertEqual({k: v[k] for k in sim.CATEGORIES}, dict.fromkeys(sim.CATEGORIES, 0), "seed %d" % seed)

    def test_fixed_run_is_not_vacuous(self):
        """The audit only means something if the run contained the situations it checks."""
        items, truth = sim.generate(1)
        kinds = {i["kind"] for i in items}
        self.assertEqual(kinds, {"email", "agent_reply", "status", "tick"})
        self.assertGreater(len(truth["waits"]), 0)
        self.assertGreater(len(truth["replies"]), 0)
        self.assertGreater(len(truth["noise"]), 0)
        emails = [i for i in items if i["kind"] == "email"]
        mids = [i["mid"] for i in emails]
        self.assertGreater(len(mids), len(set(mids)))  # duplicates were resubmitted
        flow = TicketFlow()
        for it in items:
            if it["kind"] == "email":
                flow.ingest(it["email"], it["at"])
            elif it["kind"] == "tick":
                flow.tick(it["at"])
        self.assertGreater(len(flow.alerts), 5)

    def test_naive_violates_each_core_category(self):
        total = dict.fromkeys(sim.CATEGORIES, 0)
        for seed in SEEDS:
            v = sim.run(NaiveTicketFlow, seed)
            for k in sim.CATEGORIES:
                total[k] += v[k]
        for k in sim.CATEGORIES:
            self.assertGreater(total[k], 0, k)

    def test_naive_duplicate_and_unassigned_violations_on_every_seed(self):
        for seed in SEEDS:
            v = sim.run(NaiveTicketFlow, seed)
            self.assertGreater(v["duplicate_cases"], 0, seed)
            self.assertGreater(v["unassigned_cases"], 0, seed)
            self.assertGreater(v["alert_twice"], 0, seed)

    def test_audit_detects_a_planted_violation(self):
        """Must-fire control: break the fixed flow on purpose and confirm the audit notices."""
        class NoPause(TicketFlow):
            def set_status(self, case_id, status, at, seq=None):
                if status == "waiting_on_customer":
                    return False
                return super().set_status(case_id, status, at, seq)
        v = sim.run(NoPause, 1)
        self.assertGreater(v["clock_running_while_paused"], 0)

    def test_audit_fails_a_flow_with_cooldown_zero(self):
        """Must-fire control: renotify on every tick. The old audit passed this (review round 2)."""
        v = sim.run(lambda: TicketFlow(cooldown_minutes=0), 1)
        self.assertGreater(v["alert_twice"], 0)

    def test_audit_fails_a_flow_with_no_alerts_at_all(self):
        class Silent(TicketFlow):
            def tick(self, now):
                return []
        v = sim.run(Silent, 1)
        self.assertGreater(v["alert_missing"], 0)
        self.assertEqual(v["alert_spurious"], 0)

    def test_audit_fails_a_frozen_clock(self):
        class Frozen(TicketFlow):
            def sla(self, case_id, now):
                out = super().sla(case_id, now)
                out["resolution"] = dict(out["resolution"], elapsed=0.0, state="ok")
                out["first_response"] = dict(out["first_response"], elapsed=0.0, state="ok")
                return out
        v = sim.run(Frozen, 1)
        self.assertGreater(v["sla_elapsed_wrong"], 0)
        self.assertGreater(v["alert_missing"], 0)

    def test_audit_fails_a_frozen_resolution_clock_on_its_own(self):
        """Control for the resolution half of the elapsed check (first response stays correct)."""
        class FrozenResolution(TicketFlow):
            def sla(self, case_id, now):
                out = super().sla(case_id, now)
                out["resolution"] = dict(out["resolution"], elapsed=0.0)
                return out
        v = sim.run(FrozenResolution, 1)
        self.assertGreater(v["sla_elapsed_wrong"], 0)

    def test_audit_fails_a_frozen_first_response_clock_on_its_own(self):
        class FrozenFirstResponse(TicketFlow):
            def sla(self, case_id, now):
                out = super().sla(case_id, now)
                out["first_response"] = dict(out["first_response"], elapsed=0.0)
                return out
        v = sim.run(FrozenFirstResponse, 1)
        self.assertGreater(v["sla_elapsed_wrong"], 0)

    def test_audit_fails_a_flow_that_alerts_on_the_wrong_calendar(self):
        class WallClock(TicketFlow):
            def sla(self, case_id, now):
                case = self.cases[case_id]
                out = super().sla(case_id, now)
                wall = (now - case["created_at"]).total_seconds() / 60.0
                out["first_response"] = dict(out["first_response"], elapsed=wall)
                return out
        v = sim.run(WallClock, 1)
        self.assertGreater(v["sla_elapsed_wrong"], 0)

    def test_audit_fails_a_flow_that_ignores_outbound_message_ids(self):
        class NoOutboundIndex(TicketFlow):
            def agent_reply(self, case_id, at, message_id=None):
                return super().agent_reply(case_id, at, message_id="unrelated-%s@x" % case_id)
        v = sim.run(NoOutboundIndex, 1)
        self.assertGreater(v["reply_not_threaded"], 0)

    def test_audit_fails_a_flow_with_a_one_hour_cooldown(self):
        v = sim.run(lambda: TicketFlow(cooldown_minutes=60), 1)
        self.assertGreater(v["alert_twice"], 0)

    def test_oracle_matches_itself_on_a_hand_computed_case(self):
        """Hand check of the independent oracle: Friday 16:30 UTC to Monday 09:30 UTC is 60 business minutes."""
        from datetime import datetime, timezone
        a = datetime(2026, 3, 6, 16, 30, tzinfo=timezone.utc)
        b = datetime(2026, 3, 9, 9, 30, tzinfo=timezone.utc)
        self.assertEqual(sim._bm("UTC", a, b), 60)
        ny_a = datetime(2026, 3, 6, 21, 30, tzinfo=timezone.utc)  # Fri 16:30 EST, DST starts Sunday
        ny_b = datetime(2026, 3, 9, 13, 30, tzinfo=timezone.utc)  # Mon 09:30 EDT
        self.assertEqual(sim._bm("America/New_York", ny_a, ny_b), 60)

    def test_replies_to_the_agent_mail_and_to_waiting_cases_are_in_the_run(self):
        for seed in SEEDS:
            items, truth = sim.generate(seed)
            via_out = [i for i in items if i["kind"] == "email" and "northwind.example" in (i["email"].get("in_reply_to") or "")]
            in_wait = [1 for rid, mid, r in truth["replies"] for m, ws, we in truth["waits"] if m == mid and ws <= r <= we]
            self.assertGreater(len(via_out), 0, seed)
            self.assertGreater(len(in_wait), 0, seed)


if __name__ == "__main__":
    unittest.main()

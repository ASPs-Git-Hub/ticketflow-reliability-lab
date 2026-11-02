"""Run the seeded simulation (seeds 1..5) against the fixed and the naive implementation."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from ticketflow import NaiveTicketFlow, TicketFlow, sim  # noqa: E402

for name, factory in (("fixed", TicketFlow), ("naive", NaiveTicketFlow)):
    total = dict.fromkeys(sim.CATEGORIES, 0)
    print("== %s ==" % name)
    for seed in range(1, 6):
        v = sim.run(factory, seed)
        print("seed %d cases=%d %s" % (seed, v["_cases"], {k: v[k] for k in sim.CATEGORIES}))
        for k in sim.CATEGORIES:
            total[k] += v[k]
    print("TOTAL", total)

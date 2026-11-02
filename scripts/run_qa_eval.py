"""Evaluate the rule-based QA scorer against labels written by the lab author model.
Usage: run_qa_eval.py [a|b|c|both|all]    (both = a and b, all = a, b and c)

Set A is the tuning set: qa.py rules were adjusted after looking at its disagreements, so A
numbers are optimistic. Set B was written and committed before qa.py existed, but the fix pass of
review round 2 changed qa.py after B was scored, and 14 of B's 90 agent turns repeat set A turns:
B is a regression set, not a clean held-out set. Set C was written after that fix pass with wording
independent of A and B and is the least contaminated of the three. Same author model labelled all
three, so none of them is independent human labelling. "verdict" is a DERIVED verdict (the scorer's
weights applied to the human per-criterion labels), not a separate human judgement."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from ticketflow import qa  # noqa: E402


def fmt(r):
    lo, hi = r["wilson95"]
    kappa = "n/a" if r["kappa"] is None else "%.2f" % r["kappa"]
    return "%2d/%d  agreement %.3f  Wilson95 [%.3f, %.3f]  kappa %s" % (
        r["agree"], r["n"], r["agreement"], lo, hi, kappa)


def run(name):
    data = json.load(open(os.path.join(ROOT, "data", "qa_labels_%s.json" % name)))
    rep = qa.calibrate(data["items"])
    status = {"a": "TUNED (optimistic)", "b": "REGRESSION SET (frozen labels, 14 turns repeat A, re-scored after the round 2 fix pass)",
              "c": "LEAST CONTAMINATED (written after the fix pass, not tuned against)"}[name]
    print("=== set %s: %d transcripts, %s ===" % (name.upper(), rep["n"], status))
    for crit, r in rep["criteria"].items():
        print("%-15s %s" % (crit, fmt(r)))
    print("%-15s %s" % ("derived verdict", fmt(rep["verdict"])))
    c = rep["critical"]
    print("critical-fail detection: tp %d fp %d fn %d precision %s recall %s" % (
        c["tp"], c["fp"], c["fn"], "n/a" if c["precision"] is None else "%.2f" % c["precision"],
        "n/a" if c["recall"] is None else "%.2f" % c["recall"]))
    print("disagreements (%d):" % len(rep["disagreements"]))
    for d in rep["disagreements"]:
        print("  %s %-14s auto=%d human=%d" % (d["id"], d["criterion"], d["auto"], d["human"]))
    return rep


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    for s in {"both": ["a", "b"], "all": ["a", "b", "c"]}.get(which, [which]):
        run(s)
        print()

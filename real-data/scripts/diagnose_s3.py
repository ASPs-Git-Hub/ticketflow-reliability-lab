"""S3 (headers damaged): how far is the lab from the best possible answer, and what is left? (v2, my own analysis)

Usage: python3 -I diagnose_s3.py RECORDS.jsonl
For seeds 1 to 5 of S3 it prints counts only:
  ceiling      truth threads of size 2+ that equal one header-oracle component (the most any program can
               get right from the headers that remain)
  lab exact    truth threads of size 2+ the lab got exactly right
  lost to damage   truth threads of size 2+ that no header-only program could rebuild (ceiling gap)
  oracle gap   oracle components the lab did not reproduce, and the category of each
Categories for a component the lab did not reproduce:
  lab split it, bridged   its messages sit in more than one lab case, and at least one message of it names
                          (in its own headers) messages that ended in different lab cases. The lab does not
                          merge two cases that already exist because a message names both (a forged
                          References header could otherwise fuse two customers' cases).
  lab split it, other     split, and no message bridges the cases
  lab case bigger      its messages sit in one lab case that also holds other messages
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import threading_eval as te  # noqa: E402


def main():
    recs = te.load(sys.argv[1])
    ids = [r["id"] for r in recs]
    parent = te.truth_parent(recs)
    troot, tcomp = te.components_from_parent(parent)
    big = [c for c in tcomp.values() if len(c) >= 2]
    tot = Counter()
    for seed in te.SEEDS:
        byid, emails, first_pos, subjects, info = te.build(recs, "S3", seed)
        ocomps = te.oracle_components(list(byid.values()), ids)
        oset = {frozenset(o) for o in ocomps}
        ceiling = sum(1 for c in big if frozenset(c) in oset)
        cases, _ = te.run_lab(emails)
        sets = [set(c) for c in cases]
        where = {}
        for n, s in enumerate(sets):
            for m in s:
                where[m] = n
        exact = sum(1 for c in big if any(s == c for s in sets))
        cat = Counter()
        hdr = {i: ([byid[i]["irt"]] if byid[i]["irt"] else []) + list(byid[i]["refs"]) for i in ids}
        for o in ocomps:
            ns = {where[m] for m in o}
            if len(ns) == 1 and sets[next(iter(ns))] == o:
                continue
            if len(ns) > 1:
                bridged = any(len({where[x] for x in hdr[m] + [m] if x in where}) > 1 for m in o)
                cat["lab split it, bridged" if bridged else "lab split it, other"] += 1
            else:
                cat["lab case bigger"] += 1
        print("seed %d: dropped In-Reply-To %d, dropped both %d | ceiling %d of %d | lab exact %d | lost to damage %d | "
              "oracle components %d, lab reproduced %d, gap %s" % (
                  seed, info["dropped_irt"], info["dropped_both"], ceiling, len(big), exact, len(big) - ceiling,
                  len(ocomps), len(ocomps) - sum(cat.values()), dict(cat) or "none"))
        tot["ceiling"] += ceiling
        tot["exact"] += exact
    print("mean over seeds: ceiling %.1f, lab exact %.1f (of %d threads of size 2+)" % (
        tot["ceiling"] / len(te.SEEDS), tot["exact"] / len(te.SEEDS), len(big)))


if __name__ == "__main__":
    main()

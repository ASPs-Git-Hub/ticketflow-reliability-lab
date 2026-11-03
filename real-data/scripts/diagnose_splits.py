"""Why does TicketFlow split threads when messages arrive out of order? (S1, seed 1, my own analysis)

Usage: python3 -I diagnose_splits.py RECORDS.jsonl
Replays the S1 seed 1 delivery and, for every truth thread (size >= 2) that ends up in more than
one case, checks whether the cause is 'two or more cases were opened by messages that point at the
same parent id before that parent arrived' (the lab remembers one case per missing parent id).
Prints counts only."""
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import threading_eval as te  # noqa: E402
from ticketflow import TicketFlow  # noqa: E402


def main():
    recs = te.load(sys.argv[1])
    parent = te.truth_parent(recs)
    troot, tcomp = te.components_from_parent(parent)
    byid, emails, first_pos, subjects, info = te.build(recs, "S1", 1)
    tf = TicketFlow()
    founder_missing = {}
    for i, e in emails:
        before = set(tf.ingestor._index)
        r = tf.ingest(e)
        if r["action"] == "created":
            hdr = [x.strip("<>").lower() for x in ([e.get("in_reply_to")] if e.get("in_reply_to") else []) + e.get("references", [])]
            founder_missing[r["case_id"]] = {x for x in hdr if x not in before}
    mid2case = {}
    for cid, c in tf.cases.items():
        for m in c["messages"]:
            mid2case[te.strip_id(m["message_id"])] = cid
    by_missing = defaultdict(set)
    for cid, miss in founder_missing.items():
        for x in miss:
            by_missing[x].add(cid)
    shared = {x: cs for x, cs in by_missing.items() if len(cs) >= 2}
    cats = Counter()
    for root, comp in tcomp.items():
        if len(comp) < 2:
            continue
        cs = {mid2case[m] for m in comp}
        if len(cs) < 2:
            cats["thread intact"] += 1
            continue
        explained = any(len(cs & c2) >= 2 for c2 in shared.values())
        cats["split, two cases opened by messages pointing at the same not-yet-arrived parent" if explained
             else "split, other cause"] += 1
    for k, v in sorted(cats.items()):
        print("%5d  %s" % (v, k))
    print("missing parent ids referenced by 2+ opening messages:", len(shared))
    print("cases total", len(tf.cases), "ignored", len(tf.ingestor.ignored), "flagged", len(tf.ingestor.flagged))


if __name__ == "__main__":
    main()

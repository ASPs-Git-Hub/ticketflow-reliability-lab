"""Score threading on anonymised real list records under the handicaps in PROTOCOL.md.

Usage: python3 -I threading_eval.py RECORDS.jsonl [--handcheck]
Imports the lab from ../../src. Prints raw results; redirect stdout to evidence/."""
import json
import os
import random
import re
import statistics
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))
from ticketflow import NaiveTicketFlow, TicketFlow  # noqa: E402

DOM = "@pl.invalid"
SEEDS = (1, 2, 3, 4, 5)


def load(path):
    with open(path) as fh:
        return [json.loads(x) for x in fh if x.strip()]


def truth_parent(recs):
    ids = {r["id"] for r in recs}
    parent = {}
    for r in recs:
        p = None
        if r["irt"] in ids:
            p = r["irt"]
        else:
            for x in reversed(r["refs"]):
                if x in ids:
                    p = x
                    break
        parent[r["id"]] = p
    return parent


def components_from_parent(parent):
    root = {}
    for i in parent:
        j, seen = i, set()
        while parent[j] is not None and j not in seen:
            seen.add(j)
            j = parent[j]
        root[i] = j
    comp = defaultdict(set)
    for i, r in root.items():
        comp[r].add(i)
    return root, comp


def oracle_components(damaged, ids):
    uf = {i: i for i in ids}

    def find(x):
        while uf[x] != x:
            uf[x] = uf[uf[x]]
            x = uf[x]
        return x

    for r in damaged:
        for p in ([r["irt"]] if r["irt"] else []) + r["refs"]:
            if p in uf:
                uf[find(r["id"])] = find(p)
    comp = defaultdict(set)
    for i in ids:
        comp[find(i)].add(i)
    return list(comp.values())


def mangle_subject(rec, rng):
    """Subject text handed to the flows. skey stands for the normalised subject."""
    u = rng.random()
    if u < 0.30:
        return ""
    if u < 0.50:
        return rng.choice(["RE: ", "Fwd: ", "AW: ", ""]) + rec["skey"]
    return "[Python-Dev] " + ("Re: " if rec["irt"] else "") + rec["skey"]


def build(recs, scenario, seed):
    rng = random.Random(seed * 1000 + {"S0": 0, "S1": 1, "S2": 2, "S3": 3}[scenario])
    byid = {r["id"]: dict(r) for r in recs}
    info = {"redelivered": 0, "dropped_irt": 0, "dropped_both": 0}
    order = sorted(byid, key=lambda i: (byid[i]["date"], i))
    if scenario != "S0":
        rng.shuffle(order)
    if scenario == "S3":
        replies = sorted(i for i in byid if byid[i]["irt"])
        rng.shuffle(replies)
        k1, k2 = round(0.10 * len(replies)), round(0.05 * len(replies))
        for i in replies[:k1]:
            byid[i]["irt"] = None
            info["dropped_irt"] += 1
        for i in replies[k1:k1 + k2]:
            byid[i]["irt"] = None
            byid[i]["refs"] = []
            info["dropped_both"] += 1
    delivery = list(order)
    if scenario in ("S2", "S3"):
        pick = rng.sample(sorted(byid), round(0.10 * len(byid)))
        for i in pick:
            p = delivery.index(i)
            delivery.insert(rng.randint(p + 1, len(delivery)), i)
            info["redelivered"] += 1
    subj_rng = random.Random(seed * 7 + 99)
    subjects = {i: mangle_subject(byid[i], subj_rng) for i in sorted(byid)}
    emails = []
    for i in delivery:
        r = byid[i]
        e = {"message_id": "<%s%s>" % (i, DOM), "from": "u%s%s" % (r["frm"], "@example.invalid"),
             "subject": subjects[i], "body": "", "date": r["date"]}
        if r["irt"]:
            e["in_reply_to"] = "<%s%s>" % (r["irt"], DOM)
        if r["refs"]:
            e["references"] = ["<%s%s>" % (x, DOM) for x in r["refs"]]
        emails.append((i, e))
    first_pos = {}
    for n, (i, _) in enumerate(emails):
        first_pos.setdefault(i, n)
    return byid, emails, first_pos, subjects, info


def strip_id(mid):
    return mid.strip("<>").lower().replace(DOM, "")


def run_lab(emails):
    tf = TicketFlow()
    acts = Counter()
    for _, e in emails:
        acts[tf.ingest(e)["action"]] += 1
    cases = [[strip_id(m["message_id"]) for m in c["messages"]] for c in tf.cases.values()]
    return cases, acts


def run_naive(emails):
    nf = NaiveTicketFlow()
    for _, e in emails:
        nf.ingest(e)
    return [[strip_id(m["message_id"]) for m in c["messages"]] for c in nf.cases.values()], Counter()


def norm_subject(s):
    s = re.sub(r"\s+", " ", s or "").strip().lower()
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"^(re|fw|fwd|aw)\s*:\s*", "", s)
        s = re.sub(r"^\[[^\]]*\]\s*", "", s)
    return s.strip()


def run_subject(emails):
    groups, singles = {}, []
    for i, e in emails:
        k = norm_subject(e["subject"])
        if not k:
            singles.append([i])
        else:
            groups.setdefault(k, []).append(i)
    return list(groups.values()) + singles, Counter()


def score(cases, truth_root, truth_comp, ids, injected):
    sets = [set(c) for c in cases]
    in_cases = defaultdict(list)
    for n, c in enumerate(cases):
        for m in set(c):
            in_cases[m].append(n)
    occurrences = sum(len(c) for c in cases)
    out = {}

    def exact(components):
        ok = 0
        for comp in components:
            cs = {n for m in comp for n in in_cases.get(m, [])}
            if len(cs) == 1 and sets[next(iter(cs))] == comp:
                ok += 1
        return ok

    comps = list(truth_comp.values())
    big = [c for c in comps if len(c) >= 2]
    out["threads_all"] = len(comps)
    out["threads_ge2"] = len(big)
    out["exact_all"] = exact(comps)
    out["exact_ge2"] = exact(big)
    wrong, merged = 0, 0
    for s in sets:
        cnt = Counter(truth_root[m] for m in s)
        if len(cnt) > 1:
            merged += 1
            top = sorted(cnt.items(), key=lambda kv: (-kv[1], -len(truth_comp[kv[0]]), kv[0]))[0][0]
            wrong += sum(v for k, v in cnt.items() if k != top)
    out["wrong_thread_msgs"] = wrong
    out["merged_cases"] = merged
    split, extra = 0, 0
    for comp in big:
        cs = {n for m in comp for n in in_cases.get(m, [])}
        if len(cs) > 1:
            split += 1
            extra += len(cs) - 1
    out["split_threads_ge2"] = split
    out["extra_cases_from_splits"] = extra
    out["cases"] = len(cases)
    out["lost_msgs"] = sum(1 for i in ids if i not in in_cases)
    out["surplus_copies"] = occurrences - len(in_cases)
    out["injected_redeliveries"] = injected
    return out, sets, in_cases


def late_pairs(byid, parent, first_pos, in_cases, sets):
    total = rec = 0
    for c, p in parent.items():
        if p is None:
            continue
        r = byid[c]
        if not (r["irt"] == p or p in r["refs"]):
            continue
        if first_pos[c] < first_pos[p]:
            total += 1
            if set(in_cases.get(c, [])) & set(in_cases.get(p, [])):
                rec += 1
    return total, rec


def evaluate(recs, scenario, seed, handcheck=False):
    ids = [r["id"] for r in recs]
    parent = truth_parent(recs)
    troot, tcomp = components_from_parent(parent)
    byid, emails, first_pos, subjects, info = build(recs, scenario, seed)
    ocomps = oracle_components(list(byid.values()), ids)
    res = {}
    for name, fn in (("lab", run_lab), ("naive", run_naive), ("subject", run_subject)):
        cases, acts = fn(emails)
        sc, sets, in_cases = score(cases, troot, tcomp, ids, info["redelivered"])
        sc["late_pairs"], sc["late_reconciled"] = late_pairs(byid, parent, first_pos, in_cases, sets)
        cs = lambda comp: {n for m in comp for n in in_cases.get(m, [])}
        sc["oracle_components"] = len(ocomps)
        sc["oracle_match"] = sum(1 for o in ocomps if len(cs(o)) == 1 and sets[next(iter(cs(o)))] == o)
        sc["actions"] = dict(acts)
        res[name] = sc
        if handcheck and name == "lab":
            res["_handcheck"] = describe_mismatch(sets, ocomps, troot, tcomp, byid, parent, first_pos)
    res["_info"] = info
    return res


def describe_mismatch(sets, ocomps, troot, tcomp, byid, parent, first_pos):
    """Pattern counts only, no identifiers. Categories for lab cases that are not an oracle component."""
    oset = {frozenset(o) for o in ocomps}
    cat = Counter()
    for s in sets:
        if frozenset(s) in oset:
            continue
        roots = {troot[m] for m in s}
        if len(roots) > 1:
            cat["case spans more than one truth thread"] += 1
        else:
            cat["fragment of one truth thread"] += 1
    return dict(cat)


def main():
    path = sys.argv[1]
    hand = "--handcheck" in sys.argv
    recs = load(path)
    print("records:", len(recs), "file:", os.path.basename(path))
    parent = truth_parent(recs)
    troot, tcomp = components_from_parent(parent)
    print("truth threads:", len(tcomp), "size>=2:", sum(1 for c in tcomp.values() if len(c) >= 2),
          "messages with a parent in sample:", sum(1 for p in parent.values() if p))
    allres = {}
    for sc in ("S0", "S1", "S2", "S3"):
        for seed in ((1,) if sc == "S0" else SEEDS):
            r = evaluate(recs, sc, seed, handcheck=hand and seed == 1)
            allres[(sc, seed)] = r
            print("RAW", sc, "seed", seed, json.dumps(r, sort_keys=True))
    print()
    keys = ["exact_all", "exact_ge2", "wrong_thread_msgs", "merged_cases", "split_threads_ge2",
            "extra_cases_from_splits", "cases", "lost_msgs", "surplus_copies", "late_pairs",
            "late_reconciled", "oracle_match"]
    for sc in ("S0", "S1", "S2", "S3"):
        seeds = (1,) if sc == "S0" else SEEDS
        first = allres[(sc, 1)]
        print("== %s (threads=%d, threads size>=2=%d, oracle components=%d, injected redeliveries=%s) ==" % (
            sc, first["lab"]["threads_all"], first["lab"]["threads_ge2"], first["lab"]["oracle_components"],
            [allres[(sc, s)]["lab"]["injected_redeliveries"] for s in seeds]))
        print("%-26s %-22s %-22s %-22s" % ("metric (mean [min-max])", "TicketFlow", "NaiveTicketFlow", "SubjectBaseline"))
        for k in keys:
            row = []
            for sysn in ("lab", "naive", "subject"):
                v = [allres[(sc, s)][sysn][k] for s in seeds]
                row.append("%.1f [%d-%d]" % (statistics.mean(v), min(v), max(v)))
            print("%-26s %-22s %-22s %-22s" % (k, *row))
        print()


if __name__ == "__main__":
    main()

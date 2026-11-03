"""Parse downloaded pipermail text files into anonymised records, and write the frozen sample.

Usage: python3 -I prepare_threading.py RAW_DIR WORK_DIR OUT_SAMPLE

Reads *.txt in RAW_DIR (never executes anything). Writes WORK_DIR/full.jsonl (all messages,
salted-hash ids; stays in the scratch area) and OUT_SAMPLE (whole threads, size-capped, for commit).
The salt is generated once into WORK_DIR/salt.txt and is never committed.
Message bodies are never read into the records. Subjects are reduced to a salted hash of the
normalised subject."""
import email
import email.utils
import hashlib
import json
import os
import random
import re
import sys
from collections import Counter

SEP = re.compile(r"^From \S+ at \S+\s+\w{3} \w{3} +\d+ \d\d:\d\d:\d\d \d{4}$")
ID_TOKEN = re.compile(r"<([^<>\s]+)>")
CAP_BYTES = 190_000


def h(salt, text, n=12):
    return hashlib.sha256((salt + "|" + text).encode("utf-8", "replace")).hexdigest()[:n]


def norm_subject(s):
    s = re.sub(r"\s+", " ", s or "").strip().lower()
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"^(re|fw|fwd|aw)\s*:\s*", "", s)
        s = re.sub(r"^\[[^\]]*\]\s*", "", s)
    return s.strip()


def split_messages(text):
    chunk = []
    for line in text.split("\n"):
        if SEP.match(line) and (not chunk or chunk[-1] == ""):
            if chunk:
                yield "\n".join(chunk)
            chunk = [line]
        else:
            chunk.append(line)
    if chunk:
        yield "\n".join(chunk)


def parse_dir(raw_dir, salt):
    recs, stats = {}, Counter()
    for name in sorted(os.listdir(raw_dir)):
        if not name.endswith(".txt"):
            continue
        with open(os.path.join(raw_dir, name), "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for chunk in split_messages(text):
            stats["chunks"] += 1
            head = chunk.split("\n\n", 1)[0]
            head = head.split("\n", 1)[1] if head.startswith("From ") else head
            msg = email.message_from_string(head)
            mids = ID_TOKEN.findall(msg.get("Message-ID", "") or "")
            if not mids:
                stats["no_message_id"] += 1
                continue
            mid = mids[0].lower()
            try:
                dt = email.utils.parsedate_to_datetime(msg.get("Date", ""))
                if dt.tzinfo is None:
                    stats["naive_date"] += 1
                    continue
                iso = dt.astimezone(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
            except (TypeError, ValueError):
                stats["bad_date"] += 1
                continue
            if mid in recs:
                stats["duplicate_id_in_archive"] += 1
                continue
            irt_all = ID_TOKEN.findall(msg.get("In-Reply-To", "") or "")
            refs = [x.lower() for x in ID_TOKEN.findall(msg.get("References", "") or "")]
            frm = (msg.get("From", "") or "").split("(")[0].strip().lower()
            prec = (msg.get("Precedence", "") or "").strip().lower()
            if prec:
                stats["precedence_" + prec] += 1
            if msg.get("Auto-Submitted"):
                stats["auto_submitted_header"] += 1
            recs[mid] = {
                "id": h(salt, "id:" + mid), "irt": h(salt, "id:" + irt_all[0].lower()) if irt_all else None,
                "refs": [h(salt, "id:" + r) for r in refs], "date": iso, "frm": h(salt, "from:" + frm, 8),
                "skey": h(salt, "subj:" + norm_subject(msg.get("Subject", "")), 8),
                "_empty_subject": not norm_subject(msg.get("Subject", "")),
            }
            if len(irt_all) > 1:
                stats["irt_with_multiple_ids"] += 1
    stats["messages_kept"] = len(recs)
    return list(recs.values()), stats


def truth_components(recs):
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
    root = {}
    for i in ids:
        j, seen = i, set()
        while parent[j] is not None and j not in seen:
            seen.add(j)
            j = parent[j]
        root[i] = j
    return root


def main():
    raw_dir, work_dir, out_sample = sys.argv[1:4]
    os.makedirs(work_dir, exist_ok=True)
    salt_path = os.path.join(work_dir, "salt.txt")
    if os.path.exists(salt_path):
        salt = open(salt_path).read().strip()
    else:
        salt = "%032x" % random.SystemRandom().getrandbits(128)
        open(salt_path, "w").write(salt)
    recs, stats = parse_dir(raw_dir, salt)
    recs.sort(key=lambda r: (r["date"], r["id"]))
    for k in sorted(stats):
        print("stat", k, stats[k])
    with open(os.path.join(work_dir, "full.jsonl"), "w") as fh:
        for r in recs:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")
    root = truth_components(recs)
    threads = {}
    for r in recs:
        threads.setdefault(root[r["id"]], []).append(r)
    sizes = Counter(len(v) for v in threads.values())
    print("full set: messages", len(recs), "truth threads", len(threads),
          "of size>=2", sum(1 for v in threads.values() if len(v) >= 2), "largest", max(sizes))
    print("size distribution (size:count)", dict(sorted(sizes.items())[:12]), "...")
    order = sorted(threads, key=lambda k: h(salt, "pick:" + k))
    picked, total = [], 0
    for k in order:
        add = sum(len(json.dumps({x: v for x, v in r.items() if not x.startswith("_")}, separators=(",", ":"))) + 1
                  for r in threads[k])
        if total + add > CAP_BYTES:
            continue
        picked.append(k)
        total += add
    out = []
    for k in picked:
        out.extend(threads[k])
    out.sort(key=lambda r: (r["date"], r["id"]))
    with open(out_sample, "w") as fh:
        for r in out:
            fh.write(json.dumps({x: v for x, v in r.items() if not x.startswith("_")}, separators=(",", ":")) + "\n")
    print("frozen sample: messages", len(out), "threads", len(picked), "bytes", os.path.getsize(out_sample))
    # empty-subject share in the archive itself
    print("archive messages with empty subject:", sum(1 for r in recs if r["_empty_subject"]))


main()

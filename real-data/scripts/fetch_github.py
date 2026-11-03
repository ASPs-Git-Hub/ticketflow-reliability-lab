"""Fetch issues and their first comments with the authenticated gh CLI (read-only).

Usage: python3 -I fetch_github.py OUT_DIR
Calls only GET endpoints (search/issues, repos/{r}/issues/{n}/comments), pauses 0.3 s between calls.
Raw JSON goes to OUT_DIR (scratch, not committed). Window/cap choices are fixed in PROTOCOL.md."""
import json
import os
import subprocess
import sys
import time

REPOS = ["nodejs/node", "pandas-dev/pandas", "hashicorp/terraform"]
WINDOWS = {"W1": ("2025-03-02", "2025-03-15"), "W2": ("2025-03-23", "2025-04-05"),
           "W3": ("2025-10-22", "2025-11-05")}
CAP = 150
PAUSE = 0.3


def gh(args, retries=3):
    for n in range(retries):
        p = subprocess.run(["gh", "api", "-X", "GET"] + args, capture_output=True, text=True)
        time.sleep(PAUSE)
        if p.returncode == 0:
            return json.loads(p.stdout)
        sys.stderr.write("gh error (try %d): %s\n" % (n + 1, p.stderr.strip()[:200]))
        time.sleep(5 * (n + 1))
    raise SystemExit("gh failed")


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    summary = {}
    for repo in REPOS:
        rdir = os.path.join(out, repo.replace("/", "__"))
        os.makedirs(rdir, exist_ok=True)
        for wn, (a, b) in WINDOWS.items():
            items, total = [], None
            for page in (1, 2):
                q = "repo:%s is:issue created:%s..%s" % (repo, a, b)
                res = gh(["search/issues", "-f", "q=" + q, "-f", "sort=created", "-f", "order=asc",
                          "-f", "per_page=100", "-f", "page=%d" % page])
                total = res["total_count"]
                items.extend(res["items"])
                if len(res["items"]) < 100 or len(items) >= CAP:
                    break
                time.sleep(2)
            items = items[:CAP]
            slim = []
            for it in items:
                rec = {"number": it["number"], "created_at": it["created_at"], "state": it["state"],
                       "assoc": it["author_association"], "user_type": (it.get("user") or {}).get("type"),
                       "user_login": (it.get("user") or {}).get("login", ""), "comments_n": it["comments"],
                       "comments": []}
                if it["comments"] > 0:
                    rec["comments"] = gh(["repos/%s/issues/%d/comments" % (repo, it["number"]),
                                          "-f", "per_page=100"])
                slim.append(rec)
            with open(os.path.join(rdir, wn + ".json"), "w") as fh:
                json.dump({"repo": repo, "window": wn, "total_count": total, "issues": slim}, fh)
            summary[(repo, wn)] = (total, len(slim))
            print(repo, wn, "search total_count", total, "fetched", len(slim), flush=True)
            time.sleep(3)


main()

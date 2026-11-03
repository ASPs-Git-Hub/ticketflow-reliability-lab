"""SLA clocks on real GitHub issue timestamps: fixed (lab) vs naive vs fixed-offset. See PROTOCOL.md.

Usage: python3 -I sla_eval.py GH_DIR OUT_CSV
Reads raw JSON from fetch_github.py. Writes OUT_CSV with timestamps only (repo, window, opened,
first response) and prints the report. No logins, issue numbers or text are written out."""
import csv
import glob
import json
import os
import random
import statistics
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src"))
from ticketflow import sla  # noqa: E402

UTC = timezone.utc
MAINT = ("OWNER", "MEMBER", "COLLABORATOR")
ZONES = ["UTC", "America/Los_Angeles", "America/New_York", "Europe/London", "Europe/Berlin",
         "Asia/Kolkata", "Asia/Shanghai", "Australia/Sydney"]
TARGETS = {"high": 120, "normal": 240, "low": 480}


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def is_bot(user_type, login):
    return user_type == "Bot" or "bot" in (login or "").lower()


def load_tickets(gh_dir):
    tickets, counts, texts = [], {}, []
    for path in sorted(glob.glob(os.path.join(gh_dir, "*", "*.json"))):
        d = json.load(open(path))
        repo, win = d["repo"], d["window"]
        c = counts.setdefault((repo, win), dict(search_total=d["total_count"], fetched=0, bot=0, internal=0,
                                                 unanswered=0, answered=0, truncated_comments=0))
        for it in d["issues"]:
            c["fetched"] += 1
            if is_bot(it["user_type"], it["user_login"]):
                c["bot"] += 1
                continue
            if it["assoc"] in MAINT:
                c["internal"] += 1
                continue
            if it["comments_n"] > 100:
                c["truncated_comments"] += 1
            first = None
            for cm in it["comments"]:
                u = cm.get("user") or {}
                if cm.get("author_association") in MAINT and not is_bot(u.get("type"), u.get("login")) \
                        and u.get("login") != it["user_login"]:
                    t = ts(cm["created_at"])
                    if first is None or t < first[0]:
                        first = (t, cm.get("body") or "")
            if first is None:
                c["unanswered"] += 1
                continue
            c["answered"] += 1
            opened = ts(it["created_at"])
            tickets.append({"repo": repo, "window": win, "opened": opened, "resp": first[0]})
            texts.append((repo, first[1]))
    return tickets, counts, texts


def naive_minutes(t):
    return max(0.0, (t["resp"] - t["opened"]).total_seconds() / 60.0)


def cause(t, tz):
    a, b = t["opened"].astimezone(tz), t["resp"].astimezone(tz)
    d = a.date()
    while d <= b.date():
        if d.weekday() >= 5:
            return "weekend"
        d += timedelta(days=1)
    if a.date() != b.date():
        return "overnight"
    return "same local day"


def brute_minutes(start, end, tz, cache):
    """Independent per-minute counter (written by me): local weekday and hour read per UTC minute."""
    def flag(minute_epoch):
        hour_key = minute_epoch // 3600
        off = cache.get(hour_key)
        if off is None:
            off = int(datetime.fromtimestamp(hour_key * 3600, UTC).astimezone(tz).utcoffset().total_seconds())
            cache[hour_key] = off
        loc = minute_epoch + off
        days = loc // 86400
        wd = (days + 3) % 7   # 1970-01-01 was a Thursday (weekday 3)
        sec = loc % 86400
        return 1 if (wd < 5 and 9 * 3600 <= sec < 17 * 3600) else 0

    s, e = start.timestamp(), end.timestamp()
    if e <= s:
        return 0.0
    S = -(-int(s) // 60) * 60
    E = (int(e) // 60) * 60
    if S >= E:
        return flag(int(s) // 60 * 60) * (e - s) / 60.0
    tot = sum(flag(m) for m in range(S, E, 60))
    tot += flag(S - 60) * (S - s) / 60.0 if S > s else 0.0
    tot += flag(E) * (e - E) / 60.0 if e > E else 0.0
    return tot


def main():
    gh_dir, out_csv = sys.argv[1:3]
    tickets, counts, texts = load_tickets(gh_dir)
    with open(out_csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["repo", "window", "opened_utc", "first_maintainer_response_utc"])
        for t in tickets:
            w.writerow([t["repo"], t["window"], t["opened"].strftime("%Y-%m-%dT%H:%M:%SZ"),
                        t["resp"].strftime("%Y-%m-%dT%H:%M:%SZ")])
    print("== sample accounting (per repo, window) ==")
    print("%-22s %-3s %12s %8s %5s %9s %11s %9s %9s" % ("repo", "win", "search_total", "fetched", "bot", "internal",
                                                       "unanswered", "answered", "c>100"))
    for (repo, win), c in sorted(counts.items()):
        print("%-22s %-3s %12d %8d %5d %9d %11d %9d %9d" % (repo, win, c["search_total"], c["fetched"], c["bot"],
                                                           c["internal"], c["unanswered"], c["answered"],
                                                           c["truncated_comments"]))
    repos = sorted({t["repo"] for t in tickets})
    print("answered tickets total:", len(tickets), {r: sum(1 for t in tickets if t["repo"] == r) for r in repos})
    waits = sorted(naive_minutes(t) for t in tickets)
    q = lambda p: waits[min(len(waits) - 1, int(p * len(waits)))]
    print("wall-clock wait to first maintainer reply, minutes: p25 %.0f median %.0f p75 %.0f p90 %.0f" % (
        q(.25), q(.5), q(.75), q(.9)))

    for label, target in TARGETS.items():
        print()
        print("== first-response target %s = %d minutes (n = answered tickets) ==" % (label, target))
        print("%-20s %-10s %5s %12s %12s %10s %10s %8s" % ("zone", "scope", "n", "naive_breach", "fixed_breach",
                                                         "naive_only", "fixed_only", "naive_only%"))
        spread = []
        for z in ZONES:
            tz = ZoneInfo(z)
            for scope in ["ALL"] + repos:
                ts_ = [t for t in tickets if scope == "ALL" or t["repo"] == scope]
                nb = fb = no = fo = 0
                for t in ts_:
                    n_el = naive_minutes(t)
                    f_el = sla.business_minutes_between(t["opened"], t["resp"], tz)
                    nbr, fbr = n_el > target, f_el > target
                    nb += nbr
                    fb += fbr
                    no += nbr and not fbr
                    fo += fbr and not nbr
                print("%-20s %-10s %5d %12d %12d %10d %10d %7.1f%%" % (
                    z, scope.split("/")[1] if scope != "ALL" else "ALL", len(ts_), nb, fb, no, fo,
                    100.0 * no / len(ts_)))
                if scope == "ALL":
                    spread.append(100.0 * no / len(ts_))
        print("spread of naive-only rate across the %d zones (ALL repos): min %.1f%% max %.1f%%" % (
            len(ZONES), min(spread), max(spread)))

    print()
    print("== cause of naive-only breaches (target 240, first match wins) ==")
    for z in ZONES:
        tz = ZoneInfo(z)
        causes = {"weekend": 0, "overnight": 0, "same local day": 0}
        for t in tickets:
            if naive_minutes(t) > 240 and sla.business_minutes_between(t["opened"], t["resp"], tz) <= 240:
                causes[cause(t, tz)] += 1
        print("%-20s %s" % (z, causes))

    print()
    print("== DST: zone-aware clock vs constant-offset (January offset) clock, target 240 ==")
    print("%-20s %-4s %6s %14s %16s %12s %14s" % ("zone", "scope", "n", "cross_transit", "state_differs_x",
                                                   "max_diff_min", "state_differs_all"))
    for z in ZONES:
        tz = ZoneInfo(z)
        jan = datetime(2025, 1, 15, 12, tzinfo=UTC).astimezone(tz).utcoffset()
        if all(datetime(2025, m, 15, 12, tzinfo=UTC).astimezone(tz).utcoffset() == jan for m in range(1, 13)):
            print("%-20s no DST in 2025 (offset constant), skipped" % z)
            continue
        fixed_tz = timezone(jan)
        n_x = d_x = d_all = 0
        maxd = 0.0
        for t in tickets:
            a = sla.business_minutes_between(t["opened"], t["resp"], tz)
            b = sla.business_minutes_between(t["opened"], t["resp"], fixed_tz)
            crosses = t["opened"].astimezone(tz).utcoffset() != t["resp"].astimezone(tz).utcoffset()
            differs = (a > 240) != (b > 240)
            d_all += differs
            if crosses:
                n_x += 1
                d_x += differs
                maxd = max(maxd, abs(a - b))
        print("%-20s %-4s %6d %14d %16d %12.1f %14d" % (z, "ALL", len(tickets), n_x, d_x, maxd, d_all))

    print()
    print("== independent per-minute check of the lab clock (my own code, not independent review) ==")
    rng = random.Random(1)
    sample = rng.sample(tickets, min(150, len(tickets)))
    worst = 0.0
    nchecks = 0
    for z in ("America/New_York", "Europe/Berlin", "Australia/Sydney"):
        tz = ZoneInfo(z)
        cache = {}
        zw = 0.0
        for t in sample:
            a = sla.business_minutes_between(t["opened"], t["resp"], tz)
            b = brute_minutes(t["opened"], t["resp"], tz, cache)
            zw = max(zw, abs(a - b))
            nchecks += 1
        print("%-20s tickets %d max |lab - brute force| = %.6f minutes" % (z, len(sample), zw))
        worst = max(worst, zw)
    print("checks", nchecks, "worst difference", "%.6f" % worst, "PASS" if worst < 0.001 else "FAIL")

    print()
    print("== Part 3: qa.check_critical on first maintainer reply per answered ticket (no human labels, no accuracy claim) ==")
    from ticketflow import qa
    flagged = {}
    kinds = {"abuse": 0, "secret_request": 0}
    per_repo = {}
    for ctl in ("you are stupid and dumb", "Please send me your password so I can log in."):
        assert qa.check_critical({"turns": [["agent", ctl]]}), "must-fire control failed"
    print("must-fire controls: 2 of 2 known-bad strings are flagged by qa.check_critical")
    for repo, body in texts:
        item = {"turns": [["agent", body]]}
        crit = qa.check_critical(item)
        r = per_repo.setdefault(repo, [0, 0])
        r[0] += 1
        r[1] += int(crit)
        if crit:
            if qa.ABUSE.search(body):
                kinds["abuse"] += 1
            else:
                kinds["secret_request"] += 1
    for repo, (n, f) in sorted(per_repo.items()):
        print("%-22s replies %4d flagged %3d (%.1f%%)" % (repo, n, f, 100.0 * f / n))
    tn = sum(v[0] for v in per_repo.values())
    tf_ = sum(v[1] for v in per_repo.values())
    print("ALL replies %d flagged %d (%.1f%%)  by pattern: %s" % (tn, tf_, 100.0 * tf_ / tn, kinds))
    return texts


if __name__ == "__main__":
    main()

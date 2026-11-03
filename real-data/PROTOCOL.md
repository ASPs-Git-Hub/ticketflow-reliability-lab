# Real-data evaluation protocol (written 2026-11-03, before any scored run)

Frozen when `evidence/protocol-sha256.txt` was written. Changes after seeing results are listed
at the bottom under "Deviations", never made in place.

Scope: lab code in `src/ticketflow/` is used as it is. It is not edited. The 212 synthetic tests
and the 111-mutant run stay as the safety net. Everything here is extra evidence.
Only Python 3 standard library. Downloaded data is parsed with `python3 -I`, never executed.

## Part 1. Email threading on a public mailing-list archive

Data: the python-dev list archive on mail.python.org (pipermail text files), months 2018-11 to
2019-04 (six files). Chosen because the files are static, public, and the site robots.txt allows
reading with a 2 second delay. I fetched 6 files in total, with a 2 to 3 second pause.
I looked at message counts only (not threading results) before fixing these months.

Parsing: split on the mbox "From " separator, parse with `email` (policy compat32). Keep Message-ID,
In-Reply-To, References, Date, From (hashed), Subject (kept only in memory for the baseline).
Message bodies are not used and not stored: the lab's threading does not read them.
Header "Precedence" and other list headers are NOT passed to the lab (the lab treats
`Precedence: bulk` as an auto-reply and ignores it; list traffic is not what that rule is for).
A diagnostic counts how many messages would have been ignored if headers were passed.

Ground truth (the "truth thread"): build a graph over the sampled messages. Each message points to
its parent: the In-Reply-To id if that id is in the sample, else the last References id that is in
the sample, else none. A truth thread is a connected component of this graph. Parents that are not in
the sample are ignored. Messages with identical Message-ID inside the archive are deduplicated first.

Header oracle (independent of the lab): after handicaps are applied, union-find over every
In-Reply-To and References id that is present in the sample. A lab that uses all remaining headers
correctly should match this. It is the ceiling for what is recoverable.

Handicaps (the lab gets the damaged input; truth is always the undamaged chain):
- S0 control: date order, headers intact.
- S1 shuffle: delivery order randomly permuted. Many replies now arrive before their parent.
- S2 = S1 + redelivery: 10% of messages (chosen at random) are delivered a second time, same
  Message-ID, inserted at a random later position.
- S3 = S2 + header damage: of replies (messages with In-Reply-To), a random 10% lose In-Reply-To
  but keep References; a further random 5% lose both In-Reply-To and References.
- Subjects, all scenarios, for the subject baseline and passed to every flow: 30% of messages get an
  empty subject, 20% get a different prefix style ("RE:", "Fwd:", list tag removed); the rest are kept.
Scenarios S1 to S3 are run with seeds 1 to 5. The headline is the mean over seeds with the min and
max. S0 is deterministic.

Systems compared:
- TicketFlow (the lab, `ingest()` called per message with `from` = hashed address @example.invalid,
  `date` = original Date, `body` = empty string).
- NaiveTicketFlow (the lab's own baseline: no threading, no idempotency).
- SubjectBaseline (my own harness code, not part of the lab): thread key = subject lowercased with
  leading re/fw/fwd prefixes and bracket tags removed and whitespace collapsed; empty subject means
  its own case; duplicate Message-IDs are not collapsed. This is the usual "group by subject" approach.

Metrics (counted on the final state after all messages are delivered):
1. Exact threads: truth threads (reported for all, and for size >= 2) whose message set equals the
   message set of exactly one case.
2. Wrong-thread messages: a message in a case whose plurality truth thread (largest share; tie broken
   by thread size then id) is a different truth thread. Also: number of cases containing messages from
   more than one truth thread ("merged cases"). A message left alone in its own case is not wrong.
3. Split threads: truth threads of size >= 2 whose messages end up in more than one case, and the
   extra cases created.
4. Duplicates: injected redeliveries, how many the system reports as "duplicate", and whether any case
   holds a message twice or a redelivery created a case.
5. Late parents: pairs (child, parent) where both are in the sample, the child still has a header
   pointing at the parent after damage, and the child was delivered before the parent. Reconciled =
   both end in the same case.
6. Match to header oracle (S3 only is the interesting case): cases whose message set equals an oracle
   component, over oracle components.
Counts the lab "rejected" or "ignored" are reported as lost messages.

What counts as correct: for S0 to S2, the case partition equals the truth partition. For S3 the
partition should equal the header oracle partition (the headers no longer contain more), and no
message may be in the wrong truth thread.

Hand check: if wrong-thread messages or oracle mismatches exist, I read up to 20 of them (from the
primary seed 1, S3), labelled by me, not independent. I only describe the pattern.

Frozen sample: whole truth threads selected in ascending order of a salted hash of the thread root
until the file would pass 190 KB. Fields: salted-hash ids (12 hex chars), hashed sender (8 hex),
original date. No subjects, bodies, names or addresses. The scoring script runs on this file too and
the result is reported next to the full run. The salt is not committed, so ids cannot be reversed
or matched back to the archive.

## Part 2. SLA clocks on real GitHub issues

Data: `gh api` (authenticated, read-only, about 0.3 s between calls) for three repositories:
nodejs/node, pandas-dev/pandas, hashicorp/terraform. Chosen for size and a human maintainer team.
No individual is named; only aggregates are reported and committed.

Sample: issues (not pull requests) created in three 14-day windows chosen to contain clock changes:
- W1 2025-03-02 to 2025-03-15 (US spring forward 2025-03-09)
- W2 2025-03-23 to 2025-04-05 (EU spring forward 2025-03-30)
- W3 2025-10-22 to 2025-11-05 (EU fall back 2025-10-26, US fall back 2025-11-02)
In each window and repo, up to 150 issues by oldest creation first (search API `created:` range,
`sort=created&order=asc`). Excluded: issues whose author is a bot (type Bot or login containing "bot"),
and issues whose author association is OWNER, MEMBER or COLLABORATOR (internal tracking items).

Ticket opened: issue `created_at`. First response: earliest comment `created_at` among the first
100 issue comments whose author association is OWNER, MEMBER or COLLABORATOR, who is not a bot and
not the issue author. Comments from outsiders (CONTRIBUTOR, NONE) are not responses. Labels, closing
without comment, and reactions are not responses. Issues with no such comment are "unanswered": they
are counted and excluded from the comparison (the maintainer zone is unknown, and "still waiting" needs
a stated cut-off that I do not want to choose after the fact).

Clocks:
- Fixed clock: `ticketflow.sla.business_minutes_between(opened, responded, zone)`, Mon to Fri 09:00 to
  17:00 local, no holidays (the lab's only calendar).
- Naive clock: wall-clock minutes between the two instants (what `NaiveTicketFlow.sla` computes).
- Fixed-offset clock (to isolate DST): the same lab function with a constant UTC offset equal to the
  zone's January offset.
Targets are the lab defaults in business minutes: first response 240 (normal, headline), 120 (high),
480 (low). The naive clock uses the same numbers as wall minutes. A ticket is breached when elapsed
is greater than the target.

Zones (maintainer zone is unknown, so all are reported): UTC, America/Los_Angeles, America/New_York,
Europe/London, Europe/Berlin, Asia/Kolkata, Asia/Shanghai, Australia/Sydney.

Reported per repo and zone, for the 240 target: answered tickets n; breached by naive; breached by
fixed; naive-only breaches (naive says breached, fixed says not); fixed-only breaches (expected 0,
because wall minutes are never fewer than business minutes; a nonzero value would be a finding).
Spread across zones: min and max of the naive-only rate. Cause of each naive-only breach, first match
wins: interval includes a local Saturday or Sunday; else interval crosses a local midnight; else other.
DST: tickets whose interval contains a UTC-offset change in that zone (only zones that have DST); for
those, how many change breach state between the zone-aware and fixed-offset clocks, and the largest
difference in minutes.

Independent check of the clock code on real timestamps: a per-minute brute-force counter (local weekday
and hour read for each UTC minute) for a random 150 answered tickets (seed 1) in America/New_York,
Europe/Berlin and Australia/Sydney. It must equal the lab to within 0.001 minute. Written by me, so
not independent.

## Part 3 (optional). Critical-failure flags on real maintainer replies

The first qualifying maintainer comment per answered issue is scored with `qa.check_critical` (abuse
pattern, or a request for a secret aimed at the agent). Only counts are reported. There are no human
labels for these comments, so no accuracy claim is made. I read up to 10 flagged comments to describe
what the flag matched (labelled by me, not independent). Comment text is held only in the scratch folder.

## Evidence

Every run writes raw stdout to `evidence/*.txt`. Numbers in the README come only from those files.

## Deviations

Written after the runs, in the order they happened. The protocol text above is unchanged.

1. Hand check (Part 1): the protocol said I would read up to 20 mismatched messages. I did not read
   any message text. `--handcheck` classified every lab case that is not a header-oracle component
   (all were fragments of one truth thread, none spanned two threads), and `scripts/diagnose_splits.py`
   was written after seeing the S1 numbers to find the cause of the splits. That script is exploratory,
   not pre-registered.
2. Part 3: a must-fire control (two known-bad strings must be flagged) was added to `sla_eval.py`
   after the first run, and the script was run again. The numbers did not change. No flagged comment
   existed to read, so the "read up to 10" step did not happen.
3. The windows never reached the 150 issue cap (largest window had 66 issues), so the final sample is
   smaller than the protocol hoped for: 363 issues fetched, 196 answered by a maintainer.
4. `threading_eval.py` got an `if __name__` guard after the first run so `diagnose_splits.py` could
   import it. The scored numbers were regenerated with the final script.
5. The per-minute check in Part 2 is exact (whole-minute grid plus the partial first and last minute),
   which is why the difference is 0.000000 and not just under 0.001.

# TicketFlow on real data

Status: BUILT_UNVERIFIED for the findings below. I ran every number myself and saved the raw output in
`evidence/`. Nobody else has checked it. The rest of the lab uses made-up "Northwind Cloud" data. This
folder tests the same code on two sets of real, public data. Run 1 left `src/` untouched. Run 2 changed `src/ticketflow/ingest.py` and `flow.py`
(the merge fix, below); all 212 old tests still pass and 21 tests were added.

The plan was written before the first scored run: `PROTOCOL.md`. Changes made after seeing results are
listed at its bottom under "Deviations". Where the data came from and the terms: `SOURCES.md`.

## Words used here

- **Message-ID**: a unique name every email carries. A reply carries the Message-ID of the mail it answers in
  its **In-Reply-To** header, and the whole chain so far in **References**.
- **Threading**: putting emails of one conversation into one ticket. The lab reads those headers to do it.
- **SLA**: a promise about time, for example "answer within 4 hours". The lab counts only business hours:
  Monday to Friday, 09:00 to 17:00 in a chosen time zone.
- **Naive clock**: counts every minute on the wall clock, night and weekend included.
- **DST**: daylight saving time. Clocks jump one hour twice a year, and the jump dates differ by country.

## Part 1. Threading real mailing-list mail

**Data.** The public python-dev mailing list archive, six months (Nov 2018 to Apr 2019): 1618 messages in
218 real conversations (147 of them have two or more messages, the largest has 77).
**Right answer.** The reply chain written in the headers. A conversation is correct when the lab puts exactly
those messages, and no others, into one ticket.
**What I did to make it hard.** The real archive is in clean order, so I damaged the input in steps. Each
step adds to the one before. Steps S1 to S3 were run with five random seeds.

| step | what I did to the input |
|---|---|
| S0 | nothing: date order, headers whole |
| S1 | shuffle the delivery order (about 700 replies arrive before the mail they answer) |
| S2 | S1, plus 10% of messages delivered a second time (162 repeats) |
| S3 | S2, plus 10% of replies lose In-Reply-To (References kept) and 5% lose both |

Subjects were also damaged in every step (30% emptied, 20% given a different prefix). The lab does not read
subjects. Only the subject baseline below does. Bodies were not used.

**Three systems.** `TicketFlow` (the lab). `NaiveTicketFlow` (the lab's own bad version: no threading).
`SubjectBaseline`, which I wrote for this test: group by cleaned-up subject, the usual shortcut. It is not
part of the lab.

Conversations with 2+ messages that came out exactly right, out of 147 (mean over 5 seeds, range in brackets):

| step | TicketFlow | NaiveTicketFlow | SubjectBaseline |
|---|---|---|---|
| S0 | 147 (100%) | 0 | 32 |
| S1 | 89.2 [86-94] (61%) | 0 | 24.8 [21-32] |
| S2 | 94.0 [87-98] (64%) | 0 | 24.8 [21-32] |
| S3 | 77.2 [75-80] (53%) | 0 | 24.8 [21-32] |

Messages put into the wrong conversation (worse than leaving a message alone), all steps and seeds:

| | TicketFlow | NaiveTicketFlow | SubjectBaseline |
|---|---|---|---|
| S0 | 0 | 0 | 50 |
| S1 to S3 (mean) | 0 in every run | 0 | 54.8 [50-57] |
| tickets that mix two conversations | 0 | 0 | 12.8 [11-14] |

More results:

- **Repeats.** In S2 and S3 the lab reported all 162 repeats as "duplicate" and stored zero extra copies. The
  naive version and the subject baseline kept 162 extra copies each.
- **Late parents.** In S1, 697 replies arrived before their parent (mean). The lab ended with the reply and
  parent in the same ticket for 549, which is 79%. S2: 563 of 710. S3: 522 of 670.
- **Against the best possible answer.** In S3 some replies lost their only link, so no program could rebuild
  those conversations. A union-find over the headers that remain gives 245 pieces. The lab matched 180.2 [174-184]
  of them (74%). In S1 and S2, where no headers were removed, the lab matched 160.2 and 165.0 of 218.
- **Lost messages.** 0 rejected or ignored in every run.
- **Same test on the frozen sample** (1066 messages, 136 conversations, 91 with 2+ messages, the file committed here):
  S0 91 of 91; S1 54.6; S2 55.4; S3 46.8; wrong-thread 0 in every run. Same shape as the full run
  (`evidence/threading-frozen-sample.txt`).

**What went wrong.** The lab splits conversations when messages arrive out of order. In S1 (seed 1),
57 of the 147 conversations ended in more than one ticket. In 56 of those, two or more replies had arrived
before their shared parent, and each one opened its own ticket. The lab remembers one ticket per missing
parent (`self._pending.setdefault(mid, case_id)` in `src/ticketflow/ingest.py`). When the parent finally comes,
it joins only the first ticket, and the lab has no step that merges tickets. The comment in `ingest.py` says a
reply that comes early "joins that case" when the parent arrives, which holds for one early reply and fails
for two. The old synthetic tests did not cover this. I did not change `src/` in run 1. The one remaining split was not
explained (`evidence/threading-diagnosis.txt`). This is a design gap in the lab, found by real data.
**Update 2026-11-03: the gap is fixed and measured again, see "What I changed and what happened" below. The
numbers in the tables above are run 1 (before the fix).**

## What I changed and what happened (run 2, after the fix)

**The problem, in plain words.** Think of a conversation as a family tree of emails. Each reply says
"I am answering that one". Sometimes the answer arrives before the question, because mail is delivered
in a random order. Say replies B and C both answer message A, and B and C arrive first. The lab did not
know A yet, so it opened one ticket for B and another for C and wrote down "both are waiting for A". When
A arrived it joined only one of the two tickets and the other stayed alone. The lab had no step that
joins two tickets into one. So one conversation became two or three tickets.

**The fix.** The lab now remembers every ticket that is waiting for a missing message, not only the first.
When the missing message arrives, all of those tickets and the new message become one ticket. The ticket
with the lowest number stays; the others are folded into it and nothing is thrown away: their messages,
the people in them, the agent's replies and status changes, and the earliest first reply all move over.
An old ticket number keeps working and points to the surviving ticket. An alert that was open on a folded
ticket is closed, because that ticket no longer has a clock of its own. The same joining happens when a
message that arrives has its own header pointing at a ticket that already exists.

**Rules I kept.** A message is still counted once (a repeat is reported as "duplicate"). A subject
containing a ticket number from a sender who is not in that ticket still does not attach and still does
not merge anything. Two replies that point at a missing message that never arrives stay in two tickets,
because the lab does not guess. Two tickets that already exist are not merged just because one message
names both (see "what still fails").

**Tests.** `tests/test_merge.py` has 21 new tests. They were written first and run against the old code:
16 of the 21 failed there (`evidence/v2-new-tests-on-old-code.txt`). With the fix, all 233 tests pass
(212 old, 21 new; `evidence/v2-tests.txt`). One test tries every one of the 720 delivery orders of a
six-message conversation and checks for exactly one ticket each time. `scripts/mutate.py` got 16 new
mutants for the merge code and 3 old patterns were updated to the new code. Result: 127 mutants, 127 caught, 0 survived
(`evidence/v2-mutants.txt`; the first attempt left 3 survivors that were redundant code, which I removed and re-ran). `scripts/check-confidential.sh` is clean on the lab
(`evidence/v2-check-confidential.txt`).

**The re-test.** `PROTOCOL-v2.md` was written and its checksum saved before the re-test. Same data, same
damage steps S0 to S3, same seeds 1 to 5, same scripts, nothing tuned. First I ran the OLD code through the
same scripts and got run 1 back byte for byte (`evidence/v2-old-code-reproduction*.txt`), which shows the
data and scripts did not move. The NaiveTicketFlow and SubjectBaseline columns are identical before and
after, as they must be. One honest note: I ran the new code on this data while building it, to make sure it
produced no wrong-thread message, so this is a re-test and not a blind test.

Full set, TicketFlow only (mean over 5 seeds, `evidence/v2-threading-full.txt` against `threading-full.txt`):

| measure | S1 before | S1 after | S2 before | S2 after | S3 before | S3 after |
|---|---|---|---|---|---|---|
| conversations (2+ messages) exactly right, of 147 | 89.2 | 147.0 | 94.0 | 147.0 | 77.2 | 121.8 [117-125] |
| messages in the wrong conversation | 0 | 0 | 0 | 0 | 0 | 0 |
| extra tickets created by splits | 141.0 | 0 | 138.6 | 0 | 189.8 | 31.2 |
| repeats reported as duplicate, copies stored | 0 copies | 0 copies | 0 copies | 0 copies | 0 copies | 0 copies |
| early replies that ended in their parent's ticket | 549.4 of 697.4 | 697.4 of 697.4 | 562.8 of 710.0 | 710.0 of 710.0 | 521.8 of 670.0 | 670.0 of 670.0 |
| tickets equal to the best possible answer from the headers left | 160.2 of 218 | 218 | 165.0 of 218 | 218 | 180.2 of 245 | 239.6 of 244.4 |

S0 is unchanged at 147 of 147. In S2 all 162 repeats are still reported as duplicates (no change from run 1).
The frozen sample (91 conversations of 2+ messages) went from S1 54.6, S2 55.4, S3 46.8 to S1 91, S2 91, S3 75.2,
with 0 wrong-thread messages (`evidence/v2-threading-frozen-sample.txt`).

The split that run 1 could not explain was a side effect of the same gap. The diagnosis script now says
"thread intact" for all 147 conversations in S1 seed 1 (before: 90 intact, 56 split by the shared parent, 1
other) (`evidence/v2-threading-diagnosis.txt`).

**What still fails: S3 (121.8 of 147 exactly right, so 25.2 are not).** Of those 25.2, 21.2 are out of reach of any header-only program and 4.0 are a deliberate choice, as follows.
- *Cannot be fixed from headers.* In S3 I removed In-Reply-To from 10% of replies and both headers from a
  further 5%. A reply that lost both has no link to anything, and when its parent was also damaged the
  conversation falls apart whatever the program does. Counting only what the remaining headers still connect
  (`scripts/diagnose_s3.py`, `evidence/v2-s3-diagnosis.txt`), the best possible result is 125.8 conversations
  of 147. The lab gets 121.8. So 21.2 conversations cannot be rebuilt from headers. Reading the subject or the
  body might rebuild some of them, but the subject baseline in this test put 54.8 messages into the wrong
  conversation, and the lab's rule that a subject alone never attaches a message is a safety rule I did not weaken.
- *Fixable, left open on purpose.* The remaining 4.0 conversations (2.7%) are split because one message names
  messages that already sit in two different tickets, and the lab does not fuse two existing tickets on that
  evidence. All gaps (2 to 6 per seed) are of this kind (`evidence/v2-s3-diagnosis.txt`). Someone who knows
  one message-id of each of two customers' tickets could otherwise fuse the tickets by writing one email. I
  would only add a merge here together with a check on who may link the tickets. It is a decision for the owner
  and it was not part of the plan.
- Part 1 still uses one mailing list, my own damage steps and a header-chain truth. S1 and S2 reaching every
  conversation says the fix covers this failure. It does not say all out-of-order mail is handled.

## Part 2. Business-hours clocks on real GitHub issues

**Data.** Issues from three public repositories: nodejs/node, pandas-dev/pandas, hashicorp/terraform. I took every
issue (up to 150) opened in three two-week windows chosen to include clock changes: 2 to 15 Mar 2025 (US DST
starts 9 Mar), 23 Mar to 5 Apr 2025 (Europe 30 Mar), 22 Oct to 5 Nov 2025 (Europe 26 Oct, US 2 Nov).
**Ticket opened** = issue creation time. **First response** = the first comment by someone GitHub marks as
OWNER, MEMBER or COLLABORATOR (not a bot, not the issue author). Issues opened by such people were left out.

| | count |
|---|---|
| issues fetched | 363 |
| opened by project members (left out) | 47 |
| opened by bots (left out) | 0 |
| no maintainer comment yet (left out) | 120 |
| **answered tickets used** | **196** (node 80, pandas 81, terraform 35) |

Wall-clock wait for the first maintainer reply: 25% under 187 minutes, median 817 minutes, 75% under 3053
minutes, 90% under 23402 minutes (about 16 days).

I do not know in which time zone the maintainers sit, so I ran eight: UTC, Los Angeles, New York, London, Berlin,
Kolkata, Shanghai, Sydney. The target is the lab's "normal" first-response target, 240 business minutes. The
naive clock gets 240 wall minutes. These are the lab's default numbers, not the projects' own promises.

Out of 196 tickets, the naive clock calls 140 breached (late). The business-hours clock calls fewer:

| zone | business clock: breached | naive says breached, business says not | the other way round |
|---|---|---|---|
| UTC | 95 | 45 (23.0%) | 0 |
| Los Angeles | 101 | 39 (19.9%) | 0 |
| New York | 105 | 35 (17.9%) | 0 |
| London | 97 | 43 (21.9%) | 0 |
| Berlin | 96 | 44 (22.4%) | 0 |
| Kolkata | 90 | 50 (25.5%) | 0 |
| Shanghai | 90 | 50 (25.5%) | 0 |
| Sydney | 88 | 52 (26.5%) | 0 |

So between 17.9% and 26.5% of tickets get a different verdict, depending on the assumed zone. With a 120
minute target the range is 18.4% to 28.1%; with 480 minutes it is 27.6% to 30.1%. "The other way round" is
zero by construction: wall minutes are never fewer than business minutes. The size of the first column
is the finding. The direction is not.

Why the naive clock was wrong (240 minute target, first cause that fits): New York 35 tickets = 16 spanned a
weekend, 7 crossed a night, 12 stayed inside one local day (opened after hours). Sydney 52 = 19, 18, 15.
All eight zones are in `evidence/github-sla.txt`.

**DST.** I compared the lab clock with a clock that uses one fixed hour offset all year (the January one).
17 tickets (20 in Sydney) had a clock change between opening and reply. The two clocks gave a different verdict for 0 of
them in four of the five DST zones, and for 1 in New York. The largest gap in minutes was 60. Over all 196
tickets, the fixed-offset clock gave a different verdict for 6 in Los Angeles, 3 in New York, 2 in London, 0 in
Berlin and 0 in Sydney. DST matters less than weekends here. It is a small effect on this sample.

**Check of the lab's clock code on real timestamps.** I wrote a separate per-minute counter (it asks "is this
minute inside Mon to Fri 9 to 17 local?") and compared 150 random tickets in 3 zones: 450 comparisons,
largest difference 0.000000 minutes. I wrote that counter, so it is a check by me, not an independent review.

## Part 3. QA critical-failure flags on real replies

`qa.check_critical` (abuse words, or an agent asking for a password-like secret) ran on the first maintainer
reply of each of the 196 tickets: 0 flagged (node 0 of 80, pandas 0 of 81, terraform 0 of 35). Two known-bad
test strings were flagged by the same function, so it was working. There are no human labels for these
replies, so this says nothing about accuracy: it only says the rule never fired on 196 replies.

## What this does not prove

- Part 1 uses one list (python-dev) and six months. Other lists, with other mail clients, can behave differently.
- The damage in S1 to S3 is my invention. Real out-of-order delivery may be milder or harsher.
- The right answer is the header chain. Where a human replies in the wrong place, the chain is wrong and so
  is my "truth". I did not read messages to check.
- The naive clock is a weak opponent by design. The result shows how large the gap is, not that anybody
  runs such a clock.
- GitHub issues are not help-desk tickets. Open-source maintainers owe nobody a 4-hour reply, and I make no
  claim that these projects breached anything. 120 of 363 issues had no maintainer comment and are left out,
  so the answered group is the quicker group.
- The sample is 196 tickets from 3 repositories in 3 short windows. The per-repo rows are small.
- Business hours are the lab's only calendar: Monday to Friday, 9 to 17, no holidays, one zone per run.
- I did not test the lab's rule that drops mail with a `Precedence: bulk` header. The archive text files carry
  no such header, so it never came up.
- Nothing here was reviewed by anyone else. Every hand classification was done by me.

## Files

- `PROTOCOL.md`, `PROTOCOL-v2.md` (re-test plan), `SOURCES.md`, this file.
- `scripts/prepare_threading.py` (parse, hash, freeze sample), `threading_eval.py`, `diagnose_splits.py`,
  `fetch_github.py`, `sla_eval.py`, `diagnose_s3.py` (run 2). Python 3 standard library only, run with `python3 -I`.
- `sample/threading_sample.jsonl`: 1066 messages, salted hashes only (190 KB). `sample/github_sla_timestamps.csv`:
  196 rows, repository, window, two UTC times, no names or numbers.
- `evidence/`: raw output of each run, download checksums, and the protocol checksum
  (taken before the first scored run; `PROTOCOL.md` has since gained the Deviations section).

Reproduce Part 1 from the committed sample: `python3 -I scripts/threading_eval.py sample/threading_sample.jsonl`.
Part 2 needs `gh` and the fetch script, and will return later data if issues have changed.

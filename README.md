# TicketFlow Reliability Lab

Status: BUILT_UNVERIFIED. Author-run results only. The CI workflow has never run on a remote.
Review round 2 (two independent reviews, 45 findings) was applied on 2026-11-03; see
`docs/REVIEW_ROUND2.md` for the accept / reject table. Nothing here has been reviewed a third time.

An independent, synthetic-data demonstration of the reliability problems that show up when a
help desk handles email: duplicate cases, replies that open new cases, bounces that open cases,
SLA clocks that ignore business hours, alerts that fire forever, unassigned cases, and QA scoring
that nobody has checked against human judgement. Fictional company ("Northwind Cloud"), Python 3
standard library only (3.9 and later), built from general engineering patterns. No real helpdesk,
employer or customer data is involved.

## Layout

```
src/ticketflow/
  ingest.py      normalise emails, idempotent on message-id, authenticated threading, outbound-id
                 threading, late-parent reconciliation, quote/signature stripping, bounce and
                 auto-reply detection that needs strong evidence
  reducer.py     deterministic status reducer: state comes from (event time, rank, sequence)
  routing.py     rule-based queue assignment, word-boundary keywords, security precedence
  sla.py         business-hours clocks per timezone (zoneinfo, DST safe), pause while waiting
  escalation.py  alerts, one per (case, rule), cooldown applies across clear and reactivate
  qa.py          rubric QA scoring (rules only), critical-fail override, calibration vs labels
  metrics.py     first response, resolution, backlog, breach rate per clock
  flow.py        TicketFlow: the fixed implementation composing the modules
  naive.py       NaiveTicketFlow: the usual defects, same interface
  sim.py         seeded simulation plus an audit judged by an independent oracle
tests/           unittest suite (212 tests): contract, unit, QA, simulation, round 2, review record
data/            qa_labels_a.json (tuning), qa_labels_b.json (frozen), qa_labels_c.json (new)
docs/            REVIEW_ROUND2.md (accept / reject record), LABELING_RULES.md
scripts/         run_qa_eval.py, run_simulation.py, mutate.py
evidence/        saved real output of the runs below
.github/workflows/test.yml   valid only when this folder is the root of its own repository
ci/monorepo-workflow.yml     copy to <monorepo root>/.github/workflows/ to run it from a subfolder
```

Run: `python3 -B -m unittest discover -s tests`

CI note: GitHub only reads `.github/workflows/` at a repository root. The workflow in this folder
works when the folder is its own repo. Inside a monorepo, copy `ci/monorepo-workflow.yml` to the
monorepo root (it sets `working-directory` and a path filter). That copy has not been made and
nothing has run on a remote.

## Results (all produced by the commands in evidence/)

Tests: 212 run, all pass, on Python 3.14.6, 3.12 and 3.9.6 (evidence/test-run.txt, test-run-py312.txt,
test-run-py39.txt). Python 3.10 is in the CI matrix but was not run locally.

Contract tests: 18 tests, each targeting one naive defect. They run against both implementations.
TicketFlow passes all 18. NaiveTicketFlow is caught by all 18: 17 by an AssertionError and one
(malformed input) by the KeyError crash that is itself the defect. The negative controls now accept
only those outcomes, and a meta-test proves that a constructor raising RuntimeError is NOT counted as
"defect caught".

### Simulation

Seeds 1 to 5, 60 cases per seed, arrivals in the first 4.5 simulated days of a window that contains
the 2026 US DST change, hourly ticks over 13 days, six timezones, random priorities, replies to the
customer's own message and to the agent's outbound mail (half each), replies to cases that are
waiting on the customer, duplicates, bounces and auto-replies (evidence/simulation.txt).

The audit no longer trusts the implementation. It reads public state and the notifications returned by
`tick()`, and judges them against an oracle built from the simulator's own ground truth: per-minute
business-hour flags (not `sla.py`), its own copies of targets, the 0.8 at-risk ratio and the 120
minute cooldown, and a status timeline rebuilt from the generated events. Checks: business minutes
for both clocks at three checkpoints, every expected alert present within one cooldown, no alert the
oracle does not expect, no two notifications for one (case, rule) closer than the cooldown, no clock
movement while waiting.

| violation (totals over 5 seeds) | fixed | naive |
|---|---|---|
| duplicate cases per message-id | 0 | 41 |
| cases left unassigned | 0 | 215 |
| notifications for one (case, rule) inside the cooldown, plus duplicate alert records | 0 | 313058 |
| alert expected by the oracle but not delivered within a cooldown | 0 | 8753 |
| alert delivered that the oracle does not expect | 0 | 105089 |
| SLA elapsed minutes differing from the oracle (3 checkpoints x 2 clocks) | 0 | 1749 |
| SLA clock running while paused | 0 | 21 |
| bounce or auto-reply opened a case | 0 | 70 |
| reply not threaded to its case | 0 | 81 |

Read the naive column as "re-fires at every tick and uses wall-clock minutes", not as a measured
magnitude: 313058 is hourly ticks times cases times rules over 13 days and would change with the tick
rate. The per-category claim (the fixed flow is 0, the naive flow is above 0 in every category) is what
the tests assert. Earlier README text explained a zero for one naive seed; that explanation was wrong
and the per-seed claim was removed.

Must-fire controls in `tests/test_sim.py`, each a flow with one planted defect that the audit must
fail: no pause while waiting, cooldown 0, cooldown 60 minutes, `tick()` returning nothing, a frozen
clock (both clocks, resolution only, first response only), a wall-clock first response, and a flow that
never indexes the agent's outbound message-id. The fixed flow's zero is therefore a statement against
an audit that is known to be able to fail.

### Mutation (scripts/mutate.py)

One-line defects are planted in a temp copy of the lab and the whole suite is run on each. Result:
see evidence/mutation-run.txt for the exact count and for whether each kill was by an assertion or by
an import crash. The list covers the previous mutants (renamed where the code moved) and new ones for
authenticated threading, outbound indexing, late-parent reconciliation, the reducer ordering, bounce
and auto-reply evidence, RFC 3834 parsing, quote stripping, clause-local negation, and the audit
itself. A pass is a statement about these mutants, not about all possible defects. Round 1 had 2
survivors that were fixed by strengthening tests; round 2 found 2 more survivors in its own new
mutants (a critical-fail recall miscount and an audit that skipped the resolution clock), which were
also fixed by strengthening tests.

### QA scoring against labels

IMPORTANT, read before quoting any number: all three label sets were written by the same author
model that wrote the rubric and the scorer. There is one labeller, so there is no inter-human
agreement figure and none of these figures is independent human labelling. "Derived verdict" is the
scorer's own weights applied to the labelled per-criterion values; it is not a separate human overall
judgement.

- Set A (30 transcripts): the tuning set. Numbers are optimistic. One label (A03 resolution) was
  corrected in round 2.
- Set B (30): written and committed (bf6056f) before the first committed scorer. The git history
  cannot show whether it was consulted earlier. B was frozen before the round 2 fix pass, and the fix
  pass changed `qa.py`, so B numbers were re-measured and are NOT clean: B is a regression set. Two
  further facts: 14 of B's 90 agent turns are copied verbatim from A (the file is frozen and was not
  edited; a test pins the 14), and the two sets share one template. B moved after the fix pass
  (critical fail 28/30 to 29/30, derived verdict 27/30 to 28/30) as a side effect of narrowing the
  secret-request pattern; it was not a tuning target. The honest claim is "regression-checked".
- Set C (30): written after the fix pass, with no agent turn repeated from A or B (a test enforces
  it), scored once, and not tuned against. Still the same author model, using the written rules in
  `docs/LABELING_RULES.md`. This is the least contaminated of the three, and it is still not independent.

Set C (n = 30), the least contaminated:

| criterion | agree | Wilson 95% | kappa |
|---|---|---|---|
| greeting | 29/30 | 0.833 to 0.994 | 0.65 |
| identification | 29/30 | 0.833 to 0.994 | 0.93 |
| correct information | 28/30 | 0.787 to 0.982 | 0.86 |
| tone | 29/30 | 0.833 to 0.994 | 0.87 |
| resolution | 22/30 | 0.556 to 0.858 | 0.49 |
| closing | 27/30 | 0.744 to 0.965 | 0.67 |
| critical fail | 29/30 | 0.833 to 0.994 | 0.65 |
| derived verdict | 27/30 | 0.744 to 0.965 | 0.80 |

Critical-fail detection on C: 1 true positive, 0 false positives, 1 miss (precision 1.00, recall 0.50 on
two positives, so the recall figure is almost meaningless). Set B (regression): greeting 28, identification
29, correct information 29, tone 29, resolution 27, closing 29, critical fail 29 of 30; derived verdict
28/30. Set A (tuned): verdict 29/30. Full intervals and every disagreement are in evidence/qa-eval.txt.
The differences between the sets are observed variation between three separately authored samples. They
may include tuning optimism on A, but these data cannot attribute them to tuning (resolution agreement is
26/30 on A, 27/30 on B and 22/30 on C). With n = 30 every interval is about 20 points wide.

### Where the rule-based scorer is wrong

From B (regression set):

- B11, B23 greeting: "Morning!" and "Greetings from ..." are greetings to a human, absent from the word list.
- B12 correct information: "a quarter of an hour" is correct but does not contain the string "15 minutes".
- B13 tone: sarcasm passes because no listed rude phrase occurs and a courtesy word does.
- B16 critical fail, miss: asking for "the 16 digits on your card" is not matched by the card-number pattern.
- B08, B16 resolution: an action sentence is credited even when the customer outcome is wrong or the request is unsafe.
- B30 resolution: "Delete it." is a real instruction the patterns do not recognise.
- B18 closing: "enjoy the product" is a sign-off the list does not contain.
- B19 identification: the agent never restates the problem, but "so you can enrol" matches the "so you" pattern.
- (B15, "sharing your password with us is never needed", used to be a false alarm; it is no longer flagged
  because the pattern no longer matches "sharing". That is a side effect, not a rule that understands negation after the match.)

From C (not tuned against, so these stay):

- C05 critical fail, miss: "please send me your current password" is not matched; the pattern allows "full" but not "current".
- C13 greeting: "Good day" is not in the greeting list. C17 identification: "you want to cancel" restates the request but matches no pattern.
- C03, C30 correct information: "fifteen minutes" and "will never request" are correct paraphrases the string patterns miss.
- Resolution (8 of the 8 disagreements): C13, C15, C22, C23, C26, C27, C30 are valid instructions or actions
  ("I've added a note", "forward it", "Try a backup code", "spread your calls out", "block the number") that the
  verb lists do not contain; C17 credits "I've cancelled it" although the stated outcome (loss of access) is wrong.
- Closing: C25 "Thank you." and C29 "Enjoy the rest of your day." are sign-offs outside the list; C20 "Anything else?" is credited
  after a rude reply.
- C05 tone: a courtesy word hides a pushy demand.

These are surface-pattern limits, not bugs to patch away: a pattern list can always be beaten by a
paraphrase, and patching them against B or C would turn those sets into tuning sets.

## What this does NOT prove

- Everything is synthetic. The transcripts, emails and arrival patterns were written or generated
  by the author; real traffic has failure shapes not modelled here.
- Rule-based QA is not language understanding. It checks words and shapes. It cannot tell whether
  advice is correct in substance, and it can be gamed by including the right phrases.
- The labels come from one author model that also wrote the rules. Agreement here measures consistency
  with that author, not with a trained QA team. No second labeller has seen any set.
- B is not a held-out set (see above), and C is the same author's work. No QA number here is an accuracy.
- Threading treats a message-id as a capability: anyone holding a case's message-id can thread into it.
  A signed reply token would be stronger and is not implemented. A subject token needs a known participant.
- Only the malformed-input types listed in tests are validated; the fuzz test is a fixed list of values,
  not a property-based search.
- Inbound mail with a naive date is read as UTC; address parsing is `email.utils.parseaddr`, not a full RFC 5322 parser;
  MIME bounce detection reads a `Content-Type` header string, no real MIME message is parsed.
- Lowercasing message-ids merges ids that differ only in local-part case.
- No integration with any real helpdesk, mail server or ticketing API. The "email" is a dict.
- The simulation audit checks nine invariants against an oracle written by the same author. Passing them
  does not mean the system is correct in general; the oracle shares the author's idea of correct.
- Business hours are Monday to Friday 09:00 to 17:00 local, no holidays, one calendar. The default calendar
  still comes from the email `tz` field (a simulation device); `tz_by_queue` is the realistic option and is
  only unit tested.
- Single process, in memory, no persistence, no concurrency control, restart loses deduplication.
- CI has not run remotely; Python 3.10 was not exercised locally.

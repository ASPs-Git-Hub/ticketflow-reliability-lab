# Review round 2: accept / reject record

Date: 2026-11-03. Inputs, verbatim: a ChatGPT review (21 findings, referred to as C1 to C21 in the order
printed) and a Claude (Fable) review (24 findings: 0 P0, 6 P1, 11 P2, 7 P3, referred to as F1 to F24 in the
order printed). 21 + 24 = 45 findings, 45 rows below. Duplicates are merged: the row names the
finding it duplicates, and the fix is recorded under that row. I traced every finding to the code before giving a verdict.

Status words: FIXED (code and test changed, test executed), PARTIAL (part fixed, remainder stated),
DUPLICATE (same defect as the named row), REJECTED (reviewer wrong or out of scope, evidence given).
A fix is BUILT_UNVERIFIED until CI has run remotely. The evidence in evidence/ comes from my own runs.

| # | Sev | Finding (short) | Verdict | What changed / evidence |
|---|---|---|---|---|
| C1 | P1 | Subject token alone threads mail from an unrelated sender into someone's case | FIXED | `Ingestor._find_case` accepts a subject token only from a participant; others open a new flagged case. tests `AuthenticatedThreading`; mutants `threading_not_authenticated`, `requester_not_a_participant`, `repliers_not_added_as_participants`. Residual: a message-id is still a capability (no signed token) |
| C2 | P1 | Arrival order corrupts status; earlier replies undercount running time | FIXED | new `reducer.py`: every status change and every customer message is an event; state is derived from (time, rank, sequence). tests `StatusReducer`; 6 reducer mutants |
| C3 | P1 | `headers=[1]`, `references=1` raise arbitrary exceptions | FIXED | `normalise` validates headers, references, in_reply_to, subject, body, from; fuzz test over 12 weird values x 11 fields never raises |
| C4 | P1 | Simulation audit passes with `tick() -> []` and a zero clock | FIXED | audit rewritten around an independent oracle (own per-minute business flags, own constants), consumes `tick()` notifications; must-fire controls for no alerts, cooldown 0 and 60, frozen clock, wall clock, no outbound index |
| C5 | P1 | `I never received your request, please send me your password` escapes the critical fail | FIXED | negation is clause-local (`CLAUSE_SPLIT`); test `RoundTwoCriticalTests`; mutants `negation_guard_removed`, `negation_spans_whole_sentence` |
| C6 | P1 | Human mail titled `Cannot fix delivery failure` and mail with `X-Auto-Response-Suppress` are discarded | FIXED | subject-only and suppress-only are weak evidence: kept, flagged in `ingestor.flagged` and `case["flags"]`. tests `BounceAndAutoReplyEvidence`; mutants `bounce_by_subject_alone`, `suppress_header_alone_is_auto`, `auto_subject_alone_is_auto` |
| C7 | P2 | CI workflow is nested and cannot be discovered; commands would run in the wrong directory | PARTIAL | `.github/workflows/test.yml` is valid when this folder is its own repo root (matrix now 3.9, 3.10, 3.12). For a monorepo, `ci/monorepo-workflow.yml` sets `working-directory` and a path filter. Not done: placing it at the monorepo root, because this pass may only touch the lab folder. Nothing has run on a remote |
| C8 | P2 | A reply delivered before its parent opens a second case forever | FIXED | `_pending` parent links; the parent joins the child's case and `created_at` moves to the earlier time. test `LateParent`; 2 mutants |
| C9 | P2 | Same-time `open` and `waiting_on_customer` give 180 or 60 minutes by arrival order | FIXED | reducer sorts by (time, rank, source sequence); `set_status(..., seq=)` accepts the source sequence. Documented limit: two agent events at one instant with no sequence resolve by arrival counter |
| C10 | P2 | Reactivation emits a notification inside the cooldown | FIXED | `Escalator` keeps the cooldown across clear and reactivate; tests `test_reactivation_inside_cooldown_is_silent`; mutants `reactivation_ignores_cooldown`, `reactivation_keeps_old_clock` |
| C11 | P2 | Agent reply stamped before the case existed gives a zero-minute response; naive times accepted | PARTIAL | `agent_reply` and `set_status` reject unknown cases, naive datetimes and times before `created_at`. Not changed: inbound mail with a naive date string is still read as UTC (documented in `parse_dt`), because real Date headers always carry an offset and rejecting would drop mail |
| C12 | P2 | `fr_target` replaced by 60, and customer-reply event kinds replaced by `created`, survive | FIXED | tests for due time and target per priority and for emitted event contents; mutants `due_at_constant_target`, `reply_event_kind_wrong`, `agent_event_kind_wrong` |
| C13 | P2 | `except Exception` lets a crashing naive constructor satisfy every inverted contract | FIXED | `run_inverted` accepts AssertionError, plus a KeyError-family crash only for the malformed-input test; meta-test proves a `RuntimeError` constructor is not credited |
| C14 | P2 | Label A03 credits resolution with no action and a wrong fact; inconsistent with B08 | FIXED (A only) | A03 resolution 1 to 0 in `qa_labels_a.json` (A is the tuning set, not frozen) with a note in the file; `docs/LABELING_RULES.md` added. The B16 and B26 disputes (Fable relabelling) are recorded there; B is frozen and was not edited |
| C15 | P2 | README says B was evaluated once after rules froze; git cannot show that | FIXED | README wording: B was committed before the first committed scorer; whether it was consulted earlier is not provable from git; B was re-scored after this fix pass, so it is a regression set |
| C16 | P2 | `Auto-Submitted: no; owner-email=...` discarded; DSN MIME report without English subject opens a case | FIXED | RFC 3834 token parsing (parameters and comments), `multipart/report; report-type=delivery-status` and `X-Failed-Recipients` detection. mutants `auto_submitted_parameters_not_parsed`, `delivery_status_report_ignored` |
| C17 | P2 | `From:` line empties the body; indented signature delimiter retains signature keywords | FIXED | `From:` is a quote header only when Sent/Date/To/Cc/Subject lines follow; delimiters are matched on the stripped line; raw body kept in `messages[i]["raw"]`. tests `QuotedTextStripping` |
| C18 | P2 | `capital` matches `api`; account keywords can outrank a hacking report | FIXED | word-boundary regexes with a short inflection suffix; `Router.precedence=("security",)`. tests `RoutingBoundaries`; 5 routing mutants |
| C19 | P2 | README seed-3 explanation wrong; "nine days" wrong | FIXED | explanation removed (the numbers changed with the new audit and the per-category claim is all the tests assert); window stated as arrivals in the first 4.5 days, hourly ticks for 13 days |
| C20 | P3 | Generic card and security-code examples in B | REJECTED | B16 uses "the 16 digits on your card" as a scorer-miss example; the critical-fail rule needs generic payment-secret vocabulary to be tested at all. No real card data, no employer or vendor material (`scripts/check-confidential.sh` output in evidence/). B is frozen. If the owner wants all card wording gone, that is an owner decision and would also remove the B16 miss |
| C21 | P3 | "The gap between A and B is the cost of tuning" is causal and unsupported | FIXED | README now says the gap is observed sample variation, possibly including tuning optimism, and that resolution agreement is higher on B |
| F1 | P1 | Reply to the agent's own outbound mail opens a second case | FIXED | `agent_reply(case, at, message_id=None)` registers a generated or given outbound id; the simulation now sends half its replies to outbound ids; contract test added (naive fails it) |
| F2 | P1 | Interleaved (bottom-posted) reply is stripped to nothing | FIXED | attribution lines are skipped, not terminal; text between quoted blocks is kept; contract test (naive fails it) |
| F3 | P1 | `Please enter your password on the sign-in page` zeroes a correct transcript | FIXED | secret requests must be aimed at the agent (`me/us/here/in chat/reply with/what is your`); critical-fail precision and recall now reported by `calibrate`. Known cost: `send me your current password` is MISSED (C05, recall 0.5 on set C), not tuned away |
| F4 | P1 | Alert invariant is vacuous (cooldown 0 and `tick() -> []` pass) | DUPLICATE of C4 | fixed under C4 |
| F5 | P1 | B is clean of regex tuning only: 14 of 90 turns copied from A, same template and author | PARTIAL | Accepted in full and documented. B is NOT rewritten, because it is a frozen file (instruction: do not edit frozen files in place); instead set C was written with no repeated agent turn (test enforced). Same author model labelled C, so C is not independent; a second human labeller is still the only real remedy |
| F6 | P1 | Workflow lives where GitHub never reads it | DUPLICATE of C7 | fixed under C7 |
| F7 | P2 | Anyone who knows a case number appends to it | DUPLICATE of C1 | fixed under C1 |
| F8 | P2 | Human mail silently dropped by bounce subject | DUPLICATE of C6 | fixed under C6; flagged mail is now visible in `flagged` |
| F9 | P2 | No address parsing: display-name mailer-daemon is human; requester stored with display string | FIXED | `email.utils.parseaddr` in `normalise`; tests |
| F10 | P2 | Substring keywords (`not urgent` is urgent; `rapid` routes technical) | DUPLICATE of C18 | fixed under C18, plus negation guard for `not urgent` and `non-urgent` |
| F11 | P2 | Late reply sets status open while SLA says met | DUPLICATE of C2 | fixed under C2 |
| F12 | P2 | Pause invariant one-sided: a frozen clock audits clean | DUPLICATE of C4 | fixed under C4 (elapsed compared with the oracle in both directions, three checkpoints) |
| F13 | P2 | Acronyms (API, HTTP, FAQ) count as shouting | FIXED | allowlist of common acronyms; test with API, HTTP, FAQ, PDF in one turn |
| F14 | P2 | Business hours use the tz in the inbound email, not the queue calendar | PARTIAL | `TicketFlow(tz_by_queue=...)` makes the queue calendar win. Default still reads the email `tz` field because the simulation uses it to model customers in several zones; real mail has no such field. Documented in README |
| F15 | P2 | Tests pin B disagreements, turning B into a tuning fixture | FIXED | only A is pinned exactly; B and C carry loose floors; README says B is a regression set |
| F16 | P2 | Naive alert count 150333 is an artefact of hourly ticks | FIXED | README reports the number with what it counts and states that its magnitude depends on tick frequency; the meaningful statement is "re-fires on every tick" |
| F17 | P2 | RFC 2822 Date headers rejected | FIXED | `parse_dt` falls back to `email.utils.parsedate_to_datetime`; tests |
| F18 | P3 | `I don't understand why...` passes tone; `dumb limitation` is abuse | FIXED | negation before a courtesy word cancels it; mild abuse words need a `you` target |
| F19 | P3 | Wrapped `On ... wrote:` attribution not matched | DUPLICATE of C17 | fixed under C17 |
| F20 | P3 | Pass/fail "agreement" is derived from the scorer's weights, not a human verdict | FIXED | renamed "derived verdict" in code docstring, eval output and README |
| F21 | P3 | Breach rate biased low by open cases | FIXED | breach rate computed per clock (`per_clock`) and summed |
| F22 | P3 | Reply Date and ingestion time are separate draws; no reply targets a waiting case | FIXED | one draw; replies now also target waiting cases (test asserts at least one per seed) |
| F23 | P3 | `mutate.py` miscounts with several bad patterns; crash-kills not classified | FIXED | all bad patterns counted; kills classified as assertion or import/syntax crash |
| F24 | P3 | HANDOFF cites check-confidential.sh with no saved output | FIXED | output saved to `evidence/check-confidential.txt` |

Counts are asserted by `tests/test_review_record.py`, which parses this table.

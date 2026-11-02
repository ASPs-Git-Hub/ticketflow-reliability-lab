# HANDOFF: ticketflow-reliability-lab

Status: BUILT_UNVERIFIED

- Branch: cc/webhook-rescue-lab (working tree changes, nothing committed by this pass; the orchestrator commits)
- Label set B commit: bf6056f "Freeze ticketflow QA label set B before code" (contains only data/qa_labels_b.json)
- Round 2 record: docs/REVIEW_ROUND2.md (45 findings: 32 fixed, 8 duplicates, 4 partial, 1 rejected)

## What changed in round 2

New `reducer.py` (status from event time, rank, sequence), authenticated and outbound-id threading,
late-parent reconciliation, strong-evidence bounce and auto-reply detection (RFC 3834 and 3464),
clause-local negation in the QA critical-fail rule, word-boundary routing, a simulation audit judged by
an independent oracle, set C, 111 mutants, a Python 3.9 compatible CI matrix.

## Results actually executed (my machine, 2026-11-03)

See evidence/ for the raw output. Tests: 212, pass on Python 3.14.6, 3.12 and 3.9.6. Simulation: fixed
zero in all nine categories, naive above zero in all nine. Mutation: see evidence/mutation-run.txt.
check-confidential.sh output: evidence/check-confidential.txt.

## Weaknesses

- One labeller (the author model) wrote the rubric, the rules and all three label sets. None is independent.
- B is a regression set: 14 of 90 turns repeat A, and the fix pass changed the scorer after B was first scored.
- C is a single 30-item sample; resolution agreement there is 22/30 (kappa 0.49).
- Kappa has no confidence interval. n = 30 per set: intervals about 20 points wide.
- A message-id is a threading capability; no signed reply token.
- The audit oracle is written by the same author as the implementation.
- Python 3.10 and the GitHub workflow never ran; the monorepo copy of the workflow is not in place.
- evidence/ was produced on my machine, not in CI.

## Next action

1. Copy ci/monorepo-workflow.yml to the monorepo root `.github/workflows/` (or push the lab as its own repo) and confirm the matrix is green.
2. Have a second person label set C blind to qa.py and to docs/LABELING_RULES.md, then report human-human and human-scorer kappa.
3. Add a bootstrap interval for kappa.
4. Decide with the owner whether to publish this folder; run scripts/check-confidential.sh again on the final tree.

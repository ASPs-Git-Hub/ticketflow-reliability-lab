# Real-data evaluation protocol v2: rerun after the merge fix (written 2026-11-03, before the rerun)

This file is written before any scored v2 run. It does not change `PROTOCOL.md`. It says what is
reused and what is the only thing that differs.

## Why a second run

Run 1 (`evidence/threading-full.txt`, `threading-frozen-sample.txt`) found that `src/ticketflow/ingest.py`
remembered one waiting case per missing parent id and had no step that merges cases. Two replies that
arrived before their shared parent therefore ended in two tickets. 56 of the 57 split conversations in
S1 seed 1 fit this. The code was then fixed (merge when the connecting message arrives) and this run
measures the fixed code.

## What stays exactly the same

- Data: the same 1618 messages from the python-dev archive (6 monthly files, checksums in
  `evidence/raw-sha256-threading.txt`, re-checked before this run) and the same frozen sample
  (`sample/threading_sample.jsonl`).
- Parsing, truth threads, header oracle, handicaps S0 to S3, subject damage, redelivery rate, header
  damage rates: unchanged. `scripts/threading_eval.py` and `scripts/diagnose_splits.py` are not edited.
  Their SHA-256 values are in `evidence/v2-protocol-sha256.txt`. Run 1 did not record script hashes, so "unchanged" rests on the file times (both last written before the run-1 results were saved) and on control 1 below.
- Seeds: 1 to 5 for S1 to S3; S0 is deterministic.
- Metrics: exact threads (all, and size 2+), wrong-thread messages, merged cases, split threads and
  extra cases, duplicates and surplus copies, late parents reconciled, match to the header oracle,
  lost messages. Same definitions.
- Systems: TicketFlow (fixed code), NaiveTicketFlow and SubjectBaseline. The last two are untouched, so
  their numbers must equal run 1. That is a control: if they differ, the data or the script changed.
- Command lines, as in run 1 (found by reproducing run 1 byte for byte, see control 1): full set
  `python3 -I scripts/threading_eval.py work/full.jsonl --handcheck`; frozen sample
  `python3 -I scripts/threading_eval.py sample/threading_sample.jsonl` (no flag);
  `python3 -I scripts/diagnose_splits.py work/full.jsonl`, plus `diagnose_splits.py` on the sample.

## What differs

Only the lab code under test: `ingest.py` and `flow.py` as changed for the merge fix (rules in the
docstring at the top of `ingest.py`). Nothing is tuned: the fix was designed from the diagnosis of run 1
and the synthetic tests in `tests/test_merge.py`, not by adjusting a setting until a number rose. I did
run the new code on this data while developing it, to check that it caused no wrong-thread message.
That is a regression check; it is stated here so the run below is not presented as a blind test.

## Controls before the scored run

1. Re-run the OLD code (the committed version of `ingest.py` and `flow.py`) on the full set. It must
   reproduce `evidence/threading-full.txt` line for line. This proves the data file and script are the
   ones of run 1. Output: `evidence/v2-old-code-reproduction.txt`.
2. Raw download checksums match `evidence/raw-sha256-threading.txt`.

## Safety rules that must still hold (a failure here is a failure of the fix, whatever the exact counts)

- Wrong-thread messages: 0 in every run, as in run 1.
- Lost messages: 0 (nothing rejected or ignored). Surplus copies: 0 with redelivery.
- All 212 older tests pass and the new merge tests pass. The mutation check is extended for the merge
  logic and must kill every mutant.
- The merge is triggered only by a message that connects cases (a missing parent that arrives, or a
  message whose own headers point to a case that already exists). A forged subject token from a sender
  who is not in the case never merges or attaches. These are pinned by tests.

## What I expect, written down now so it can be wrong

- S1 and S2: exact threads of size 2+ rise clearly, because two replies sharing a missing parent are the
  main cause of splits. I expect most but not all of the remaining gap to close, because a few
  conversations have a message that is the only link and arrives together with damage in S3.
- S3: rises less. Replies that lost In-Reply-To and References have no link left, and no program can
  rebuild them without reading the subject or body, which the protocol forbids the lab here (subjects
  are damaged and the safety rule says a subject alone never attaches mail). So S3 against the truth
  stays below S1. Against the header oracle (the best possible answer from the headers left) the lab
  should now match nearly all components.
- Reporting: every row of `threading-full.txt` before and after, the frozen sample before and after, the
  diagnosis script's categories before and after, and whatever still fails.

## Evidence files of this run

`evidence/v2-*.txt`: test run, mutation run, tests-on-old-code (new tests against the old source, which
must fail), old-code reproduction, full run, frozen-sample run, diagnosis runs, checksums.

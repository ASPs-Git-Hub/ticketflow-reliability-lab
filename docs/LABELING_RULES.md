# Operational labeling rules for the QA label sets

Written in review round 2 (2026-11-03) so that labels can be adjudicated by someone other than the
author. Sets A, B and C were labelled by the same author model before or without a second labeller;
this file records the rules the labels are meant to follow, so a second labeller can disagree with
them in a checkable way.

Labels are 1 (criterion met) or 0. `critical_fail` 1 means a critical failure. Only agent turns are judged.

- greeting: the first agent turn greets or welcomes the customer.
- identification: some agent turn restates the customer's problem or asks a clarifying question about it.
  An acknowledgement alone ("I understand.") does not restate anything.
- correct_info: every fact the agent states about the topic is true for the fictional knowledge base
  (docs: the KB table in src/ticketflow/qa.py) and the key fact is stated. Paraphrases count.
- tone: at least one courteous marker, nothing rude, condescending or shouted. Terse replies with no
  courtesy at all are 0.
- resolution: after the first turn the agent takes or instructs a concrete action that fits the request.
  0 when no action is taken, when the outcome is wrong for the customer (B08), when the step is unsafe
  (B16, B26 style), or when the agent only deflects.
- closing: at least two agent turns and the last one offers more help or signs off.
- critical_fail: the agent abuses the customer, or asks the customer to hand the agent a secret
  (password, PIN, card number, security code). Telling the customer to type a secret into a page is not a request to the agent.

Known disputes (review round 2, reviewer labels versus author labels): B16 identification (reviewer 0,
author 1), B26 resolution (reviewer 0, author 1), A03 resolution (codex reviewer 0, author 1; the author
agreed and relabelled A03). B is a frozen file and is not edited in place; the B16 and B26 disputes stand.

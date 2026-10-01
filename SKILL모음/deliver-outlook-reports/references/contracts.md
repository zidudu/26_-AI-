# Delivery ledger
Persist delivery_key UNIQUE, run_id, sender identity, recipients, subject/body hash, attachment path/hash/size, authorization context, state, intent_at, submitted_at, evidence and errors. Avoid logging credentials or unnecessary mail contents.

Transitions: prepared → intent → submitted → confirmed when independent evidence exists. prepared → failed_before_submission can retry after fixing a proven pre-send error. intent → unknown on a crash or ambiguous failure; submitted → unknown only when observed evidence warrants it. Marking intent before Send prevents silent duplicates but cannot provide exactly-once semantics across SQLite and an external mailbox.

Claim of sent-item presence must come from observing a matching item, using run marker plus account/recipient/attachment context as needed. A transport/message identifier alone may not confirm recipient delivery. Distinguish draft, submitted, present in sent folder and recipient delivery/read receipt.

ResolveAll or per-recipient resolution failure must prevent Send. Select the sender explicitly for multiple accounts. Recheck attachment hashes immediately before adding. Do not send to sample addresses copied from source code. A human-review flag on analyses is not a send gate unless explicitly enforced by policy and checked before intent.

COM cleanup must not close the user's unrelated Outlook session. Handle busy Office and security prompts as environmental states. Do not disable security controls to make automation work.

Origin: V10 worker mail intent handling and office.py. This skill includes a workflow contract, not a live mailbox connector or an unconditional send script.

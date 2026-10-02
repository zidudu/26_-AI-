# Stage and recovery contracts
- Run: run_id, request_id, config snapshot/hash, source catalog snapshot, timezone, start/end instants, mode, parent_run_id, timestamps and stage outcomes.
- Collection: source identity, item identity, original URL, published_at if known, observed_at, unchanged body_raw, media identities, capture references and capture status. Store normalized text separately.
- Analysis: source/version hash, provider/model/config identity, raw response reference, structured result, evidence references, structural validation status and semantic review status.
- Rendering: exact analysis snapshot and source versions, per-slide links, originals and derivatives, full report, summary outputs, raw notes and verification results.
- Delivery: approved recipients/account/attachments, artifact hashes, idempotency identity, intent timestamp and observed state. Submission is not delivery confirmation.

Use stage-specific statuses rather than a misleading single success flag. A source can be collected successfully while analysis is pending or preview rendering failed. An uncertain send must not enter an automatic retry queue.

Reference state edges: queued → running → completed/partial/failed; running → stopping → cancelled; abandoned running → interrupted after checking worker ownership. Track mail_unknown separately or as a delivery state. Terminal transitions must be durable.

Minimum boundary scenarios: one failed source among successful ones; missing cursor on a first run; duplicate request ID; source changed after analysis; renderer failed after analysis saved; cancel during an external call; process crash around mail submission; rebuild from a historical run.

Origin: reusable patterns extracted from cafe_monitoring V10 (distribution 1.0), service.py, worker.py and database.py. Do not inherit its automotive fields, site selectors, recipients or schedule defaults.

# API contract
Suggested endpoints: read settings/status/runs/artifacts; mutate settings/start/stop/login. Validate both browser-originated and raw HTTP requests. Accept only known Host and port combinations; compare Origin to the served origin when present; require a nonce/token for mutating requests even if Origin is absent. Reject non-finite numbers and unexpected types. Set request and body-read limits; avoid leaving unread oversized bodies on reusable connections. Never add permissive CORS for convenience.

Workers receive run IDs and necessary scoped configuration, not uncontrolled shell strings. Place secrets in supported secret storage or scoped child environment, never command lines or browser-visible config. Verify artifact path containment and hash before open/download.

Single-active-job protection: durable uniqueness or transactional lease plus OS/process ownership as needed. Thread locks alone cannot coordinate multiple processes. A stale PID may have been reused: record start identity or an owned process handle. Stop should wait for a bounded external call then checkpoint; terminate owned unresponsive children according to explicit policy.

Scheduler policy example: named timezone; weekday-only; do not replay missed times before startup; persist due marker before attempting launch; busy or login-in-progress means recorded skip with no automatic retry. This is one policy, not a universal default. If catch-up is needed, specify maximum backlog and order. On DST ambiguous/nonexistent local times choose once-per-wall-day with an explicit offset policy, or use UTC schedules.

Persisting the due marker before launch favors at-most-one attempt but can miss a run after a crash. Persisting it afterwards can duplicate attempts. Use a durable enqueue transaction keyed by schedule_id + due instant when stronger recovery is required; then let the worker's idempotency key deduplicate.

Origin: V10 __main__.py, service.py, worker.py and web UI contracts.

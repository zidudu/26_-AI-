# Incremental collection contract
Use source_key + item_key as identity; retain canonical_url plus observed URL if different. Collection completeness must describe pagination, sorting and published-time availability. Advance a watermark only after the adapter establishes completeness for that interval. If it cannot, record a partial result.

The bundled planner expects a JSON object mapping selected source keys to timezone-aware ISO timestamps (null means absent). `recent` and `custom` return one common window; cursor mode returns per-source windows. Overlap is elapsed hours. Flooring the end to a minute is an explicit utility behavior; use timezone-aware datetime in the adapter too. Store UTC instants and display in the user's named timezone. Calendar-day scheduling and elapsed 24 hours differ around DST.

Throttling design: acquire a shared asynchronous lock; use a monotonic clock to wait until last_grant + interval; release a grant, then navigate. A semaphore controls the number of active source tasks but does not impose a global request rate. Check cancellation during waits. Use bounded retries for transient transport failures; honor server backoff; never loop on blocked or unauthorized responses.

Session design: use a dedicated persistent browser profile, profile lock, explicit login-in-progress state, and identity binding to resolved profile path + browser. Keep login status separate from current website access. On Windows, use OS-protected secret storage for supplemental session caches; do not fall back to plaintext when encryption fails. Never package cookies, profiles or tokens, print them in logs, or treat a remembered flag as proof of authentication. Native DPAPI caches are machine/user-bound and are not portable backups.

Capture manifest: source_key, item_key, version hash, capture time, original file hash/size, viewport/fullpage mode, content selector, capture status. Preserve article identity through redirects. Distinguish missing capture from missing text. Long-page segmentation must retain ordering and overlap conventions.

Origin: V10 service.period, naver_session.py and engine/v9/collection_control.py; the Naver-specific selectors and cookie names are intentionally excluded.

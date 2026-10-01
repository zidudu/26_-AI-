# Persistence contracts
Suggested production tables: records UNIQUE(source_key,item_key); versions UNIQUE(record_id,fingerprint); observations UNIQUE(run_id,record_id) with version_id, matched_terms, disposition; analyses bound to version/config; review events bound to analysis; runs UNIQUE(request_id); artifacts(path,sha256,size,kind); source cursors; mail intents; import ledger.

Keep first_collected as the earliest observation and last_checked as the latest. A reversion to previously seen content may legitimately point to an older version ID: latest means most recently observed content, not maximum version ID. Serialize writes and compare observation instants. Do not copy a review approval to a different analysis/version.

The reference script uses canonical UTF-8 JSON, sorted object keys and deterministic media arrays as supplied. Media order is significant. Timestamps are normalized to UTC before comparison. Same-run/same-record observations are upserted only when the incoming observation is no older. A changed record in the same run may add an immutable version. Configure connection busy timeout and use SQLite backup API; never copy only a live .db file with a pending WAL.

Artifact paths must resolve under a selected trusted root. Reject traversal and symlink escape. For foreign-platform paths, parse using the originating path syntax; use explicit relocation mappings rather than arbitrary basename matches. Verify size/hash before opening or attaching, and fail closed on unexpected changes.

Import ledger: source_ref + input_fingerprint → destination run/version identifiers, result counts, errors. Repeated identical input skips; changed input creates a new snapshot. Never derive incremental cursors from an incomplete legacy folder or inferred date.

Origin: V10 schema.sql, database.py, migrate.py. The bundled script implements the record/version/observation core only; production artifact, analysis and delivery ledgers must be added for the chosen application.

# Evidence format and cache contract
Sources file: JSON array of objects `{id, body_raw}`. IDs must be unique nonempty strings. Raw body must be a string, including an intentionally empty body.
Analysis file: `{claims: [{id, text, evidence: [{source_id, source_sha256, start, end, quote}]}]}`. Claim IDs are unique; each claim needs at least one evidence entry. Offsets are Python Unicode code-point offsets into the exact body_raw, start inclusive/end exclusive. Do not use UTF-8 byte or JavaScript UTF-16 offsets without conversion. Each nonempty quote must equal body_raw[start:end]; the hash is lowercase SHA-256 of exact UTF-8 bytes. Normalizing line endings invalidates the binding unless normalization is an explicit versioned input transform.

The helper checks this evidence format, references, bounds and hashes. It does NOT check whether the quote supports the claim, the document is truthful, sentiment is correct, or a domain JSON schema passes. Test a semantically unsupported claim whose quote matches to demonstrate this distinction.

Separate first-person experience from hearsay; separate an asserted defect from a question, general advice or a quoted third party. Keep domain rules in a versioned schema/reference, not hardcoded automotive vocabulary in a generic adapter.

Cache key material: source identity/content hash, segmentation version, schema version, system/task prompt hash, provider identity, actual model identifier and inference options affecting output. Never use title or filename alone. Record compatibility and invalidation reasons. A failed source-hash check invalidates the result; it is not a warning that can be ignored before reporting.

Source-grounded summarization can need an additional provider call during report assembly. Keep provider lifetime through all callbacks or resolve all AI results first and freeze them before rendering. A report-only rebuild must use saved results and must not silently trigger fresh analysis.

Origin: V10 engine/v9 analysis_engine.py and analysis_stage.py. No original proprietary source text, credentials or model defaults are embedded.

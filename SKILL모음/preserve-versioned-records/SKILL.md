---
name: preserve-versioned-records
description: 수집 문서의 원문 보존, 변경 감지, SQLite 실행 이력, 검토 이력, 산출물 해시, 기존 데이터 가져오기와 백업이 필요할 때 사용한다. 원문·문서 버전·실행 관측을 분리하고 중복과 덮어쓰기를 방지한다.
---

# 원문·버전·실행 이력 보존

## Workflow
1. Inspect current schema and backup/restore paths before changing persistence. Read [schema and migration contracts](references/contracts.md).
2. Separate logical records, immutable content versions and run observations. Define canonical fingerprint fields explicitly: title, raw body, published timestamp and media identities are a useful baseline. Exclude volatile request timestamps.
3. Use `scripts/version_store.py` for a small reference SQLite store: `ingest --db DB --input records.json --run-id ID`, `backup --db DB --output COPY`. The JSON array requires source, id, title, body_raw, published, media, observed_at. It is a reference store, not a drop-in migration of an existing database.
4. Preserve raw bytes/text; use separate derived columns. Track analysis and review against exact versions. Keep run-specific matches apart from cumulative keyword relationships.
5. Import legacy data read-only. Check identities, source hashes, paths and schema versions, and back up the destination first. Make re-import idempotent using source identifiers plus content hashes. Report missing or unverifiable artifacts without fabricating verification.
6. Validate backup integrity and a restore into a separate temporary location. Back up external artifacts separately: a database backup alone does not include reports or images.

Test unchanged input, changed content, an older historical observation, repeated imports and transaction rollback. Report migration counts and rejected records; never silently overwrite a newer latest pointer with an older import.

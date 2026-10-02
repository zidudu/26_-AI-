---
name: build-local-automation-console
description: 반복 자동화를 로컬 웹 화면에서 실행·중지·예약하고 상태와 로그를 조회하는 운영 도구를 만들 때 사용한다. 루프백 API, 실행 토큰, 단일 작업 제약, 워커 분리, 시간대·예약 누락 정책을 다룬다.
---

# 로컬 운영 화면과 예약 실행

## Workflow
1. Inspect the current backend, worker and UI. Define run modes and state transitions before adding controls. Read [API and scheduling contract](references/contracts.md).
2. Bind to loopback by default. Protect mutating endpoints with strict Host/Origin checks and a per-session token. Apply body limits, strict JSON and clear errors; do not expose secrets in settings responses.
3. Separate UI/API from long-running workers. Persist a frozen config and request ID before spawning; enforce a single active job transactionally where required. Do not rely only on disabling the Run button.
4. Implement cooperative stop and progress snapshots. Distinguish stop requested from stopped. Recover abandoned runs only after checking actual worker ownership/liveness.
5. Define timezone, weekdays, DST behavior, missed-run policy, busy/login policy and retry rules for scheduling. Use persisted due identifiers to prevent duplicate attempts. Never silently enable a recurring schedule while saving unrelated settings.
6. Show actual runtime dependencies, session access, partial failures, artifacts and historical runs. Make report-only and export operations preserve their original context.
7. Exercise duplicate requests, malformed/oversized JSON, foreign Origin/Host, a busy worker, restart around a scheduled due time and stop during a bounded external call.

Deliver usable controls and the implemented scheduling policy. Keep implementation/security details in developer documentation unless the user needs them to act. Do not present loopback access controls as authentication suitable for public hosting.

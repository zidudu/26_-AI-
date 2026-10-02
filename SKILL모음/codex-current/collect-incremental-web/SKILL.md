---
name: collect-incremental-web
description: 웹사이트·게시판·뉴스의 반복 수집, 마지막 성공 이후 증분 수집, 로그인 세션 유지, 원문과 캡처 연결이 필요할 때 사용한다. 기간 경계, 사이트별 커서, 동시성·요청 간격, 인증 실패를 다룬다. 사이트별 접근 수단과 선택자는 별도로 확인한다.
---

# 증분 웹 수집과 로그인 관리

## Workflow
1. Identify sources and authorized access methods. Prefer supported APIs/connectors. Use a browser only under the environment's browser/session rules. Never bypass access controls or anti-bot checks.
2. Specify stable source/item keys and timestamp semantics. Read [collection contracts](references/contracts.md) before implementing cursors, sessions or captures.
3. Plan aware, half-open intervals `[start, end)`. Freeze the end at run creation. Use `scripts/plan_window.py --help` to calculate recent, custom or cursor windows. The helper plans windows only; it neither logs in nor collects data.
4. On first use of cursor mode, require an explicit initial window when any selected source lacks a cursor. Subtract a configurable overlap to catch delayed records. Deduplicate by item/version, not title.
5. Limit concurrent source jobs and separately gate request start times across those jobs. Persist per-source success; do not advance failed-source cursors or normal cursors for custom historical runs.
6. Bind text and screenshots to the same article identity. Preserve raw text and original images, store cleaned analysis text separately, and record unavailable bodies/captures explicitly.
7. Stop the shared run on authentication expiry or access denial requiring user action. Provide an actionable state, preserving successful sources and resumable work.

Deliver the implemented source adapter, period/cursor contract and collection manifest. Validate overlap duplicates, exact end-boundary exclusion, one failed source, missing first cursor and session expiry. Do not claim a generic selector works on an uninspected site.

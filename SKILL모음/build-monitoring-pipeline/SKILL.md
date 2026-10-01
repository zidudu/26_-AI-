---
name: build-monitoring-pipeline
description: 지속적인 모니터링·수집·AI 분석·보고·발송을 연결하는 자동화를 설계하거나 기존 파이프라인을 분리할 때 사용한다. 실행 모드, 단계 계약, 부분 실패, 재실행과 산출물 추적을 정의한다. 단순 일회성 검색에는 사용하지 않는다.
---

# 범용 모니터링 자동화 설계

## Workflow
1. Inspect the actual entry points, persisted data and available execution environment. Map each user-visible function to code or a verified artifact. Separate implemented behavior from desired changes.
2. Define the collection unit, stable identity, timezone, period, source permissions and outputs. Reuse existing session authorization; do not ask again for authorized work.
3. Read [stage contracts](references/contracts.md). Freeze settings, selected sources and the period into an immutable run snapshot before launching work.
4. Separate collect, analyze, render and deliver. Give each a typed input/output contract, checkpoint, retry policy and artifact manifest. Make each downstream stage consume persisted upstream artifacts.
5. Define full run, collection-only, analysis-only and report-only modes according to the user's needs. Require a source run when skipping upstream stages. Do not silently run AI or send mail during report rebuilding.
6. Design cancellation between bounded external operations, restart recovery and partial results. Advance only successful source cursors; keep later analysis/report/mail outcomes independent.
7. Implement the smallest useful pipeline in the existing project, with fixtures for failure boundaries. Use compact Mermaid only when branching or state transitions clarify the design.

## Route specialist work
Use the corresponding skill by name if available; locate it by frontmatter rather than assuming a folder name.

| Concern | Skill |
|---|---|
| Periods, sessions, throttling | collect-incremental-web |
| Raw versions, migration, backup | preserve-versioned-records |
| Evidence and analysis cache | analyze-evidence-records |
| CLI provider adapter | integrate-cli-ai |
| Source-linked slides and notes | create-evidence-ppt |
| Placement-based image sizing | optimize-report-images |
| Mail intent and uncertainty | deliver-outlook-reports |
| Local API, worker, scheduler | build-local-automation-console |
| Counts, filters, spreadsheets | export-monitoring-analytics |
| Distribution and documentation | package-windows-automation |

Deliver working changes or concrete contracts as requested, a concise runbook, and evidence of the checks actually performed. Do not present this skill pack as a ready-installed crawler or Office runtime.

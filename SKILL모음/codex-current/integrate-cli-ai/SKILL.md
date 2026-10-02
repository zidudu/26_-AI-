---
name: integrate-cli-ai
description: Codex 등 CLI 기반 AI 도구를 Python·데스크톱 자동화에 연결하거나 로그인·프로세스 종료·JSON 응답 문제를 해결할 때 사용한다. 설치 버전과 지원 기능 확인, 비대화식 실행, 인증 상태, 타임아웃과 자식 프로세스 수명을 다룬다.
---

# CLI AI 실행 연동

## Workflow
1. Inspect the executable path, installed version, authentication mode and documented noninteractive interface in the target environment. Read [adapter lifecycle](references/contracts.md). Consult current official docs if local help is insufficient; never assume a flag or model from the original app remains supported.
2. Define an adapter contract for capabilities, auth status, analyze, cancel and close. Pass argument arrays without a shell, use a known cwd and constrain credentials to the process environment or provider-supported store.
3. Separate login from inference. Reuse successful authentication; avoid duplicate login windows. Give an observed auth wall an explicit state and follow the environment's account-selection/handoff rules. Do not expose token files or device codes in retained logs.
4. Capture stdout and stderr with bounded retained output while draining pipes. Track process creation, output parsing, process completion and cleanup separately; a valid JSON result does not prove successful termination.
5. Apply separate time limits for auth status, interactive login, inference and cleanup. Poll cancellation, terminate only processes owned by this adapter, and preserve diagnostic exit status.
6. Parse provider envelopes, store raw responses and validate the requested schema. Retain the provider until all asynchronous analysis/summary callbacks finish; close it deterministically afterwards.
7. Exercise a synthetic executable that emits valid JSON, malformed JSON, stderr noise, nonzero exits, delayed exit and a hung child. Assert no orphan and no secret leakage. Report live provider/Windows tests separately from synthetic coverage.

Deliver a working adapter in the user's project, capability report and failure-state mapping. This workflow does not install a CLI, authenticate an account or choose a paid model without task context.

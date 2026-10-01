---
name: package-windows-automation
description: Python·브라우저·Office를 사용하는 Windows 자동화 프로그램을 소스 폴더·ZIP으로 배포하거나 설치 안내를 정비할 때 사용한다. 의존성, 사전 점검, 깨끗한 패키징, ZIP 안전성·해시·내용 일치와 검증 수준을 다룬다.
---

# Windows 자동화 배포와 검증

## Workflow
1. Inventory code, entry points, dependency versions, external software and generated/state directories. Read [distribution contract](references/contracts.md). Inspect the existing archive before deciding to refactor: unpacking a ZIP and restructuring source are different requests.
2. Build an explicit clean staging directory. Include required source modules/resources, setup/run/verify scripts and honest documentation. Exclude profiles, cookies, tokens, personal settings, databases, logs, reports, caches and virtual environments unless specific sanitized fixtures are intended.
3. Document Python and external runtime requirements separately. Install Python requirements in a venv; probe Windows/Office/browser/CLI requirements without sending mail or triggering paid analysis. Do not claim pip installs desktop Office or the provider CLI.
4. Run `scripts/verify_zip.py --zip PACKAGE.zip --source STAGING` to compare every file's path/size/SHA-256. It rejects duplicate/traversing paths, symlinks and oversized expansions without extracting. Use its JSON as verification evidence.
5. Test from a clean path with spaces and, where relevant, non-ASCII names. Separate unit/synthetic checks, target-Windows integration, actual collection, real Office rendering and real delivery evidence.
6. Write setup/run/troubleshooting instructions from actual commands and observed behavior. Add authentic screenshots with captions and provenance; mark historical screens. Use compact Mermaid for meaningful branching or state flow only.

Deliver clean source/ZIP as requested, checksums, file inventory and accurate validation limits. Never change original application behavior just to make the documentation claim success.

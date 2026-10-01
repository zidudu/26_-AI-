# 카페 모니터링에서 추출한 범용 스킬 11개

패키지 작성일: 2026-10-02 (한국시간)

카페 모니터링 V10 / 배포용 1.0에서 다른 업무에도 재사용할 수 있는 기능을 정리한 스킬 모음입니다. 특정 카페·자동차 분야의 고정값을 제외하고 수집, 저장, 분석, 보고, 발송, 운영, 배포 절차를 담았습니다.

각 스킬을 **압축 해제된 독립 폴더**로 제공합니다. 아래 목록에서 지침과 코드를 바로 열어볼 수 있습니다.

## 파일 다운로드

[11개 스킬 ZIP 다운로드](monitoring-skills-11_20261002.zip) · [ZIP SHA-256](SHA256SUMS.txt)

## 구성

- 스킬 11개: 각 폴더의 `SKILL.md`가 핵심 지침입니다.
- `references/contracts.md`: 입력·출력, 실패 처리와 검증 기준입니다.
- `agents/openai.yaml` 및 `assets/icon.svg`: 표시 정보와 아이콘입니다.
- 실행 보조 스크립트 6개: 기간 계산, SQLite 버전 저장·백업, 근거 검사, 이미지 최적화, Excel 내보내기, ZIP 내용 검증입니다.
- [CONTENTS.json](CONTENTS.json): 압축에서 꺼낸 스킬 파일 50개의 크기·SHA-256입니다.
- [SHA256SUMS.txt](SHA256SUMS.txt): 현재 폴더의 스킬 파일·안내·목록·ZIP 체크섬입니다. 체크섬 파일 자신은 제외합니다.

## 스킬 목록

| 스킬 폴더 | 지침 바로 열기 | 기능 |
|---|---|---|
| [build-monitoring-pipeline](build-monitoring-pipeline/) | [범용 모니터링 자동화 설계](build-monitoring-pipeline/SKILL.md) | 수집부터 분석·보고·이력까지 재사용 가능한 자동화 흐름을 설계합니다. |
| [collect-incremental-web](collect-incremental-web/) | [증분 웹 수집과 로그인 관리](collect-incremental-web/SKILL.md) | 기간·커서·중복·요청 속도를 관리하며 반복 웹 수집을 구성합니다. |
| [integrate-cli-ai](integrate-cli-ai/) | [CLI AI 실행 연동](integrate-cli-ai/SKILL.md) | CLI 인증·프로세스·시간 제한·구조화 응답을 안정적으로 연결합니다. |
| [build-local-automation-console](build-local-automation-console/) | [로컬 운영 화면과 예약 실행](build-local-automation-console/SKILL.md) | 로컬 제어 화면·별도 워커·실행 상태·예약 중복 방지를 설계합니다. |
| [preserve-versioned-records](preserve-versioned-records/) | [원문·버전·실행 이력 보존](preserve-versioned-records/SKILL.md) | 원문과 변경 버전을 보존하고 실행 이력·백업·가져오기를 설계합니다. |
| [analyze-evidence-records](analyze-evidence-records/) | [근거가 연결된 AI 분석](analyze-evidence-records/SKILL.md) | 원문 해시와 인용 근거를 확인해 구조화된 AI 분석을 검증합니다. |
| [create-evidence-ppt](create-evidence-ppt/) | [원문 근거가 담긴 PPT 보고서](create-evidence-ppt/SKILL.md) | 원문 캡처·분석·발표자 노트를 연결하고 보고서를 재생성합니다. |
| [optimize-report-images](optimize-report-images/) | [보고서 이미지 용량 최적화](optimize-report-images/SKILL.md) | 원본을 보존하면서 실제 배치 크기에 맞춰 이미지 해상도를 줄입니다. |
| [export-monitoring-analytics](export-monitoring-analytics/) | [모니터링 통계와 Excel 내보내기](export-monitoring-analytics/SKILL.md) | 문서 수와 키워드 매칭 수를 구분해 필터가 명확한 Excel을 만듭니다. |
| [package-windows-automation](package-windows-automation/) | [Windows 자동화 배포와 검증](package-windows-automation/SKILL.md) | 실행·설치·검증 경로를 정리하고 민감정보 없는 배포본을 만듭니다. |
| [deliver-outlook-reports](deliver-outlook-reports/) | [Outlook 보고서 발송 관리](deliver-outlook-reports/SKILL.md) | 수신자·첨부·발송 이력을 확인하고 불확실한 중복 발송을 막습니다. |

## 사용 방법

1. 위 목록에서 스킬 이름을 누르면 GitHub에서 `SKILL.md`를 바로 읽을 수 있습니다. 폴더 링크에서는 참고자료와 스크립트도 확인할 수 있습니다. 로컬에서 쓰려면 전체 ZIP을 내려받아 해제합니다.
2. 사용할 스킬의 `SKILL.md`와 연결된 참고자료를 읽거나, 스킬을 지원하는 도구에 해당 폴더 전체를 등록합니다. 지침만 복사하면 스크립트·참고자료 연결이 빠집니다.
3. 전체 업무 설계는 `build-monitoring-pipeline`부터 시작합니다. 개별 작업에는 해당 스킬을 선택합니다.
4. Python 스크립트는 각 스킬 폴더 기준으로 `python scripts/스크립트명.py --help`를 실행해 인자를 확인합니다.

예시 요청: “build-monitoring-pipeline 스킬로 쇼핑몰 리뷰 수집·분석·보고 과정을 구성해 주세요.”

## 실행 조건과 검증 범위

- 이 폴더와 ZIP은 스킬 지침과 보조 코드 모음입니다. 전체 카페 수집기나 Office 실행 환경을 포함하지 않습니다.
- Python 보조 코드 중 이미지 최적화에는 Pillow, Excel 내보내기에는 openpyxl이 필요합니다. 나머지 4개 보조 스크립트는 표준 라이브러리를 사용합니다.
- 실제 사이트 수집에는 해당 사이트용 연결·접근 권한·선택자 확인이 필요합니다.
- PowerPoint/Outlook COM 작업에는 해당 프로그램이 설치된 Windows 환경이 필요합니다. 실제 발송은 사용자 지시에 따라 실행합니다.
- 제작 시 41개 동작 검사와 가상 자료 기반 원문/분석, 보고서/패키징, 중단/예약 복구 시나리오를 검증했습니다. Windows·Office·실제 메일 발송을 이번 스킬 패키지로 종단 간 검증한 것은 아닙니다.
- 인용 위치·해시 검사는 원문 연결을 확인합니다. AI 판단의 의미적 정확성을 보장하지 않습니다.

원본 프로그램: https://github.com/zidudu/26_-AI-/tree/main/20261001_cafe_monitoring_v1.0

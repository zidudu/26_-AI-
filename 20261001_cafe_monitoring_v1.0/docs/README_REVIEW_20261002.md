# README 재작성 및 배포 구성 확인

검토일: 2026-10-02 (KST). 기준 커밋: `f33d78c765f823e4f8a4196601c36afab9fcc4b5`.

## 작업 범위

프로그램 소개·기능·설치·실행 안내·다이어그램을 다시 작성했다. 프로그램 내부 코드를 재배치하거나 리팩터링하지 않았다. 기존 개발 문서와 결과보고서, 배포 ZIP을 보존하고 사용자에게 필요한 안내를 프로젝트 README에 정리했다.

## 코드 대조 결과

| 항목 | 정정·보완한 설명 | 확인한 파일 |
| --- | --- | --- |
| 독립 설치 | ZIP의 엔진으로 시작하므로 기존 V5 폴더가 필수 아님 | `v10/engine.py`, `00_README_KO.md` |
| 버전 | 배포명 Version 1.0, 내부 V10/10.0.0 | `v10/__init__.py`, `00_README_KO.md` |
| 검색 범위 | 제목 키워드 검색으로 찾은 글의 본문을 수집 | `engine/v754/collect/search.py`, `docs/ARCHITECTURE_KO.md` |
| 증분 수집 | 마지막 완료 지점 사용은 ‘이전 실행 이후’에 적용. 최근 24/48시간 및 직접 지정과 구분 | `v10/service.py: Service.period` |
| 중복 제외 | 직접 지정한 기간에서 중복 제외 옵션을 켜고 직전 지문이 같은 경우 이번 결과에서 제외 | `v10/worker.py`, `v10/database.py: Database.store_article` |
| 원문 버전 | 본문뿐 아니라 제목·게시 시각·미디어도 지문에 포함. 같은 지문은 재사용 | `v10/database.py: Database.store_article` |
| 수정 감지 | 해당 글이 다시 수집되어야 변경을 관측. 전체 과거 글의 지속 감시와 구분 | `v10/service.py`, `v10/engine.py` |
| 수집 완료 지점 | 카페별 관리, 후속 분석·Office 실패와 분리. 직접 지정 기간은 갱신 안 함 | `v10/worker.py`, `v10/database.py: Database.cafe_outcome` |
| 기본 설정 | 카페 9개·키워드 18개, 예약·메일 ON. 예약은 설정 저장 후 동작 | `v10/settings.py`, `v10/service.py: Service.schedule_tick` |
| 로그인 | 네이버 전용 Chrome 종료 후 완료 확인. Codex는 버튼에서 CLI 로그인과 상태 확인 | `v10/service.py`, `v10/codex_auth.py`, `web/integration.js` |
| AI 검증 | 형식·근거·의미 규칙 검사를 실제 의미 정확성 보증과 구분. 무결성 문제와 분석 실패는 동일하게 취급하지 않음 | `engine/v9/analysis_engine.py`, `engine/v8/analysis_schema.py` |
| 원문 노트 | 옵션이 켜지면 분석에 연결된 raw 본문을 기존 노트 뒤에 추가 | `engine/v9/ppt_notes.py` |
| 발송 | 사람 검토 완료 여부를 필수 승인 조건으로 삼지 않음. Send 성공과 실제 배달을 구분 | `v10/worker.py`, `v10/office.py` |
| PPT 재생성 | 전용 동작에서 수집·AI·메일을 OFF로 지정하고 저장 자료 사용 | `web/base.js: doPptOnly`, `v10/worker.py` |
| 통계 날짜 | 최초 수집일(KST), 게시 시각과 별도. 키워드 매칭 합계와 고유 글 수 구분 | `v10/database.py`, `web/base.js: statsExportWorkbook` |
| 예약 | 서버 내부 KST 예약. 서버 시작 전 놓친 예약은 소급 실행하지 않음 | `v10/service.py: Service.schedule_tick` |
| 설치 검사 | 설치·검증 BAT는 Python 검사 실행. JS 검사는 별도 | `01_setup_v10.bat`, `03_verify_v10.bat` |

표의 프로그램 경로는 모두 [`../cafe_monitoring/`](../cafe_monitoring/) 기준이다.

## ZIP과 압축 해제본

원본: [`cafe_monitoring_1.0_20260930.zip`](../releases/cafe_monitoring_1.0_20260930.zip)

- ZIP의 최상위 폴더: `cafe_monitoring/`
- 디렉터리 항목을 제외한 파일: **356개**
- 공개된 `cafe_monitoring/`와 바이트 비교: **누락 0, 내용 차이 0, 추가 파일 0** (테스트 캐시 생성 전 비교)
- ZIP SHA-256: `43961f71bf8deb098da72e602908924284965af2f05f33fcba4e88926fa07b63`

이미 압축 해제본이 정상 공개되어 있어 중복 폴더를 만들거나 원본 ZIP을 삭제하지 않았다. [체크섬 파일](../SHA256SUMS.txt)을 추가해 배포 파일을 확인할 수 있도록 했다.

## 이번 검증과 과거 검증의 구분

2026-10-02 Linux에서 다음 명령을 프로그램 폴더 기준으로 실행했다.

```bash
python -m unittest discover -s tests -v
node --test tests/test_ui_contract.cjs
```

결과: Python **75개 중 74개 통과·Windows DPAPI 1개 제외**, JavaScript 계약 검사 **49개 통과**. Node 테스트 실행기에는 테스트 파일 1개로 표시되지만 파일 내부 검사항목은 49개다.

첫 Python 실행은 현재 검토 환경에 `jsonschema`가 없어 Codex 관련 10개 테스트에서 import 오류가 발생했다. 배포 요구사항에 명시된 `jsonschema==4.26.0`을 임시 검사 경로에 설치한 뒤 동일 검사를 재실행해 위 결과를 얻었다. 프로그램·의존성 선언·배포 ZIP은 수정하지 않았다. 실제 로그인·수집·AI 분석·Office·메일 작업은 이 검사에서 실행하지 않았다.

2026-09-30 Windows의 75개 검사 통과와 9개 카페·16건·PPT 30장·30분 45초 결과는 [배포 당시 안내](../cafe_monitoring/00_README_KO.md) 및 개발 세션에서 확인한 **RC1.7의 과거 실사용 기록**이다. 이번 Linux 재검사로 Windows 통합 동작을 새로 검증했다고 표현하지 않았다.

## 문서 구성

- 프로젝트 README: 프로그램 소개, 작업 흐름과 내부 구조, 설치·로그인·실행, 동작 조건, 검증 범위.
- [화면 안내](SCREENSHOTS.md): 탭과 기능별 캡처, 초기 화면과 과거 운영 화면의 구분.
- 이 문서: 설명 정정 근거, 배포본 동일성, 이번 검사 결과.
- 기존 `cafe_monitoring/docs/`: 배포본에 포함된 개발 당시 상세 기술 문서와 RC 수정 이력 보존.

새 PC 최초 설치, 장시간 예약, 전체 AI 분석의 의미 정확성, 과거 실행의 ‘일부 확인 필요’ 원인 확정은 이번 문서 작업의 검증 범위에 포함되지 않는다.

## 화면 재촬영 시도

이번 환경에서 별도 새 DB로 로컬 서버가 시작되는 것까지 확인했다. Browser 플러그인/스킬이 없어 일반 Playwright 경로를 준비했으나 Chromium 다운로드가 손상된 ZIP 오류로 실패해 브라우저를 실행하지 못했다. 이번 작업에서 새 UI 캡처나 렌더링 검증을 완료했다고 주장하지 않는다. 기존 캡처 18장을 보존하고 촬영 시점과 의미를 새 화면 안내에 명시했다. 실제 계정 로그인·수집·AI 호출·메일 발송은 하지 않았다.

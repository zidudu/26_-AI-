# Version 1.0 화면 안내

[프로그램 소개·설치 안내](../README.md)

## 캡처 기준

이 문서는 저장소에 있던 18장의 캡처를 기능별로 다시 정리했습니다.

| 구분 | 촬영 기록과 의미 |
| --- | --- |
| Version 1.0 화면 14장 | 2026-10-01, 배포 소스를 로컬 서버와 새 DB로 실행한 기능 소개 화면. 수집·분석·Office·메일 실적을 나타내지 않습니다. |
| 개발 당시 Windows 화면 4장 | 2026-09-30, V10 개발·검증 중 촬영. 해당 시점의 UI·설정이며 일부 인증 안내는 정식 배포본과 다릅니다. |

2026-10-02 정비에서는 아래 기존 이미지를 재사용했습니다. 새로 촬영한 화면으로 표시하지 않습니다.

## 운영과 수집 대상

### 운영 탭

왼쪽에서 카페·키워드·설정을 지정하고 오른쪽에서 실행 상태, 로그, PPT 미리보기와 게시글을 확인합니다. 초기 DB여서 실행 기록과 결과는 없습니다.

![운영 탭](../screenshots/05-version-1-operation.png)

### 카페 관리

카페를 추가하고 표시 이름·구분명·주소 및 수집 사용 여부를 관리합니다. 수집을 중단해도 과거 데이터는 유지합니다.

![카페 관리](../screenshots/06-cafe-manager.png)

### 키워드 관리

검색 키워드를 추가·검색하고 사용 여부를 관리합니다. 현재 설정과 과거 실행의 키워드 기록은 구분합니다.

![키워드 관리](../screenshots/07-keyword-manager.png)

### 수집 기간

최근 24/48시간, 이전 실행 이후 또는 시작·종료 시각 직접 지정을 선택합니다. 이전 실행 이후를 쓰려면 카페별 최초 수집 완료 기록이 필요합니다.

![수집 기간](../screenshots/08-collection-period.png)

### 예약 설정과 도움말

한국 시간 기준 실행 시각과 주말 제외를 설정합니다. 서버 실행 창과 PC가 켜져 있고 깨어 있는 동안 동작합니다.

![예약 설정과 도움말](../screenshots/09-schedule-help.png)

## AI·보고서·발송

### OpenAI API 설정

Codex와 OpenAI 중 공급자를 선택합니다. 화면에 입력한 API 키는 서버 메모리에만 보관합니다. 캡처에는 실제 키를 입력하지 않았습니다.

![OpenAI API 설정](../screenshots/10-openai-api-settings.png)

### PPT와 Outlook 설정

이미지 PPI, 카페별 요약, 원문 노트, To·CC, 전체/요약 보고서 발송을 지정합니다. PPI는 표시 크기에 맞춰 이미지 해상도를 조절하는 값입니다. 주소 입력란의 예시는 실제 수신자 설정이 아닙니다.

![PPT와 Outlook 설정](../screenshots/11-ppt-mail-settings.png)

### 처리 단계 선택

웹 수집·AI 분석·PPT 생성 단계를 선택합니다. 새 수집 자료의 PPT를 만들 때는 AI 분석을 함께 켜야 합니다.

![처리 단계 선택](../screenshots/18-pipeline-step-settings.png)

### 실행 전 확인

선택한 작업과 발송 설정을 최종 확인하는 창입니다. 이 화면은 실행 버튼 동작을 소개하는 캡처이며 작업 완료 증거가 아닙니다.

![실행 전 확인](../screenshots/17-execution-confirmation.png)

## 이력·통계·데이터 관리

### 실행 이력

실행 ID·유형과 날짜로 과거 실행을 찾고 당시 설정·결과를 확인하는 탭입니다. 캡처는 기록이 없는 초기 상태입니다.

![실행 이력](../screenshots/13-run-history.png)

### 데이터 분석

카페별 고유 게시글 수, 키워드 매칭 수, 수집 추이, 상세 목록을 확인합니다. 날짜는 최초 수집일(KST) 기준이며 키워드 합계는 글 수와 다를 수 있습니다.

![데이터 분석](../screenshots/14-data-analysis.png)

### 통계 조회 조건

기간·카페·키워드를 선택한 뒤 조회에 적용합니다. 게시 시각 기반 발생 통계와 혼동하지 않도록 최초 수집일 기준을 확인합니다.

![통계 조회 조건](../screenshots/15-analysis-date-filter.png)

### Excel 내보내기

전체 데이터와 집계를 함께 저장하거나 집계값만 저장합니다. 상세 선택과 비교 그래프의 범위 차이는 Excel 조회 정보에 기록합니다. 캡처는 메뉴를 연 상태입니다.

![Excel 내보내기](../screenshots/16-excel-export-menu.png)

### DB 관리와 V9 이관

DB 건수·백업·기존 V9 자료 이관·Outlook 계정 확인에 접근합니다. 신규 사용자는 이관이 필요하지 않습니다. DB 백업만으로 PPT·캡처 파일까지 백업되지는 않습니다.

![DB 관리와 V9 이관](../screenshots/12-database-manager.png)

## 과거 Windows 운영·검증 화면

현재 실행 방법은 [README](../README.md#설치와-실행)를 기준으로 합니다. 아래 이미지는 개발 이력으로 보존합니다.

<details>
<summary>개발 당시 다크 모드</summary>

V10 개발 당시 운영 화면과 설정 배치를 확인할 수 있습니다. 화면의 시각·키워드·옵션은 당시 테스트 값입니다.

![개발 당시 다크 모드](../screenshots/01-operation-dark.png)

</details>

<details>
<summary>개발 당시 라이트 모드</summary>

첫 실행 전 운영 화면입니다. 결과가 없는 상태를 실제 수집 완료로 해석하지 않습니다.

![개발 당시 라이트 모드](../screenshots/02-operation-light.png)

</details>

<details>
<summary>웹 수집 완료 기록</summary>

9개 카페·게시글 14건의 수집 전용 실행입니다. 이 실행에서는 AI·PPT·메일을 수행하지 않았습니다. README의 16건·PPT 30장 통합 실행과는 다른 기록입니다.

![웹 수집 완료 기록](../screenshots/03-collection-result.png)

</details>

<details>
<summary>이전 Codex 안내창</summary>

버튼에서 로그인 명령을 직접 실행하도록 개선하기 전의 안내입니다. 현재는 Codex 로그인 → 공식 로그인 진행 → 상태 확인 흐름을 사용합니다.

![이전 Codex 안내창](../screenshots/04-codex-auth-help.png)

</details>


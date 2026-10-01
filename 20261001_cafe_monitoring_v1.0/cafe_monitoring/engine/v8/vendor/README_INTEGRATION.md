# Outlook Mail Module v1

## 목적
크롤링/AI 분석/PPT 생성이 끝난 뒤 **회사 Classic Outlook**을 통해 결과 파일을 메일로 보내는 모듈입니다.

## 검증 완료
- Outlook COM 제어
- `Send()` 무인 발송
- 다중 TO
- CC
- PPTX 첨부
- 외부 Gmail 전달
- 사내 본인 계정 전달
- 사내 Outlook에서 받은 DRM PPTX를 회사 PC PowerPoint로 정상 열람

## 파일
- `outlook_mail.py` : 메인 크롤러에서 import할 Python 인터페이스
- `outlook_mail.ps1` : 실제 Outlook COM 처리
- `integration_example.py` : 연결 예제

별도 `pywin32` 설치 없이 Windows PowerShell + Classic Outlook을 사용합니다.

## 메인 크롤러에 붙이는 위치

```text
크롤링
→ AI 분석
→ PPT export 성공
→ 생성된 ppt_path 확보
→ send_outlook_mail(...)
→ 발송 결과 로그
```

### 핵심 원칙
**폴더에서 최신 PPT를 다시 찾지 말고 이번 실행에서 생성된 `ppt_path`를 직접 넘기세요.**

```python
from outlook_mail import send_outlook_mail

result = send_outlook_mail(
    to=["redacted@example.invalid", "redacted@example.invalid"],
    cc=["redacted@example.invalid"],
    subject="[동호회 모니터링] 결과 공유",
    body="금일 모니터링 결과 공유드립니다.",
    attachments=[ppt_path],
    display_only=True,
)
```

초기 통합 검증 때는 `display_only=True`로 메일 작성창까지만 확인하고,
검증 후 완전 자동 발송으로 전환할 때 `display_only=False`로 변경합니다.

## 실패 처리 권장
메일 발송 실패가 **크롤링/PPT 결과 삭제로 이어지면 안 됩니다.**

권장:
1. PPT 생성 결과 보존
2. 메일 오류 로그 기록
3. 실행 상태를 `MAIL_FAILED` 등으로 남김
4. 재발송 가능하게 설계

## Fasoo DRM
회사 PC에서 내려받은 Office 문서가 `DRMONE` 형태로 보호될 수 있습니다.

이번 기능시험에서:
- Outlook 전송 전/후 SHA-256은 동일
- 즉 Outlook/메일 서버가 파일을 변조한 것은 아님
- 사내 계정으로 받은 PPT는 회사 PC에서 정상 열림
- 외부 Gmail/개인 환경에서는 DRM 권한 때문에 열리지 않을 수 있음

따라서 사내 업무 발송은 회사의 문서 보안 정책 범위 안에서 사용하세요.

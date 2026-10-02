# 외부 구성 요소 및 참고 문서

설치 스크립트는 다음 패키지를 프로젝트 전용 `.venv`에 설치합니다.
배포 전 각 프로젝트의 라이선스·재배포 조건은 해당 배포본의 문서를 확인하세요.
이 ZIP에는 외부 실행 파일이나 폰트 파일을 직접 번들하지 않았습니다.

- FastAPI: https://fastapi.tiangolo.com/
- Starlette: https://www.starlette.dev/responses/
- Uvicorn: https://www.uvicorn.org/
- yt-dlp: https://github.com/yt-dlp/yt-dlp
- yt-dlp의 JavaScript 런타임 설명: https://github.com/yt-dlp/yt-dlp/wiki/EJS
- youtube-transcript-api: https://github.com/jdepoix/youtube-transcript-api
- imageio-ffmpeg: https://github.com/imageio/imageio-ffmpeg
- FFmpeg: https://ffmpeg.org/
- Requests: https://requests.readthedocs.io/
- Clipboard.readText: https://developer.mozilla.org/en-US/docs/Web/API/Clipboard/readText

HTML/CSS/JavaScript는 이 프로젝트에 포함되어 있으며 CDN 호출은 없습니다.
기존 V7.1의 기능 요구사항과 출력 형식을 바탕으로 서버·인덱스·작업 관리 코드를 분리했습니다.
YouTube 다운로드 동작은 외부 서비스와 설치된 엔진 버전의 영향을 받습니다.

## V8 beta 연결/자동 시작

확인한 공식 자료(2026-09-19):
- Tailscale Serve: https://tailscale.com/docs/features/tailscale-serve
- Serve CLI / --bg: https://tailscale.com/docs/reference/tailscale-cli/serve
- Windows Run unattended: https://tailscale.com/docs/how-to/run-unattended
- HTTPS / 인증서 기록 안내: https://tailscale.com/docs/how-to/set-up-https-certificates
- Cloudflare public/private Tunnel 및 영상 전송: https://developers.cloudflare.com/cloudflare-one/faq/cloudflare-tunnels-faq/
- Cloudflare 영상 제공 정책: https://developers.cloudflare.com/fundamentals/reference/policies-compliances/delivering-videos-with-cloudflare/
- Cloudflare token-file: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/run-parameters/
- Register-ScheduledTask: https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/register-scheduledtask
- New-ScheduledTaskSettingsSet: https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset

Tailscale/cloudflared 실행 파일은 이 ZIP에 들어 있지 않습니다. 설치 도우미가 사용자 확인 후 공식 winget 패키지 설치를 호출합니다. 각 제공자의 계정, 인증, 이용 조건은 사용자가 확인해야 합니다. 실제 Cloudflare/Tailscale 계정 리소스를 이 대화에서 생성하지 않았습니다.

- Tailscale Serve의 TCP HTTP proxy Host 보존 구현: https://github.com/tailscale/tailscale/blob/main/ipn/ipnlocal/serve.go

## V8 beta.2 성능 관련 공식 문서 (2026-09-21 확인)

- Pillow: https://pillow.readthedocs.io/
- FFmpeg MP4 faststart: https://ffmpeg.org/ffmpeg-formats.html
- SSE: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events
- Tailscale Funnel 제한 및 같은 포트의 공개/개인 배타성: https://tailscale.com/docs/features/tailscale-funnel
- Serve HTTPS 포트: https://tailscale.com/docs/reference/tailscale-cli/serve
- 직접/중계 연결 및 상태 확인: https://tailscale.com/docs/reference/connection-types

Funnel 대역폭의 특정 수치나 사용자의 실제 회선 속도를 추정해 하드코딩하지 않았습니다.
이 프로젝트는 Tailscale 계정/공유기/방화벽 정책을 원격으로 자동 변경하지 않습니다.

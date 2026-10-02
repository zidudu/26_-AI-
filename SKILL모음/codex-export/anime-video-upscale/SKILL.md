---
name: anime-video-upscale
description: "로컬 애니메이션·일러스트 영상의 화질 개선과 2배·3배·4배 AI 업스케일에 사용합니다. Real-ESRGAN ncnn Vulkan과 FFmpeg로 원본을 보존하며 확대하고, 원래 프레임 속도·오디오 유지와 전후 비교를 검증합니다. 실사 얼굴 복원이나 새 영상 생성용이 아닙니다."
---

# 애니메이션 AI 업스케일

Windows 로컬 GPU에서 `realesr-animevideov3`로 프레임을 확대하고 H.264 MP4로 저장합니다. 선과 색면이 뚜렷한 애니메이션, 일러스트 기반 AI 영상에 맞는 작업입니다. 기본 출력은 별도 영상, 저장된 영상에서 추출한 전후 비교 이미지, 검증 JSON입니다.

## 판단과 기본값

- 실제 프레임을 먼저 확인합니다. 파일명만으로 애니메이션 여부나 콘텐츠를 추측하지 않습니다.
- 사용자가 지정한 해상도를 우선합니다. 이번 방식의 재현은 720p 입력에 **3배 → 3840×2160**, 1080p 입력에 4K가 필요하면 **2배**입니다. 원래 종횡비를 유지합니다. 지원 배율은 2·3·4배이며, 다른 목표 해상도는 AI 확대 후 별도 축소해야 합니다.
- 모델은 `realesr-animevideov3`, tile 256, 스레드 `2:2:2`, H.264 `CRF 16 / preset medium / yuv420p`, 원본의 정확한 유리수 FPS를 사용합니다. GPU 번호를 다른 PC에서도 0으로 가정하지 않습니다.
- 샘플을 직접 비교한 뒤 같은 설정으로 전체 처리합니다. 사용자가 이미 업스케일을 요청했다면 샘플 확인은 에이전트의 품질 점검이며, 매번 추가 승인을 요구하지 않습니다.
- 원본과 기존 결과물을 덮어쓰지 않습니다. 새 결과 이름과 실행별 임시 폴더를 사용합니다. 보간, 얼굴 재생성, 임의 색 보정은 기본 작업에 포함하지 않습니다.
- 도우미는 **고정 프레임률(CFR), 8비트 SDR, 정사각 픽셀, 일반 방향 영상**을 대상으로 합니다. VFR, HDR/고비트 심도, 알파, 인터레이스, 회전 메타데이터는 조용히 변환하지 않고 감지 결과를 설명합니다. 별도 타임스탬프·색 관리 처리가 필요합니다. 실사에는 이 모델을 일괄 적용하지 않습니다.

## 준비

1. 입력 파일과 Python, GPU, FFmpeg를 확인합니다. `nvidia-smi`는 NVIDIA 환경에만 사용하며 Vulkan 지원 다른 GPU도 가능합니다. 검증된 환경은 [references/workflow-notes.md](references/workflow-notes.md)에 있습니다.
2. 스킬 폴더는 `$skillRoot`, 현재 작업 공간은 `$taskRoot`로 둡니다. 스킬 내부에 사용자 영상이나 실행 중간 파일을 저장하지 않습니다. 임시는 `$taskRoot/work/`, 전달할 파일은 `$taskRoot/outputs/`에 둡니다.
3. Python 도우미 의존성은 `Pillow`이며, PATH에 FFmpeg가 없으면 `imageio-ffmpeg`를 사용합니다. 기존 정상 런타임을 우선합니다. 의존성이 없다면 작업 공간의 venv에 설치하고 이후 그 Python을 사용합니다. 시스템 전체 패키지를 변경할 필요가 없습니다.
4. 기존 공식 portable 엔진과 모델이 있으면 `--engine-dir`로 재사용합니다. 없으면 아래 도우미로 고정된 공식 배포본을 내려받습니다. 사용자 영상은 외부로 업로드하지 않습니다.

```powershell
py -3 -X utf8 "$skillRoot/scripts/setup_engine.py" --destination "$taskRoot/work/upscale_tools"
```

이 스크립트는 공식 ZIP의 SHA-256을 확인하며 기존 엔진 폴더를 덮어쓰지 않습니다. 이미 받은 동일 ZIP은 `--archive '절대경로.zip'`로 재사용할 수 있습니다. 준비 후 `$engineRoot`를 출력된 엔진 디렉터리로 설정합니다.

## 실행

아래 명령의 `$inputVideo`, `$taskRoot`, `$skillRoot`, `$engineRoot`는 실제 절대 경로로 설정합니다. 각 명령은 별도 실행 폴더를 만들고 경로를 출력합니다. 긴 명령은 비동기로 실행하고 로그·완성된 PNG 개수로 진행 상태를 확인합니다.

### 1. 조사

```powershell
py -3 -X utf8 "$skillRoot/scripts/upscale_video.py" inspect "$inputVideo" --work-dir "$taskRoot/work/upscale"
```

출력 JSON에서 해상도·프레임 수·정확한 FPS·오디오·색 정보·지원 여부를 확인합니다. 실행 폴더의 `source_*.png`를 이미지 도구로 직접 열어 시작·중간·끝 장면을 확인합니다. `unsupported`가 비어 있지 않으면 전체 처리로 진행하지 않습니다. 색 정보가 unknown인 경우 SDR BT.709 출력 가정을 밝히고 샘플에서 색 변화를 확인합니다.

### 2. 샘플

```powershell
py -3 -X utf8 "$skillRoot/scripts/upscale_video.py" sample "$inputVideo" --work-dir "$taskRoot/work/upscale" --engine-dir "$engineRoot" --scale 3
```

`sample_comparison.jpg`에서 얼굴 인상, 눈, 머리카락, 글자 획, 배경 질감을 비교합니다. 확대할 관심 영역은 **원본 좌표** 기준 `--crop 'x,y,width,height'`로 지정합니다. 기본 비교 영역은 중앙이므로 얼굴이나 글자가 다른 곳에 있으면 좌표를 지정합니다. 선이 과하게 굵어지거나 글자·얼굴이 달라지면 배율 또는 모델 선택을 재검토합니다. 작은 정지 샘플이 좋다는 이유만으로 시간적 안정성을 보장하지 않습니다.

### 3. 전체 처리

```powershell
py -3 -X utf8 "$skillRoot/scripts/upscale_video.py" run "$inputVideo" --work-dir "$taskRoot/work/upscale" --engine-dir "$engineRoot" --scale 3 --output "$taskRoot/outputs/video_AI_4K.mp4"
```

도우미는 무손실 PNG 추출 → AI 확대 → 프레임 이름·개수·크기 확인 → 원본 FPS 재조립 → 전체 디코딩 검사 → 원본 SHA-256 재확인 순서로 처리합니다. 원본에서는 attached picture를 제외하는 `0:V:0`으로 실제 영상을 선택합니다. 원본이 무음이면 무음으로 유지합니다.

오디오는 기본 `copy`이며 모든 원본 오디오 스트림을 매핑합니다. 복사된 오디오는 패킷 해시도 비교합니다. MP4가 지원하지 않는 오디오 코덱 때문에 실패한 경우 로그를 확인하고, 필요하면 `--audio aac`로 다시 실행하여 오디오 재인코딩 사실을 알립니다. 무음으로 바꾸어 성공으로 보고하지 않습니다.

VRAM 부족은 tile 128 또는 64와 `--threads '1:1:1'`로 줄여 새 실행 폴더에서 재시도합니다. 원인이 같은 실패를 무한 반복하지 않습니다. 디스크 공간도 확인합니다. PNG 중간 파일이 최종 MP4보다 훨씬 클 수 있으며 도우미는 자동 삭제하지 않습니다. 정리가 필요하면 검증된 해당 실행의 생성 파일만 대상으로 하고, 환경에서 삭제가 차단되면 남은 파일을 알립니다.

## 최종 확인과 전달

- `*_validation.json`의 프레임 수·FPS·크기·디코딩 결과·원본 보존 여부·오디오 검사 결과를 확인합니다. 검증 실패 파일을 완성품으로 전달하지 않습니다.
- `*_comparison.jpg`는 **최종 인코딩 영상**의 중간 프레임과 원본을 동일 표시 크기로 비교한 이미지입니다. 실제로 열어 확인합니다. 가능하면 시작·중간·끝과 움직임도 확인하여 깜빡임이나 선 떨림을 살핍니다.
- 구조 검증, 정지 화면 시각 검토, 실제 재생 검토를 구분하여 보고합니다. JSON은 자동 시각 검토를 주장하지 않습니다.
- 결과 영상과 전후 비교를 링크하고, 입력→출력 해상도, FPS, 길이, 용량을 짧게 설명합니다. AI가 세부를 추정하므로 원본에 없던 실제 정보를 완벽히 복원했다고 표현하지 않습니다.
- 기본값의 근거와 범위, 재현 예시는 필요할 때만 [references/workflow-notes.md](references/workflow-notes.md)를 읽습니다.

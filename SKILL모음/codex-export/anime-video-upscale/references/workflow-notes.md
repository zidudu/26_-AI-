# 검증된 설정과 적용 범위

## 2026-09-27에 실제 처리한 사례

- 애니메이션·일러스트 기반 영상: 1280×720, 24fps, 241프레임, 10.041667초, SDR BT.709, 오디오 없음.
- Windows, NVIDIA GeForce RTX 4050 Laptop GPU 6GB.
- Real-ESRGAN ncnn Vulkan Windows 20220424 배포본, `realesr-animevideov3`, 배율 3, tile 256, `2:2:2`.
- 출력: 3840×2160, 24fps, 241프레임, H.264 CRF 16 / medium / yuv420p, MP4 faststart, 약 31.1MB.
- 원본 SHA-256 보존, 전체 영상 디코딩 오류 없음, 시작·중간·끝을 포함한 11개 장면 및 얼굴·글자 확대 비교 확인.
- 머리카락·눈·글자 윤곽이 선명해졌고 일부 배경 미세 질감이 부드러워졌습니다. 사용자가 결과 개선을 확인했습니다.
- 이 사례는 하드웨어 속도 보장이나 모든 영상의 품질 보장이 아닙니다. 원본 영상이나 사용자 경로를 스킬에 포함할 필요가 없습니다.

## 도우미 설계에서 유지할 점

- 이전 한 파일용 스크립트의 241프레임, 24fps, 고정 파일명을 재사용 코드에 하드코딩하지 않습니다.
- `ffmpeg -vf showinfo`의 정수 PTS, 유리수 time base와 FPS로 시간 간격을 확인합니다. `23.98`처럼 반올림한 FPS로 재조립하지 않습니다.
- 명목 FPS만 읽고 VFR 영상을 CFR로 간주하면 길이·동기화가 달라질 수 있습니다. 이 도우미는 그런 입력을 거절합니다.
- PNG 프레임명은 1부터 시작하며 8자리 숫자로 고정합니다. 프레임 목록이 정확히 일치하는지 검사합니다. 실행 폴더를 새로 만들어 과거 프레임이 섞이지 않게 합니다.
- 오디오는 `-map 1:a? -c:a copy`. 원본 비디오는 `-map 0:V:0`으로 attached picture를 제외하고, 재조립할 PNG 입력은 `-map 0:v:0`으로 선택합니다. 프레임 입력에는 오디오가 없습니다.
- RGB 프레임에서 SDR BT.709 제한 범위 YUV로 명시적으로 변환한 뒤 H.264로 인코딩합니다. 색 메타데이터 태그만 붙이는 것과 실제 색 변환을 혼동하지 않습니다.
- 엔진과 FFmpeg의 종료 코드만으로 성공 처리하지 않습니다. 새 출력의 전체 디코딩·프레임 수·FPS·해상도와 원본 해시를 확인합니다.
- 검증 JSON의 `visual_review`는 사람이 아니라 모델의 시각 도구로 검토했더라도 검사 수준을 정확히 기록해야 합니다. 정지 화면 검토와 영상 재생 검토는 별개입니다.

## 의존성과 설치

도우미 Python은 3.11 이상을 사용합니다. 이미 쓸 수 있는 Python/FFmpeg를 우선합니다. 별도 환경이 필요할 때의 예:

```powershell
py -3 -m venv "$taskRoot/work/upscale-venv"
& "$taskRoot/work/upscale-venv/Scripts/python.exe" -m pip install Pillow imageio-ffmpeg
```

이후 도우미도 같은 Python으로 실행합니다. 스킬 구조 검증은 UTF-8 모드(`python -X utf8`)로 실행하면 Windows 기본 인코딩 차이로 인한 오류를 피할 수 있습니다.

기존 FFmpeg를 명시하려면 `--ffmpeg 'ffmpeg.exe의 절대경로'`를 사용합니다. GPU 선택은 NCNN 로그에 표시된 장치 번호를 확인한 뒤 `--gpu`로 지정합니다. 이 스킬은 GPU 드라이버나 시스템 설정을 바꾸지 않습니다.

## 배포본 출처

- [Real-ESRGAN 공식 프로젝트](https://github.com/xinntao/Real-ESRGAN)
- [애니메이션 영상 모델 및 프레임 처리 절차](https://github.com/xinntao/Real-ESRGAN/blob/master/docs/anime_video_model.md)
- [ncnn Vulkan CLI 옵션](https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan)
- [FFmpeg 스트림 선택 규칙](https://ffmpeg.org/ffmpeg.html#Stream-specifiers)
- [고정 Windows portable ZIP](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip)

공식 URL에서 받은 ZIP의 실제 SHA-256을 준비 도우미에 고정했습니다:

```text
abc02804e17982a3be33675e4d471e91ea374e65b70167abc09e31acb412802d
```

이는 검증했던 배포 파일과 동일한지 확인하는 값이며 별도 코드 서명 검증을 의미하지 않습니다. 배포본이 바뀌면 새 원본을 확인하고 샘플·검증을 재실행한 후 핀을 의도적으로 갱신합니다.

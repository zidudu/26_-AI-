# 실행 및 검증

## 새 프로젝트

Python 3 + NumPy, Node.js/npm을 사용합니다. Codex에서는 `load_workspace_dependencies`가 반환하는 실제 런타임을 확인합니다. PATH가 없다는 이유로 시스템 전역 설치부터 하지 않습니다. 아래 명령의 python/npm/npx는 확인한 실행 파일로 바꿔도 됩니다.

```text
python <skill>/scripts/create_project.py <새 프로젝트의 절대 경로>
```

생성된 폴더를 작업 디렉터리로 설정한 뒤:

```text
npm ci
python make_audio.py
npm run typecheck
npx remotion still src/index.ts OrbitFilm out/scene-1.png --frame=75 --scale=0.5
npx remotion still src/index.ts OrbitFilm out/scene-2.png --frame=180 --scale=0.5
npx remotion still src/index.ts OrbitFilm out/scene-3.png --frame=300 --scale=0.5
npx remotion render src/index.ts OrbitFilm out/preview.mp4 --codec=h264 --scale=0.25
npm run render
```

NumPy가 없으면 선택한 환경에 설치합니다. 최초 렌더에서 브라우저 다운로드가 필요할 수 있습니다. 기존 호환 Chrome Headless Shell이 있으면 CLI의 `--browser-executable=<경로>`로 재사용할 수 있습니다. 버전이 다른 브라우저로 바꿀 때는 다시 렌더하여 확인합니다. Windows PowerShell에서는 실행 파일 경로에 `&`를 사용하고 Node의 bin 디렉터리를 해당 프로세스의 PATH에 추가하여 npm 자식 프로세스가 node를 찾게 합니다.

이 템플릿은 Remotion 4.0.524, React 19.2.3, TypeScript 5.9.3 및 원래 lockfile을 보존합니다. 이를 최신 버전이라는 뜻으로 설명하지 않습니다. 업그레이드가 요청되거나 호환성 문제가 생기면 해당 버전 공식 문서를 확인하고 별도로 재검증합니다. 선택적 기존 scaffold 의존성도 lockfile 재현을 위해 남아 있으며 Tailwind는 실제 장면 스타일링에 사용하지 않습니다.

## 확인할 프레임

세 장면 중간 프레임 75/180/300 외에, 전환 경계 112/119/120 및 232/239/240과 마지막359를 확인합니다. 장면 길이를 변경했다면 검사 위치도 다시 계산합니다. 전환 직전·직후에 빈 화면, 색상 불일치, 잘못된 레이어가 없는지 봅니다. 긴 문구로 바꾸었을 때도 실제 이미지로 글자 잘림을 확인합니다.

## 최종 미디어 검사

FFmpeg/FFprobe는 확인한 로컬 실행 파일을 사용합니다. Windows의 이 템플릿 설치에서는 `node_modules/@remotion/compositor-win32-x64-msvc/` 안에 있었으며, 다른 플랫폼에서는 같은 경로를 가정하지 않습니다.

```text
python <skill>/scripts/verify_video.py out/ORBIT_1080x1920.mp4 --ffprobe <ffprobe 경로> --ffmpeg <ffmpeg 경로> --width 1080 --height 1920 --fps 30 --frames 360 --output out/verification.json
```

오디오를 제거한 영상은 `--no-audio`를 추가합니다. 이 검사는 해상도, 평균 fps, 실제 읽은 프레임 수, 오디오 유무/길이와 전체 디코딩을 확인합니다. AAC 패딩을 고려해 오디오 길이 차이는 0.12초까지 허용합니다. 더 엄격한 납품 규격은 별도로 적용합니다. H.264/AAC 코덱과 샘플링/채널 수는 반환된 metadata에서도 확인합니다. 이 스크립트는 영상 미학, 실제 청취, LUFS/true peak를 검사하지 않습니다.

번들 FFmpeg에서 null 출력의 기본 encoder가 없을 수 있으므로 검사 코드는 `-c:v rawvideo -c:a pcm_s16le -f null -`를 명시합니다. 오류 원인을 확인하지 않고 디코딩 검사를 생략하지 않습니다.

최종 전달에는 실제로 확인한 항목만 적습니다. CapCut 직접 편집을 원하면 현재 사용 가능한 앱 제어 기능을 먼저 확인하고, 사용할 수 없다면 Remotion으로 제작하는 대안을 명확히 설명합니다.

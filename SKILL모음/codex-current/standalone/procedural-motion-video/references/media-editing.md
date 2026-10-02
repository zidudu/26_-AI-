# 이미지·동영상 클립 편집

## 재현한 작업

「종이 위의 오후」는 교실 일러스트6장을 선택한25초 가로 영상입니다. 가로 이미지는 화면 채우기, 세로/정사각형 이미지는 원본 전체와 흐린 배경으로 배치했습니다.135프레임×6−12프레임×5=750프레임,30fps,1920×1080입니다. 원본은 바꾸지 않고 복사했으며 원본 대응표와 SHA256을 기록했습니다. 이것은 이미지에 카메라 이동·전환을 적용한 편집 사례입니다.

이 문서의 템플릿은 해당 제작 소스에서 추출했습니다. 개인 다운로드 경로·이미지·검수용 전체 연락판은 스킬에 포함하지 않습니다. 임의의 폴더를 일괄 가져오지 말고 확인하여 선택한 파일 목록을 넘깁니다.

## 생성

가용 Node/npm, Python, Pillow, NumPy를 확인합니다. Codex에서는 bundled runtime 경로를 조회할 수 있습니다. npm이 Node와 다른 폴더에 있다면 실제로 발견한 `npm-cli.js`를 Node로 실행할 수 있습니다. 아래의 실행 파일 이름은 실제 환경에 맞춥니다.

```text
python <skill>/scripts/create_project.py <새 폴더> --template media --media <이미지1> <이미지2> <클립.mp4> --ffprobe <ffprobe 실행 파일> --title "나의 영상"
```

이미지만 사용할 때 `--ffprobe`는 생략할 수 있습니다. 입력은 PNG/JPG/JPEG/WebP 및 MP4/MOV/MKV/WebM/M4V를 지원 대상으로 검사하며, 파일 확장자가 실제 코덱 재생을 보장하지 않습니다. 다중 프레임 이미지는 자동으로 첫 프레임만 쓰지 않고 오류로 알려 별도 변환 결정을 받습니다.

기본값은 장면4.5초,겹침0.4초,30fps,1920×1080입니다. `--seconds-per-shot`, `--overlap-seconds`로 타이밍을 정합니다. 클립이 짧으면 장면 길이를 원본 길이로 줄이며, 겹침의 두 배보다 짧은 장면은 오류로 알려줍니다. 소스 사전 검사 실패 시 새 프로젝트를 만들지 않습니다. 기존 목적지 폴더는 덮어쓰지 않습니다.

새 폴더에서:

```text
npm ci
python make_music.py
npm run typecheck
npx remotion still src/index.ts MediaFilm out/check.png --frame=45 --scale=0.5
npx remotion render src/index.ts MediaFilm out/preview.mp4 --codec=h264 --scale=0.5
npm run render
```

실행 가능한 Chrome Headless Shell이 있으면 `--browser-executable=<실제 경로>`로 재사용합니다. 마지막 명령의 출력은 `out/film.mp4`입니다. 파일명·composition ID는 ORBIT 경로와 구별합니다.

## 타임라인과 설정

`src/film.json`이 주 편집 지점입니다. 파일 선택 후 기본 제목/자막을 사용자의 목적에 맞게 다시 작성합니다.

| 필드 | 의미 |
| --- | --- |
| title / eyebrow / subtitle | 도입 제목·영문 소제목·설명 |
| width / height / fps | 출력 규격. 현재 UI 배치는1920×1080기준이므로 비율 변경 시 재배치 |
| overlap | 장면 사이 겹침 프레임.0은 컷 연결 |
| music / musicVolume | public 기준 음악 파일명과 이득. music을 삭제하면 배경음 제거 |
| shots[].kind / src | image 또는 video / public 기준 복사본 상대 경로 |
| duration | 해당 장면이 출력 타임라인에서 차지하는 프레임 수 |
| fit / focus | cover 또는 contain / 백분율 초점 좌표[x,y] |
| scale / pan | 시작·끝 확대 배율 / 시작·끝 수평 이동px |
| title / subtitle | 장면별 자막 |
| trimBefore | video의 앞부분에서 생략할 시간에 해당하는 composition fps 기준 프레임 |
| playbackRate | 원본 클립 재생 배속. 기본1 |
| volume | 원본 클립 오디오 이득. 기본0으로 배경음과의 무의도 중복을 방지 |

전체 프레임 = 모든 duration 합 − overlap×(장면 수−1). 각 시작 프레임은 이전 장면들의(duration−overlap) 누적값입니다. Sequence 안의 useCurrentFrame은 로컬 프레임입니다.

새 장면을 이전 장면 위에 놓고 겹침 동안 새 장면 불투명도를0→1로 올립니다. 이전 장면을 동시에0으로 낮춰 배경이 비치는 오류를 피합니다. 자막은 각 장면의 초입/끝에서 별도로 페이드합니다.

이미지는 Remotion Img, 클립은 @remotion/media Video를 사용합니다. Video에 objectFit을 명시합니다. 시작점이나 속도를 바꿀 때 필요한 원본 길이는 대략 `trimBefore/fps + duration/fps × playbackRate`입니다. 실제 FFprobe의 비디오 스트림 길이를 넘지 않도록 확인하고 경계 프레임을 렌더합니다. 파일을 짧게 자르는 요청은 duration과 trimBefore를 함께 변경합니다.

## 구도·소스 대응

세로 이미지를 무조건 가로로 잘라 얼굴을 잃지 않도록 실제 내용을 보고 fit을 정합니다. contain인 정지 이미지는 같은 이미지를 흐리게 확대한 배경을 둡니다. 클립 contain은 어두운 배경 위 전체 화면을 보존합니다. 줌·팬은 재촬영 효과이며 피사체의 새로운 몸동작을 만들지 않습니다.

source-manifest.json에는 원본 경로, 복사된 상대 경로, 해시, 규격을 저장합니다. 이미지 EXIF 회전은 검사 때 고려하지만 자동 재인코딩하지는 않습니다. 회전·HDR·VFR·특수 코덱이 있는 촬영본은 대표 프레임과 실제 디코딩 결과를 확인하고 필요할 때 호환 형식으로 작업용 복사본을 변환합니다. 시험하지 않은 촬영기기/코덱까지 검증했다고 말하지 않습니다.

## 음원

make_music.py는 film.json의 전체 길이를 읽어 피아노 느낌의 배음·패드·베이스를48kHz 스테레오 WAV로 합성합니다. 장면 길이가 달라지면 다시 실행합니다. 새로운 이미지 내용에 맞게 화음·악기·리듬을 바꾸거나 제공 음악으로 대체할 수 있습니다. 내레이션/TTS 생성 기능은 번들에 포함하지 않으므로 가용 음성 도구 또는 제공 음성을 사용합니다.

원본 오디오는 volume으로 조절하고 겹침 구간에서 페이드합니다. 합성 음악과 발화가 겹치면 음악 레벨을 낮추거나 발화 구간별 볼륨 곡선을 작성합니다. 최종 샘플 피크·레벨과 실제 청취를 분리하여 확인합니다.

## 검증과 전달

1. 각 장면 중간, 겹침 시작/중간/끝, 처음/마지막 프레임을 실제 이미지로 봅니다.
2. 최종 출력 규격에 맞춰 verify_video.py를 실행합니다. 예를 들어6×135프레임,겹침12이면 --frames 750입니다.
3. 원본 오디오만 있는 편집에서도 오디오 스트림과 길이를 확인합니다. AAC 인코딩 패딩 허용과 실제 음성 잘림은 다른 문제입니다.
4. 원본 파일 해시를 다시 확인해 수정되지 않았음을 검증할 수 있습니다. 파일이름·소스·렌더 로그·검증 JSON을 작업 폴더에 남깁니다.
5. 동영상 입력 코드의 검증에는 실제 클립이 포함된 짧은 시험 렌더를 사용합니다. 이미지 전용 출력 성공을 동영상 트림/속도 변경 검증으로 대체하지 않습니다.

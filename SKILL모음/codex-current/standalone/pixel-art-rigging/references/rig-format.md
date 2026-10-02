# 설정과 실행

Python 3.10 이상, Pillow, NumPy를 사용합니다. 파츠 파일 경로는 parts.json의 폴더 기준입니다. 새 출력 폴더를 지정합니다. UTF-8 한글 경로를 지원합니다.

## parts.json

```json
{"canvas": [128, 192], "parts": [
  {"id": "body", "file": "parts/body.png", "position": [45, 62], "z": 10, "bone": "body"},
  {"id": "hair", "file": "parts/hair.png", "position": [46, 25], "z": 20, "bone": "hair"}
]}
```

position은 변형 전 전체 캔버스에서 PNG 왼쪽 위의 정수 좌표입니다. z가 작은 순서대로 그립니다. id는 고유하고 name은 선택적 레이어 표시 이름입니다.

초기화 도구는 기존 예제 형식의 index/id/rig도 읽습니다. file이 없으면 `parts/{index:02d}_{id}.png`를 찾고 bone이 없으면 rig를 씁니다. 함께 준 모션의 part_to_bone이 있으면 그 매핑을 적용합니다.

## motion.json

```json
{
  "frames": 60, "frame_ms": 80, "loop": true,
  "background": [25, 28, 38], "fixed_bones": ["root"],
  "bones": {
    "root": {"parent": null, "pivot": [64, 180], "tip": [64, 160]},
    "body": {"parent": "root", "pivot": [64, 100], "tip": [64, 65],
      "y": [[0, 0], [0.5, -1], [1, 0]]},
    "hair": {"parent": "body", "pivot": [64, 30], "tip": [64, 80],
      "rotation": [[0, 0], [0.3, 2], [0.7, -2], [1, 0]]}
  }
}
```

서식 예시입니다. 좌표나 모션을 다른 캐릭터에 그대로 적용하지 않습니다.

- `frames`는 계산 프레임 수입니다. 루프는 `t=i/frames`, 비루프는 `t=i/(frames-1)`, 1프레임이면 0입니다.
- `frame_ms`는 전체 공통 시간으로 GIF의 10ms 단위에 맞춰 10의 배수를 씁니다. 합계는 `frames*frame_ms`이며 GIF의 동일 프레임 병합 후에도 유지합니다.
- `rotation`은 각도, `x`/`y`는 원본 픽셀 단위입니다. 생략하면 0, 정수·실수 상수 또는 `[[시각,값],...]`를 지정합니다. 시각은 0으로 시작하고 1로 끝나는 엄격한 오름차순입니다. 구간 내 smoothstep 보간을 사용합니다.
- `pivot`과 `tip`은 모두 변형 전 **전체 캔버스 좌표**입니다. 자체 회전·이동에 부모 변환을 곱합니다. `bones`의 기재 순서는 상관없습니다.
- `loop:true`에서는 곡선 시작값과 끝값이 일치해야 합니다. 위상이나 주파수는 키프레임으로 표현합니다.
- `fixed_bones`는 검사 대상이며 동작을 강제하는 옵션이 아닙니다. 지정한 본의 변환이 프레임 간 달라지면 렌더러가 오류를 냅니다.
- 파츠당 하나의 본에 강체 할당합니다. 메시·웨이트 혼합·표정 교체·확대축소·프레임별 z 변경은 기본 스크립트에 없습니다.

## 선택적 두 관절 IK

```json
"ik": [{"name": "leftArm", "parent": "body",
  "upper_bone": "upperL", "lower_bone": "foreL",
  "shoulder": [50, 65], "elbow": [40, 82], "wrist": [25, 78],
  "target": [25, 78]}]
```

`shoulder`/`elbow`/`wrist`는 변형 전 전체 좌표, `target`은 고정 월드 좌표입니다. 어깨는 `parent`에 따라 움직이고 손목은 `target`으로 고정하며 원래 팔꿈치에 가까운 해를 고릅니다. 길이는 고정이고 도달 불가능한 목표는 오류로 처리합니다. `upper_bone`/`lower_bone`은 IK가 만들므로 `bones`에 중복 정의하지 않습니다. 그 자식에 일반 본을 붙일 수 있습니다. 다른 IK를 부모로 삼으면 부모 IK를 먼저 나열합니다.

## 실행

`$skillPath`에 설치된 스킬의 절대 경로를 지정합니다.

```powershell
py -3 -X utf8 "$skillPath/scripts/init_project.py" --manifest "입력/parts.json" --destination "새프로젝트"
# 기존 대응 모션이 있으면 --motion "입력/motion.json" 추가
py -3 -X utf8 "새프로젝트/render_rig.py" --project "새프로젝트" --output "새프로젝트/output-v1"
py -3 -X utf8 "새프로젝트/verify_rig.py" --output "새프로젝트/output-v1"
```

초기화가 만든 설정은 정지 상태입니다. 그림을 관찰하여 `pivot`·부모·곡선을 편집합니다. 예전 NOADOT 데모의 `tracks` 형식이나 하드코딩된 팔 좌표를 자동 변환하지 않습니다. 필요한 구조를 위 형식으로 옮깁니다.

## 출력·검증 범위

`frames/`의 PNG는 RGBA 원본 프레임이고 `preview.gif`는 배경에 합성한 공통 팔레트 미리보기입니다. `bones.gif`에는 본 안내가 있습니다. `animation.aseprite`는 파츠 레이어와 숨김 안내·기준 그림 레이어, `bind.aseprite`는 기준 자세 파츠를 갖습니다.

검증은 이 렌더러의 32bit RGBA·일반 합성·압축 cel 프레임을 별도 읽기 코드로 재구성하고 PNG와 픽셀 비교합니다. GIF는 전체 디코딩·크기·총시간·루프 설정을 검사합니다. `gif_rgb_mae`는 배경에 합성한 PNG와의 평균 색 양자화 오차이며 자연스러움 점수가 아닙니다.

렌더링 때 읽은 입력 파일의 현재 해시가 다르면 검증에 실패합니다. `sources.json`의 최초 가져오기 해시는 별도 출처 정보입니다. 이후 의도적으로 파츠를 편집할 수 있으므로 최초 복사본과 달라진 파일은 목록에 보고하며 무조건 오류로 처리하지 않습니다. 프로젝트를 옮겨 원래 경로를 읽을 수 없으면 원본 미확인 개수를 보고합니다. 파츠·설정·스크립트와 출력 폴더를 함께 옮기면 출력의 상대 프로젝트 경로가 유지됩니다.

바이너리 필드를 바꿀 때는 [Aseprite 공식 파일 사양](https://github.com/aseprite/aseprite/blob/main/docs/ase-file-specs.md)을 확인합니다. 독자 출력의 파일 검증과 Aseprite 앱 실행 검증을 구별합니다.

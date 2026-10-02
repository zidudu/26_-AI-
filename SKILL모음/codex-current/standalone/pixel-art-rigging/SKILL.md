---
name: pixel-art-rigging
description: Create editable pixel-art character cutout rigs and looping animations from supplied layered sprites, transparent PNG parts, or an illustration that needs part preparation. Use for 도트 일러스트 리깅, 파츠 분리, 본 애니메이션, idle breathing, hair or cloth motion, and GIF/Aseprite exports. Covers preparation, pivots, motion curves, rendering, and validation; does not produce native Live2D or 3D rigs.
---

# 도트 일러스트 리깅 및 애니메이션 제작

원본 그림과 파츠를 보존하면서 캐릭터에 맞는 관절·모션을 만들고, 재생 파일과 다시 편집할 소스를 전달합니다. 기존 파츠 재사용, 새 파츠 분리, 새 그림 제작을 구별하여 설명합니다.

## 입력에 맞게 시작하기

- **분리 PNG와 위치 정보가 있음**: 파일과 합성 이미지를 직접 확인한 뒤 아래 렌더러를 사용합니다. 파츠 이름만 보고 관절을 정하지 않습니다.
- **Aseprite/PSD 레이어만 있음**: 가용 편집기나 공식 CLI로 파츠와 캔버스 좌표를 추출합니다. 원본을 유지하고 추출 전후 합성 결과를 비교합니다.
- **단일 그림만 있음**: 먼저 [references/part-preparation.md](references/part-preparation.md)를 읽고 파츠를 준비합니다. 투명 파츠가 없는데 사각형 영역을 흔들고 파츠 분리를 완료했다고 보고하지 않습니다. 그림 수정·가려진 부분 복원에는 세션의 이미지 편집 도구 규칙을 따릅니다.

출력 크기·동작·길이가 이미 지정되면 따릅니다. 별도 지정이 없으면 원본 도트 해상도에서 작은 호흡과 머리카락/옷자락 움직임을 가진 짧은 반복 모션부터 만듭니다. 표정이나 손 흔들기에는 필요한 파츠·포즈가 실제로 있는지 확인합니다.

## 리깅과 제작

1. 원본 크기, 알파, 파츠별 위치, 앞뒤 순서, 겹침 여유를 확인합니다. 확대된 도트는 실제 도트 배율을 확인하며 임의 축소하지 않습니다. 원본 출처와 재사용 범위를 남깁니다.
2. `scripts/init_project.py --manifest <parts.json> --destination <새 폴더>`로 파츠·출처 해시·설정·렌더러를 새 프로젝트에 복사합니다. 스킬 설치 폴더에 사용자 캐릭터를 영구 번들하지 않습니다.
3. [references/rig-format.md](references/rig-format.md)를 읽고 `parts.json`의 bone, position, z와 `motion.json`의 부모, pivot, tip, 곡선을 설정합니다. 초기화가 만든 중심점과 정지 곡선은 출발점이며 완성 모션이 아닙니다.
4. 고정할 발·손은 독립 기준점으로 두고 부모 움직임이 중복 적용되지 않게 합니다. 머리카락과 천의 위상·진폭을 달리합니다. 연결부가 벌어지면 파츠 여유, 회전축, 동작 크기 순으로 수정합니다. 두 관절 IK는 실제 상·하위 팔다리 파츠가 있을 때 사용합니다.
5. 프로젝트 안에서 `py -3 -X utf8 render_rig.py --project . --output output-v1`을 실행합니다. Pillow와 NumPy가 필요하며 미설치라면 프로젝트의 `requirements.txt`를 사용합니다. 기존 결과를 보존하도록 새 출력 폴더를 지정합니다.
6. `py -3 -X utf8 verify_rig.py --output output-v1`로 전체 프레임·Aseprite 합성·GIF 길이·원본 해시를 검사합니다. 연결부·가림 순서·자연스러움은 프레임과 재생 결과를 직접 보고 판단합니다.

## 검수와 전달

- `bind.png`와 원본 기준 자세를 비교하고 `contact.png`, `bones.gif`, 최대 변형 및 반복 경계 프레임을 확인합니다. 1배율과 최근접 확대에서 머리·어깨·팔꿈치·손목·치마·발을 봅니다. 세부 기준은 [references/part-preparation.md](references/part-preparation.md)에 있습니다.
- `render_info.json`의 화면 밖 파츠, 완전히 사라진 파츠, 고정 본 오차, 모션 시작/끝 행렬 차이를 읽습니다. 의도된 화면 밖 움직임과 잘림 오류를 구별합니다. `verification.json`의 정지 모션 경고도 확인합니다.
- 불투명 GIF는 미리보기용입니다. 원본 RGBA는 `frames/*.png`와 Aseprite에 보존됩니다. GIF의 256색 변환과 같은 프레임 병합 때문에 계산 프레임 수와 저장 프레임 수가 다를 수 있으므로 전체 길이를 확인합니다.
- 기본 출력은 `preview.gif`, `bones.gif`, `animation.aseprite`, `bind.aseprite`, `frames/`, 설정·스크립트입니다. 필요한 결과와 소스를 전달하고 GIF를 미리 보여줍니다. 재사용하려면 파츠와 JSON, 스크립트를 함께 옮깁니다.
- Aseprite에는 계산된 파츠별 프레임을 저장합니다. 숨김 본 안내는 그림이며 드래그 가능한 네이티브 본이 아닙니다. 재리깅은 JSON과 코드로 수행합니다. Aseprite/Cubism 앱 실행, Live2D `.moc3`, 실시간 추적은 실제 검증한 수준만 보고합니다.

## 실행 도구

- `scripts/init_project.py`: 파츠와 선택적 모션을 새 독립 프로젝트로 복사합니다.
- `scripts/render_rig.py`: 계층형 회전·이동, 선택적 두 관절 IK, 최근접 샘플링, GIF/PNG/Aseprite 출력을 수행합니다.
- `scripts/verify_rig.py`: 출력물을 다시 디코딩합니다. 모든 Aseprite 형식을 읽는 범용 변환기는 아닙니다.
- `scripts/aseprite_io.py`: 표준 RGBA 레이어·압축 cel 파일을 씁니다.

특정 엔진·형식 요청은 그에 맞춥니다. 기본 렌더러에 없는 메시 변형, 표정 교체, 포즈 전환은 필요한 기능을 구현하거나 가용 도구로 처리하고 지원 범위를 설명합니다.

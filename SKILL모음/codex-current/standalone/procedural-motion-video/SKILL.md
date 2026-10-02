---
name: procedural-motion-video
description: Create and edit videos from local photos, illustrations, existing video clips, or procedural motion graphics using Remotion. Use for image slideshows, illustrated stories, product explainers, clip montages, and ORBIT-style animated shorts, including titles, camera moves, transitions, music, rendering and verification. Natural subject animation from a still image requires a separate generation model; this skill does not provide direct CapCut UI control.
---

# Image, Footage & Motion Video

사용자 이미지·촬영 영상·도형 애니메이션을 편집하여 검증한 MP4와 수정 가능한 소스를 전달합니다. 요청에 맞는 실제 영상을 먼저 완성하고, 실행으로 확인한 절차를 재사용합니다.

## 제작 방식 선택

- **사진·일러스트·촬영 영상 또는 혼합 편집**: [references/media-editing.md](references/media-editing.md)를 읽고 `--template media --media ...`로 생성합니다. 실제 미디어 파일과 JSON 타임라인으로 구성하며 확대·이동·트림·속도·전환·자막·음원을 조정합니다.
- **원본 영상 없이 도형/타이포그래피 제작**: 기존 ORBIT 템플릿을 사용합니다. [references/workflow.md](references/workflow.md)와 필요 시 [references/design-and-audio.md](references/design-and-audio.md)를 읽습니다.
- **이미지 인물이 걷거나 표정을 바꾸는 생성 영상**: 줌·팬과 구별합니다. 가용 이미지→영상 모델이 필요한 작업이며, 이 스킬만으로 자연스러운 인물 동작을 생성했다고 말하지 않습니다. 모델이 만든 클립을 편집하는 데는 사용할 수 있습니다.

## 공통 작업 흐름

1. 사용자가 준 목적·소스 폴더·문구·길이·비율·음성 요구를 따릅니다. 경로를 받으면 직접 파일을 확인합니다. 이미지 내용이나 영상 장면을 파일명으로 추측하지 말고 썸네일/대표 프레임을 봅니다. 필요한 소스만 작업 폴더로 복사하고 원본 대응·해시를 기록합니다.
2. 시각적으로 어울리는 소재를 선택하고 장면별 메시지와 순서를 정합니다. 문서 캡처·중복·무관한 자료를 자동으로 섞지 않습니다. 구도와 피사체 위치를 보고 cover/contain·초점을 선택합니다. 원본 이미지를 편집하거나 외부 생성이 필요하면 해당 도구의 작업 규칙을 따릅니다.
3. 새 프로젝트는 `scripts/create_project.py DESTINATION`으로 만듭니다. 기본값은 기존 ORBIT입니다. 이미 존재하는 폴더를 덮어쓰지 않습니다. 사용자 소재를 스킬의 assets에 영구 번들하지 않습니다.
4. 모든 모션은 `useCurrentFrame()`과 수식·보간으로 결정합니다. 벽시계·실시간 CSS 애니메이션·시드 없는 난수에 의존하지 않습니다. 비율 변경 시 좌표·폰트·피사체 크기·마스크까지 다시 배치합니다.
5. 장면 길이와 전환 겹침으로 전체 길이를 계산합니다. 음악·원본 오디오·내레이션의 역할을 정하고 중복 재생을 막습니다. 제공 음원을 우선 사용하고 필요할 때 합성합니다. 무음 요청이면 오디오를 제거합니다.
6. 타입 검사 → 대표 장면/전환/끝 프레임 이미지 검토 → 저해상도 전체 렌더 → 최종 규격 렌더 순으로 확인합니다. `scripts/verify_video.py`로 규격·프레임 수·오디오·전체 디코딩을 검사합니다. 재생/청취를 수행할 수 있으면 확인하고, 수행한 검증 수준만 보고합니다.
7. 최종 MP4와 수정할 설정/소스 위치를 제공합니다. 제작 문서·DOCX는 요청할 때만 만듭니다.

## 결과를 정확히 설명하기

- 제작·편집 엔진은 Remotion입니다. CapCut 가져오기/내보내기 또는 UI 조작은 실제 수행했을 때만 검증했다고 설명합니다.
- 영상에 들어간 텍스트는 MP4에 합쳐집니다. 오브젝트 수정은 소스에서 합니다. 별도 WAV를 다시 올릴 때 기존 MP4 음원 중복에 주의합니다.
- CSS 구체는2D로 표현한 입체감입니다. 고정 박자와 장면을 맞춘 편집은 실시간 오디오 반응형 애니메이션과 다릅니다.
- 이미지 파일이 고해상도 출력으로 바뀌어도 원본 세부 정보가 자동 복원되는 것은 아닙니다. 기존 영상의 컨테이너 지원과 실제 코덱 디코딩 성공도 구별합니다.

## 리소스

- `assets/orbit-template/`: 기존12초 ORBIT 소스·고정 lockfile·전자음 합성.
- `assets/media-template/`: 이미지/클립 혼합 렌더러·JSON 타임라인용 소스·길이에 맞춘 잔잔한 음악 합성. 미디어와 film.json은 생성 스크립트가 사용자 작업 폴더에 만듭니다.
- `scripts/create_project.py`: 템플릿 선택·로컬 소재 검증/복사·설정 및 출처 생성.
- `scripts/verify_video.py`: 기대 규격과 전체 디코딩 검사. 시각적 품질·실제 청취·LUFS 검사는 별개입니다.

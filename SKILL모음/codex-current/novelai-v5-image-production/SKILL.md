---
name: novelai-v5-image-production
description: >-
  NovelAI Diffusion V5 기준으로 이미지 요구를 분석하고, 프레임·해상도·가시성·구도·캐릭터별 위치·자연어·태그·텍스트 렌더링·UC·설정값·편집 루프를 상황에 맞게 설계하는 실전 이미지 제작 스킬.
  캐릭터 디자인/재현, 장면/배경, 다중 캐릭터, 포스터·만화·이미지 내 텍스트, Alpha Transparency, Image2Image, Inpaint, Enhance, 결과 진단 및 반복 수정에 사용한다.
  고정 프롬프트나 고정 설정값을 강제하지 않고, NovelAI V5의 현재 기능과 검증된 실전 노하우를 구분해 판단한다.
---

# NovelAI V5 Image Production Skill

## 0. 목표

이 스킬은 NovelAI Diffusion V5에서 단순히 "태그를 많이 생성"하는 것이 아니라,

**사용자 요구 파악 → 시각 요소 분석 → 캔버스/프레임 설계 → V5 프롬프트 구조 결정 → 모델/설정 선택 → 생성 → 결과 분석 → 오류 원인 판단 → 프롬프트/설정/편집 도구 수정**

까지 수행하기 위한 범용 상위 스킬이다.

핵심 목표는 다음과 같다.

- 캐릭터의 모든 설정을 무조건 넣지 않는다.
- **이번 이미지에서 실제로 보여야 하는 정보만 남긴다.**
- 태그와 자연어 중 더 적합한 표현 방식을 선택한다.
- 다중 캐릭터/오브젝트는 필요 시 Character Prompt와 Positioning으로 분리한다.
- 이미지 비율과 카메라 프레임이 프롬프트에 미치는 영향을 고려한다.
- 결과가 틀렸을 때 원인을 먼저 분류하고 수정한다.
- 오래된 V4/V4.5 노하우를 V5 확정 규칙처럼 사용하지 않는다.
- 최종 프롬프트는 사용자가 바로 복사할 수 있도록 **코드 블록(텍스트 박스)** 으로 제공한다.

---

# 1. 지식 신뢰도 라벨

NovelAI 관련 노하우를 다음 다섯 단계로 관리한다.

## [OFFICIAL-V5]
NovelAI V5 공식 Documentation / Journal / 현재 공식 UI에서 확인된 기능.

## [VERIFIED-PRACTICE]
실제 V5 생성에서 반복적으로 효과가 확인된 작업법.

## [EXPERIMENTAL]
효과가 있었으나 장면/Seed/모델 등에 따라 결과 차이가 커 추가 검증이 필요한 방식.

## [V4.5-ONLY]
V4/V4.5에서만 명확히 확인된 기능 또는 워크플로.

## [NEEDS-V5-TEST]
V5 지원 여부 또는 최적 사용법이 현재 불명확한 항목.

근거가 약한 경험칙을 [OFFICIAL-V5]로 올리지 않는다.

---

# 2. 사용 조건

다음 요청에 사용한다.

- 캐릭터 디자인
- 기존 캐릭터 재현
- 장면/배경 생성
- 포스터/표지/CG
- 다중 캐릭터
- 캐릭터 간 상호작용
- 특정 카메라 앵글
- 만화/패널형 이미지
- 이미지 안 텍스트
- 투명 배경/스프라이트
- Image2Image
- Inpaint
- Enhance/Upscale 판단
- 생성 결과 분석 및 수정
- 사용자 이미지 분석 → NovelAI V5 프롬프트 변환
- NovelAI V5 설정값 추천
- 실패 원인 진단

이미지 생성 자체를 사용자가 요청하지 않았다면 이미지를 생성하지 않는다.
이 스킬의 기본 출력은 **프롬프트/설정/수정 전략**이다.

---

# 3. 최초 요구 분석

사용자 요청을 먼저 다음 항목으로 분해한다.

## 3.1 제작 유형
- 캐릭터 단독
- 캐릭터 재현
- 장면
- 배경 전용
- 다중 캐릭터
- 캐릭터 + 오브젝트
- 포스터/타이틀 이미지
- 만화/패널
- 스프라이트/투명 배경
- 편집/수정
- 이미지 분석 → 프롬프트 변환

## 3.2 프레임
다음을 우선 결정한다.

- Landscape
- Portrait
- Square
- 사용자 지정 크기

Normal 기본 사이즈 예:
- Landscape: **1216 × 832**
- Portrait: **832 × 1216**
- Square: **1024 × 1024**

사용자가 크기를 바꾸었다면 실제 Width × Height를 우선한다.

## 3.3 카메라
- extreme close-up
- close-up
- headshot
- bust / upper body
- cowboy shot / thigh-up
- full body
- wide shot
- establishing shot
- from above
- from below
- from side
- from behind
- over-the-shoulder
- dutch angle
- first-person
- 기타

## 3.4 화면에 실제로 보여야 하는 것
- 얼굴
- 눈
- 앞머리
- 상의
- 하의
- 신발
- 뒤쪽 머리
- 등/후면 의상
- 손
- 소품
- 배경
- 텍스트
- 다른 인물
- 전경/중경/후경

---

# 4. 가장 중요한 원칙: Frame-Aware Prompt Pruning

프롬프트는 캐릭터 설정집이 아니다.

**프롬프트에 존재하는 시각 정보는 모델에게 그것을 화면에 표현하라는 압력으로 작용할 수 있다.**

따라서 이번 프레임에서 보이지 않거나 보여서는 안 되는 요소는 제거할 수 있어야 한다.

## 예시 1 — 상반신
원하는 이미지가 upper body인데 다음이 들어 있으면:

```text
upper body,
black skirt,
thighhighs,
brown shoes
```

하체 정보가 카메라를 뒤로 빼는 압력으로 작용할 수 있다.

필요하면 다음처럼 줄인다.

```text
upper body,
white blouse,
red ribbon,
long hair,
blue eyes
```

## 예시 2 — 얼굴 클로즈업
얼굴 포커스인데 복잡한 배경을 길게 쓰면 얼굴 비중이 줄 수 있다.

피해야 할 수 있는 예:

```text
extreme close-up,
face focus,
huge forest,
mountains,
castle,
river,
sunset sky
```

필요 시:

```text
extreme close-up,
face focus,
simple background
```

또는 배경 자체를 제거한다.

## 예시 3 — 완전한 뒷모습
`from behind`, `back view`를 요구하면서 다음을 함께 넣지 않는다.

```text
blue eyes,
detailed eyelashes,
smile,
small nose,
chest ribbon,
front buttons
```

대신 후면에서 실제 보이는 정보만 사용한다.

```text
from behind,
back view,
long hair flowing down her back,
back of dress,
cape,
back ribbon
```

## 핵심 판단 질문
프롬프트에 태그를 넣기 전 항상 묻는다.

> "이 특징은 현재 카메라에서 실제로 보여야 하는가?"

아니면 삭제 후보이다.

---

# 5. Character Master Description과 Scene Prompt를 분리

캐릭터의 전체 설정과 실제 생성 프롬프트를 구분한다.

## Character Master Description
캐릭터의 전체 외형/의상/소품/특징.

## Scene Prompt
이번 이미지에서 실제로 관찰 가능한 특징만 추출.

예:

```text
[MASTER]
golden eyes,
white blouse,
red skirt,
black boots,
hair ribbon,
tail
```

### 얼굴 클로즈업
```text
golden eyes,
hair ribbon,
white blouse collar
```

### 상반신
```text
golden eyes,
hair ribbon,
white blouse
```

### 전신
```text
golden eyes,
hair ribbon,
white blouse,
red skirt,
black boots,
tail
```

---

# 6. V5 프롬프트 표현 방식 선택

프롬프트는 세 모드 중 상황에 따라 선택한다.

## 6.1 Tag-first
적합:
- 외형 고정
- 반복 재현
- 의상
- 색상
- 기본 포즈
- 카메라
- 표정
- 구도
- 익숙한 Danbooru 개념

예:

```text
1girl, solo,
short brown hair,
amber eyes,
upper body,
from below,
mischievous smile,
dark reddish brown capelet
```

## 6.2 Natural-language-first
적합:
- 복잡한 행동
- 공간 관계
- 감정 상황
- 물체 관계
- 미묘한 연출
- 구체적인 사건

예:

```text
A girl stands near the edge of the forest path.
She looks over her shoulder with a small, confident smile while the wind moves her short hair.
```

## 6.3 Hybrid
대부분의 정밀 캐릭터 작업에서 우선 고려.

태그로 "무엇인지"를 고정하고,
자연어로 "어떻게 행동하고 배치되는지"를 설명한다.

```text
1girl, solo,
short brown hair, amber eyes,
dark green sweater,
upper body,

She holds the wooden stick close to her chest and looks nervously toward the person standing on her right.
```

---

# 7. Prompt Economy

자연어도 결국 프롬프트다.

같은 의미를 태그와 문장으로 과도하게 반복하지 않는다.

피해야 할 예:

```text
nervous, anxious, nervous expression,
she looks very nervous,
she is anxious and nervous,
an extremely nervous girl
```

더 나은 예:

```text
nervous expression,
She grips the wooden stick tightly while avoiding eye contact.
```

원칙:

> 태그는 속성을 고정하고, 자연어는 관계·행동·공간을 보완한다.

---

# 8. 언어 선택

## [OFFICIAL-V5]
영어/일본어 자연어 프롬프트는 V5 핵심 지원 방식.

## [VERIFIED-PRACTICE]
한국어 자연어도 실제 V5에서 이해되는 경우가 있다.

한국어 요구를 무조건 영어로 바꾸지 않는다.
다만 안정성/재현성이 필요한 경우 영어 자연어 또는 태그로 정규화하는 선택지를 고려한다.

상황에 따라 다음을 비교할 수 있다.

- 태그만
- 영어 자연어
- 한국어 자연어
- 태그 + 영어
- 태그 + 한국어

---

# 9. 가중치

V5에서 상황에 따라 다음을 사용할 수 있다.

```text
{tag}
[tag]
1.5::tag::
0.5::tag::
-1::tag::
```

원칙:

- 처음부터 많은 태그를 과도하게 강화하지 않는다.
- 우선 기본 프롬프트로 생성한다.
- 부족한 핵심 요소만 강화한다.
- 강화로 다른 요소가 무너지면 다시 낮춘다.

예:

```text
1.4::close-up portrait::,
1.3::face focus::,
1.2::mischievous smile::
```

수치는 고정 규칙이 아니다.

---

# 10. 모델 선택

## V5 Curated
우선 고려:
- 범용 캐릭터 일러스트
- 안정적인 미형 이미지
- 비교적 일반적인 태그
- 간결한 작업

## V5 Full
우선 고려:
- 희귀 개념
- 특정 캐릭터
- 특수 복장
- 복잡한 장면
- Curated에서 잘 인식되지 않는 요소
- 더 넓은 개념 지식이 필요한 경우

고정적으로 하나를 강제하지 않는다.

---

# 11. Base Prompt와 Character Prompt 분리

다중 캐릭터에서는 프롬프트를 한 박스에 몰아 넣지 않는 것을 우선 고려한다.

## Base Prompt
전체 장면 공통 정보:

- 배경
- 시간대
- 조명
- 카메라
- 전체 구도
- 장면 분위기
- 환경
- 전역 스타일

예:

```text
indoor room, evening,
medium shot, from side,
dim warm lighting,
tense atmosphere
```

## Character Prompt 1

```text
girl,
short brown hair,
closed eyes,
blue sweater with purple stripe,
grabbing wooden stick,
uneasy expression
```

## Character Prompt 2

```text
girl,
short brown hair,
red eyes,
green sweater with yellow stripe,
holding a knife,
serious expression
```

속성 소유권이 섞이지 않게 분리한다.

---

# 12. Character Positioning

## [OFFICIAL-V5]

V5 Character Positioning은 각 Character Prompt의 위치를 화면에 개별 지정할 수 있다.

다중 캐릭터에서 적극 활용한다.

예:

```text
Character 1 → left
Character 2 → right
```

V5에서는 V4/V4.5의 고정 5×5식 위치 감각에 종속하지 않는다.

---

# 13. Character Prompt를 오브젝트/구도 앵커로 활용

## [OFFICIAL-V5 + PRACTICAL]

Character Positioning은 사람뿐 아니라 **물체 위치 제어에도 응용 가능**하다.

예:

### Base Prompt
```text
fantasy illustration,
dark ancient chamber,
dramatic lighting
```

### Slot 1
```text
female warrior,
silver armor,
holding sword
```
Position: left

### Slot 2
```text
large floating crystal,
glowing blue,
ancient artifact
```
Position: center

### Slot 3
```text
large magical orb,
purple energy,
floating
```
Position: right

이 방식은:
- 소품
- 큰 무기
- 마법 효과
- 만화 패널 내 특정 요소
- 장식물
- 배경의 특정 핵심 오브젝트

등에 사용할 수 있다.

단 모든 물체가 항상 완벽히 분리되는 것은 아니므로 결과를 보고 수정한다.

---

# 14. 만화/패널 구도

만화형 장면에서는 다음을 분리한다.

- Base: 패널 전체 구조 / 장소 / 카메라 / 분위기
- Character 1: A 캐릭터
- Character 2: B 캐릭터
- Object Slot: 책상 / 검 / 특정 소품
- Text: 말풍선 / 제목 / 효과음

예:

```text
[BASE]
manga panel,
classroom,
dramatic confrontation,
clean composition
```

```text
[CHARACTER 1]
girl, black hair,
angry expression,
pointing toward the right
```

```text
[CHARACTER 2]
boy, blond hair,
surprised expression,
leaning backward
```

```text
[OBJECT]
school desk,
open notebook,
scattered papers
```

---

# 15. 이미지 속 텍스트

## [OFFICIAL-V5]
V5는 이미지 내 텍스트 생성 능력이 크게 향상되었다.

## [VERIFIED-PRACTICE]
한국어 이미지 텍스트도 실제 V5에서 매우 잘 생성되는 경우가 있다.

텍스트 이미지는 일반 이미지와 별도로 설계한다.

먼저:
1. 정확한 문구
2. 위치
3. 크기
4. 스타일
5. 주변 여백
6. 텍스트가 붙는 물체
를 결정한다.

예:

```text
poster composition,
upper body,
clean graphic layout,
empty space above the character for a title
```

```text
Text: 여원의 시작
```

자연어 보조:

```text
A large Korean title is placed at the top center, clearly separated from the character.
```

## 주의
텍스트가 필요한데 Quality Tags 등에 `no text`가 포함되어 있으면 충돌할 수 있다.

텍스트 요청이 있으면:
- `no text` 충돌 확인
- 필요 시 Quality Tags 조정/비활성화
- 생성 후 철자와 위치를 반드시 검수

---

# 16. Alpha Transparency

V5의 native alpha를 필요에 따라 사용한다.

대표 개념:

```text
transparent background
has alpha
alpha transparency
```

용도:
- 캐릭터 스프라이트
- UI용 캐릭터
- 단독 오브젝트
- 반투명 마법 효과
- 반투명 물체

투명 배경이 목적이면 배경 설명을 과도하게 넣지 않는다.

---

# 17. Background-only

사람 없는 풍경이나 배경 중심 작업에서는 V5의 background-oriented prompting을 고려한다.

예:

```text
background dataset,
fantasy forest path,
ancient stone bridge,
morning mist,
soft sunlight
```

배경 전용 이미지는 캐릭터 정보나 불필요한 인물 태그를 제거한다.

---

# 18. Quality Tags / Complexity

V5 Quality Tags와 Complexity는 자동 고정값이 아니다.

Complexity 예:

```text
low complexity
medium complexity
high complexity
ultra complexity
```

원칙:
- `ultra`가 항상 더 좋은 것은 아니다.
- 강한 스타일화에서는 low/high 모두 실험 가치가 있다.
- 독특한 화풍에서 자동 Quality Tags가 스타일을 평준화하면 OFF 테스트를 고려한다.
- 이미지 내 텍스트가 필요한 경우 `no text` 충돌을 확인한다.

---

# 19. Undesired Content

UC는 무조건 길게 만들지 않는다.

기본 원칙:

1. 적절한 V5 UC preset 선택
2. 실제 오류 확인
3. 오류에 대응하는 항목만 추가

예:

```text
heterochromia,
extra fingers,
extra limbs,
bad hands
```

사용자의 Prompt와 충돌하는 UC를 넣지 않는다.

---

# 20. Steps / Guidance / Sampler / Seed

설정값을 고정 공식처럼 쓰지 않는다.

## Steps
낮은 Step으로 구도 탐색 후 좋은 결과를 정제하는 전략을 고려한다.

## Prompt Guidance
너무 높이면 프롬프트 준수는 올라가도 이미지가 과하게 굳거나 왜곡될 수 있다.
너무 낮으면 프롬프트 영향이 약해질 수 있다.

## Sampler
특별한 이유가 없으면 공식 권장 계열을 우선 고려하고, 결과에 따라 변경한다.

## Seed
좋은 구도가 나왔는데 세부만 고치고 싶다면 Seed 고정을 적극 고려한다.

```text
구도 성공
→ Seed 고정
→ 한 요소만 변경
→ A/B 비교
```

---

# 21. Image2Image

Image2Image는 "새로 생성"과 "부분 수정" 사이의 핵심 도구다.

## Strength
원본 구조를 얼마나 벗어날 수 있는지.

## Noise
새로운 세부 정보를 얼마나 만들 여지를 줄지.

활용:
- 얼굴/의상 미세 수정
- 기존 구도 유지
- 스타일 변화
- 생성 결과 반복 개선

무조건 Strength를 높이지 않는다.

---

# 22. Inpaint

현재 V5 Full Inpaint는 V5 작업 흐름에 포함 가능.

Curated Inpaint는 현재 상태를 실제 UI 기준으로 재확인해야 하는 항목으로 취급한다.

부분 오류에 적합:
- 손
- 얼굴
- 소품
- 특정 의상
- 텍스트 일부
- 작은 배경 요소

전체 구도가 틀렸는데 Inpaint로 억지로 고치지 않는다.

---

# 23. Enhance vs Upscale

## Enhance
Diffusion을 다시 거치므로 프롬프트와 설정의 영향을 받을 수 있다.
디테일/내용 보강에 사용.

## Upscale
주로 크기 확대.
내용 오류 수정 도구로 취급하지 않는다.

판단:
- 내용/형태 문제 → Prompt / Img2Img / Inpaint / Enhance
- 최종 크기 문제 → Upscale

---

# 24. 생성 결과 진단

결과를 보고 바로 프롬프트 전체를 갈아엎지 않는다.

먼저 오류를 분류한다.

## A. 구도 문제
예:
- 얼굴이 너무 작음
- 전신이 나옴
- 배경이 과도함
- 캐릭터 위치가 틀림

수정:
- 불필요한 외형/의상/배경 태그 제거
- framing 강화
- Positioning 조정
- 캔버스 비율 재검토

## B. 캐릭터 속성 문제
예:
- 눈색 오류
- 의상 섞임
- 액세서리 누락

수정:
- 해당 Character Prompt로 이동
- 핵심 태그 강조
- 다른 캐릭터의 충돌 속성 제거
- 필요 시 Seed 고정 후 A/B

## C. 포즈/행동 문제
수정:
- 자연어로 관계 설명
- 태그와 자연어 중복 제거
- 방향 정보 추가
- object/character slot 분리

## D. 손/얼굴 등 국소 오류
수정:
- Inpaint 우선 검토
- 전체 Prompt를 불필요하게 바꾸지 않는다.

## E. 텍스트 오류
수정:
- 정확한 문자열 확인
- 위치 설명 단순화
- `no text` 충돌 확인
- Seed 유지
- 필요 시 Inpaint

## F. 스타일 문제
수정:
- Quality Tags ON/OFF 비교
- Complexity 변경
- 스타일 관련 태그를 별도 정리
- 과도한 품질 태그 제거

---

# 25. 수정 우선순위

가능하면 한 번에 하나씩 수정한다.

권장:

```text
1. 프레임/구도
2. 캐릭터 위치
3. 캐릭터 외형
4. 포즈/행동
5. 의상/소품
6. 배경
7. 조명/스타일
8. 텍스트
9. 세부 디테일
```

구도가 실패한 상태에서 눈색이나 작은 장식을 먼저 고치지 않는다.

---

# 26. 다인 장면의 속성 오염 방지

Base Prompt에 개별 캐릭터의 특성을 섞지 않는다.

피해야 할 예:

```text
red eyes,
blue eyes,
green sweater,
blue sweater,
black hair,
brown hair
```

대신:

```text
[CHARACTER 1]
red eyes,
green sweater,
brown hair
```

```text
[CHARACTER 2]
blue eyes,
blue sweater,
black hair
```

개별 Character Prompt가 너무 길어도 오히려 혼선이 생길 수 있으므로 현재 장면에 필요한 특징만 남긴다.

---

# 27. Prompt Chunks 활용

자주 쓰는 캐릭터 프롬프트는 Chunk로 저장할 수 있지만,
전체 Master Prompt를 무조건 호출하지 않는다.

추천 분할:

```text
@character_core
@character_face
@character_upper_body
@character_full_body
@character_back_view
@character_accessories
```

프레임에 따라 필요한 Chunk만 사용한다.

---

# 28. V4/V4.5 기능 분리

다음은 V5 기본 기능으로 취급하지 않는다.

## [V4.5-ONLY]
- Precise Reference
- V4.5 기준 Character Reference / Fidelity 워크플로
- V4.5 전용 Positioning 사고방식
- V4.5 Quality Tags / UC를 V5에 그대로 복사하는 방식

## [NEEDS-V5-TEST]
- Vibe Transfer의 현재 V5 지원 상태
- V5 Curated native Inpaint 상태
- V5에서 SMEA / SMEA DYN 실제 지원 상태
- CFG Rescale의 V5 최적 사용법
- 한국어 자연어 프롬프트의 정량적 안정성
- 한국어 텍스트 렌더링의 길이/폰트/배치 한계
- Character Prompt를 복잡한 비인물 오브젝트에 사용할 때의 안정성
- 해상도/크롭별 Prompt Pruning 효과 정량 비교
- 다수 Character Prompt에서 안정적인 실전 인원 수

---

# 29. 사용자 이미지 분석 → NovelAI Prompt 변환

입력 이미지를 분석할 때 다음 순서를 따른다.

1. 캔버스 비율
2. 카메라 거리
3. 카메라 각도
4. 인원 수
5. 각 인물 위치
6. 얼굴 방향
7. 표정
8. 머리
9. 눈
10. 신체 포즈
11. 의상
12. 손/소품
13. 전경
14. 중경
15. 배경
16. 조명
17. 색감
18. 스타일
19. 이미지 내 텍스트
20. 실제 프레임에서 보이지 않는 정보 제거

그 후:
- Tag-first
- Natural-language-first
- Hybrid
중 하나를 선택한다.

---

# 30. 기본 출력 형식

사용자가 NovelAI 프롬프트를 요청하면 설명만 하지 말고 **복사 가능한 코드 블록**을 제공한다.

단일 캐릭터 예:

### Prompt
```text
...
```

### Natural Language Add-on
```text
...
```

### Undesired Content
```text
...
```

### Settings
```text
Model:
Resolution:
Steps:
Prompt Guidance:
Sampler:
Seed:
Quality Tags:
Complexity:
```

---

# 31. 다중 캐릭터/오브젝트 출력 형식

### Base Prompt
```text
...
```

### Character / Object 1
```text
...
```
Position: ...

### Character / Object 2
```text
...
```
Position: ...

### Character / Object 3
```text
...
```
Position: ...

### Image Text
```text
...
```

### Text Placement / Description
```text
...
```

### Undesired Content
```text
...
```

### Settings
```text
...
```

필요한 슬롯만 출력한다.

---

# 32. 응답 스타일

- 먼저 짧게 전략을 설명한다.
- 실제 NovelAI 입력용 내용은 코드 블록으로 분리한다.
- 사용자가 "태그만" 요청하면 설명을 최소화하고 태그 코드 블록을 우선한다.
- 사용자가 설정값까지 요청하면 설정도 별도 코드 블록으로 준다.
- 다인/오브젝트 Positioning이 필요하면 Base와 각 Character/Object Prompt를 반드시 분리한다.
- 이미지 내 텍스트가 있으면 문자열을 별도 코드 블록으로 출력한다.
- 요청하지 않은 이미지 생성은 하지 않는다.

---

# 33. 최종 판단 규칙 요약

이 스킬은 다음 세 질문을 항상 먼저 해결한다.

## Visibility
**이번 프레임에 무엇이 실제로 보여야 하는가?**

## Prompt Economy
**같은 정보를 중복 없이 얼마나 짧고 명확하게 전달할 수 있는가?**

## Spatial Ownership
**각 특징·인물·물체가 누구에게 속하고 화면 어디에 있어야 하는가?**

그리고 생성 후에는:

> "무엇을 더 넣을까?"보다  
> "무엇이 현재 결과를 망가뜨리고 있는가?"를 먼저 판단한다.

이 원칙을 NovelAI V5 이미지 제작 전 과정에 적용한다.

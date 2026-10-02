# NovelAI Diffusion V5 — Codex용 프롬프트 작성 가이드

> 목적: Codex/AI에게 **NovelAI Diffusion V5의 특징과 프롬프트 작성 방식을 빠르게 전달하기 위한 참고 문서**.
>
> 이 문서는 사용자가 제공한 NovelAI Diffusion V5 출시 소개 내용과 대화에서 정리한 실전 프롬프트 운용 원칙을 기반으로 한다.

---

## 1. 한 줄 요약

**NovelAI Diffusion V5는 기존 태그 프롬프트를 그대로 지원하면서, 영어/일본어 자연어 이해, 복잡한 장면 구성, 다인물 배치, 배경, 텍스트, 투명도, 미세 디테일 표현을 크게 강화한 이미지 생성 모델이다.**

V5에서는 프롬프트를 단순한 태그 나열만으로 작성하기보다 다음과 같은 **하이브리드 방식**을 기본으로 생각하는 것이 좋다.

- **태그** → 캐릭터의 고정적인 외형, 의상, 색, 표정, 화풍, 기본 구도
- **영어 자연어** → 행동, 위치 관계, 상호작용, 카메라, 장면 구조, 배경, 조명, 연출
- **Character Prompt** → 다인물 장면에서 각 캐릭터의 독립적인 외형과 특징

즉:

> **태그 = 무엇이 존재하는가**  
> **자연어 = 그것들이 어떻게 배치되고 행동하는가**

---

# 2. V5의 주요 변경점

## 2.1 자연어 이해 강화

V4.5에서도 자연어 프롬프트를 사용할 수 있었지만, V5에서는 자연어 이해가 크게 강화되었다.

예전처럼 모든 내용을 Danbooru 스타일 태그로 압축하지 않아도 된다.

### 기존 태그식 예시

```text
1girl, blonde hair, green eyes, white dress,
standing, flower field, sunset, looking at viewer,
wind, wide shot
```

### V5 자연어 혼합 예시

```text
1girl, blonde hair, green eyes, white dress,
flower field, sunset, wide shot, high complexity.

A young woman is standing alone in a vast flower field.
A soft evening wind blows her hair and dress toward the left.
She is looking slightly past the viewer instead of directly at the camera.
The setting sun is behind her, illuminating the edges of her silhouette.
```

태그 프롬프트는 폐기된 것이 아니며 여전히 정상적으로 지원된다.

---

## 2.2 공식 프롬프트 언어

V5가 공식적으로 중점 지원하는 언어는 다음 두 가지다.

- **영어**
- **일본어**

테스트 과정에서는 중국어, 독일어, 스페인어, 포르투갈어 등 다른 언어도 작동하는 것이 확인되었지만, 이들은 학습의 주된 초점이 아니므로 결과 편차가 있을 수 있다.

### Codex 기본 원칙

NovelAI V5용 프롬프트를 생성할 때 별도 요구가 없다면:

> **영어 태그 + 영어 자연어**를 기본값으로 사용한다.

---

# 3. 프롬프트 공간 증가

사용자가 제공한 NovelAI V5 자료의 Prompt Size 표에는 다음 수치가 표시되어 있다.

| 모델 | Prompt (effective) | Text (incl. space and newline) |
|---|---:|---:|
| V4.5 | 505 | 118 |
| V5 Curated | 703 | 374 |
| V5 Full | 1471 | 750 |

V5 Full은 V4.5에 비해 훨씬 긴 프롬프트를 수용할 수 있다.

따라서 복잡한 캐릭터 디자인, 장면 묘사, 인물 관계, 환경 설명 등을 이전보다 덜 압축해서 작성할 수 있다.

단:

> **프롬프트 공간이 크다고 해서 항상 최대 길이까지 채우는 것이 좋은 것은 아니다.**

중요한 정보와 반복적으로 유지해야 하는 특징을 명확하게 배치하고, 불필요한 동의어 반복은 피한다.

예:

```text
long hair, very long hair, extremely long hair,
beautiful long hair, waist-length long hair...
```

처럼 같은 의미를 과도하게 반복하는 것은 권장하지 않는다.

---

# 4. 이미지 품질 및 세부 표현

V5는 자체 데이터셋으로 학습한 **32채널 커스텀 VAE**를 사용한다.

V4.5는 16채널 VAE를 사용했다.

V5에서는 다음과 같은 작은 요소의 표현력이 개선된 것으로 소개되었다.

- 눈의 미세한 선
- 보석
- 액세서리
- 작은 의상 디테일
- 텍스트
- 건축물의 세부 구조
- 식생 및 복잡한 환경 요소

특히 복잡한 오리지널 캐릭터나 장식이 많은 판타지 캐릭터 제작에 유리하다.

---

# 5. 복잡한 캐릭터 디자인 운용

V5는 복잡한 캐릭터를 이전보다 잘 표현할 수 있지만, **완벽한 캐릭터 고정 기능 자체를 의미하는 것은 아니다.**

여러 이미지에서 같은 캐릭터를 유지하려면 프롬프트 설계가 여전히 중요하다.

## 5.1 Identity Anchor

복잡한 캐릭터는 핵심 특징 5~10개 정도를 **Identity Anchor**처럼 고정해서 반복 사용하는 것이 좋다.

예:

```text
very long dark purple hair,
gold highlights,
heterochromia,
asymmetrical dragon horns,
fragmented golden halo,
black and purple layered dress,
gold ornaments,
large dragon wings
```

장면이 바뀌더라도 위 핵심 특징은 가능하면 동일한 표현으로 유지한다.

---

## 5.2 외형 정보의 우선순위

### 반드시 유지할 정보

- 머리색 / 머리 구조
- 눈 색 또는 특수 눈
- 대표 장식
- 뿔 / 날개 / 꼬리 등 종족 특징
- 의상의 전체 실루엣
- 대표 색 조합
- 상징적인 소품 또는 무기

### 상황에 따라 생략 가능한 정보

- 매우 작은 브로치
- 미세한 자수
- 작은 체인 하나
- 화면에서 보이지 않는 장식
- 특정 구도에서 의미 없는 세부 요소

캐릭터 디자인이 복잡하다고 해서 모든 디테일을 매번 동일한 비중으로 넣을 필요는 없다.

---

# 6. V5 권장 프롬프트 구조

Codex가 V5용 프롬프트를 생성할 때 다음 구조를 기본 템플릿으로 사용한다.

```text
[캐릭터 수 / 핵심 대상]
[외형]
[의상]
[장식 / 소품]
[화풍]
[기본 구도]
[complexity 관련 태그]

[자연어: 장면]
[자연어: 행동]
[자연어: 인물 간 관계]
[자연어: 공간 배치]
[자연어: 카메라]
[자연어: 조명]
[자연어: 분위기]
```

---

# 7. 태그와 자연어의 역할 분담

## 태그로 쓰기 좋은 것

```text
1girl
silver hair
red eyes
long hair
black dress
white gloves
dragon horns
dragon wings
full body
wide shot
looking at viewer
visual novel cg
high complexity
depthness
```

즉 **원자적인 속성**은 태그가 편하다.

---

## 자연어로 쓰기 좋은 것

### 복잡한 행동

```text
She sits sideways on the chair with one leg crossed over the other,
holding a cup near her chest while looking at the woman across the table.
```

### 위치 관계

```text
The blonde girl stands on the left side of the image.
The black-haired girl stands slightly behind her on the right.
```

### 상호작용

```text
The blonde girl grabs the other girl's hand and pulls her forward,
while the black-haired girl looks surprised.
```

### 카메라

```text
The camera looks down from a high angle.
The foreground character is close to the camera while the city appears far below.
```

### 환경 구조

```text
The street continues deep into the background,
with narrow medieval buildings on both sides and a large castle visible at the end.
```

### 비대칭 디자인 설명

```text
Her left horn is longer and more curved than her right horn,
creating a deliberately asymmetrical silhouette.
```

이처럼 **A와 B의 관계**가 들어가는 정보는 자연어로 작성하는 것이 유리하다.

---

# 8. 다인물 장면

V5는 캐릭터 배치 기능이 크게 강화되었다.

출시 소개 자료에서는 테스트 과정에서 캐릭터 배치 기능을 이용해 **최대 22명의 서로 다른 캐릭터를 한 화면에 표시한 사례**가 언급되었다.

또한 캐릭터 프롬프트를 캔버스에 자유롭게 배치할 수 있도록 UI가 개편되었다.

다인물에서는 다음 구조를 권장한다.

## Base Prompt

장면 전체를 설명한다.

```text
2girls, fantasy tavern, medieval interior,
wooden table, warm lighting,
high complexity, depthness.

Two young women are sitting across from each other at a small wooden table.
The woman on the left leans forward excitedly while explaining something with both hands.
The woman on the right quietly watches her while holding a ceramic cup near her lips.
A folded map is placed between them.
```

## Character A

```text
1girl,
short blonde hair,
green eyes,
small ahoge,
white blouse,
brown vest,
green skirt,
cheerful
```

## Character B

```text
1girl,
very long black hair,
red eyes,
black coat,
white shirt,
silver necklace,
expressionless
```

### 원칙

> **Base Prompt = 장면의 감독**  
> **Character Prompt = 각 캐릭터의 설정표**

다인물일수록 캐릭터 외형을 Base Prompt 하나에 전부 섞어 쓰지 않는 것이 좋다.

---

# 9. 배경 및 환경

V5는 배경, 건축물, 초목 등 복잡한 환경 표현도 강화되었다.

배경이 중요한 그림에서는 자연어를 적극적으로 사용한다.

```text
1girl, small figure,
traveler, hooded cape, backpack,
fantasy, ultra wide shot,
high complexity, depthness.

A lone traveler walks along an ancient stone road through an enormous green valley.
The traveler occupies only a small portion of the lower center of the image.
The landscape should dominate the composition.

On the left side of the valley, enormous ancient trees rise above the surrounding forest.
On the right, a wide river winds toward a distant mountain range.
Far ahead, a medieval city is visible beneath a massive stone castle.

The road begins prominently in the foreground and gradually becomes narrower as it continues toward the city, creating strong visual depth.
```

중요한 점은 단순히 배경 오브젝트를 나열하는 것이 아니라:

- 무엇이 전경인가
- 무엇이 후경인가
- 캐릭터가 화면에서 얼마나 큰가
- 길이나 구조물이 어느 방향으로 이어지는가
- 장면의 주 피사체가 캐릭터인지 환경인지

등을 문장으로 설명하는 것이다.

---

# 10. 투명도 / Alpha

V5의 커스텀 VAE는 알파 채널 투명도를 지원한다.

관련 태그:

```text
transparent background
has alpha
alpha transparency
```

## transparent background

캐릭터 등을 실제 투명 배경 위에 출력할 때 사용한다.

필요에 따라 강화 예시:

```text
2.1::transparent background::
```

## alpha transparency

장면 속 특정 요소 자체를 반투명하게 표현하고 싶을 때 사용할 수 있다.

예:

- 마법 효과
- 물
- 불
- 우산
- 베일
- 반투명 날개

예시:

```text
alpha transparency, has alpha.

Several thin rings of translucent water float around her body.
The water should remain partially transparent so that her clothing can still be seen through it.
```

---

# 11. V5 신규/주요 태그

## depthness

```text
depthness
```

이미지 음영과 깊이감을 강화하는 용도의 태그로 소개되었다.

---

## attractive male

```text
attractive male
```

매력적인 남성 표현용 태그.

---

## Complexity 태그

```text
low complexity
medium complexity
high complexity
ultra complexity
```

이미지의 복잡도 및 정보량에 영향을 준다.

### 일반적인 권장

```text
high complexity
```

일반적인 고품질 이미지에 적합한 기본값으로 소개되었다.

### 특수 스타일 실험

```text
low complexity
ultra complexity
```

보다 개성 있는 스타일 변화에 사용할 수 있다.

---

## 시대감 관련 메타 태그

```text
meta:novel era
meta:golden era
```

이미지의 시대감/현대적인 느낌을 미묘하게 조정하는 용도로 소개되었다.

---

## Visual Novel 관련 태그

```text
visual novel art
visual novel bg
visual novel cg
visual novel chibi
visual novel sprite
```

비주얼 노벨 용도에 맞는 이미지 유형을 모델에게 알려주는 태그다.

---

# 12. 텍스트 렌더링

V5는 이미지 내 텍스트 표현이 강화되었다.

영어, 일본어, 중국어 등 다양한 언어의 텍스트 렌더링을 지원한다고 소개되었다.

프런트엔드에서는 원하는 텍스트를 따옴표로 묶어 사용할 수 있다.

예:

```text
A handwritten speech bubble with green letters and a white background
floating beside the purple-haired girl's head, "Hello, world!"
```

자연어를 이용해 다음 요소도 설명할 수 있다.

- 텍스트 위치
- 글씨 색
- 말풍선 모양
- 손글씨 느낌
- 배경색

필요한 경우 직접 `Text:` 블록을 지정하는 기존 방식도 사용할 수 있다.

---

# 13. 만화 / Comic 생성

V5는 자연어로 만화 페이지의 레이아웃을 설명하거나 캐릭터 위치를 지정해 여러 패널을 한 번에 생성하는 기능이 강화되었다.

V4.5의 단순한 제한적 패널 구성보다 더 복잡한 다단 컷 레이아웃을 다룰 수 있도록 소개되었다.

예시 방향:

```text
A manga page divided into four panels.
The upper half contains one wide establishing panel.
The lower half is divided into three smaller panels.
The same two characters appear consistently throughout all panels.
```

만화에서는 특히:

- 패널 수
- 패널 위치
- 캐릭터 배치
- 각 컷의 행동
- 대사 위치

를 자연어로 명확히 지정하는 것이 좋다.

---

# 14. Visual Novel Sprite 예시

```text
1girl,
medium blonde hair,
green eyes,
white blouse,
navy skirt,
small gold necklace,
gentle smile,
standing,
front view,
arms relaxed,
visual novel sprite,
high complexity,
transparent background,
has alpha.

A clean full-body visual novel character sprite.
She is standing naturally while facing almost directly toward the viewer.
Both arms should remain clearly visible and separated from the body.
Keep the silhouette clean and readable.
No environmental background or ground.
```

---

# 15. Visual Novel CG 예시

```text
1girl, 1boy,
fantasy,
visual novel cg,
high complexity, depthness,
night, starry sky.

A young woman and a young man stand on the balcony of an old castle late at night.
The woman stands near the stone railing on the left side, looking toward the distant city.
The man stands several steps behind her on the right.

She turns only her head slightly toward him without fully turning her body.
He hesitates as though he wants to say something but cannot find the words.

The emotional focus of the image should be the quiet distance between the two characters rather than physical contact.
```

---

# 16. 복잡한 캐릭터 예시

```text
1girl, solo,
very long dark purple hair,
black hair, gold highlights,
heterochromia,
red eye, golden eye,
pale skin,
asymmetrical black dragon horns,
large black dragon wings,
long dragon tail,
fragmented golden halo,
floating crystal shards,
layered black and purple dress,
high collar,
detached sleeves,
gold embroidery,
ornate corset,
multiple belts,
gold chains,
black gloves,
thigh boots,
ancient ornaments,
clock motif,
ring motif,
high complexity,
depthness,
fantasy,
full body.

An ancient dragon woman stands in the center of a ruined circular temple.
Her outfit consists of several overlapping layers of black and deep purple fabric decorated with thin golden patterns.
A broken golden halo floats behind her head in several separate pieces.

Her left horn is longer and more curved than her right horn, creating a deliberately asymmetrical silhouette.
Behind her, enormous black dragon wings are partially spread.

Small crystal fragments slowly orbit around her body.
The stone floor beneath her contains concentric circular patterns.
Parts of the surrounding space appear subtly distorted, as though the distance between objects is bending.

The camera is positioned slightly below eye level.
Soft golden light enters from behind while the foreground remains dark and mysterious.
```

---

# 17. 액션 포즈 예시

```text
1girl,
pink hair, ponytail, amber eyes,
light armor, short jacket,
black gloves, combat boots,
long spear,
high complexity,
dynamic pose,
action, fantasy.

A young spearfighter is sprinting across the battlefield from left to right.
She holds the spear horizontally with both hands while lowering her upper body into a fast running stance.

Her left foot has just pushed off the ground while her right leg is extended forward.
Dust and small fragments of stone scatter behind her feet.

The spear points slightly upward toward an enemy outside the frame.
Her ponytail and jacket trail sharply behind her because of her speed.

The camera follows her from a low three-quarter side angle.
The character should remain clearly readable despite the strong movement.
```

복잡한 포즈에서는 `running`, `holding spear`, `dynamic pose` 같은 태그만 쓰기보다 신체의 방향과 움직임을 자연어로 설명하는 것이 좋다.

---

# 18. Max✨ Enhance

V5에서는 이미지 향상/업스케일 기능에 새로운 **Max✨** 단계가 추가되었다.

소개 내용에 따르면:

- 더 높은 해상도
- 더 선명한 결과
- 개선된 업스케일러

를 제공하며, 업스케일러를 단독으로 사용할 수도 있다.

---

# 19. 인페인팅

출시 시점 기준:

- **V5 Full** → V5 인페인팅 지원
- **V5 Curated** → 전용 V5 Curated 인페인팅 모델은 아직 준비 중
- 그동안 V5 Curated에서는 V4.5 Curated 인페인팅 기능을 사용

---

# 20. 출시 시점에 아직 포함되지 않은 기능

제공된 출시 소개 기준으로 다음 기능은 V5 출시 시점에 아직 준비 중이다.

- Precision Reference
- V5 Curated 전용 Inpainting
- Vibe Transfer

이 기능들은 V5용으로 추가 학습 후 순차 배포될 예정이라고 소개되었다.

Codex는 위 기능이 이미 존재한다고 가정해서는 안 된다.

---

# 21. UI 변경

V5 출시와 함께 이미지 생성 UI가 전면 개편되었다.

주요 변경:

- 출력 뷰어 스크롤
- 출력 뷰어 확대/축소
- 이전 생성 결과를 현재 결과 아래에 누적 표시
- `Simple Output Viewer` 설정
- `Reduce Motion` 설정
- Character Prompt 고정 가능
- Character Prompt 이름 지정 가능
- 출력 뷰어에서 캐릭터 위치 직접 지정
- 위치 정렬용 격자 옵션
- 여러 이미지 동시 고정
- 고정 이미지를 왼쪽 전용 영역에 표시
- 고급 설정이 접혀 있어도 현재 설정값 표시
- 기록 사이드바 크기 조절
- 모바일 UI를 드래그 가능한 단일 시트 형태로 변경

Character Prompt의 이름은 메타데이터에 저장되거나 다시 불러올 때 유지되지 않는다고 소개되었다.

---

# 22. Codex가 NAI V5 프롬프트를 만들 때 지켜야 할 규칙

## 기본 규칙

1. 별도 요청이 없다면 **영어로 작성한다.**
2. 기존 NovelAI/Danbooru 태그를 버리지 않는다.
3. 캐릭터의 핵심 디자인은 태그로 먼저 고정한다.
4. 행동/관계/공간/카메라는 자연어를 적극 활용한다.
5. 다인물은 가능하면 Character Prompt를 분리한다.
6. 캐릭터 핵심 특징은 생성마다 동일한 표현을 유지한다.
7. 중요하지 않은 세부 특징을 무조건 모두 반복하지 않는다.
8. 복잡한 장면일수록 위치 관계를 명확히 설명한다.
9. 프롬프트 길이가 늘었다는 이유만으로 불필요하게 장황하게 쓰지 않는다.
10. V5가 자연어를 이해한다고 해서 결과가 완전히 결정론적으로 고정되는 것은 아니다.

---

# 23. 요청별 Codex 출력 방식

## 사용자가 "태그만" 요청

순수 태그만 제공한다.

```text
1girl, silver hair, red eyes, black dress, ...
```

---

## 사용자가 "V5 프롬프트" 요청

기본적으로 **태그 + 영어 자연어**를 사용한다.

```text
[tags]

[natural-language scene description]
```

---

## 사용자가 "복잡한 캐릭터" 요청

다음 순서로 정리한다.

```text
Core Identity
Hair / Eyes / Body
Non-human Features
Clothing
Accessories
Weapon / Props
Composition

Natural-language scene description
```

---

## 사용자가 "여러 캐릭터" 요청

가능하면 다음과 같이 분리한다.

```text
BASE PROMPT
...

CHARACTER 1
...

CHARACTER 2
...
```

---

## 사용자가 "배경 중심" 요청

캐릭터 태그를 과도하게 늘리지 말고 자연어로 공간 구조를 상세히 작성한다.

특히:

- foreground
- midground
- background
- left/right
- distance
- scale
- camera angle

을 명확히 한다.

---

# 24. 가장 중요한 실전 결론

V4.5의 전형적인 접근:

```text
태그 + 태그 + 태그 + 태그 + 태그
```

V5의 권장 접근:

```text
정확하게 고정할 정보 → 태그
복잡하게 설명할 정보 → 영어 자연어
다인물 개별 정보 → Character Prompt
```

최종적으로 다음 한 문장으로 기억하면 된다.

> **NovelAI Diffusion V5에서는 태그를 캐릭터의 DNA처럼 사용하고, 자연어를 장면을 지휘하는 감독 지시문처럼 사용한다.**

---

# 25. Codex용 초간단 컨텍스트

아래 내용만 별도로 Codex 프롬프트에 붙여 넣어도 된다.

```text
NovelAI Diffusion V5 supports both classic NovelAI/Danbooru-style tags and significantly improved natural-language prompting.

When writing prompts for NAI V5, use a hybrid structure by default:
- Use tags for atomic and persistent character traits: hair, eyes, clothing, accessories, species features, expressions, art style, and basic composition.
- Use English natural language for spatial relationships, detailed poses, interactions, camera direction, environment structure, lighting, and narrative staging.
- For multi-character scenes, keep the base prompt focused on the overall scene and use separate Character Prompts for each character whenever possible.
- Keep a stable set of identity-anchor tags for recurring original characters.
- Do not assume that longer prompts are automatically better; prioritize clear, non-redundant information.

Useful V5 tags include:
depthness,
low complexity,
medium complexity,
high complexity,
ultra complexity,
transparent background,
has alpha,
alpha transparency,
visual novel art,
visual novel bg,
visual novel cg,
visual novel chibi,
visual novel sprite,
meta:novel era,
meta:golden era,
attractive male.

English and Japanese are the primary officially supported prompt languages.
```

---

## 문서 기준

이 문서는 사용자가 제공한 **NovelAI Diffusion V5 출시 소개 내용**, Prompt Size 이미지, 그리고 해당 내용을 바탕으로 정리한 V5 실전 프롬프트 작성 원칙을 Codex가 이해하기 쉬운 형태로 재구성한 것이다.


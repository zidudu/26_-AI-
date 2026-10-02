---
name: search-images
description: Search the web for relevant images and display them directly in chat. Use when the user asks to find, show, collect, or visually reference images. Search intensity controls how broadly and repeatedly to search; when unspecified, use the default intensity.
---

# Search Images

Find images that are genuinely relevant to the user's subject and show them directly in the conversation.

## Core behavior

- Use image search rather than ordinary web search when the main goal is visual reference.
- Do not stop after one weak search if better images are likely available.
- Vary search wording when repeated searches can uncover meaningfully different results.
- Prefer useful, clear, high-quality images over filling space with near-duplicates.
- Show the retrieved images in image groups/carousels.
- Keep text brief unless the user also asks for explanation.
- Do not generate new images unless the user explicitly asks to create or modify an image.

## Search intensity

Interpret natural language flexibly. The user does not need to use exact level names.

### Light
Examples: "조금", "간단히", "몇 개만", "약하게"

- 1 image-search pass
- Narrow, direct query
- Usually 1 image group

### Default
Used when no intensity is specified.

- 2 image-search passes when useful
- Start with the direct query, then one useful variation
- Usually 1–2 image groups
- Avoid redundant results

### Strong
Examples: "많이", "강하게", "여러 개", "다양하게", "이미지 많이 찾아줘"

- 3–4 image-search passes
- Use distinct query angles, synonyms, model/product names, parts, close-ups, diagrams, or contextual views as appropriate
- Show multiple useful image groups when results are meaningfully different

### Maximum
Examples: "최대한", "풀로", "싹 다", "이미지 검색 풀가동", "아주 많이"

- 5+ image-search passes when worthwhile
- Explore multiple query formulations and subtopics
- Prefer several focused image groups over one huge mixed group
- Stop when additional searches mostly repeat the same images

## Query expansion

For repeated searches, vary only what helps the user's intent. Possible variations include:

- Korean / English terminology
- Full name / abbreviation
- Overview / close-up / internal structure
- Real photo / diagram / schematic
- Front / rear / side / installed view
- Older / newer generation
- Model or version names
- Component names
- Application or usage context

Do not mechanically use every variation.

## Relevance rules

- Prefer images that directly depict the requested subject.
- Avoid obvious stock images, watermarked junk, unrelated thumbnails, and duplicates.
- When the subject could mean multiple things, use surrounding conversation to infer the likely meaning.
- If ambiguity materially changes the search, search the most likely interpretation first and briefly state the interpretation.
- For people, places, products, vehicles, components, animals, historical events, and other visual subjects, prioritize concrete visual references.

## Output behavior

- Put image groups near the relevant explanation.
- If several searches reveal different visual categories, separate them by purpose rather than mixing everything together.
- Do not claim an image shows something that cannot be verified from the search result.
- If useful images are scarce, say so instead of padding the response with weak results.

## Examples

User: "IDB 밸브 이미지 보여줘"
→ Default intensity. Search the direct term plus one useful technical variation, then show the best image group(s).

User: "SRR 4.0 이미지 많이 찾아줘"
→ Strong intensity. Search several variations such as product name, installed view, sensor module, and vehicle application; show multiple non-redundant image groups.

User: "타스-R 관련 이미지 풀로"
→ Maximum intensity. Run multiple complementary image searches and present several focused groups until new searches become repetitive.

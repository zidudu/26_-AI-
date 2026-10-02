---
name: eli5
description: Explain complex topics or unfamiliar code with large diagrams and concise text in a visual HTML page. Use for ELI5, /eli5, 쉽게 설명, 그림으로 설명, or visual onboarding requests.
---

# ELI5

Explain the requested topic as if the reader has no prior knowledge, using a self-contained HTML artifact with large pictures and concise text. The original instruction is preserved in [references/upstream.md](references/upstream.md); source revision is in [references/provenance.json](references/provenance.json).

Use Korean unless requested otherwise. Keep a respectful adult tone. Explain new technical terms briefly at first use. Start with the big picture, show one concrete example, then explain the essential mechanism. For code, read the actual source and reference real paths or symbols; distinguish verified behavior from inference. For theory, label analogies and explain their limits.

Use HTML/CSS/SVG for precise explanatory diagrams. Add interactive controls only when they clarify the mechanism; label inputs and units. Make the page responsive, keyboard-accessible, and readable offline. Check layout and any interaction before delivery.

Save artifacts using the applicable persistent file workflow; repository-backed work follows repository rules. Link the final HTML and briefly explain what it shows. Do not claim to have installed a local CLI or modified the user's PC.

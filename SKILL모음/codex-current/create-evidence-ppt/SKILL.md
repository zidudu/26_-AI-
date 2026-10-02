---
name: create-evidence-ppt
description: 수집 원문 캡처와 분석을 함께 담은 PowerPoint 보고서, 원문 발표자 노트, 요약본·미리보기 또는 이전 실행 기반 PPT 재생성이 필요할 때 사용한다. 생성된 이미지로 증거를 대체하지 않고 자료·슬라이드 관계를 검증한다.
---

# 원문 근거가 담긴 PPT 보고서

## Workflow
1. Inspect source manifests, analysis snapshots and the desired audience. Read [report contracts](references/contracts.md). Use the available presentation-creation skill for layout/rendering details when appropriate.
2. Design overview, per-source summaries and detail slides. Bind each detail to the exact source version, evidence/capture and saved analysis; allow a visible missing-capture state rather than inventing a screenshot.
3. Calculate long-image segmentation from actual slide geometry. Preserve capture order, text readability and the maximum full-image placement width across slides. Use optimize-report-images for optional derivatives.
4. Keep full raw body in slide notes when requested, alongside metadata. Verify the exact raw-text segment after saving/reopening; normalize only explicitly allowed newline forms, never silently strip content.
5. Generate summary slide indices dynamically. Export preview PNGs and a summary-only deck from a copy of the full deck. Remove or rewrite hyperlinks that target omitted slides, and reopen to verify slide counts and links.
6. Support report-only rebuilding from immutable prior artifacts without recollection, new analysis or sending unless explicitly requested. Validate hashes and report missing dependencies.
7. Render and visually inspect representative overview, dense detail, long capture and summary slides. Record render failures separately from deck-generation success.

Deliver full and requested summary outputs, a slide/source mapping and verification notes. Explain environmental limits if actual PowerPoint rendering cannot be exercised. Never claim a mock image or synthetic UI is an authentic application capture.

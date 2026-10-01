---
name: optimize-report-images
description: PowerPoint·문서에 넣을 긴 화면 캡처나 고해상도 이미지의 용량을 줄일 때 사용한다. 실제 전체 그림 배치 너비와 목표 PPI로 픽셀 크기를 계산하고 재사용 배치의 최대 크기·원본 해시·가독성을 확인한다.
---

# 보고서 이미지 용량 최적화

## Workflow
1. Inspect originals, their use in the report and required readability. Read [geometry and verification](references/contracts.md).
2. Measure the actual full picture display width in points, including the image behind a crop. For an image reused in multiple placements, use the maximum full-image width. Do not substitute crop-window width or metadata DPI.
3. Run `scripts/optimize_image.py --input ORIGINAL --output NEW.png --width-pt WIDTH --ppi PPI`; repeat `--width-pt` for all placements. Requires Pillow. It refuses overwriting an existing output and never writes the input.
4. Keep the derivative separate. Preserve aspect ratio and transparency; avoid upscaling. Retain original/derivative hashes, dimensions, target PPI and the input placement widths.
5. Reinsert into the actual document and verify geometry, crop offsets, font/screenshot readability and final file size. Compare text-heavy regions at the intended viewing size. Fall back to the original if quality is insufficient; optimization is optional.

Deliver optimized assets plus a manifest and measured savings. Do not promise a percentage reduction, and do not treat setting a PNG's DPI tag as pixel resampling. This helper supports single-frame images; inspect animated or multipage inputs through an appropriate separate workflow.

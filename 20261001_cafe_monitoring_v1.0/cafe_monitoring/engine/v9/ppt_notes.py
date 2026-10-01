"""Write and verify raw article notes without normalizing source contents."""
from v754.core import V7Error
from v754 import powerpoint as legacy

def notes_range(slide):
    for i in range(1, slide.NotesPage.Shapes.Count + 1):
        shape = slide.NotesPage.Shapes.Item(i)
        if shape.Type == 14 and shape.PlaceholderFormat.Type == 2:
            return shape.TextFrame.TextRange
    raise V7Error("NOTES_FAILED", "슬라이드 노트 본문 자리표시자가 없습니다.")


def verify_notes(slide, expected):
    # Compare line breaks as text; do not strip whitespace or alter the raw input.
    def line_breaks(text):
        return text.replace("\r\n", "\n").replace("\r", "\n").replace("\v", "\n")
    if line_breaks(notes_range(slide).Text) != line_breaks(expected):
        raise V7Error("NOTES_CONTENT_MISMATCH", "저장된 슬라이드 노트가 검토 정보·원문 본문과 다릅니다.")


def add_notes(slide, article, cfg, page_label, report):
    from v8.analysis_engine import load_bound_source
    source, _ = load_bound_source(article)
    raw_body = source.get("body_raw") if cfg.get("v10_raw_notes",True) else ""
    if not isinstance(raw_body, str):
        raise V7Error("RAW_BODY_MISSING", f"게시글 {article['id']}: 저장 원문의 body_raw가 없습니다.")
    # Keep the existing notes writer and append the unmodified collected string.
    warning_start = len(report["warnings"])
    legacy.add_notes(slide, article, cfg, page_label, report)
    if any(w.get("code") == "NOTES_FAILED" for w in report["warnings"][warning_start:]):
        raise V7Error("NOTES_FAILED", f"게시글 {article['id']}: 기존 검토 노트를 작성하지 못했습니다.")
    target = notes_range(slide)
    expected = target.Text + ("\r\r[원문 본문]\r" + raw_body if cfg.get("v10_raw_notes",True) else "")
    target.Text = expected
    verify_notes(slide, expected)
    return expected



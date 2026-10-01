"""Editable detail slides and their expected contents for reopen validation."""
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from v754.core import V7Error, wrap_text
from v754.summary_layout import logical_text, summary_variants
from v754.powerpoint import (
    COLS, ROWS, SUMMARY_SIZE, SUMMARY_MIN_SIZE,
    CAPTURE_X, CAPTURE_Y, CAPTURE_W,
    format_text, textbox, capture_geometry, verify_summary_fit,
    verify_analysis_table, verify_analysis_slide, verify_metadata_table, put_source_link,
)
from v8.analysis_engine import verify_display_artifact
from v9.ppt_images import insert_tile, verify_inserted_ppi
from v9.ppt_notes import add_notes
from v9.ppt_timing import phase


@dataclass
class DetailResult:
    """Expected state, kept in creation order until final dashboard insertion."""
    targets: dict
    names: list
    crops: list
    articles: list
    pages: list
    notes: list
    picture_texts: list


def refresh_analysis_fields(slide, article, cfg):
    table = slide.Shapes.Item("V7_INFO_TABLE").Table
    for row, col, key in ((1, 2, "vehicle"), (1, 4, "specs"),
                          (1, 6, "complaint"), (3, 6, "same_count")):
        format_text(table.Cell(row, col).Shape, article[key], cfg["font_name"],
                    11, 2, 9, table_cell=True)


class FrameFactory:
    """표 병합·테두리·고정 글자를 한 번 만든 뒤 PowerPoint 자체 복제를 사용합니다."""
    def __init__(self, deck, article, cfg, report):
        self.deck, self.cfg, self.report = deck, cfg, report
        with phase(report, "template_build"):
            from v9.template_cache import create_or_load
            self.template = create_or_load(deck, cfg, report)
        report["ppt_frame_copies"] = 0
        report["ppt_discarded_measurement_frames"] = 0

    def new(self, article, summary, page_label, summary_font=None):
        with phase(self.report, "frame_copy_and_fill"):
            slide = self.template.Duplicate().Item(1)
            slide.MoveTo(self.deck.Slides.Count)
            slide.Name = f"V7_{article['id']}_{self.deck.Slides.Count - 1}"
            self.report["ppt_frame_copies"] += 1
            font = self.cfg["font_name"]
            lines = wrap_text(article["title"], 49)
            title = "\n".join(lines[:2]) + ("…" if len(lines) > 2 else "")
            format_text(slide.Shapes.Item("V7_TITLE"), "■ " + title, font, 22, 1, 14, False)
            table = slide.Shapes.Item("V7_INFO_TABLE").Table
            refresh_analysis_fields(slide, article, self.cfg)
            format_text(table.Cell(2,2).Shape, article["cafe_name"], font, 11, 2, 9, table_cell=True)
            put_source_link(table.Cell(2,4).Shape, article, font)
            format_text(table.Cell(3,7).Shape, article["views"] + "/" + article["comments"],
                        font, 11, 2, 9, table_cell=True)
            format_text(table.Cell(4,2).Shape, summary, font, summary_font or SUMMARY_SIZE,
                        1, summary_font or SUMMARY_MIN_SIZE, table_cell=True)
            format_text(slide.Shapes.Item("V7_FOOTER"),
                        f"직원 검토 전 초안 · 게시글 {article['id']} · {page_label}",
                        font, 9, 1, 8, False)
            verify_metadata_table(table, article)
            verify_analysis_table(table, article, summary)
            verify_summary_fit(table)
            return slide

    def close(self):
        if self.template is not None:
            self.template.Delete()
            self.template = None


def fit_summary_on_slide(slide, article, cfg):
    """최종 슬라이드의 동일 셀에서 실측하며 문장 내용은 보존합니다."""
    table = slide.Shapes.Item("V7_INFO_TABLE").Table
    shape = table.Cell(4, 2).Shape
    text = article["display_text"]
    for variant in summary_variants(text, sum(COLS[1:]) - 10, SUMMARY_MIN_SIZE):
        shape.TextFrame.TextRange.Text = ""
        table.Rows.Item(4).Height = ROWS[3]
        try:
            format_text(shape, variant, cfg["font_name"], SUMMARY_SIZE, 1,
                        SUMMARY_MIN_SIZE, table_cell=True)
            verify_summary_fit(table)
        except V7Error as exc:
            if exc.code not in ("TEXT_OVERFLOW", "SUMMARY_OVERFLOW"):
                raise
            continue
        if logical_text(variant) != logical_text(text):
            raise V7Error("SUMMARY_CONTENT_MISMATCH", "요약 줄바꿈 중 내용이 달라졌습니다.")
        return variant, float(shape.TextFrame.TextRange.Font.Size)
    raise V7Error("SUMMARY_FIT_REQUIRED", "요약 전체를 최소 글자 크기로도 한 칸에 배치하지 못했습니다.")


def fit_with_repair(slide, article, cfg, folder, report, summary_resolver):
    from v8.analysis_engine import mark_summary_pending

    def fit():
        with phase(report, "summary_fit"):
            return fit_summary_on_slide(slide, article, cfg)

    try:
        result = fit()
    except V7Error as exc:
        if exc.code != "SUMMARY_FIT_REQUIRED":
            raise
        with phase(report, "summary_repair"):
            repaired = summary_resolver(article, "SUMMARY_OVERFLOW") if summary_resolver else False
        if not repaired:
            mark_summary_pending(article, folder, report, "SUMMARY_FIT_REQUIRED")
        try:
            result = fit()
        except V7Error as retry_exc:
            if retry_exc.code != "SUMMARY_FIT_REQUIRED":
                raise
            mark_summary_pending(article, folder, report, "SUMMARY_FIT_REQUIRED")
            result = fit()
        # 재요약이 차종·분류도 바꿨다면 이미 만든 최종 표에 반영합니다.
        refresh_analysis_fields(slide, article, cfg)
    verify_analysis_table(slide.Shapes.Item("V7_INFO_TABLE").Table, article, result[0])
    return result


def build_details(deck, articles, plans, cfg, folder, report, checkpoint, summary_resolver=None):
    factory = FrameFactory(deck, articles[0], cfg, report) if articles else None
    if factory is None:
        report.update(ppt_template_builds=0,ppt_frame_copies=0,ppt_discarded_measurement_frames=0)
    detail_targets = {}
    expected = []
    expected_crops = []
    expected_articles = []
    expected_pages = []
    expected_notes = []
    expected_picture_texts = []
    report["ppt_ppi_insert_measurements"] = []
    report["ppt_ppi_reopen_measurements"] = []
    report["notes_insert_checked"] = 0
    report["notes_reopen_checked"] = 0
    report['analysis_insert_checked'] = 0
    report['analysis_reopen_checked'] = 0
    report["capture_geometry_insert_checked"] = 0
    report["capture_geometry_reopen_checked"] = 0
    report["source_url_insert_checked"] = 0
    report["source_url_reopen_checked"] = 0
    report["metadata_insert_checked"] = 0
    report["metadata_reopen_checked"] = 0
    report["ppt_article_timings"] = []
    for article_index, (article, pages) in enumerate(plans, 1):
        article_started = perf_counter()
        report["active_article"] = article["id"]
        report["stage"] = "CREATE_AND_FIT_FINAL_SLIDE"
        checkpoint()
        print(f"[PPT 작성 {article_index}/{len(plans)}] 게시글 {article['id']} / {len(pages)}장", flush=True)
        if (article.get("summary_pending") or any(i.get("code") == "COMPLAINT_UNCLEAR" for i in article.get("analysis_issues", []))) and summary_resolver:
            with phase(report, "summary_repair"):
                summary_resolver(article, "SEMANTIC_REVIEW")
        first_slide = factory.new(article, "", f"1/{len(pages)}")
        from v9.dashboard_data import article_key
        detail_targets[article_key(article)] = first_slide.SlideID
        variant, font = fit_with_repair(first_slide, article, cfg, folder, report, summary_resolver)
        verify_display_artifact(article)
        for page in pages:
            page.update(summary=variant, summary_font_size=font, text_measurement="powerpoint_com")
        print(f"[요약 맞춤 {article_index}/{len(plans)}] 게시글 {article['id']} / 최종 슬라이드 실측 / {font:g}pt", flush=True)
        for page_index, page in enumerate(pages, 1):
            report["stage"] = "CREATE_SLIDE"
            report["active_article"] = article["id"]
            checkpoint()
            slide = first_slide if page_index == 1 else factory.new(
                article, page["summary"], f"{page_index}/{len(pages)}", page["summary_font_size"])
            content_started = perf_counter()
            report["source_url_insert_checked"] += 1
            report["metadata_insert_checked"] += 1
            names = ["V7_TITLE", "V7_INFO_TABLE"]
            crops = {}
            picture_texts = {}
            if not page["tiles"]:
                textbox(slide, "V7_NO_CAPTURE", "캡처 확인 필요\n원문 링크와 결과 기록을 확인하세요.",
                        CAPTURE_X, CAPTURE_Y, CAPTURE_W, 80, cfg["font_name"], 15)
                if article['id'] not in report['missing_capture_articles']:
                    report['missing_capture_articles'].append(article['id'])
            for col, tile in enumerate(page["tiles"]):
                report["stage"] = "INSERT_CAPTURE"
                report["active_capture"] = tile["capture"]["index"]
                checkpoint()
                pic = insert_tile(slide, tile, col)
                ppi_measurement = verify_inserted_ppi(pic, tile)
                if ppi_measurement is not None:
                    report["ppt_ppi_insert_measurements"].append(dict(ppi_measurement, cafe_id=article["cafe_id"], article_id=article["id"], capture_index=tile["capture"]["index"], slide=len(expected)+1))
                picture_texts[pic.Name] = pic.AlternativeText
                names.append(pic.Name)
                crops[pic.Name] = capture_geometry(tile, col)
                report["capture_geometry_insert_checked"] += 1
            if page["tiles"]:
                caption = " / ".join(f"캡처 {t['capture']['index']} (전체)" for t in page["tiles"])
                textbox(slide, "V7_CAPTURE_CAPTION", caption, CAPTURE_X, 528, CAPTURE_W, 18,
                        cfg["font_name"], 8.5, min_size=8)
            expected_notes.append(add_notes(slide, article, cfg, f"{page_index}/{len(pages)}", report))
            expected_picture_texts.append(picture_texts)
            report["notes_insert_checked"] += 1
            verify_analysis_slide(slide, article, page)
            report['analysis_insert_checked'] += 1
            if cfg["logo_path"]:
                logo_path = Path(cfg["logo_path"])
                if not logo_path.is_absolute():
                    logo_path = cfg["project_root"] / logo_path
                logo = slide.Shapes.AddPicture(str(logo_path.resolve()), 0, -1, 0, 0, -1, -1)
                scale = min(116 / logo.Width, 24 / logo.Height)
                logo.LockAspectRatio = -1
                logo.Width *= scale
                logo.Left, logo.Top = 813 - logo.Width, 555
                logo.Name = "V7_USER_LOGO"
            expected.append(names)
            expected_crops.append(crops)
            expected_articles.append(article)
            expected_pages.append(page)
            timings = report.setdefault("ppt_timings_seconds", {})
            timings["slide_content_and_validation"] = timings.get("slide_content_and_validation", 0.0) + perf_counter() - content_started
        elapsed = perf_counter() - article_started
        report["ppt_article_timings"].append({"article_id": article["id"], "slides": len(pages), "seconds": elapsed})
        print(f"[PPT 완료 {article_index}/{len(plans)}] 게시글 {article['id']} / {elapsed:.1f}초", flush=True)
        checkpoint()
    if factory is not None:factory.close()
    return DetailResult(detail_targets, expected, expected_crops, expected_articles,
                        expected_pages, expected_notes, expected_picture_texts)

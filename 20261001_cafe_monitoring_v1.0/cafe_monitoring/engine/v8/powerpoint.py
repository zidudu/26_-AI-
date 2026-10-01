"""V8.2: 양식을 한 번 만들고 복제한 최종 슬라이드에서 요약을 맞춥니다."""
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter

from v754.core import V7Error, wrap_text
from v754.summary_layout import logical_text, summary_variants
from v754 import powerpoint as legacy
from v754.powerpoint import (
    SLIDE_W, SLIDE_H, COLS, ROWS, SUMMARY_SIZE, SUMMARY_MIN_SIZE,
    CAPTURE_X, CAPTURE_Y, CAPTURE_W, CAPTURE_H,
    ensure_windows, format_text, textbox, get_dimensions,
    describe_plan, capture_geometry, insert_tile, add_notes,
    verify_summary_fit, verify_analysis_table, verify_analysis_slide,
    verify_source_link, verify_metadata_table, verify_capture_geometry,
    put_source_link,
)

from v8.capture_layout import plan_article

PPT_BUILD = "8.2.0_boundary_capture_final_slide_fit"


@contextmanager
def phase(report, name):
    started = perf_counter()
    try:
        yield
    finally:
        timings = report.setdefault("ppt_timings_seconds", {})
        timings[name] = timings.get(name, 0.0) + perf_counter() - started


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
            # 긴 실제 메타데이터가 원본 양식의 행 높이에 영향을 주지 않도록 짧은 자리표시자를 사용합니다.
            template_article = dict(article, title="", vehicle="-", specs="-", complaint="-",
                                    cafe_name="-", same_count="-", views="0", comments="0")
            self.template = legacy.make_frame(deck, template_article, "", cfg, "양식")
            self.template.Name = "V81_INTERNAL_TEMPLATE"
        report["ppt_template_builds"] = 1
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


def render(articles, cfg, folder, report, checkpoint, *, summary_resolver=None):
    from v8.analysis_engine import verify_display_artifact
    for article in articles:
        verify_display_artifact(article)
    ensure_windows()
    report["renderer_build"] = PPT_BUILD
    print(f"[PPT 모듈] {PPT_BUILD}", flush=True)
    import pythoncom
    from win32com.client import dynamic
    pythoncom.CoInitialize()
    app = deck = None
    try:
        report["stage"] = "START_POWERPOINT"
        checkpoint()
        app = dynamic.Dispatch("PowerPoint.Application")
        app.Visible = -1
        deck = app.Presentations.Add(-1)
        deck.PageSetup.SlideWidth = SLIDE_W
        deck.PageSetup.SlideHeight = SLIDE_H
        report["stage"] = "CHECK_CAPTURE_INSERTION"
        checkpoint()
        with phase(report, "capture_validation"):
            get_dimensions(deck, articles, report)
        # 먼저 캡처 수만으로 장수를 계산합니다. 이 단계에서는 슬라이드를 만들지 않습니다.
        plans = [(article, plan_article(article, cfg)) for article in articles]
        count = sum(len(pages) for _, pages in plans)
        if count > cfg["max_slides"]:
            raise V7Error("TOO_MANY_SLIDES", f"예상 {count}장으로 최대 {cfg['max_slides']}장을 넘습니다. 글 수를 줄여 실행하세요.")
        report["layout_settings"] = {"mode": "whole_capture", "max_columns": min(cfg["capture_columns"], 2),
                                     "split_captures": False, "single_capture_centered": True, "short_tail_packing": True,
                                     "continuation_scale": "consistent",
                                     "summary_mode": "complete_in_one_cell_no_continuation",
                                     "summary_font_range": [SUMMARY_MIN_SIZE, SUMMARY_SIZE],
                                     "frame_strategy": "one_template_copy_and_fit_final_slide"}
        report["inserted_source_captures"] = sum(len(a["captures"]) for a in articles)
        report["capture_segments"] = sum(len(p["tiles"]) for _, pages in plans for p in pages)
        factory = FrameFactory(deck, articles[0], cfg, report)
        expected = []
        expected_crops = []
        expected_articles = []
        expected_pages = []
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
                    names.append(pic.Name)
                    crops[pic.Name] = capture_geometry(tile, col)
                    report["capture_geometry_insert_checked"] += 1
                if page["tiles"]:
                    caption = " / ".join(f"캡처 {t['capture']['index']} (전체)" for t in page["tiles"])
                    textbox(slide, "V7_CAPTURE_CAPTION", caption, CAPTURE_X, 528, CAPTURE_W, 18,
                            cfg["font_name"], 8.5, min_size=8)
                add_notes(slide, article, cfg, f"{page_index}/{len(pages)}", report)
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
        factory.close()
        report["article_plans"] = [describe_plan(a, pages) for a, pages in plans]
        print(f"[양식 재사용] 양식 생성 {report['ppt_template_builds']}회 / 최종 슬라이드 복제 {report['ppt_frame_copies']}회 / 측정 후 폐기 0장", flush=True)
        report["stage"] = "SAVE_PPTX"
        report["slides"] = deck.Slides.Count
        checkpoint()
        path = folder / "monitoring.pptx"
        with phase(report, "save"):
            deck.SaveAs(str(path), 24)
        if not path.is_file() or path.stat().st_size == 0:
            raise V7Error("PPT_SAVE_FAILED", "저장된 PPT 파일을 확인하지 못했습니다.")
        report["pptx"] = str(path)
        deck.Close()
        deck = None
        report["stage"] = "REOPEN_PPTX"
        checkpoint()
        reopen_started = perf_counter()
        reopened = app.Presentations.Open(str(path), 0, 0, -1)
        if reopened.Slides.Count != len(expected):
            raise V7Error("REOPEN_MISMATCH", f"슬라이드 수 불일치: 계획 {len(expected)} / "
                          f"저장 전 {report['slides']} / 재열기 {reopened.Slides.Count}")
        for i, names in enumerate(expected, 1):
            slide = reopened.Slides.Item(i)
            for name in names:
                shape = slide.Shapes.Item(name)
                if name.startswith("V7_CAPTURE_") and shape.Type != 13:
                    raise V7Error("REOPEN_MISMATCH", f"슬라이드 {i}의 그림 개체를 확인하지 못했습니다.")
            if not slide.Shapes.Item("V7_INFO_TABLE").HasTable:
                raise V7Error("REOPEN_MISMATCH", "편집 가능한 표를 확인하지 못했습니다.")
            verify_source_link(slide.Shapes.Item("V7_INFO_TABLE").Table.Cell(2, 4).Shape,
                               expected_articles[i - 1])
            report["source_url_reopen_checked"] += 1
            verify_metadata_table(slide.Shapes.Item('V7_INFO_TABLE').Table, expected_articles[i - 1])
            report['metadata_reopen_checked'] += 1
            verify_analysis_slide(slide, expected_articles[i - 1], expected_pages[i - 1])
            report['analysis_reopen_checked'] += 1
            for name, geometry in expected_crops[i - 1].items():
                verify_capture_geometry(slide.Shapes.Item(name), geometry)
                report["capture_geometry_reopen_checked"] += 1
        print(f"[캡처 배치 검증] 삽입 {report['capture_geometry_insert_checked']}개 / "
              f"재열기 {report['capture_geometry_reopen_checked']}개", flush=True)
        print(f"[원문 URL 검증] 표시·링크 삽입 {report['source_url_insert_checked']}개 / "
              f"재열기 {report['source_url_reopen_checked']}개", flush=True)
        report["ppt_timings_seconds"]["reopen_and_validation"] = perf_counter() - reopen_started
        report["reopened_structure_verified"] = True
        print(f"[분석 표 검증] 삽입 {report['analysis_insert_checked']}개 / 재열기 {report['analysis_reopen_checked']}개", flush=True)
        print(f"[매체·조회수/댓글 검증] 삽입 {report['metadata_insert_checked']}개 / 재열기 {report['metadata_reopen_checked']}개", flush=True)
        phase_labels = {"capture_validation": "캡처 확인", "template_build": "기본 양식 생성",
                        "frame_copy_and_fill": "양식 복제·내용 입력", "summary_fit": "요약 실측",
                        "summary_repair": "요약 수정", "slide_content_and_validation": "캡처·노트·슬라이드 검증",
                        "save": "파일 저장", "reopen_and_validation": "재열기 검증"}
        for name, elapsed in report["ppt_timings_seconds"].items():
            print(f"[PPT 상세 시간] {phase_labels.get(name, name)} / {elapsed:.1f}초", flush=True)
        report["visual_check"] = "user_required"
        report["stage"] = "COMPLETE"
        # PowerPoint는 열어 두어 사용자가 실제 표시와 분할 경계를 확인합니다.
    finally:
        # 다른 사용자의 PPT를 닫거나 PowerPoint 프로세스를 종료하지 않습니다.
        # 실패한 새 문서도 화면에 남겨 원인과 부분 결과를 확인할 수 있게 합니다.
        app = deck = None
        pythoncom.CoUninitialize()

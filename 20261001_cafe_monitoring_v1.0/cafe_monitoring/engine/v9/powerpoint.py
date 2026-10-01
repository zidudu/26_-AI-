"""PowerPoint lifecycle: plan, compose, save and verify one presentation.

COM stays on the calling thread. Preserve final slide order and keep the new
presentation open for review; never quit another user's Office application.
"""
from time import perf_counter
from datetime import datetime
from v8.configuration import KST
from v754.core import V7Error
from v754.powerpoint import (
    SLIDE_W, SLIDE_H, SUMMARY_MIN_SIZE, SUMMARY_SIZE,
    ensure_windows, get_dimensions, describe_plan,
)
from v8.capture_layout import plan_article
from v9.ppt_images import prepare_ppt_images
from v9.ppt_timing import phase
from v9.ppt_validation import verify_reopened
from v9.report_paths import monitoring_path
# Compatibility imports: callers from earlier V9 builds keep working.
from v9.ppt_notes import notes_range, verify_notes, add_notes
from v9.ppt_details import (
    FrameFactory, fit_summary_on_slide, fit_with_repair,
    refresh_analysis_fields, build_details,
)

PPT_BUILD = "9.6.7_refactor_shapes_ppi"


def dashboard_prefix_count(cfg, report):
    if not report.get('dashboard_stats'):
        return 0
    options = cfg.get('v96_dashboard', {})
    # Old V9 callers retain the original fixed-catalog contract. V10 opts in
    # with an execution-frozen catalog and an explicit summary-slide setting.
    if 'include_cafe_summaries' not in options:
        return 10
    return 1 + (len(report['dashboard_stats']['cafes']) if options['include_cafe_summaries'] else 0)


def render(articles, cfg, folder, report, checkpoint, *, summary_resolver=None, dashboard_resolver=None):
    from v8.analysis_engine import verify_display_artifact
    for article in articles:
        verify_display_artifact(article)
    ensure_windows()
    generated_at = datetime.now(KST)
    path = monitoring_path(folder, created_at=generated_at)
    report['ppt_generated_at'] = generated_at.isoformat()
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
        with phase(report, "capture_validation"):
            prepare_ppt_images(deck, articles, report, plans=plans)
        prefix_count = dashboard_prefix_count(cfg, report)
        count = sum(len(pages) for _, pages in plans) + prefix_count
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
        details = build_details(deck, articles, plans, cfg, folder, report, checkpoint, summary_resolver)
        if prefix_count:
            from v9.dashboard_ppt import append_dashboard
            from v9.dashboard_ai import prepare
            with phase(report,'cafe_dashboard'):
                if dashboard_resolver:dashboard_resolver(articles)
                else:prepare(articles,cfg,folder,report,checkpoint,allow_ai=False)
                try:
                    actual_prefix=append_dashboard(deck,cfg,report,articles,details.targets)
                finally:
                    from v9.dashboard_com import write_audits
                    import sys
                    primary_error=sys.exc_info()[1]
                    write_audits(folder,report,primary_error)
                if actual_prefix!=prefix_count:
                    raise V7Error('DASHBOARD_COUNT_MISMATCH','고정 요약 슬라이드 수가 다릅니다.')
                for row in report['ppt_ppi_insert_measurements']:row['slide']+=prefix_count
                from v754.core import write_json
                write_json(folder/'dashboard_stats.json',report['dashboard_stats'])
                write_json(folder/'dashboard_manifest.json',report['dashboard_manifest'])
        report['dashboard_slides']=prefix_count
        report["article_plans"] = [describe_plan(a, pages) for a, pages in plans]
        print(f"[양식 재사용] 양식 생성 {report['ppt_template_builds']}회 / 최종 슬라이드 복제 {report['ppt_frame_copies']}회 / 측정 후 폐기 0장", flush=True)
        report["stage"] = "SAVE_PPTX"
        report["slides"] = deck.Slides.Count
        checkpoint()
        with phase(report, "save"):
            deck.SaveAs(str(path), 24)
            if prefix_count:
                from v9.dashboard_ppt import verify_dashboard
                verify_dashboard(deck,report,'after_save')
        if not path.is_file() or path.stat().st_size == 0:
            raise V7Error("PPT_SAVE_FAILED", "저장된 PPT 파일을 확인하지 못했습니다.")
        report["pptx"] = str(path)
        report["pptx_size_bytes"] = path.stat().st_size
        print(f"[PPT 파일 용량] {report['pptx_size_bytes']:,} bytes / {report['pptx_size_bytes'] / 1_000_000:.2f} MB / DRM 적용 파일의 실제 크기", flush=True)
        deck.Close()
        deck = None
        report["stage"] = "REOPEN_PPTX"
        checkpoint()
        reopen_started = perf_counter()
        reopened = app.Presentations.Open(str(path), 0, 0, -1)
        verify_reopened(reopened, details, prefix_count, report)
        report["ppt_timings_seconds"]["reopen_and_validation"] = perf_counter() - reopen_started
        report["reopened_structure_verified"] = True
        print(f"[분석 표 검증] 삽입 {report['analysis_insert_checked']}개 / 재열기 {report['analysis_reopen_checked']}개", flush=True)
        print(f"[원문 노트 검증] 삽입 {report['notes_insert_checked']}개 / 재열기 {report['notes_reopen_checked']}개", flush=True)
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
        try:
            if report.get('dashboard_chart_audit') or report.get('dashboard_com_audit'):
                from v9.dashboard_com import write_audits
                import sys
                primary_error=sys.exc_info()[1]
                write_audits(folder,report,primary_error)
        finally:
            app = deck = None
            pythoncom.CoUninitialize()

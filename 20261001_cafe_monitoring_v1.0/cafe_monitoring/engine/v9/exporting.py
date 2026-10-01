"""Coordinate the analysis consumer (PPT) while preserving report semantics."""
from v754.core import V7Error
from v754.main_v754 import timed

def export_items(items, cfg, folder, report, checkpoint):
    from v9.comparison import write_comparison
    from v754.powerpoint import check_office
    from v9.powerpoint import render
    if not report.get('collection_complete'):
        raise V7Error('COLLECTION_INCOMPLETE', '기간 수집 미완료 상태에서는 분석·PPT 생성을 진행하지 않습니다.')
    if not items:
        if report.get('dashboard_stats'):
            check_office()
            from v9.dashboard_ai import prepare
            def dashboard_empty(articles):
                prepare(articles,cfg,folder,report,checkpoint,allow_ai=False)
            render([],cfg,folder,report,checkpoint,dashboard_resolver=dashboard_empty)
        report.update(status='completed_empty', stage='COMPLETE', analyzed_articles=0)
        checkpoint()
        print('[대상 없음] 확인된 대상 0건 / 추가 AI 없음 / 요약 슬라이드는 설정에 따라 생성합니다.')
        return 0
    check_office()
    from v9.analysis_stage import prepare_analysis
    with prepare_analysis(items, cfg, folder, report, checkpoint) as analysis:
        analyzed = analysis.articles
        with timed(report, 'powerpoint', checkpoint):
            render(analyzed, cfg, folder, report, checkpoint,
                   summary_resolver=analysis.summary_resolver,
                   dashboard_resolver=analysis.dashboard_resolver)
    partial = (len(analyzed) != len(items) or bool(report['capture_failures'] or report['missing_capture_articles'] or report['warnings'])
               or any(a['capture_problem'] or a['metadata_warnings'] or a['analysis_issues'] or a.get('summary_pending') for a in analyzed))
    report['analysis_review_count'] = sum(bool(i.get('analysis_issues') or i.get('summary_pending')) for i in analyzed)
    report.update(status='partial' if partial else 'completed',
                  analyzed_articles=sum(bool(i.get('analysis_candidate')) for i in analyzed),
                  included_articles=len(analyzed),
                  pending_summary_articles=[i['id'] for i in analyzed if i.get('summary_pending')])
    checkpoint()
    write_comparison(folder, report)
    print(f"[PPT 저장] {report['pptx']}")
    print(f"[분석] API {report['api_calls']}회 / Codex {report.get('codex_calls', 0)}회 / 재사용 {report['analysis_cache_hits']}건")
    print(f"[카페 요약 별도] 새 호출 {report.get('cafe_summary_calls',0)}회 / 재사용 {report.get('cafe_summary_cache_hits',0)}건")
    print(f"[다시 열기 확인] {report['slides']}장 / 분석 표·URL·메타데이터·그림 검증")
    print(f"[원문·분석 비교] {folder / 'comparison.html'}")
    print('직원 검토 전 초안입니다. 분류와 요약을 원문과 대조하세요.')
    return 2 if partial else 0

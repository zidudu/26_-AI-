"""V8.2의 출력 경로. V7.5.4.1 분석·검증을 유지하고 PPT 렌더러만 교체합니다."""
from v754.core import V7Error
from v754.main_v754 import timed

def export_items(items, cfg, folder, report, checkpoint):
    from v754.analysis_config import load_analysis_config
    from v8.analysis_prompts import make_spec
    from v8.analysis_engine import analyze_articles
    from v754.comparison import write_comparison
    from v754.powerpoint import check_office
    from v8.powerpoint import render
    if not report.get('collection_complete'):
        raise V7Error('COLLECTION_INCOMPLETE', '기간 수집 미완료 상태에서는 분석·PPT 생성을 진행하지 않습니다.')
    if not items:
        report.update(status='completed_empty', stage='COMPLETE', analyzed_articles=0)
        checkpoint()
        print('[대상 없음] 해당 기간·검색어의 확인된 대상이 0개입니다. AI 호출·PPT 생성 없음.')
        return 0
    check_office()
    spec = make_spec(load_analysis_config(cfg['project_root']))
    report['complaint_policy'] = spec['complaint_definition']
    print(f"[AI 안내] 선정 {len(items)}개 / 최초 분석 최대 {len(items)}회 + 필요 시 재요약 글당 최대 1회 / 모델 {spec['model']}")
    print('동일 원문·모델·지침 캐시는 재사용합니다. V8.2는 분류·근거 검증 지침이 바뀌어 최초 분석이 새로 실행될 수 있습니다. 새 분석은 API 사용량이 발생합니다.')
    with timed(report, 'analysis', checkpoint):
        analyzed = analyze_articles(items, spec, folder, report, checkpoint)
    write_comparison(folder, report)
    if not analyzed:
        raise V7Error('NO_ANALYZED_ARTICLES', '사용 가능한 분석이 없습니다. analysis_audit.json을 확인하세요.')
    from v8.summary_repair import SummaryRepair
    resolver = SummaryRepair(spec, folder, report, checkpoint)
    with timed(report, 'powerpoint', checkpoint):
        render(analyzed, cfg, folder, report, checkpoint, summary_resolver=resolver)
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
    print(f"[분석] 새 API {report['api_calls']}회 / 재사용 {report['analysis_cache_hits']}건")
    print(f"[다시 열기 확인] {report['slides']}장 / 분석 표·URL·메타데이터·그림 검증")
    print(f"[원문·분석 비교] {folder / 'comparison.html'}")
    print('직원 검토 전 초안입니다. 분류와 요약을 원문과 대조하세요.')
    return 2 if partial else 0


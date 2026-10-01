"""Analysis session independent of PowerPoint and Outlook.

The provider context remains alive for the existing summary-repair callbacks.
This boundary exposes saved-analysis dictionaries for future DB/review adapters;
it neither chooses a DB schema nor implements a JEV judgement policy.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable
from v754.core import V7Error
from v754.main_v754 import timed


@dataclass
class AnalysisStageResult:
    articles: list
    summary_resolver: Callable
    dashboard_resolver: Callable

    def snapshot(self):
        """Detach data before passing it to another consumer or review layer.

        Rendering can update display text via a measured summary repair. A
        snapshot taken before rendering preserves that earlier analysis state.
        Source hashes/IDs are retained; original source files are never changed.
        """
        return deepcopy(self.articles)


@contextmanager
def prepare_analysis(items, cfg, folder, report, checkpoint):
    """Yield analysis and callbacks; close the provider after the consumer exits."""
    from v9.ai_provider import analysis_binding, load_settings, show_settings
    from v9.analysis_engine import analyze_articles
    from v9.comparison import write_comparison
    if not report.get('collection_complete'):
        raise V7Error('COLLECTION_INCOMPLETE', '기간 수집 미완료 상태에서는 분석을 진행하지 않습니다.')
    settings = cfg.get('v95_ai_settings')
    if settings is None:
        settings = load_settings(cfg['project_root'])
    with analysis_binding(cfg['project_root'], settings, folder) as (spec, factory):
        report['complaint_policy'] = spec['complaint_definition']
        print(f"[AI 안내] 선정 {len(items)}개 / 최초 분석 최대 {len(items)}회 + 필요 시 재요약 글당 최대 1회 / 모델 {spec['model']}")
        show_settings(settings, requested_concurrency=cfg['v93_performance']['analysis_concurrency'])
        print('동일 원문·AI 방식·모델·지침 캐시는 재사용합니다. 새 분석은 선택한 AI 방식의 사용량이 발생합니다.')
        provider = settings['provider']
        concurrency = cfg['v93_performance']['analysis_concurrency']
        kwargs = {}
        if provider == 'codex':
            concurrency = min(concurrency, settings['codex']['max_parallel'])
            kwargs.update(provider=provider, analyzer_factory=factory)
        report['ai_settings'] = settings
        with timed(report, 'analysis', checkpoint):
            analyzed = analyze_articles(items, spec, folder, report, checkpoint, concurrency=concurrency, **kwargs)
        write_comparison(folder, report)
        if not analyzed:
            raise V7Error('NO_ANALYZED_ARTICLES', '사용 가능한 분석이 없습니다. analysis_audit.json을 확인하세요.')
        from v9.summary_repair import SummaryRepair
        resolver = SummaryRepair(spec, folder, report, checkpoint, **kwargs)
        from v9.dashboard_ai import prepare
        def dashboard_resolver(articles):
            prepare(articles,cfg,folder,report,checkpoint,spec=spec,factory=factory)
        yield AnalysisStageResult(analyzed, resolver, dashboard_resolver)

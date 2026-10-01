"""Validate source-to-export identity and bind artifacts to one run.

No Office, mail or browser calls: these checks can be reused by future sinks.
"""
from pathlib import Path
from v8.configuration import V8Error, read_json
from v8.pipeline import sha, safe_path
from v9.run_model import key, unique_items

def validate_export(report, folder, source_items):
    if report.get('selected_articles')!=len(source_items) or report.get('collection_complete') is not True:
        raise V8Error('EXPORT_MISMATCH','통합 출력의 수집 건수가 다릅니다.')
    actual=unique_items(report.get('items',[]))
    expected=unique_items(source_items)
    if not actual <= expected: raise V8Error('EXPORT_MISMATCH','다른 카페·게시글의 결과가 포함되었습니다.')
    sources={key(a):a for a in source_items}
    for article in report.get('items',[]):
        original=sources[key(article)]
        for field in ('url','written_at','selection_window','source_artifact_sha256'):
            if article.get(field)!=original.get(field):
                raise V8Error('EXPORT_SOURCE_MISMATCH','원문 출처·기간이 출력 결과와 다릅니다.')
    if not expected:
        if report.get('status')!='completed_empty' or actual:
            raise V8Error('EXPORT_EMPTY_MISMATCH','0건 결과 상태가 다릅니다.')
        if not report.get('dashboard_stats'):return
    if (expected and not actual) or report.get('status') not in ('completed','partial','completed_empty') or report.get('reopened_structure_verified') is not True:
        raise V8Error('PPT_UNVERIFIED','통합 PPT의 재열기 검증을 통과하지 못했습니다.')
    ppt=Path(report.get('pptx','')).resolve()
    if ppt.parent!=folder.resolve() or ppt.suffix.lower()!='.pptx' or not ppt.is_file() or not ppt.stat().st_size or not report.get('slides'):
        raise V8Error('PPT_PATH_INVALID','이번 실행의 PPT 파일을 확인할 수 없습니다.')
    if report.get('dashboard_stats') and (report.get('dashboard_slides')!=10 or
            report.get('dashboard_reopen_checked')!=10):
        raise V8Error('PPT_UNVERIFIED','고정 요약 10장의 재열기 검증이 없습니다.')


def read_artifact(folder, record):
    ref=record['artifact']
    summary=safe_path(folder,ref['summary'])
    if sha(summary)!=ref['summary_sha256']:
        raise V8Error('ARTIFACT_CHANGED','저장된 출력 기록이 변경되었습니다.')
    ppt=safe_path(folder,ref['ppt']) if ref['ppt'] else None
    if ppt and sha(ppt)!=ref['ppt_sha256']:
        raise V8Error('ARTIFACT_CHANGED','검증한 PPT 파일이 변경되었습니다.')
    return read_json(summary),ppt



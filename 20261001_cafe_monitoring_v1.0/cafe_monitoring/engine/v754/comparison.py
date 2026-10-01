"""직원이 원문·기존 요약·새 요약·분류·근거를 함께 읽는 로컬 비교 보고서."""
from html import escape
from .core import read_json


def write_comparison(folder, report):
    path = folder / 'analysis_audit.json'
    audit = read_json(path) if path.is_file() else {'items': []}
    e = lambda value: escape(str(value if value is not None else '—'), quote=True)
    blocks = []
    for item in audit['items']:
        source = item.get('source', {})
        old = item.get('old_monitoring', {})
        display = item.get('display', {})
        candidate = item.get('candidate') or {}
        reasons = [x.get('message', '') for x in item.get('issues', [])] + candidate.get('review_reasons', [])
        if item.get('error'):
            reasons.append(item['error']['code'] + ': ' + item['error']['message'])
        new_summary = ' '.join(c['text'] for c in candidate.get('summary_claims', [])) or None
        rows = ([('기존 V6 요약/표시', old.get('display_text'))] if old else []) + [('새 AI 요약 (검사 전)', new_summary),
                ('새 PPT 요약/표시', display.get('display_text')),
                ('재요약 전 AI 요약', ' '.join(c['text'] for c in (item.get('prior_candidate') or {}).get('summary_claims', [])) or None),
                ('불만 구분 (검토용)', display.get('complaint')), ('동일건수', '집계 기준 미정 · 계산하지 않음'),
                ('요약 상태', '확인 필요' if item.get('summary_pending') else 'AI 요약'),
                ('AI 처리', item.get('request_execution')), ('직원 검토', '미완료')]
        values = ''.join(f'<tr><th>{e(k)}</th><td>{e(v)}</td></tr>' for k, v in rows)
        bindings = ''.join('<li><b>' + e(b['field']) + '</b>: ' +
                           e(' / '.join(q['text'] for q in b['quotes'])) + '</li>' for b in item.get('bindings', []))
        blocks.append(f'''<article><h2>{e(item['article_id'])} · {e(source.get('title'))}</h2>
<table>{values}</table><p class="review">{e(' / '.join(reasons) or '알려진 규칙 위반이 발견되지 않았습니다. 의미 정확성은 원문과 대조하세요.')}</p>
<details open><summary>원문 전체</summary><pre>{e(source.get('body'))}</pre></details>
<details><summary>AI 문장별 원문 근거</summary><ul>{bindings}</ul></details></article>''')
    html = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><title>V7.5.4.1 원문·분석 비교</title>
<style>body{{font:16px/1.7 "Malgun Gothic",sans-serif;max-width:1100px;margin:32px auto;padding:0 20px;color:#202938;background:#f4f6f8}}
article{{background:white;border:1px solid #d6dce2;padding:24px;margin:24px 0}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #d6dce2;padding:12px;white-space:pre-wrap;text-align:left}}th{{width:170px;background:#eef2f6}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}}.review{{color:#674500}}summary{{cursor:pointer;font-weight:bold}}</style>
<h1>V7.5.4.1 원문·분석 비교</h1><p>실행 상태: {e(report.get('status'))} · 새 API 호출 {e(report.get('api_calls', 0))}회 · 캐시 {e(report.get('analysis_cache_hits', 0))}건</p>
<p>불만 구분은 증상 중심의 검토용 분류입니다. 회사 공식 기준과 동일건수 기준은 미확정입니다.
이 문서는 직원 검토 완료 상태를 저장하지 않습니다. JSON·근거·PPT 문자열 일치는 의미 정확성을 보장하지 않습니다.</p>
{''.join(blocks) or '<p>저장된 분석 결과가 없습니다. summary.json의 오류를 확인하세요.</p>'}</html>'''
    (folder / 'comparison.html').write_text(html, encoding='utf-8')


def inspect_v6(items):
    """저장 초안에서 확인되는 경계만 보고합니다. 실제 API 요청은 추정하지 않습니다."""
    rows = []
    for item in items:
        draft = read_json(item['draft_path'])
        candidate = draft.get('ai_candidate') or {}
        monitoring = draft['monitoring']
        rows.append({'article_id': item['id'], 'title': item['title'],
            'stored_ai_candidate_has_complaint': 'complaint' in candidate,
            'stored_ai_document_type': candidate.get('document_type'),
            'stored_ai_symptoms': candidate.get('symptoms'),
            'saved_monitoring_symptoms': monitoring.get('symptoms'),
            'v752_complaint_display': item['complaint'],
            'old_summary': monitoring['display_text'],
            'actual_api_request_verified': False,
            'finding': 'V7.5.2 표 연결 코드는 complaint와 same_count에 항상 -를 대입했습니다.'})
    return rows

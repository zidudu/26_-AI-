"""V9.6 deterministic dashboard data. Never infer counts from AI prose."""
from copy import deepcopy
from pathlib import Path
from v754.core import V7Error, digest, read_json
from v9.configuration import CATALOG

VERSION = '9.6.0'
DEFAULTS = {'version': VERSION, 'enabled': True, 'cafe_summary_ai': True,
            'max_input_chars': 150000, 'max_output_tokens': 4000,
            'keyword_order': [], 'keyword_colors': {}, 'chart_renderer': 'shapes'}
CAFE_COLORS = ['317AC4','E18D32','2F997E','8B71B5','CA666C','42A2AF','A18F35','6786AD','9A687D']
KEYWORD_COLORS = ['2E75B6','E18732','C65F64','3A9586','8E75B5','BC9840','408D9E','B66E9C',
                  '739745','C5764C','527DA1','8B8469','648EB0','A06A45','638F7C','7975A6','AC765E','5D8C91']

def settings(root):
    path = Path(root) / 'config_dashboard_v96.json'
    data = {**deepcopy(DEFAULTS), **(read_json(path) if path.exists() else {})}
    import re
    if (set(data) != set(DEFAULTS) or data['version'] != VERSION
            or data['chart_renderer'] not in ('shapes','native')
            or any(type(data[k]) is not bool for k in ('enabled','cafe_summary_ai'))
            or type(data['max_input_chars']) is not int or not 10000 <= data['max_input_chars'] <= 500000
            or type(data['max_output_tokens']) is not int or not 1000 <= data['max_output_tokens'] <= 16000
            or not isinstance(data['keyword_order'], list)
            or any(not isinstance(w,str) or not w.strip() for w in data['keyword_order'])
            or len(data['keyword_order']) != len(set(data['keyword_order']))
            or not isinstance(data['keyword_colors'],dict)
            or any(not isinstance(v,str) or not re.fullmatch('[0-9A-Fa-f]{6}',v)
                   for v in data['keyword_colors'].values())):
        raise V7Error('DASHBOARD_CONFIG_ERROR','config_dashboard_v96.json 형식을 확인하세요.')
    return data

def article_key(item):
    return str(item['cafe_id']) + ':' + str(item['id'])

def axis(values):
    """Exactly one-post major ticks, with a data-dependent integer maximum."""
    if any(v is not None and (type(v) is not int or v < 0) for v in values):
        raise V7Error('DASHBOARD_COUNT_ERROR','건수는 0 이상의 정수 또는 미확인 값이어야 합니다.')
    maximum = max([v for v in values if v is not None] + [0])
    return {'minimum': 0, 'maximum': max(1,maximum), 'major_unit': 1}

def build_stats(items, cafes, words, opts, *, catalog=None):
    catalog = CATALOG if catalog is None else catalog
    words = list(dict.fromkeys(words))
    preferred = opts['keyword_order']
    if preferred and set(preferred) != set(words):
        raise V7Error('DASHBOARD_KEYWORDS_CHANGED','설정된 범례 키워드와 실제 검색어가 다릅니다.')
    words = preferred or words
    if not words:
        raise V7Error('DASHBOARD_KEYWORDS_MISSING','집계 기준 검색어가 없습니다.')
    unique = {}
    for item in items:
        k = article_key(item)
        if k in unique:
            raise V7Error('DUPLICATE_ARTICLE','카페·게시글 복합 식별자가 중복되었습니다.')
        unknown = set(item.get('matched_keywords',[])) - set(words)
        if unknown:
            raise V7Error('DASHBOARD_KEYWORDS_CHANGED','기록에 설정 밖의 검색어가 있습니다.')
        unique[k] = item
    selected = {c['slug']:c for c in cafes}
    if len(selected)!=len(cafes) or set(selected)-{c[2] for c in catalog}:
        raise V7Error('DASHBOARD_CAFE_MISMATCH','수집 대상 카페 목록이 등록 목록과 다릅니다.')
    rows = []
    counted = set()
    for index,(code,name,slug,default_id) in enumerate(catalog):
        c = selected.get(slug)
        cid = str(c.get('club_id') or '') if c else str(default_id or '')
        complete = bool(c and (c.get('collection') or c.get('status') in
                        ('collected','completed','completed_empty','partial')))
        found = [a for a in unique.values() if str(a['cafe_id']) == cid] if c else []
        if found and not complete:
            raise V7Error('DASHBOARD_COLLECTION_STATE','미완료 카페의 게시글이 확정 집계에 섞였습니다.')
        if complete and c.get('count') is not None and c['count'] != len(found):
            raise V7Error('DASHBOARD_COUNT_MISMATCH','수집 기록과 카페별 집계 건수가 다릅니다.')
        keys = sorted(article_key(a) for a in found)
        counted.update(keys)
        counts = [sum(w in set(a.get('matched_keywords',[])) for a in found) for w in words]
        state = 'complete' if complete else 'incomplete' if c else 'not_selected'
        rows.append({'code':code,'name':name,'slug':slug,'cafe_id':cid,'selected':bool(c),
                     'state':state,'count':len(found) if complete else None,
                     'keyword_counts':counts if complete else [None]*len(words),
                     'article_keys':keys,'start':c.get('start') if c else None,
                     'end':c.get('end') if c else None,'color':CAFE_COLORS[index%len(CAFE_COLORS)]})
    if counted != set(unique):
        raise V7Error('DASHBOARD_CAFE_MISMATCH','등록 카페에 연결되지 않은 게시글이 있습니다.')
    totals = [sum((r['keyword_counts'][i] or 0) for r in rows) for i in range(len(words))]
    result = {'version':VERSION,'cafes':rows,'keywords':words,
              'keyword_colors':[opts['keyword_colors'].get(w,KEYWORD_COLORS[i%len(KEYWORD_COLORS)]).upper()
                                for i,w in enumerate(words)],
              'selected_cafes':len(selected),'total_posts':len(unique),'keyword_counts':totals,
              'source_fingerprint':digest([{k:a.get(k) for k in ('cafe_id','id','source_artifact_sha256',
                  'selection_window','matched_keywords')} for a in sorted(items,key=article_key)]),
              'count_basis':'completed_collections_unique_cafe_id_article_id',
              'keyword_basis':'saved_matched_keywords_once_per_article_per_keyword'}
    result['fingerprint'] = digest(result)
    return result

def context(cfg, report, source_items, cafes, words):
    opts = settings(cfg['project_root'])
    cfg['v96_dashboard'] = opts
    if opts['enabled']:
        report['dashboard_stats'] = build_stats(source_items,cafes,words,opts)

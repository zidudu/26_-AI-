"""V9 settings are independent; V8 settings and result files are read-only."""
from copy import deepcopy
import re
from pathlib import Path
from v8.configuration import (KST, V8Error, read_json, write_json, parse_time,
                              validate as validate_v8, load as load_v8)

PERFORMANCE_DEFAULTS = {'search_recheck': 'adaptive', 'analysis_concurrency': 2,
                        'ppt_template_cache': True, 'ppt_image_ppi': 125}


def migrate_image_setting(values):
    values = dict(values)
    if 'ppt_image_level' in values:
        old = values.pop('ppt_image_level')
        if type(old) is not int or not (old == 0 or 96 <= old <= 150):
            raise ValueError('잘못된 기존 PNG 축소 수준')
        values.setdefault('ppt_image_ppi', 0 if old in (0,150) else 125)
    values.setdefault('ppt_image_ppi', PERFORMANCE_DEFAULTS['ppt_image_ppi'])
    return values


def performance(cfg):
    return {**PERFORMANCE_DEFAULTS, **migrate_image_setting(cfg.get('performance', {}))}

CATALOG = [
    ('GN7', '그랜저 멤버스', 'bestcm', None),
    ('MX5', '싼타페 MX5 패밀리', 'iroid', '20179506'),
    ('ME', '아이오닉 멤버스', 'cafeclip', None),
    ('제네시스 전차종', '제클', 'newgenesisdh', None),
    ('RG3', '제네시스 G80 위너클래스', 'fam100', None),
    ('JX1', 'GV80 멤버스', 'gruu', None),
    ('GL3', 'K8 오너스클럽', 'ite', '11672934'),
    ('KA4', '카니발 포에버', 'story77', None),
    ('MQ4', '쏘렌토 MQ4 멤버스', 'englishenglish', '10037204'),
]


def from_v8(root):
    prior = load_v8(root)
    return {'version': '9.0.0', 'schedule': deepcopy(prior['schedule']),
            'mail': deepcopy(prior['mail']), 'inherit_v8_cursor': True,
            'collection': {'concurrency': 2}, 'performance': deepcopy(PERFORMANCE_DEFAULTS),
            'cafes': [dict(code=c, name=n, slug=s, club_id=i, enabled=True,
                           id_source='user_provided' if i else 'unresolved') for c,n,s,i in CATALOG]}


def validate(data):
    cfg = deepcopy(data)
    try:
        cfg.setdefault('collection', {'concurrency': 2})
        cfg.setdefault('performance', deepcopy(PERFORMANCE_DEFAULTS))
        if set(cfg) != {'version','schedule','mail','inherit_v8_cursor','cafes','collection','performance'} or cfg['version'] != '9.0.0':
            raise ValueError('V9 설정 형식')
        perf = cfg['performance']
        if isinstance(perf, dict):
            cfg['performance'] = perf = migrate_image_setting(perf)
        from v9.ppt_images import valid_ppi
        if (not isinstance(perf, dict) or set(perf) != set(PERFORMANCE_DEFAULTS)
                or perf['search_recheck'] not in ('adaptive', 'full')
                or type(perf['analysis_concurrency']) is not int
                or perf['analysis_concurrency'] not in (1, 2, 3)
                or type(perf['ppt_template_cache']) is not bool
                or not valid_ppi(perf['ppt_image_ppi'])):
            raise ValueError('성능 설정: 검색 adaptive/full, AI 동시 분석 1·2·3, PPT 양식 true/false, PNG PPI 0 또는 96~300')
        collection=cfg['collection']
        if (not isinstance(collection,dict) or set(collection)!={'concurrency'} or
                type(collection['concurrency']) is not int or collection['concurrency'] not in (1,2,3)):
            raise ValueError('동시 수집 카페 수는 1, 2, 3 중 하나')
        common = validate_v8({'version':'8.0.0','schedule':cfg['schedule'],'mail':cfg['mail']})
        cfg.update(schedule=common['schedule'], mail=common['mail'])
        if type(cfg['inherit_v8_cursor']) is not bool:
            raise ValueError('inherit_v8_cursor는 true/false')
        if not isinstance(cfg['cafes'], list) or len(cfg['cafes']) != 9:
            raise ValueError('대상 카페 9개가 필요합니다')
        seen, ids = set(), set()
        catalog = {row[2] for row in CATALOG}
        for cafe in cfg['cafes']:
            if set(cafe) != {'code','name','slug','club_id','enabled','id_source'}:
                raise ValueError('카페 설정 항목')
            if cafe['slug'] not in catalog or cafe['slug'] in seen:
                raise ValueError('카페 슬러그 중복 또는 잘못된 슬러그')
            seen.add(cafe['slug'])
            if type(cafe['enabled']) is not bool:
                raise ValueError('enabled는 true/false')
            for key in ('code','name','id_source'):
                if not isinstance(cafe[key],str) or not cafe[key].strip() or len(cafe[key])>200 or any(x in cafe[key] for x in '\r\n\x00'):
                    raise ValueError('카페 이름·코드·ID 출처')
            cid=cafe['club_id']
            if cid is not None:
                if not isinstance(cid,str) or not re.fullmatch(r'[1-9][0-9]{0,11}',cid) or cid in ids:
                    raise ValueError('club_id는 중복 없는 숫자 문자열')
                ids.add(cid)
        if not any(c['enabled'] for c in cfg['cafes']):
            raise ValueError('사용할 카페를 1개 이상 선택하세요')
    except (KeyError, TypeError, ValueError) as exc:
        raise V8Error('V9_CONFIG_ERROR',f'config_v9.json을 확인하세요: {exc}') from exc
    return cfg


def load(root):
    path=Path(root)/'config_v9.json'
    if not path.is_file():
        raise V8Error('V9_SETUP_REQUIRED','먼저 30_setup_v9.bat를 실행하세요.')
    return validate(read_json(path))


def selected(cfg, slugs=None):
    if slugs is None:
        return [deepcopy(c) for c in cfg['cafes'] if c['enabled']]
    wanted = set(slugs)
    if not wanted or not wanted <= {c['slug'] for c in cfg['cafes']}:
        raise V8Error('INVALID_CAFE_SELECTION','카페 번호 또는 등록된 슬러그를 확인하세요.')
    return [deepcopy(c) for c in cfg['cafes'] if c['slug'] in wanted]

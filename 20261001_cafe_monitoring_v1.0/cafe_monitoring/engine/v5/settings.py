"""모든 경로는 설정 파일 위치 기준. 검색 범위는 V4와 동일합니다."""
import math
from pathlib import Path
import re
import collector
from keywords import parse_keywords

ROOT=Path(__file__).resolve().parent.parent
DEFAULT_CONFIG=ROOT/'config_v5.json'
VERSION=collector.VERSION
CONDITIONS={'scope':'title','board':'all','period':'all','sort':'latest'}

def read_settings(path=DEFAULT_CONFIG):
    path=Path(path).resolve()
    cfg,target=collector.read_config(path)
    cfg['root']=path.parent
    cfg['config_path']=path
    cfg['keywords']=parse_keywords(cfg.get('keywords',['불량','고장']))
    limits={'initial_count':(10,1,100),'overlap_days':(1,0,7),
            'max_pages_per_keyword_per_run':(20,2,100),'max_search_page':(500,2,1000),
            'max_collect_per_run':(100,1,1000),'retry_after_minutes':(60,1,10080),
            'max_attempts':(3,1,10)}
    for key,(default,low,high) in limits.items():
        v=cfg.get(key,default)
        if type(v) is not int or not low<=v<=high:
            raise collector.CollectorError('CONFIG_ERROR',f'{key}는 {low}~{high} 사이 정수여야 합니다.')
        cfg[key]=v
    gap=cfg.get('request_interval_seconds',2)
    if type(gap) not in (int,float) or not math.isfinite(gap) or not 1<=gap<=60:
        raise collector.CollectorError('CONFIG_ERROR','request_interval_seconds는 1~60 사이 숫자여야 합니다.')
    cfg['request_interval_seconds']=gap
    value=cfg.get('history_db','data/history_v5.sqlite3')
    if not isinstance(value,str) or not value.strip():
        raise collector.CollectorError('CONFIG_ERROR','history_db에는 파일 경로가 필요합니다.')
    cfg['history_db']=(path.parent/value).resolve()
    if cfg['history_db']==cfg['profile_dir'] or cfg['profile_dir'] in cfg['history_db'].parents:
        raise collector.CollectorError('CONFIG_ERROR','이력 DB는 브라우저 프로필 밖에 두세요.')
    cfg['profile_lock']=cfg['profile_dir'].parent/(cfg['profile_dir'].name+'_v5.lock')
    sources=cfg.get('import_dirs',['output_v4'])
    if not isinstance(sources,list) or any(not isinstance(v,str) or not v.strip() for v in sources):
        raise collector.CollectorError('CONFIG_ERROR','import_dirs는 폴더 경로 문자열 배열이어야 합니다.')
    cfg['import_dirs']=list(dict.fromkeys((path.parent/v).resolve() for v in sources))
    for folder in cfg['import_dirs']:
        if folder==cfg['profile_dir'] or folder in cfg['profile_dir'].parents or cfg['profile_dir'] in folder.parents:
            raise collector.CollectorError('CONFIG_ERROR','수집 결과 폴더만 import_dirs에 지정하세요.')
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',cfg.get('schedule_time','')):
        raise collector.CollectorError('CONFIG_ERROR','schedule_time은 HH:MM 형식이어야 합니다.')
    return cfg,target

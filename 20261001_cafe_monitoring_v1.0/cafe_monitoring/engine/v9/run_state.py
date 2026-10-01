"""Persistent run records, V8 cursor migration and per-cafe commit rules.

JSON schemas and checkpoint ordering remain compatible with V9.6.6.
The caller holds the existing process locks.
"""
from datetime import datetime
import re
from v8.configuration import KST, V8Error, read_json, write_json
from v8.pipeline import sha, stamp
from v8.schedule import scope
from v9.run_model import DONE, PATTERN

def load_state(root, cfg, words, state_path):
    words=sorted(set(words))
    if state_path.exists():
        state=read_json(state_path)
        if state.get('version')!='9.0.0' or state.get('keywords')!=words:
            raise V8Error('SCOPE_CHANGED','V9 예약 키워드와 저장 이력이 다릅니다. 기존 키워드를 복구하세요. 기간 지정 실행은 별도입니다.')
        if set(state.get('cafes',{}))!={c['slug'] for c in cfg['cafes']}:
            raise V8Error('STATE_MISMATCH','저장 이력의 카페 목록이 다릅니다.')
        for c in cfg['cafes']:
            entry=state['cafes'][c['slug']]
            if c.get('club_id') and entry.get('club_id') and c['club_id']!=entry['club_id']:
                raise V8Error('SCOPE_CHANGED','카페 ID가 처리 이력과 다릅니다: '+c['slug'])
            for name in ('cursor','failed_end'):
                value=entry.get(name)
                if value and datetime.fromisoformat(value).utcoffset()!=KST.utcoffset(None):
                    raise V8Error('STATE_TIME_ERROR','처리 이력은 한국시간이어야 합니다.')
        return state
    state={'version':'9.0.0','keywords':words,'active':None,'cafes':{
        c['slug']:{'club_id':c['club_id'],'cursor':None,'failed_end':None} for c in cfg['cafes']}}
    old=root/'output_v8/state.json'
    if cfg['inherit_v8_cursor'] and old.exists():
        prior=read_json(old)
        matches=[c for c in cfg['cafes'] if c['club_id']==prior.get('scope',{}).get('cafe_id')]
        if matches:
            if prior.get('scope')!=scope(matches[0]['club_id'],words):
                raise V8Error('V8_SCOPE_CHANGED','기존 V8와 키워드가 달라 완료 시각을 이어받지 않았습니다.')
            if prior.get('active'):
                active=prior['active']
                if not re.fullmatch(r'run_\d{8}_\d{4}_\d{8}_\d{4}_[a-f0-9]{12}',str(active)):
                    raise V8Error('V8_STATE_INVALID','V8 진행 중 실행 번호를 확인하세요.')
                record=read_json(root/'output_v8/runs'/active/'run.json')
                if record.get('mail',{}).get('status') in ('sending','unknown','displayed') or record.get('status')=='mail_held':
                    raise V8Error('V8_PENDING_MAIL','V8에 확인할 메일이 남아 있습니다. 18_resolve_mail_v8.bat로 확인 후 V8 실행을 마무리하세요.')
                if record.get('mail',{}).get('status')=='sent':
                    raise V8Error('V8_CURSOR_PENDING','V8 발송 기록의 완료 처리가 남았습니다. 12_run_v8.bat로 기록을 마무리하세요.')
            cursor=prior.get('cursor')
            if cursor:
                value=datetime.fromisoformat(cursor)
                if value.utcoffset()!=KST.utcoffset(None):
                    raise V8Error('V8_STATE_INVALID','V8 완료 시각 형식을 확인하세요.')
                state['cafes'][matches[0]['slug']]['cursor']=cursor
                state['migration']={'source':str(old),'sha256':sha(old),'slug':matches[0]['slug'],'cursor':cursor}
                print(f"[V8 기록 이어받기] {matches[0]['name']} / {cursor}")
    return state


def save_record(folder, record):
    record['updated_at']=stamp()
    write_json(folder/'run.json',record)


def get_record(out, identifier, words):
    if not isinstance(identifier,str) or not PATTERN.fullmatch(identifier):
        raise V8Error('INVALID_RUN','V9 실행 번호를 확인하세요.')
    mode='period' if identifier.startswith('period_') else 'runs'
    folder=out/mode/identifier
    record=read_json(folder/'run.json')
    if record.get('id')!=identifier or record.get('keywords')!=sorted(set(words)):
        raise V8Error('RUN_MISMATCH','실행 번호·키워드가 다릅니다.')
    return folder,record


def commit_state(state_path, state, record):
    if state is None: return
    if state.get('active')!=record['id']:
        raise V8Error('STATE_ACTIVE_MISMATCH','현재 진행 중인 예약 기록이 다릅니다.')
    for cafe in record['cafes']:
        row=state['cafes'][cafe['slug']]
        if row.get('club_id') and cafe.get('club_id') and row['club_id']!=cafe['club_id']:
            raise V8Error('STATE_CAFE_MISMATCH','완료 처리할 카페 ID가 다릅니다.')
        if cafe['status'] in DONE:
            if not row.get('cursor') or datetime.fromisoformat(row['cursor'])<datetime.fromisoformat(cafe['end']):
                row['cursor']=cafe['end']
            row.update(club_id=cafe['club_id'],failed_end=None,last_run=record['id'])
        else:
            row.update(failed_end=cafe['end'],last_failure=record['id'])
    state['active']=None
    write_json(state_path,state)



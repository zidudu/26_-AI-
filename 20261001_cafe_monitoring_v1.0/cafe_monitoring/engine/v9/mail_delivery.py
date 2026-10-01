"""One delivery per run, with durable checkpoints before Outlook submission.

The injected sender is the only external operation. Uncertain outcomes always
require manual resolution; a retry flag is never permission to send twice.
"""
from copy import deepcopy
from v8.configuration import V8Error
from v8.pipeline import stamp
from v9.artifacts import read_artifact
from v9.run_model import LABELS, failure

def summary_text(record):
    rows=['동호회 모니터링 V9 결과','상태: '+LABELS.get(record.get('outcome',record['status']),record['status']),
          '기간은 한국시간, 시작 포함·종료 미포함입니다.','']
    if record.get('performance',{}).get('search_recheck')=='adaptive':
        rows.insert(3,'검색 목록: 한 페이지에서 판정이 끝난 검색은 최초 조회 시점을 기준으로 합니다.')
    for c in record['cafes']:
        count='미확인' if c.get('count') is None else str(c['count'])+'건'
        rows.append(f"{c['code']} · {c['name']}: {LABELS.get(c['status'],c['status'])} / 선정 {count} / PPT 포함 {c.get('included',0)}건")
        rows.append(f"  {c['start'][:16]} ~ {c['end'][:16]}")
        if c.get('error'): rows.append('  '+c['error']['code']+': '+c['error']['message'])
    return '\n'.join(rows)


def deliver(folder, record, sender, save):
    # Keep this guard at the delivery boundary as well as in Runner recovery.
    # A future consumer must not bypass it by invoking this module directly.
    if record['mail']['status'] in ('sending', 'unknown'):
        record.update(status='mail_unknown', stage='MAIL')
        record['mail']['status'] = 'unknown'
        save(folder, record)
        print('[발송 여부 확인 필요] 자동 재발송하지 않습니다. 38번을 실행하세요.')
        return
    report,ppt=read_artifact(folder,record)
    if record['mail']['status']=='sent':
        record['status']=record['outcome']
        return
    if not ppt:
        record['mail']['status']='skipped_empty' if record['outcome']=='completed_empty' else 'skipped_no_ppt'
    elif not record['send_mail']:
        record['mail']['status']='disabled'
    elif record['outcome'] in ('partial','failed') and not record['mail_settings']['send_partial']:
        record.update(status='mail_held',stage='MAIL')
        record['mail']['status']='held_partial'
        save(folder,record)
        print('[메일 보류] PPT는 저장했습니다. 설정 수정 후 37번으로 이어서 처리하세요.')
        return
    else:
        cfg=record['mail_settings']
        if not cfg['to'] or not sender: raise V8Error('RECIPIENT_REQUIRED','메일 수신자를 확인하세요.')
        body=summary_text(record)+'\n첨부 PPT는 직원 검토 전 초안입니다. 분류·요약을 원문과 대조하세요.\n'
        body+='\n검색어: '+', '.join(record['keywords'])+'\n실행 번호: '+record['id']
        prefix='[기간 지정] ' if record['mode']=='period' else ''
        subject=f"{prefix}{cfg['subject_prefix']} [V9] [{LABELS.get(record['outcome'],record['outcome'])}] / {len(record['cafes'])}개 카페 / {record['artifact']['included']}건"
        payload=dict(to=cfg['to'],cc=cfg['cc'],subject=subject,body=body,attachments=[str(ppt)],timeout=cfg['timeout_seconds'])
        record.update(stage='MAIL',mail={'status':'sending','started_at':stamp(),'payload':payload})
        save(folder,record)
        try:
            result=sender(**payload,display_only=False)
            if result.get('ok') is not True or result.get('action')!='sent':
                raise V8Error('MAIL_RESULT_UNEXPECTED','Outlook 결과를 확인하지 못했습니다.')
            record['mail'].update(status='sent',finished_at=stamp(),result=result)
            save(folder,record)
            print('[Outlook 발송 요청 완료]',subject)
        except BaseException as exc:
            record.update(status='mail_unknown')
            record['mail'].update(status='unknown',error=failure(exc))
            save(folder,record)
            print('[발송 여부 확인 필요] 자동 재발송하지 않습니다. 38번을 실행하세요.')
            return
    record['status']=record['outcome']


def resolve_delivery(folder, record, decision, save):
    if record['mail']['status'] not in ('sending','unknown') or decision not in ('sent','not-sent'):
        raise V8Error('NO_UNCERTAIN_MAIL','발송 확인 대상 또는 선택값을 확인하세요.')
    record.setdefault('mail_history',[]).append(deepcopy(record['mail']))
    record['mail']={'status':'sent' if decision=='sent' else 'not_started','user_verified_at':stamp(),'decision':decision}
    record.update(status='ready')
    save(folder,record)
    print('[확인 기록 저장] 37_retry_v9.bat로 마무리하세요. 기간 지정 실행은 해당 실행 번호를 선택하세요.')


"""One isolated pipeline per process. Browser navigation is rate limited by V9."""
from contextlib import redirect_stdout,redirect_stderr
from copy import deepcopy
from pathlib import Path
import argparse
import asyncio
import json
import sys
import traceback
from .common import Problem,InstanceLock,write_json,read_json,stamp
from .database import Database


class Journal:
    def __init__(self,db,rid,file):self.db,self.rid,self.file=db,rid,file;self.pending=''
    def write(self,text):
        self.file.write(text);self.file.flush();self.pending+=text
        while '\n' in self.pending:
            line,self.pending=self.pending.split('\n',1)
            self.db.log(self.rid,line)
        return len(text)
    def flush(self):self.file.flush()
    def isatty(self):return False


def execute(db,rid,*,engine_factory=None,office=None):
    row=db.run_row(rid);cfg=json.loads(row['settings_json']);meta=json.loads(row['record_json'])
    folder=Path(meta['output']);folder.mkdir(parents=True,exist_ok=True)
    from .engine import LegacyEngine
    from . import office as real_office
    engine_factory=engine_factory or LegacyEngine;office=office or real_office
    db.update_run(rid,state='running',stage='prepare')
    db.log(rid,'실행 시작 · '+ ' · '.join(label+(' ON' if cfg[key] else ' OFF') for key,label in
        [('stepCollect','웹 수집'),('stepAnalyze','AI 분석'),('stepPpt','PPT 생성'),('sendMail','메일 발송')]))
    failures=[];report=None
    try:
        db.check_stop(rid)
        with engine_factory(db,rid,cfg,folder) as engine:
            if cfg['stepCollect']:
                db.update_run(rid,stage='collect')
                cafes=[c for c in cfg['catalog']['cafes'] if c['id'] in cfg['selectedCafeIds']]
                rc=db.run(rid,details=False)['cafeResults']
                bounds={c['cafe_id']:(c['start_at'],c['end_at']) for c in rc}
                dedupe=cfg['range']=='기간 직접 지정' and cfg['periodDedupeEnabled']
                def collected(cafe,items,sources,error):
                    uid=cafe['id']
                    if error:
                        failures.append(uid);db.cafe_outcome(rid,uid,'failed',error=error)
                        db.log(rid,cafe['code']+' 수집 실패: '+error,'ERROR');return
                    selected=0
                    for item,source in zip(items,sources):
                        saved=db.store_article(rid,uid,item,source,dedupe=dedupe)
                        selected+=saved['included']
                    db.update_run(rid,hasRaw=True)
                    # Cursor progress is collection-specific and independent of
                    # AI/Office/mail outcomes. Custom-range runs never advance it.
                    db.cafe_outcome(rid,uid,'completed',len(items),advance=cfg['range']!='기간 직접 지정')
                    db.log(rid,f"{cafe['code']} 수집 {len(items)}건 / 이번 결과 {selected}건")
                asyncio.run(engine.collect(cafes,bounds,collected))
                db.check_stop(rid)
                if len(failures)==len(cafes):raise Problem('모든 카페의 수집이 미완료입니다. 수집 로그를 확인하세요.')
                db.update_run(rid,done='collect',hasRaw=True)
            else:
                db.reuse(rid,cfg['sourceRun'],require_analysis=cfg['stepPpt'] and not cfg['stepAnalyze'])
                db.update_run(rid,hasRaw=True,hasAnalysis=not cfg['stepAnalyze'])
            items_with_ids=db.engine_items(rid);items=[a for _,a in items_with_ids]
            mapping={(str(a['cafe_id']),str(a['id'])):uid for uid,a in items_with_ids}
            # Rebuild uses the source run's catalog and period, never today's names.
            context=db.run(rid,details=False);source=db.run(cfg['sourceRun'],details=False) if not cfg['stepCollect'] else context
            source_cfg=source['settings']
            catalog=source.get('summaryCafes') or source_cfg['summaryCafes']
            source_cafes={c['cafe_id']:c for c in source['cafeResults']}
            for_summary=[]
            verified={c['id']:c.get('naverCafeId') for c in db.catalog()['cafes']}
            for c in catalog:
                cr=source_cafes.get(c['id'],{})
                found=[a for uid,a in items_with_ids if uid==c['id']]
                cid=str(found[0]['cafe_id']) if found else str(verified.get(c['id']) or c.get('naverCafeId') or '')
                for_summary.append(dict(code=c['code'],name=c['name'],slug=c['url'].rstrip('/').split('/')[-1],club_id=cid,
                    count=len(found),status='collected' if cr.get('status')=='completed' or (not cfg['stepCollect'] and found) else 'failed',
                    start=cr.get('start_at') or source.get('periodStart'),end=cr.get('end_at') or source.get('periodEnd')))
            if not cfg['stepCollect']:
                engine.ui={**engine.ui,'keywords':source_cfg['keywords']}
                db.update_run(rid,summaryCafes=catalog,periodStart=source.get('periodStart'),periodEnd=source.get('periodEnd'))
            if cfg['stepAnalyze'] or cfg['stepPpt']:
                report=engine.report(items,for_summary)
                if not cfg['stepCollect']:
                    old_analysis=next((a for a in source.get('artifacts',[]) if a['kind']=='analysis'),None)
                    if old_analysis:
                        artifact=db.artifact(old_analysis['id'])
                        saved_report=read_json(artifact['path'])
                        if saved_report.get('cafe_summaries'):report['cafe_summaries']=deepcopy(saved_report['cafe_summaries'])
                def checkpoint():
                    write_json(folder/'analysis.json',report)
                    # Stop at a save boundary. Existing calls/COM operations finish
                    # first; do not kill Office or leave unrecorded mail retries.
                    db.check_stop(rid)
                checkpoint()
                def save_analysis(phase):
                    for a in report.get('items',[]):
                        if a.get('analysis_result_file') or a.get('analysis_candidate'):
                            db.save_analysis(rid,mapping[(str(a['cafe_id']),str(a['id']))],a,phase)
                if cfg['stepAnalyze'] and items:
                    db.update_run(rid,stage='analyze')
                    try:
                        with engine.analysis_context(items,report,checkpoint) as analysis:
                            report['items']=analysis.snapshot();save_analysis('analysis')
                            db.update_run(rid,done='analyze',hasAnalysis=True)
                            if cfg['stepPpt']:
                                db.check_stop(rid);db.update_run(rid,stage='report')
                                # Rendering may create a revised display; retain the
                                # pre-render analysis plus the final display version.
                                report['items']=analysis.articles
                                engine.render(analysis.articles,report,checkpoint,analysis)
                                save_analysis('render')
                    except BaseException:
                        save_analysis('partial');raise
                else:
                    if cfg['stepAnalyze']:db.update_run(rid,done='analyze',hasAnalysis=True)
                    if cfg['stepPpt']:
                        db.check_stop(rid);db.update_run(rid,stage='report')
                        engine.render(items,report,checkpoint)
                        save_analysis('render')
                write_json(folder/'analysis.json',report)
                db.add_artifact(rid,'analysis',folder/'analysis.json')
                db.update_run(rid,analysis=str(folder/'analysis.json'),aiCalls=report.get('ai_calls',0),
                    reviewPending=sum(bool(a.get('summary_pending') or a.get('analysis_issues')) for a in report.get('items',[])))
            if cfg['stepPpt']:
                ppt=Path(report['pptx']);aid=db.add_artifact(rid,'ppt',ppt)
                db.update_run(rid,done='report',ppt=str(ppt),slides=str(report['slides'])+'장')
                prefix=report.get('dashboard_slides',1)
                attachment=aid;previews=[]
                try:
                    images,summary=office.presentation_outputs(ppt,folder,prefix,summary=cfg['sendMail'] and cfg['mailScope']=='summary10')
                    for n,image in enumerate(images,1):previews.append(db.add_artifact(rid,'preview:'+str(n),image))
                    if summary:
                        summary_id=db.add_artifact(rid,'summary_ppt',summary)
                        db.update_run(rid,summaryPpt=str(summary))
                        if cfg['mailScope']=='summary10':attachment=summary_id
                except Exception as exc:
                    db.log(rid,'PPT 미리보기/요약본: '+str(exc),'WARNING')
                    if cfg['sendMail'] and cfg['mailScope']=='summary10':raise
                    failures.append('preview')
                db.update_run(rid,previews=previews)
                if cfg['sendMail']:
                    db.check_stop(rid);db.update_run(rid,stage='mail')
                    artifact=db.artifact(attachment)
                    # Commit intent before Send() to prevent replay after crash.
                    db.mail_start(rid,{'to':cfg['mailTo'],'cc':cfg['mailCc']},attachment)
                    try:result=office.send_mail(cfg,artifact['path'],rid)
                    except BaseException as exc:
                        db.mail_finish(rid,'unknown',{'message':str(exc)})
                        db.update_run(rid,state='mail_unknown',finish=True,notes='Outlook에서 보낸 편지함을 확인하세요. 자동 재발송하지 않습니다.')
                        return
                    db.mail_finish(rid,'submitted',result)
                    db.update_run(rid,done='mail',outlookSender=result.get('sender',''))
            db.check_stop(rid)
            partial=bool(failures or (report and (report.get('analysis_remaining',0) or report.get('analysis_review_count',0) or report.get('capture_failures') or report.get('missing_capture_articles'))))
            db.update_run(rid,state='partial' if partial else 'completed',stage='complete',finish=True,
                notes=('확인 필요: '+', '.join(failures)) if failures else '')
    except (InterruptedError,KeyboardInterrupt):
        db.update_run(rid,state='cancelled',finish=True,notes='실행 중지 · 저장 완료된 원문/분석은 보존했습니다.')
        db.log(rid,'사용자 요청으로 중지했습니다.')
    except BaseException as exc:
        db.update_run(rid,state='failed',finish=True,notes=str(exc),errorCode=getattr(exc,'code',type(exc).__name__))
        db.log(rid,str(exc),'ERROR')
        (folder/'error_traceback.txt').write_text(traceback.format_exc(),encoding='utf-8')
    finally:
        if report:write_json(folder/'analysis.json',report)
        write_json(folder/'run.json',db.run(rid))


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',required=True);p.add_argument('--run');p.add_argument('--login',action='store_true');args=p.parse_args()
    db=Database(args.data)
    with InstanceLock(db.directory/'worker.lock'):
        if args.login:
            from .engine import login
            try:login(db)
            except Exception as exc:db.put('naver_auth',{'state':'unverified','checkedAt':None,'message':str(exc)})
            return
        rid=args.run;row=db.run_row(rid);folder=Path(json.loads(row['record_json'])['output']);folder.mkdir(parents=True,exist_ok=True)
        with (folder/'run.log').open('a',encoding='utf-8') as log:
            journal=Journal(db,rid,log)
            with redirect_stdout(journal),redirect_stderr(journal):execute(db,rid)


if __name__=='__main__':main()

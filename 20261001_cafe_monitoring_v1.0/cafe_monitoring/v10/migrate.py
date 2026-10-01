"""Read-only V9 import with content-addressed, repeatable import receipts."""
from copy import deepcopy
from pathlib import Path,PureWindowsPath
import hashlib
import json
from .common import Problem,read_json,stamp,parse_time,digest
from .settings import defaults,keyword_id,normalized


def relocated(value,root):
    """Map a moved project prefix; reject references outside the selected project."""
    if not isinstance(value,str) or not value:return None
    root=Path(root).resolve();p=Path(value)
    if p.is_file() and p.resolve().is_relative_to(root):return p.resolve()
    parts=PureWindowsPath(value).parts if '\\' in value else p.parts
    for index,part in enumerate(parts):
        if part.startswith('output_') or part in ('data','results'):
            candidate=root.joinpath(*parts[index:]).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():return candidate
    return None


def rebase_item(item,root):
    item=deepcopy(item)
    for field in ('refreshed_source_file','analysis_result_file','draft_path'):
        p=relocated(item.get(field),root)
        if p:item[field]=str(p)
    for capture in item.get('captures',[]):
        p=relocated(capture.get('path'),root)
        if p:capture['path']=str(p)
    return item


def source_of(item,root):
    path=relocated(item.get('refreshed_source_file'),root)
    if not path:raise Problem('원문 파일 누락: '+str(item.get('cafe_id'))+':'+str(item.get('id')))
    source=read_json(path)
    if str(source.get('cafe_id'))!=str(item['cafe_id']) or str(source.get('article_id'))!=str(item['id']):
        raise Problem('원문과 게시글 ID가 일치하지 않습니다.')
    if item.get('source_artifact_sha256') and digest(source)!=item['source_artifact_sha256']:
        raise Problem('원문 해시가 저장 당시와 다릅니다.')
    return source


def import_project(db,root):
    root=Path(root).expanduser().resolve()
    if not root.is_dir() or not (root/'output_v9').is_dir():raise Problem('output_v9 폴더가 있는 기존 naver_cafe 폴더를 지정하세요.')
    if db.active():raise Problem('실행이 끝난 뒤 이관하세요.',409)
    results=[]
    paths=sorted(list((root/'output_v9/runs').glob('*/run.json'))+list((root/'output_v9/period').glob('*/run.json')))
    if not paths:raise Problem('기존 실행 기록(run.json)을 찾지 못했습니다.')
    backup=db.backup(db.directory/'backups'/('before_import_'+stamp().replace(':','').replace('+','_')+'.sqlite3'))
    for path in paths:
        rid=None
        try:
            old=read_json(path)
            if not isinstance(old.get('id'),str) or old['id']!=path.parent.name:raise Problem('실행 ID와 폴더가 다릅니다.')
            # Include sources and analysis receipts in the import fingerprint.
            export=None
            artifact=old.get('artifact') or {}
            if artifact.get('summary'):
                p=(path.parent/artifact['summary']).resolve()
                if p.is_relative_to(path.parent) and p.is_file():export=p
            if not export:
                for name in reversed(old.get('exports',[])):
                    p=(path.parent/name/'summary.json').resolve()
                    if p.is_relative_to(path.parent) and p.is_file():export=p;break
            report=read_json(export) if export else {}
            if export and artifact.get('summary_sha256') and hashlib.sha256(export.read_bytes()).hexdigest()!=artifact['summary_sha256']:
                raise Problem('완료된 분석 요약의 해시가 다릅니다.')
            items=report.get('items',[])
            if not items:
                for cafe in old.get('cafes',[]):
                    if not cafe.get('collection'):continue
                    cp=(path.parent/cafe['collection']/'collection.json').resolve()
                    if cp.is_relative_to(path.parent) and cp.is_file():
                        collection=read_json(cp)
                        if collection.get('artifact_sha256')!=digest({k:v for k,v in collection.items() if k!='artifact_sha256'}):raise Problem('수집 목록의 해시가 다릅니다.')
                        items.extend(collection.get('items',[]))
            prepared=[];missing=[]
            for original in items:
                item=rebase_item(original,root)
                try:source=source_of(item,root)
                except Problem as exc:missing.append(str(exc));continue
                if item.get('analysis_result_file'):
                    ap=relocated(item['analysis_result_file'],root)
                    if ap:
                        a=read_json(ap)
                        if a.get('artifact_sha256')!=digest({k:v for k,v in a.items() if k!='artifact_sha256'}) or (item.get('analysis_artifact_sha256') and a['artifact_sha256']!=item['analysis_artifact_sha256']):
                            raise Problem('저장 분석의 해시가 다릅니다.')
                    else:missing.append('분석 결과 파일 누락: '+str(item['id']));item.pop('analysis_result_file',None)
                prepared.append((item,source))
            checksum=digest({'run':old,'report':report,'sources':[(i,s) for i,s in prepared]})
            source_ref=str(path)
            with db.connect() as c:prior=c.execute('SELECT * FROM imports WHERE source=?',(source_ref,)).fetchone()
            if prior:
                if prior['sha256']==checksum:results.append({'source':str(path),'status':'skipped','runId':prior['run_id']});continue
                # Immutable previous import; a changed source becomes a new run.
                source_ref=str(path)+'#'+checksum
                with db.connect() as c:prior=c.execute('SELECT * FROM imports WHERE source=?',(source_ref,)).fetchone()
                if prior:results.append({'source':str(path),'status':'skipped','runId':prior['run_id']});continue
            cfg=defaults();cfg['sendMail']=False;cfg['scheduleEnabled']=False
            cfg['catalog']=db.catalog();catalog=cfg['catalog']['cafes'];by_slug={c['url'].rstrip('/').split('/')[-1]:c for c in catalog}
            selected=[];by_numeric={};bounds={}
            for oc in old.get('cafes',[]):
                slug=oc['slug'];cafe=by_slug.get(slug)
                if not cafe:
                    cafe=dict(id='cafe:'+slug,code=oc.get('code',slug),name=oc.get('name',slug),url='https://cafe.naver.com/'+slug,active=False,naverCafeId=None,createdAt=old.get('created_at') or stamp())
                    catalog.append(cafe);by_slug[slug]=cafe
                selected.append(cafe['id'])
                cid=str(oc.get('club_id') or '')
                if cid:by_numeric[cid]=cafe['id']
                if oc.get('start') and oc.get('end'):bounds[cafe['id']]=(stamp(parse_time(oc['start'])),stamp(parse_time(oc['end'])))
            words=old.get('keyword_order') or old.get('keywords') or report.get('keywords') or cfg['keywords']
            keys={k['id']:k for k in cfg['catalog']['keywords']}
            for w in words:keys.setdefault(keyword_id(w),dict(id=keyword_id(w),name=w,active=False,deleted=True,createdAt=stamp()))
            cfg['catalog']['keywords']=list(keys.values());cfg.update(selectedCafeIds=selected,selectedCafes=[c['code'] for c in catalog if c['id'] in selected],summaryCafes=[deepcopy(c) for c in catalog if c['id'] in selected],keywords=words,keywordIds=[keyword_id(w) for w in words],
                range='기간 직접 지정' if old.get('mode')=='period' else '이전 실행 이후',provider=(old.get('ai_settings') or {}).get('provider','openai'))
            frozen={c['slug']:c for c in old.get('cafes',[])}
            for cafe in cfg['summaryCafes']:
                original=frozen.get(cafe['url'].rstrip('/').split('/')[-1],{})
                cafe.update(name=original.get('name',cafe['name']),code=original.get('code',cafe['code']))
            prior_mail=old.get('mail_settings') or {}
            joined=lambda value:'; '.join(value) if isinstance(value,list) else str(value or '')
            cfg.update(sendMail=bool(old.get('send_mail')),mailTo=joined(prior_mail.get('to')),
                mailCc=joined(prior_mail.get('cc')),mailSubject=prior_mail.get('subject','자동차 동호회 모니터링 결과'))
            rid='legacy_'+old['id']+'_'+checksum[:8]
            original_mail=(old.get('mail') or {}).get('status','unknown')
            extra=dict(type='V9 이관',startedAt=old.get('created_at') or stamp(),legacyRunId=old['id'],legacyStatus=old.get('status'),legacyMailStatus=original_mail,
                hasRaw=bool(prepared),hasAnalysis=bool(prepared) and all(i.get('analysis_result_file') for i,s in prepared),
                stepsDone=(['collect'] if prepared else [])+(['analyze'] if any(i.get('analysis_result_file') for i,s in prepared) else []),
                notes='기존 기록 이관 · 원본 상태: '+str(old.get('status'))+ ('\n'+'\n'.join(missing) if missing else ''),output=str(path.parent),
                logPath=str(path.parent/'run.log'),mailStatus=original_mail,slides=str(report.get('slides',0))+'장')
            db.create_run(rid,cfg,bounds,request_id='import:'+checksum,trigger='import',record=extra,state='imported')
            for cid,uid in by_numeric.items():db.resolve_cafe(uid,cid)
            for item,source in prepared:
                uid=by_numeric.get(str(item['cafe_id']))
                if not uid:raise Problem('실행 카페에 연결되지 않은 게시글입니다.')
                db.store_article(rid,uid,item,source,observed=stamp(parse_time(item.get('collected_at') or old['created_at'])))
                if item.get('analysis_result_file'):db.save_analysis(rid,uid,item,'legacy')
            for oc in old.get('cafes',[]):
                uid=by_slug[oc['slug']]['id']
                if uid in bounds:db.cafe_outcome(rid,uid,'completed' if oc.get('collection') else 'failed',oc.get('count'),str(oc.get('error') or '') or None,advance=False)
            for kind,value in [('analysis',str(export) if export else ''),('ppt',str(path.parent/artifact['ppt']) if artifact.get('ppt') else report.get('pptx',''))]:
                p=relocated(value,root)
                if p:
                    if kind=='ppt' and artifact.get('ppt_sha256'):
                        with p.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
                        if actual!=artifact['ppt_sha256']:
                            missing.append('PPT 해시가 저장 당시와 다릅니다.');continue
                    db.add_artifact(rid,kind,p)
                    db.update_run(rid,**{kind if kind=='ppt' else 'analysis':str(p)},done='report' if kind=='ppt' else None)
            if (path.parent/'run.log').is_file():
                text=(path.parent/'run.log').read_text(encoding='utf-8-sig',errors='replace')
                for line in text.splitlines()[-2000:]:db.log(rid,line)
            with db.connect(write=True) as c:
                end=old.get('updated_at') or old.get('created_at') or stamp()
                c.execute('UPDATE runs SET ended_at=? WHERE id=?',(end,rid))
                c.execute('INSERT INTO imports VALUES(?,?,?,?)',(source_ref,checksum,rid,stamp()))
            stage={'COMPLETE':'complete','MAIL':'mail','EXPORT':'report','COLLECT':'collect'}.get(old.get('stage'),'complete')
            db.update_run(rid,state='partial' if missing else 'imported',stage=stage,notes='기존 기록 이관 · 원본 상태: '+str(old.get('status'))+ ('\n'+'\n'.join(missing) if missing else ''))
            results.append({'source':str(path),'status':'partial' if missing else 'imported','runId':rid,'posts':len(prepared),'missing':missing})
        except Exception as exc:
            if rid:
                try:db.update_run(rid,state='failed',finish=True,notes='이관 중 오류: '+str(exc))
                except Exception:pass
            results.append({'source':str(path),'status':'error','message':str(exc)})
    db.put('legacy_root',str(root));db.put('last_import',results)
    return {'results':results,'backup':str(backup),'counts':db.counts()}

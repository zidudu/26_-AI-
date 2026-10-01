"""Bounded cafe workers, one async browser context, centralized journal updates."""
import asyncio
from datetime import datetime
import re
import signal
import time
import traceback
import uuid

from v8.configuration import read_json, write_json, V8Error
from v8.pipeline import safe_path, stamp
from v9.collection_control import RequestGate, task_scope, log, keyword, ACCESS_CODES
from v9.history import scan, announce_history, compare_collected


async def collect_all(runner, folder, record, concurrency):
    # All mutations of the common record execute on this one event-loop thread.
    # No shared write contains an await; the identity cache has an explicit lock.
    from v9.pipeline import key, unique_items, failure
    backend=runner.backend
    todo=[c for c in record['cafes'] if not c.get('collection')]
    gate=RequestGate(backend.webcfg['request_interval_seconds'])
    limit=asyncio.Semaphore(concurrency)
    identity_lock=asyncio.Lock()
    active=peak=0
    began=time.perf_counter()

    async def identify(cafe):
        async with identity_lock:
            gate.check()
            path=runner.out/'connections.json'
            connections=read_json(path) if path.exists() else {}
            cached=connections.get(cafe['slug'])
            if cached:
                cid=cached.get('club_id')
                if not isinstance(cid,str) or not re.fullmatch(r'[1-9][0-9]{0,11}',cid):
                    raise V8Error('CONNECTION_INVALID','저장된 카페 연결 정보를 확인하세요.')
                if cafe.get('club_id') and cafe['club_id']!=cid:
                    raise V8Error('CAFE_ID_MISMATCH','설정과 저장된 연결 ID가 다릅니다.')
            else:
                cid,evidence=await backend.identify_async(cafe)
                if any(v.get('club_id')==cid and slug!=cafe['slug'] for slug,v in connections.items()):
                    raise V8Error('DUPLICATE_CAFE_ID','다른 카페에 이미 연결된 clubId입니다.')
                connections[cafe['slug']]={'club_id':cid,'checked_at':stamp(),'evidence':evidence}
                write_json(path,connections)
            cafe['club_id']=cid

    async def worker(cafe):
        nonlocal active,peak
        async with limit:
            with task_scope(cafe['code']+' · '+cafe['slug'],gate):
                active+=1
                peak=max(peak,active)
                try:
                    gate.check()
                    log('[카페 수집 시작]',flush=True)
                    await identify(cafe)
                    lo,hi=datetime.fromisoformat(cafe['start']),datetime.fromisoformat(cafe['end'])
                    history,prior=scan(runner.root,cafe['club_id'],lo,hi,backend.legacy_out)
                    announce_history(history)
                    recovered=False
                    for attempt in reversed(cafe['attempts']):
                        child=safe_path(folder,attempt)
                        if not (child/'collection.json').exists(): continue
                        try:
                            items=backend.load_collection(cafe,child,lo,hi)
                            recovered=True
                            break
                        except Exception: continue
                    if not recovered:
                        relative='cafes/'+cafe['slug']+'/collect_'+uuid.uuid4().hex[:12]
                        child=safe_path(folder,relative)
                        child.mkdir(parents=True,exist_ok=False)
                        cafe['attempts'].append(relative)
                        runner.save(folder,record)
                        await backend.collect_async(cafe,child,lo,hi)
                        items=backend.load_collection(cafe,child,lo,hi)
                    unique_items(items)
                    cafe.update(collection=str(child.relative_to(folder)),status='collected',count=len(items),
                                article_keys=sorted(key(i) for i in items))
                    cafe.pop('error',None)
                    keyword('')
                    log(f'[카페 수집 완료] {len(items)}건'+(' / 저장 결과 재사용' if recovered else ''),flush=True)
                    try:
                        compared=compare_collected(child,prior,history,child.parent/'history_overlap.json')
                        cafe['duplicates']=compared['previously_collected_articles']
                    except Exception as exc:
                        cafe['history_note']=type(exc).__name__
                except (KeyboardInterrupt,EOFError):
                    gate.stop('USER_CANCELLED')
                    raise
                except Exception as exc:
                    if getattr(exc,'code','') in ACCESS_CODES:
                        gate.stop(exc.code)
                    cafe.update(status='failed',error=failure(exc))
                    error_path=folder/'cafes'/cafe['slug']/'error_traceback.txt'
                    error_path.parent.mkdir(parents=True,exist_ok=True)
                    error_path.write_text(traceback.format_exc(),encoding='utf-8')
                    log(f"[카페 미완료] {cafe['error']['code']} / {cafe['error']['message']}",flush=True)
                finally:
                    active-=1
                    runner.save(folder,record)

    # SIGINT stops new requests instead of cancelling an in-flight Playwright RPC.
    original_handler=None
    try:
        original_handler=signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT,lambda *_: gate.stop('USER_CANCELLED'))
    except (ValueError,AttributeError):
        original_handler=None
    try:
        async with backend.async_browser():
            tasks=[asyncio.create_task(worker(cafe)) for cafe in todo]
            group=asyncio.gather(*tasks,return_exceptions=True)
            try:
                results=await asyncio.shield(group)
            except asyncio.CancelledError:
                gate.stop('USER_CANCELLED')
                await asyncio.shield(group)
                raise
            if gate.reason=='USER_CANCELLED': raise KeyboardInterrupt()
            for result in results:
                if isinstance(result,BaseException): raise result
    finally:
        if original_handler is not None:
            signal.signal(signal.SIGINT,original_handler)
        record.setdefault('collection_attempts',[]).append(dict(
            finished_at=stamp(),mode='async',concurrency=concurrency,peak_active_cafes=peak,
            navigation_interval_seconds=gate.interval,navigation_grants=gate.grants,
            stop_reason=gate.reason,wall_seconds=round(time.perf_counter()-began,3)))
        runner.save(folder,record)

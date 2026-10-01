"""Windows-only dashboard graph trial with synthetic data. No AI, collection or mail."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path
import traceback
import uuid

from v754.core import config, write_json
from v754.collect.run_lock import RunLock
from v754.powerpoint import ensure_windows, SLIDE_W, SLIDE_H
from v9.configuration import CATALOG
from v9.dashboard_data import build_stats, DEFAULTS, settings
from v9.dashboard_ppt import append_dashboard, verify_dashboard
from v9 import __version__

WORDS=['SCC','전방카메라','경고등','후방','우측','제동','쏠림','전방 카메라','운전자 보조',
       '크루즈','좌측','긴급','자율 주행','레이더','전방','컨트롤','계기판','보조']


def fixture():
    cafes=[];articles=[]
    for i, ((code,name,slug,cid),count) in enumerate(zip(CATALOG,(1,13,3,None,0,6,4,None,6))):
        cid=str(cid or 80000000+i)
        cafes.append({'code':code,'name':name,'slug':slug,'club_id':cid,'count':count,
                      'status':'failed' if count is None else 'collected',
                      'start':'2000-01-01T00:00:00','end':'2000-01-02T00:00:00'})
        for n in range(count or 0):
            articles.append({'cafe_id':cid,'id':str(n+1),'matched_keywords':['SCC','경고등']})
    stats=build_stats(articles,cafes,WORDS,DEFAULTS)
    report={'synthetic_chart_trial':True,'dashboard_stats':stats,'cafe_summaries':{}}
    for row in stats['cafes']:
        report['cafe_summaries'][row['slug']]={'state':'not_generated' if row['count'] else
                                            'empty' if row['count']==0 else 'incomplete'}
    return articles,report


def execute(root,folder):
    ensure_windows()
    import pythoncom
    from win32com.client import dynamic
    from v9.dashboard_com import Guard, write_audits
    articles,report=fixture()
    pythoncom.CoInitialize()
    try:
        with ExitStack() as locks:
            locks.enter_context(RunLock(root/'output_v8/v8.lock'))
            locks.enter_context(RunLock(config(root)['out_root']/'v754_export.lock'))
            guard=Guard(report.setdefault('dashboard_com_audit',[]),'trial')
            app=guard.wrap(guard.call('connect PowerPoint',lambda:dynamic.Dispatch('PowerPoint.Application')),'app')
            app.Visible=-1
            deck=app.Presentations.Add(-1)
            deck.PageSetup.SlideWidth=SLIDE_W;deck.PageSetup.SlideHeight=SLIDE_H
            opts=settings(root)
            report['chart_renderer']=opts['chart_renderer']
            print('[그래프 방식]', 'PowerPoint 도형·텍스트 / Excel 연결 없음' if report['chart_renderer']=='shapes' else '기존 Office 차트 / Excel 연결 사용',flush=True)
            append_dashboard(deck,{'font_name':'맑은 고딕','v96_dashboard':opts},report,articles,{})
            path=folder/'chart_trial_v967_refactor.pptx'
            deck.SaveAs(str(path),24)
            verify_dashboard(deck,report,'after_save')
            if not path.is_file() or path.stat().st_size==0:raise RuntimeError('시험 PPT 저장 실패')
            deck.Close()
            reopened=app.Presentations.Open(str(path),0,0,-1)
            if reopened.Slides.Count!=10:raise RuntimeError('시험 슬라이드 수가 다릅니다.')
            verify_dashboard(reopened,report,'reopen')
            report.update(status='passed',reopened_slides=10,reopened_charts=11,pptx=str(path))
            print('[차트 시험 통과] 10장·11개 차트 저장·재열기 / 0건·미완료·13건 초과·제목·색상·눈금 검증',flush=True)
            print('[시험 PPT]',path,flush=True)
            return report
    except BaseException as exc:
        report.update(status='failed',error={'type':type(exc).__name__,'message':str(exc)})
        raise
    finally:
        import sys
        primary_error=sys.exc_info()[1]
        try:write_audits(folder,report,primary_error,include_trial=True)
        finally:
            # The newly created deck remains for inspection. Never Quit an Office app.
            pythoncom.CoUninitialize()


def main():
    from v9.rebuild_saved_ppt import Tee
    import sys,os
    root=Path(__file__).resolve().parent.parent
    folder=root/'output_v96_chart_trial'/('trial_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6])
    folder.mkdir(parents=True,exist_ok=False)
    code=1
    with (folder/'trial.log').open('x',encoding='utf-8') as log:
        with redirect_stdout(Tee(sys.stdout,log)),redirect_stderr(Tee(sys.stderr,log)):
            print(f'[V{__version__} 차트 시험] 가상 자료 10장 / 수집·AI·메일 없음 / 기존 결과 변경 없음',flush=True)
            print('[결과 폴더]',folder,flush=True)
            try:execute(root,folder);code=0
            except BaseException:
                trace=traceback.format_exc();print(trace)
                (folder/'error_traceback.txt').write_text(trace,encoding='utf-8')
                print('[차트 시험 실패] trial.log·dashboard_chart_audit.json·dashboard_com_audit.json을 확인하세요.',flush=True)
    if os.name=='nt':
        try:os.startfile(str(folder))
        except OSError:pass
    return code


if __name__=='__main__':raise SystemExit(main())

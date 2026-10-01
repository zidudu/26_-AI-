"""Office calls are confined to the worker's Windows COM thread."""
from pathlib import Path
import os
from .common import Problem, addresses


def require_windows():
    if os.name!='nt':raise Problem('Windows의 PowerPoint/Outlook이 필요합니다.')


def presentation_outputs(path,folder,prefix_count,*,summary=False):
    require_windows()
    import pythoncom
    from win32com.client import dynamic
    pythoncom.CoInitialize();deck=None
    images=[];summary_path=None
    try:
        app=dynamic.Dispatch('PowerPoint.Application')
        deck=app.Presentations.Open(str(Path(path).resolve()),-1,0,0)
        preview=Path(folder)/'preview';preview.mkdir(exist_ok=True)
        for i in range(1,min(prefix_count,deck.Slides.Count)+1):
            out=preview/f'slide_{i:03}.png'
            height=round(1600*float(deck.PageSetup.SlideHeight)/float(deck.PageSetup.SlideWidth))
            deck.Slides.Item(i).Export(str(out),'PNG',1600,height)
            if not out.is_file():raise Problem('PPT 미리보기 이미지가 생성되지 않았습니다.')
            images.append(out)
        if summary:
            summary_path=Path(folder)/(Path(path).stem+'_summary.pptx')
            # SaveCopyAs preserves the full report; edit only the new copy.
            deck.SaveCopyAs(str(summary_path),24)
            deck.Close();deck=None
            deck=app.Presentations.Open(str(summary_path),0,0,0)
            for i in range(deck.Slides.Count,prefix_count,-1):deck.Slides.Item(i).Delete()
            if deck.Slides.Count!=prefix_count:raise Problem('요약본 장수가 일치하지 않습니다.')
            remaining={str(deck.Slides.Item(i).SlideID) for i in range(1,deck.Slides.Count+1)}
            for i in range(1,deck.Slides.Count+1):
                slide=deck.Slides.Item(i)
                for n in range(1,slide.Shapes.Count+1):
                    shape=slide.Shapes.Item(n);action=shape.ActionSettings(1)
                    target=str(action.Hyperlink.SubAddress or '')
                    if target and target.split(',')[0] not in remaining:
                        action.Action=0;action.Hyperlink.SubAddress=''
                        if shape.HasTextFrame and shape.TextFrame.HasText:
                            if shape.TextFrame.TextRange.Text.strip()=='상세 보기':
                                shape.TextFrame.TextRange.Text='전체본 참고'
            deck.Save();deck.Close();deck=None
            deck=app.Presentations.Open(str(summary_path),-1,0,0)
            if deck.Slides.Count!=prefix_count:raise Problem('요약본 재열기 검증에 실패했습니다.')
        return images,summary_path
    finally:
        if deck is not None:deck.Close()
        pythoncom.CoUninitialize()


def outlook_account():
    require_windows()
    import pythoncom
    from win32com.client import dynamic
    pythoncom.CoInitialize()
    try:
        app=dynamic.Dispatch('Outlook.Application')
        draft=app.CreateItem(0);account=draft.SendUsingAccount
        return str(account.SmtpAddress or account.DisplayName) if account else 'Outlook 기본 계정 (주소 확인 불가)'
    finally:pythoncom.CoUninitialize()


def send_mail(cfg,attachment,run_id):
    require_windows()
    import pythoncom
    from win32com.client import dynamic
    pythoncom.CoInitialize()
    try:
        app=dynamic.Dispatch('Outlook.Application');mail=app.CreateItem(0)
        mail.To=';'.join(addresses(cfg['mailTo']));mail.CC=';'.join(addresses(cfg['mailCc']))
        mail.Subject=cfg['mailSubject'];mail.Body='자동차 동호회 모니터링 결과입니다.\r\n실행 ID: '+run_id+'\r\nAI 분석은 사후 검토 대상입니다.'
        mail.Attachments.Add(str(Path(attachment).resolve()))
        if not mail.Recipients.ResolveAll():raise Problem('Outlook에서 일부 수신자를 확인하지 못했습니다.')
        account=mail.SendUsingAccount
        sender=str(account.SmtpAddress or account.DisplayName) if account else 'Outlook 기본 계정'
        mail.Send()
        # Send() acceptance is not recipient-delivery confirmation.
        return {'state':'submitted','sender':sender,'message':'Outlook 전송 요청 수락 · 실제 배달 여부는 Outlook에서 확인'}
    finally:pythoncom.CoUninitialize()

"""Persist an invocation-specific result and open a nonblocking result viewer."""
from datetime import datetime
import os
from pathlib import Path
from uuid import uuid4

from v8.configuration import KST, read_json, write_json
from v8.pipeline import safe_path

STATUS = {'completed': '완료', 'partial': '부분 완료 · 원문 검토 필요',
          'completed_empty': '완료 · 선정 게시글 0건', 'failed': '실패',
          'running': '이전 실행 중단 · 재시도 확인 필요',
          'mail_unknown': '메일 발송 여부 확인 필요', 'mail_held': '부분 완료 · 메일 보류',
          'previewed': '메일 미리보기', 'waiting': '진행할 예약 구간 없음',
          'skipped_busy': '다른 작업 실행 중 · 이번 실행 건너뜀', 'cancelled': '취소'}
MAIL = {'sent': 'Outlook 발송 요청 완료 (수신 여부는 별도 확인)',
        'skipped_empty': '선정 게시글 0건으로 발송 생략', 'unknown': '확인 필요 · 자동 재발송하지 않음',
        'sending': '확인 필요 · 자동 재발송하지 않음', 'displayed': '작성창 표시 · 발송하지 않음',
        'not_started': '발송하지 않음'}


def capture_runner_result(runner, notice):
    """Use only this invocation's record, never a latest-run heuristic."""
    folder = getattr(runner, 'last_folder', None)
    record = getattr(runner, 'last_record', None)
    if not folder or not record:
        notice.update(status='waiting')
        return
    notice.update(status=record['status'], run_id=record['id'], start=record['start'], end=record['end'],
                  stage=record.get('stage'), mail=record.get('mail', {}).get('status', 'not_started'),
                  error=record.get('error'), run_folder=str(folder), record_path=str(folder/'run.json'),
                  run_log=str(folder/'run.log'))
    if record.get('artifact'):
        try:
            report = read_json(safe_path(folder, record['artifact']['summary']))
            notice.update(selected=report.get('selected_articles'), included=report.get('included_articles'),
                          review_count=report.get('analysis_review_count'), slides=report.get('slides'),
                          ppt=report.get('pptx'))
        except Exception:
            notice['report_note'] = '세부 결과를 읽지 못했습니다. 실행 기록을 확인하세요.'


def notice_text(data):
    lines = ['네이버 카페 모니터링 V8.2 실행 결과', '',
             '결과: ' + STATUS.get(data.get('status'), data.get('status', '확인 필요')),
             '실행 시각: ' + data.get('started_at', ''),
             '완료 시각: ' + data.get('finished_at', '')]
    if data.get('start'):
        lines.append(f"기간: {data['start']} 이상 ~ {data['end']} 미만 (한국시간)")
    for key, label in [('selected','선정 게시글'),('included','PPT 포함 게시글'),('slides','슬라이드'),
                       ('review_count','분류·요약 검토 필요')]:
        if data.get(key) is not None: lines.append(f'{label}: {data[key]}')
    lines.append('메일: ' + MAIL.get(data.get('mail'), '발송하지 않음'))
    if data.get('stage'): lines.append('마지막 단계: ' + data['stage'])
    if data.get('error'):
        lines.append('오류: ' + str(data['error'].get('code','')) + ' / ' + str(data['error'].get('message','')))
    if data.get('status') in ('failed','running'):
        lines.append('기록을 확인하고 원인을 해결한 뒤 17_retry_v8.bat로 재시도하세요.')
    if data.get('status') == 'skipped_busy':
        lines.append('진행 중인 작업 완료 후 12_run_v8.bat 또는 다음 예약에서 미처리 구간을 확인합니다.')
    for key,label in [('ppt','PPT'),('record_path','실행 기록'),('run_log','실행 로그'),('invocation_log','시작·종료 로그')]:
        if data.get(key): lines.append(label + ': ' + data[key])
    if data.get('report_note'): lines.append(data['report_note'])
    lines += ['', '이 창을 닫아도 예약 설정과 결과 파일은 유지됩니다.',
              '결과 창은 수집 작업과 별도이며 실행 잠금을 유지하지 않습니다.']
    return '\n'.join(lines) + '\n'


def new_notice(root):
    folder = Path(root) / 'output_v8' / 'notices'
    folder.mkdir(parents=True, exist_ok=True)
    name = datetime.now(KST).strftime('%Y%m%d_%H%M%S_%f') + '_' + uuid4().hex[:8]
    path = folder / (name + '.json')
    return path, {'version': '8.2.0', 'status': 'failed', 'started_at': datetime.now(KST).isoformat(),
                  'invocation_log': str(path.with_suffix('.log'))}


def send_current_session_message(title, message, *, api=None):
    """Windows owns this asynchronous dialog; no child joins the scheduled job.

    WTS_CURRENT_SESSION targets only the invoking user's own Windows session.
    bWait=False returns immediately; Timeout=0 leaves the dialog for review.
    """
    import ctypes
    from ctypes import wintypes
    if api is None:
        api = ctypes.WinDLL('wtsapi32', use_last_error=True).WTSSendMessageW
        api.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD,
                        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                        ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
        api.restype = wintypes.BOOL
    response = wintypes.DWORD()
    return bool(api(None, 0xFFFFFFFF, title, len(title.encode('utf-16-le')),
                    message, len(message.encode('utf-16-le')), 0x40, 0,
                    ctypes.byref(response), False))


def open_viewer(path):
    if os.name != 'nt':
        return False
    data = read_json(path)
    # The native dialog stays compact. Full paths, errors and log details are
    # retained in the result text and the manually opened 21-series viewer.
    lines = notice_text(data).splitlines()
    lines = [line for line in lines if not line.startswith(
        ('PPT:', '실행 기록:', '실행 로그:', '시작·종료 로그:', '결과 창은'))]
    lines += ['전체 결과·PPT·로그 열기: 21_show_result_v82.bat',
              '결과 기록: ' + str(path.with_suffix('.txt'))]
    return send_current_session_message('V8.2 · 실행 결과', '\n'.join(lines))


def finish_notice(path, data, code):
    data.update(finished_at=datetime.now(KST).isoformat(), exit_code=code)
    write_json(path, data)
    path.with_suffix('.txt').write_text(notice_text(data), encoding='utf-8-sig')
    try:
        opened = open_viewer(path)
    except OSError as exc:
        opened = False
        data['viewer_error'] = type(exc).__name__
    data['viewer_launch_requested'] = opened
    write_json(path, data)
    if not opened:
        print('[알림 표시 확인 필요] 21_show_result_v82.bat에서 저장된 결과를 확인하세요.', flush=True)
    print('[실행 결과 기록]', path.with_suffix('.txt'), flush=True)


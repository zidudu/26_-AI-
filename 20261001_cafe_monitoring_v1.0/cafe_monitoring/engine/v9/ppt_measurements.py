"""Measure original captures with Office; own COM objects live on one STA thread."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from v754.core import V7Error

_session = ContextVar('ppt_original_measurement_session', default=None)


class OfficeProbe:
    def __init__(self):
        self.app = self.deck = None
        self.initialized = False
        from v754.powerpoint import ensure_windows, SLIDE_W, SLIDE_H
        ensure_windows()
        import pythoncom
        self.pythoncom = pythoncom
        pythoncom.CoInitialize()
        self.initialized = True
        try:
            from win32com.client import dynamic
            self.app = dynamic.Dispatch('PowerPoint.Application')
            self.app.Visible = -1
            # Match final rendering's Add(-1) and slide size exactly. Only this
            # unsaved measurement deck belongs to us, even in a shared app.
            self.deck = self.app.Presentations.Add(-1)
            self.deck.PageSetup.SlideWidth = SLIDE_W
            self.deck.PageSetup.SlideHeight = SLIDE_H
        except BaseException:
            self.close()
            raise

    def measure(self, entries, output_dir):
        from v754.powerpoint import get_dimensions
        root = Path(output_dir).resolve()
        caps = []
        for entry in entries:
            path = (root / entry['path']).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise V7Error('PPT_IMAGE_MEASUREMENT_PATH', '원본 실측 경로를 확인하지 못했습니다.')
            caps.append({'path': str(path), 'index': entry['index'],
                         'width': entry['width'], 'height': entry['height']})
        article = {'id': 'capture_ppi_measurement', 'captures': caps,
                   'capture_problem': False, 'capture_notes': []}
        report = {'capture_failures': []}
        # Same AddPicture(-1,-1), ratio validation and cleanup as final rendering.
        get_dimensions(self.deck, [article], report)
        if len(article['captures']) != len(entries) or report['capture_failures']:
            raise V7Error('PPT_IMAGE_MEASUREMENT_INCOMPLETE', '게시글의 원본 캡처를 모두 실측하지 못했습니다.')
        return {c['index']: {'office_width': c['office_width'], 'office_height': c['office_height'],
                            'slide_width_pt': float(self.deck.PageSetup.SlideWidth),
                            'slide_height_pt': float(self.deck.PageSetup.SlideHeight)}
                for c in article['captures']}

    def close(self):
        try:
            if self.deck is not None:
                deck, self.deck = self.deck, None
                try:
                    deck.Saved = -1
                    deck.Close()
                except Exception:
                    print('[PPT 원본 실측] 임시 프레젠테이션 닫기 확인 필요', flush=True)
        finally:
            # PowerPoint may also host the user's own presentations: never Quit.
            self.app = None
            if self.initialized:
                self.initialized = False
                self.pythoncom.CoUninitialize()


class MeasurementSession:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='ppt-original-measure')
        self.probe = None
        self.start_failed = False
        self.closed = False

    def _measure(self, entries, output_dir):
        if self.start_failed:
            raise V7Error('PPT_IMAGE_MEASUREMENT_UNAVAILABLE', '이번 실행의 PowerPoint 원본 실측을 시작하지 못했습니다.')
        if self.probe is None:
            try:
                self.probe = OfficeProbe()
            except Exception:
                self.start_failed = True
                raise
        return self.probe.measure(entries, output_dir)

    def measure(self, entries, output_dir):
        if self.closed:
            raise V7Error('PPT_IMAGE_MEASUREMENT_CLOSED', '원본 실측 세션이 종료됐습니다.')
        # A snapshot crosses the thread boundary; source dictionaries are not mutated.
        snapshot = [{k: e[k] for k in ('index', 'path', 'width', 'height')} for e in entries]
        return self.pool.submit(self._measure, snapshot, str(output_dir)).result()

    def _close(self):
        if self.probe is not None:
            try:
                self.probe.close()
            finally:
                self.probe = None

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.pool.submit(self._close).result()
            finally:
                self.pool.shutdown(wait=True)


@contextmanager
def measurement_session():
    existing = _session.get()
    if existing is not None:
        yield existing
        return
    session = MeasurementSession()
    token = _session.set(session)
    try:
        yield session
    finally:
        _session.reset(token)
        session.close()


def measure_originals(entries, output_dir):
    session = _session.get()
    if session is not None:
        return session.measure(entries, output_dir)
    # Direct one-post capture callers use the same path with a short-lived scope.
    with measurement_session() as session:
        return session.measure(entries, output_dir)

"""Separate viewer: no scheduler state, locks, browser, API or mail calls."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from v8.configuration import read_json
from v8.result_notice import notice_text, STATUS


def show(path):
    data = read_json(path)
    try:
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText
        window = tk.Tk()
    except Exception:
        # Some company Python installs omit Tk. The result still stays visible
        # in the default text-file application, independently of the task.
        if os.name == 'nt': os.startfile(str(path.with_suffix('.txt')))
        return
    window.title('V8.2 · ' + STATUS.get(data.get('status'), '실행 결과'))
    window.geometry('820x480')
    text = ScrolledText(window, wrap='word', font=('맑은 고딕', 11))
    text.pack(fill='both', expand=True, padx=12, pady=12)
    text.insert('1.0', notice_text(data)); text.configure(state='disabled')
    buttons = ttk.Frame(window); buttons.pack(fill='x', padx=12, pady=(0,12))
    def open_existing(value):
        if value and Path(value).exists() and os.name == 'nt': os.startfile(str(value))
    if data.get('ppt'):
        ttk.Button(buttons, text='PPT 열기', command=lambda: open_existing(data['ppt'])).pack(side='left')
    ttk.Button(buttons, text='결과 폴더', command=lambda: open_existing(data.get('run_folder',path.parent))).pack(side='left', padx=8)
    ttk.Button(buttons, text='로그 열기', command=lambda: open_existing(data.get('invocation_log'))).pack(side='left')
    ttk.Button(buttons, text='닫기', command=window.destroy).pack(side='right')
    window.mainloop()


def main():
    if len(sys.argv) == 2 and sys.argv[1] == '--latest':
        paths = list((ROOT/'output_v8/notices').glob('*.json'))
        if not paths:
            print('V8.2 예약 실행 결과 기록이 아직 없습니다.'); return 1
        path = max(paths, key=lambda p: p.stat().st_mtime_ns)
    elif len(sys.argv) == 2:
        path = Path(sys.argv[1]).resolve()
    else:
        return 1
    show(path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

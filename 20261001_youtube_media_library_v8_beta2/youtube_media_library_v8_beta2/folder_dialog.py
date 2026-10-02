"""서버의 스레드와 분리해 Windows 시스템 폴더 선택기를 엽니다."""
import json
import sys
import tkinter as tk
from tkinter import filedialog
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
root = tk.Tk()
root.withdraw()
root.attributes('-topmost', True)
path = filedialog.askdirectory(title='기존 YouTube 결과의 output 폴더 선택', parent=root)
root.destroy()
print(json.dumps({'path': path or ''}, ensure_ascii=False))

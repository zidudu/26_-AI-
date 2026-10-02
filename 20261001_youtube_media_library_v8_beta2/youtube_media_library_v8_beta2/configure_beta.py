"""서버 PC에서 실행하는 계정/주소 설정 GUI. 원격 브라우저에서는 실행하지 않습니다."""
from __future__ import annotations
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing
import subprocess
import sys
from pathlib import Path
from yme.remote_security import ROOT, ServerConfig, set_owner

CONFIG = ROOT / 'config'


def private_permissions(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        folder.chmod(0o700)
        return
    raw = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, check=True).stdout
    sid = re.search(rb'S-1-\d+(?:-\d+)+', raw)
    if not sid:
        raise RuntimeError('현재 Windows 사용자 SID를 확인하지 못했습니다.')
    grant = '*' + sid.group().decode('ascii') + ':(OI)(CI)F'
    r = subprocess.run(['icacls', str(folder), '/inheritance:r', '/grant:r', grant,
                        '*S-1-5-18:(OI)(CI)F', '*S-1-5-32-544:(OI)(CI)F'], capture_output=True)
    if r.returncode:
        raise RuntimeError('설정 폴더 접근권한을 제한하지 못했습니다.')


def migrate_library(old_root: Path, new_data: Path):
    source = (old_root / 'data/library.sqlite3').resolve()
    if not source.is_file():
        raise ValueError('선택한 V8 폴더에 data/library.sqlite3가 없습니다.')
    new_data.mkdir(parents=True, exist_ok=True)
    target = new_data / 'library.sqlite3'
    if target.exists():
        raise ValueError('beta DB가 이미 존재합니다. 안전을 위해 덮어쓰지 않았습니다. 새 DB 폴더를 지정하거나 기존 자료 가져오기를 이용하세요.')
    tmp = new_data / 'library.migration.tmp'
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(tmp)) as dst:
            src.backup(dst)
            names = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'items','files','tags','item_tags','jobs'} <= names:
                raise ValueError('V8 라이브러리 형식이 아닙니다.')
            dst.execute("UPDATE jobs SET status='interrupted',stage='이전 버전에서 가져온 작업 · 필요 시 재시도' WHERE status IN ('queued','running','cancelling')")
            dst.commit()
            dst.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            dst.execute('PRAGMA journal_mode=DELETE')
            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('복사한 DB 검사에 실패했습니다.')
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def tailscale_exe():
    found = shutil.which('tailscale')
    if found:
        return found
    p = Path(os.environ.get('ProgramFiles', 'C:/Program Files'))/'Tailscale/tailscale.exe'
    return str(p) if p.exists() else None


def detect_private_url():
    exe = tailscale_exe()
    if not exe:
        raise ValueError('Tailscale이 설치되지 않았습니다. 02_connect_tailscale.bat을 이용하세요.')
    p = subprocess.run([exe, 'status', '--json'], capture_output=True, timeout=15)
    if p.returncode:
        raise ValueError('PC의 Tailscale 로그인 상태를 확인하세요.')
    data = json.loads(p.stdout.decode('utf-8'))
    host = data.get('Self', {}).get('DNSName', '').rstrip('.')
    if data.get('BackendState') != 'Running' or not host:
        raise ValueError('Tailscale 로그인과 MagicDNS 설정을 확인하세요.')
    return 'https://' + host


def main():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    try:
        cfg = ServerConfig.load(CONFIG)
    except RuntimeError:
        cfg = ServerConfig()
    owner_file = CONFIG/'owner.json'
    owner = json.loads(owner_file.read_text(encoding='utf-8')) if owner_file.is_file() else {}
    root = tk.Tk();root.title('YouTube Media Library · V8 beta.2 서버 설정')
    root.geometry('790x770');root.minsize(700,730)
    font = 'Malgun Gothic' if os.name=='nt' else 'Sans'
    root.option_add('*Font', (font,10))
    box = ttk.Frame(root,padding=24);box.pack(fill='both',expand=True);box.columnconfigure(1,weight=1)
    ttk.Label(box,text='개인 미디어 서버 설정',font=(font,19,'bold')).grid(row=0,column=0,columnspan=3,sticky='w')
    ttk.Label(box,text='이전 V8 서버를 종료하고 설정하세요. 계정 비밀번호는 해시로 저장합니다.',wraplength=690).grid(row=1,column=0,columnspan=3,sticky='w',pady=(6,16))
    user=tk.StringVar(value=owner.get('username',''));pw=tk.StringVar();confirm=tk.StringVar()
    private=tk.StringVar(value=cfg.private_url);cf=tk.StringVar(value=cfg.cloudflare_url)
    data=tk.StringVar(value=cfg.data_dir);output=tk.StringVar(value=cfg.output_dir);old=tk.StringVar()
    port=tk.StringVar(value=str(cfg.port))
    row=2
    def field(label,var,show=None,browse=False):
        nonlocal row
        ttk.Label(box,text=label).grid(row=row,column=0,sticky='w',pady=6,padx=(0,10))
        entry=ttk.Entry(box,textvariable=var,show=show or '')
        entry.grid(row=row,column=1,columnspan=1 if browse else 2,sticky='ew',pady=6)
        if browse:
            def choose():
                p=filedialog.askdirectory(parent=root)
                if p:var.set(p)
            ttk.Button(box,text='찾기',command=choose).grid(row=row,column=2,padx=(8,0))
        row+=1
    field('로그인 아이디',user);field('비밀번호 · 8자 이상',pw,'*');field('비밀번호 확인',confirm,'*')
    ttk.Label(box,text='계정이 이미 있으면 비밀번호 두 칸을 비워 기존 비밀번호를 유지할 수 있습니다.',wraplength=690).grid(row=row,column=0,columnspan=3,sticky='w',pady=(0,14));row+=1
    field('개인 접속 HTTPS 주소',private)
    def detect():
        try:private.set(detect_private_url())
        except Exception as e:messagebox.showerror('주소 확인',str(e),parent=root)
    ttk.Button(box,text='설치된 Tailscale에서 주소 확인',command=detect).grid(row=row,column=1,columnspan=2,sticky='w',pady=(0,7));row+=1
    field('Cloudflare 주소 · 선택',cf)
    ttk.Label(box,text='개인 주소는 처음에 비워도 됩니다. 02번 연결 도우미가 저장합니다.\nCloudflare 공개 주소는 관리 전용이며 영상·음원은 전달하지 않습니다.',wraplength=690).grid(row=row,column=0,columnspan=3,sticky='w',pady=(0,14));row+=1
    field('태그 / DB 폴더',data,browse=True);field('새 영상 저장 폴더',output,browse=True)
    field('이전 V8 폴더 · 선택',old,browse=True)
    ttk.Label(box,text='이전 폴더를 선택하면 태그·즐겨찾기를 포함한 DB만 복사합니다.\n영상은 원래 위치를 참조하므로 이전 output 폴더는 삭제하지 마세요.',wraplength=690).grid(row=row,column=0,columnspan=3,sticky='w',pady=(0,10));row+=1
    field('로컬 포트',port)
    result={'saved':False}
    def save():
        try:
            if pw.get()!=confirm.get():raise ValueError('비밀번호 확인이 일치하지 않습니다.')
            if not owner and not pw.get():raise ValueError('처음에는 로그인 계정을 만들어야 합니다.')
            if owner and user.get().strip()!=owner['username'] and not pw.get():raise ValueError('아이디 변경 시 새 비밀번호도 입력하세요.')
            settings=ServerConfig(port=int(port.get()),private_url=private.get(),cloudflare_url=cf.get(),data_dir=data.get(),output_dir=output.get(),fast_url=cfg.fast_url).checked()
            # 모든 입력을 검사한 다음 파일을 기록합니다.
            if pw.get():
                from yme.remote_security import password_record
                password_record(pw.get())
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{2,79}',user.get().strip()):raise ValueError('아이디를 확인하세요. 영문/숫자 3자 이상입니다.')
            private_permissions(CONFIG)
            if old.get().strip():
                if not messagebox.askokcancel('기존 자료 가져오기','이전 V8 서버를 종료하셨나요? DB만 복사하며 원본 영상 폴더는 유지해야 합니다.',parent=root):return
                migrate_library(Path(old.get().strip()),Path(settings.data_dir))
            if pw.get():set_owner(CONFIG,user.get(),pw.get())
            settings.save(CONFIG)
            Path(settings.output_dir).mkdir(parents=True,exist_ok=True)
            result['saved']=True
            messagebox.showinfo('설정 저장','설정을 저장했습니다.\n\n외부 연결: 02_connect_tailscale.bat\n서버 시작: run_v8_beta.bat\n\n실행 중인 beta 서버가 있다면 다시 시작하세요.',parent=root)
            root.destroy()
        except Exception as e:messagebox.showerror('설정 확인',str(e),parent=root)
    ttk.Button(box,text='설정 저장',command=save).grid(row=row,column=1,columnspan=2,sticky='ew',pady=(16,0))
    root.mainloop()
    return 0 if result['saved'] else 2


if __name__=='__main__':
    raise SystemExit(main())

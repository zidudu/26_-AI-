"""프로젝트 전용 가상환경. 기존 PC용 V7 환경은 변경하지 않습니다."""
from __future__ import annotations
import hashlib
import os
import subprocess
import sys
from pathlib import Path


def main():
    if sys.version_info < (3, 10):
        print('[ERROR] Python 3.10 or newer is required.')
        return 1
    root = Path(__file__).resolve().parent
    venv = root / '.venv'
    python = venv / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        print('[SETUP] Creating a project-only Python environment...', flush=True)
        subprocess.check_call([sys.executable, '-m', 'venv', str(venv)])
    requirements = root / 'requirements.txt'
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    stamp = venv / '.yme_dependencies'
    check = subprocess.run([str(python), '-c', 'import fastapi,uvicorn,requests,yt_dlp,yt_dlp_ejs,youtube_transcript_api,imageio_ffmpeg,PIL; from starlette.responses import FileResponse'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    upgrade = '--upgrade' in sys.argv
    if check.returncode or not stamp.exists() or stamp.read_text().strip() != digest or upgrade:
        print('[SETUP] Installing dependencies. Internet is needed for this step.', flush=True)
        cmd = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(requirements)]
        if upgrade:
            cmd.append('--upgrade')
        subprocess.check_call(cmd)
        stamp.write_text(digest)
    print('[OK] Project environment is ready.', flush=True)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (subprocess.CalledProcessError, OSError) as exc:
        print('[ERROR] Setup failed:', exc)
        print('Check your Internet connection, disk space and Python installation.')
        raise SystemExit(1)

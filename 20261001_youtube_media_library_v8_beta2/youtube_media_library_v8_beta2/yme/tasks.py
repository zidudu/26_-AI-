"""영속 작업 대기열. UI와 분리된 프로세스를 순차 실행하고 취소합니다."""
from __future__ import annotations
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from .library import BASE, Store, atomic_text, now, safe_json
from .windows_job import windows_job

TERMINAL = {'success', 'partial', 'failed', 'cancelled', 'interrupted'}


def kill_tree(proc: subprocess.Popen):
    if proc.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=20)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


from .events import JobEvents


class Manager:
    def __init__(self, store: Store):
        self.store = store
        self.stop_event = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.current: str | None = None
        self.thread = None
        self.lock = threading.RLock()
        self.events = JobEvents()
        self.notify()

    def notify(self, library_changed=False):
        self.events.publish(self.store.job_summaries(), library_changed)

    def start(self):
        # 이전 서버 종료 시 미완료 작업을 성공으로 표시하지 않습니다.
        with self.store.connect() as c:
            c.execute("UPDATE jobs SET status='interrupted',stage='이전 실행이 중단되었습니다. 재시도할 수 있습니다.' WHERE status IN ('running','cancelling')")
        self.notify(library_changed=True)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def enqueue(self, kind: str, payload: dict) -> str:
        ident = uuid.uuid4().hex
        with self.store.connect() as c:
            pending = c.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancelling')").fetchone()[0]
            if pending >= 50:
                raise ValueError('대기 작업은 최대 50개입니다. 일부 작업이 끝난 뒤 추가해 주세요.')
            c.execute('INSERT INTO jobs(id,kind,payload,status,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                      (ident, kind, json.dumps(payload, ensure_ascii=False), 'queued', now(), now()))
        self.notify()
        return ident

    def cancel(self, ident: str):
        with self.store.connect() as c:
            r = c.execute('SELECT status FROM jobs WHERE id=?', (ident,)).fetchone()
            if not r:
                raise ValueError('작업을 찾을 수 없습니다.')
            if r['status'] in TERMINAL:
                return
            state = 'cancelled' if r['status'] == 'queued' else 'cancelling'
            c.execute('UPDATE jobs SET status=?,stage=?,updated_at=? WHERE id=?', (state, '사용자 취소 요청', now(), ident))
        self.notify()
        with self.lock:
            if self.current == ident and self.proc:
                kill_tree(self.proc)

    def close(self):
        self.stop_event.set()
        self.events.close()
        with self.lock:
            if self.proc:
                kill_tree(self.proc)
        if self.thread:
            self.thread.join(timeout=8)

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                with self.store.connect() as c:
                    job = c.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at,rowid LIMIT 1").fetchone()
                    if job:
                        # cancel과 선택이 경합하지 않도록 같은 트랜잭션에서 전환합니다.
                        c.execute("UPDATE jobs SET status='running',stage='작업 시작',updated_at=? WHERE id=? AND status='queued'", (now(), job['id']))
                if job:
                    self.notify()
                    self._run(dict(job))
                else:
                    self.stop_event.wait(.4)
            except Exception as exc:
                print('[작업 관리 오류]', exc, flush=True)
                self.stop_event.wait(1)

    def _run(self, job: dict):
        folder = self.store.data_dir / 'jobs'
        folder.mkdir(exist_ok=True)
        request_path = folder / (job['id'] + '.request.json')
        result_path = folder / (job['id'] + '.result.json')
        log_path = folder / (job['id'] + '.log')
        payload = json.loads(job['payload'])
        atomic_text(request_path, json.dumps({'kind': job['kind'], 'payload': payload,
                     'data_dir': str(self.store.data_dir)}, ensure_ascii=False))
        proc, lines, last_write, prog, stage = None, [], 0.0, 0, '작업 시작'
        close_job = None
        try:
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'}
            with self.lock:
                with self.store.connect() as c:
                    state = c.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()[0]
                if state in {'cancelled', 'cancelling'} or self.stop_event.is_set():
                    self.store.job_update(job['id'], status='cancelled', stage='취소됨')
                    return
                self.current = job['id']
                self.proc = proc = subprocess.Popen([sys.executable, '-u', '-m', 'yme.worker', str(request_path), str(result_path)],
                     cwd=BASE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                     creationflags=flags, start_new_session=os.name != 'nt')
                close_job = windows_job(proc)
            with log_path.open('w', encoding='utf-8') as log:
                # bytes로 읽어 Windows cp949 자동 디코딩을 사용하지 않습니다.
                for raw in iter(proc.stdout.readline, b''):
                    line = raw.decode('utf-8', errors='replace').rstrip()
                    if line.startswith('@YME@'):
                        try:
                            event = json.loads(line[5:])
                            if event.get('event') == 'library_changed':
                                self.notify(library_changed=True)
                            if 'progress' in event:
                                prog = max(prog, min(100, float(event['progress'])))
                            if 'stage' in event:
                                stage = str(event['stage'])[:250]
                                lines.append(stage)
                        except (ValueError, TypeError):
                            lines.append(line)
                    else:
                        lines.append(line)
                    log.write(line + '\n')
                    log.flush()
                    lines = lines[-220:]
                    if time.monotonic() - last_write > 1.0:
                        self.store.job_update(job['id'], progress=prog, stage=stage, logs='\n'.join(lines)[-24000:])
                        self.notify()
                        last_write = time.monotonic()
            code = proc.wait()
            with self.store.connect() as c:
                state = c.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()[0]
            if self.stop_event.is_set():
                final, result, stage = 'interrupted', {}, '서버 종료로 중단됨 · 재시도 가능'
            elif state == 'cancelling':
                final, result, stage = 'cancelled', {}, '취소됨 · 이미 완료된 파일은 유지됩니다'
            else:
                saved = safe_json(result_path)
                final = saved.get('status', 'failed')
                result = saved.get('result', {'error': f'작업 프로세스 종료 코드: {code}'})
                stage = {'success': '완료', 'partial': '부분 완료 · 상세 로그 확인', 'failed': '실패 · 상세 로그 확인'}.get(final, '종료')
            self.store.job_update(job['id'], status=final, stage=stage, progress=100 if final in {'success','partial'} else prog,
                                  result=result, logs='\n'.join(lines)[-24000:])
        except Exception as exc:
            self.store.job_update(job['id'], status='failed', stage='작업 실행 오류', result={'error': str(exc)}, logs='\n'.join(lines)[-24000:])
        finally:
            self.notify(library_changed=True)
            if proc and proc.poll() is None:
                kill_tree(proc)
            if close_job:
                close_job()
            with self.lock:
                self.proc = None
                self.current = None

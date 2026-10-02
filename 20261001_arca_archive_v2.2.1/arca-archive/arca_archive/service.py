"""상주 서비스: 단일 워커 스레드 + 내장 스케줄러 + 브라우저 세션(로그인 창) 관리."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

from .common import AppError, iso_after, now_iso, parse_iso, utcnow
from .config import Settings
from .db import Database
from .pipeline.runner import run_channel

log = logging.getLogger("arca.service")


class CrawlerService:
    POLL_SECONDS = 15

    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self._queue: deque[tuple[int, str]] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._shutdown = threading.Event()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self.current: dict | None = None
        self.scheduler_enabled = settings.scheduler_enabled
        self._login_process: subprocess.Popen | None = None
        self._session_lock = threading.Lock()
        self.last_error: str | None = None
        self.stopping = False
        self.stop_requested_at: str | None = None
        for item in db.queued_runs():
            channel = db.get_channel(item["channel_id"])
            if channel and (channel["enabled"] or item["trigger"] not in ("schedule", "backlog")):
                self._queue.append((item["channel_id"], item["trigger"]))
            else:
                db.clear_queued_runs(item["channel_id"])

    # ------------------------------------------------------------------ 수명
    def start(self) -> None:
        from .common import RunLock

        probe = RunLock(self.settings.lock_path)
        if probe.acquire():
            # 다른 프로세스가 수집 중이 아닐 때만 남아 있는 running 기록을 정리합니다.
            try:
                interrupted = self.db.active_runs()
                aborted = self.db.abort_stale_runs("서버가 재시작되어 실행이 중단되었습니다.")
                for run in interrupted:
                    channel = self.db.get_channel(run["channel_id"]) if run.get("channel_id") else None
                    if channel and channel["enabled"]:
                        self.request_run(channel["id"], "backlog" if run["trigger"] in ("backlog", "backlog_manual") else "resume")
            finally:
                probe.release()
            if aborted:
                log.warning("이전 실행 %d개를 중단 상태로 정리했습니다.", aborted)
        else:
            log.warning("다른 프로세스(CLI 등)가 수집 중입니다. 기존 실행 기록은 그대로 둡니다.")
        for channel in self.db.list_channels():
            if channel["enabled"] and not channel.get("next_run_at"):
                self.db.update_channel(channel["id"], next_run_at=iso_after(60))
        self._worker = threading.Thread(target=self._loop, name="crawler-worker", daemon=True)
        self._worker.start()

    def shutdown(self, timeout: float = 10) -> None:
        self._shutdown.set()
        self._stop_event.set()
        self._wake.set()
        if self._worker:
            self._worker.join(timeout)

    # ------------------------------------------------------------------ 요청
    def request_run(self, channel_id: int, trigger: str = "manual") -> dict:
        channel = self.db.get_channel(channel_id)
        if channel is None:
            return {"ok": False, "reason": "채널이 없습니다."}
        if self.login_window_open():
            return {"ok": False, "reason": "로그인 창이 열려 있습니다. 창을 닫은 뒤 실행하세요."}
        with self._lock:
            if self.current and self.current["channel_id"] == channel_id:
                return {"ok": False, "reason": "이미 실행 중입니다."}
            if any(cid == channel_id for cid, _ in self._queue):
                return {"ok": False, "reason": "이미 대기열에 있습니다."}
            self._queue.append((channel_id, trigger))
            self.db.queue_run(channel_id, trigger)
        self._wake.set()
        return {"ok": True, "queued": len(self._queue)}

    def drop_scheduled(self, channel_id: int) -> int:
        """일시중지된 채널의 예약(schedule) 대기 항목을 대기열에서 뺍니다. 수동 요청은 남겨 둡니다."""
        with self._lock:
            before = len(self._queue)
            kept = [(cid, trig) for cid, trig in self._queue if not (cid == channel_id and trig in ("schedule", "backlog"))]
            self._queue.clear()
            self._queue.extend(kept)
            self.db.clear_queued_runs(channel_id, scheduled_only=True)
            return before - len(kept)

    def stop_current(self) -> dict:
        with self._lock:
            cleared = len(self._queue)
            self._queue.clear()
            self.db.clear_queued_runs()
            if not self.current:
                return {"ok": True, "message": f"실행 중인 작업이 없습니다. 대기열 {cleared}개를 비웠습니다."}
            if self.stopping:
                return {"ok": True, "message": "이미 중지 중입니다. 진행 중인 요청이 끝나면 멈춥니다."}
            self.stopping = True
            self.stop_requested_at = now_iso()
            self._stop_event.set()
        self._wake.set()
        return {"ok": True, "message": "중지를 요청했습니다. 진행 중인 요청이 끝나는 즉시(보통 수 초 안에) 멈춥니다."}

    def status(self) -> dict:
        with self._lock:
            current = dict(self.current) if self.current else None
            queue = list(self._queue)
        run = self.db.get_run(current["run_id"]) if current and current.get("run_id") else None
        elapsed = None
        if current:
            started = parse_iso(current.get("started_at"))
            if started:
                elapsed = int((utcnow() - started).total_seconds())
        channels = {c["id"]: c for c in self.db.list_channels()}
        next_due = None
        if self.scheduler_enabled:
            due = sorted(self._scheduled_jobs())
            if due:
                next_due = {"at": due[0][0].isoformat(), "channel_slug": channels[due[0][1]]["slug"], "trigger": due[0][2]}
        return {
            "running": current is not None,
            "stopping": self.stopping and current is not None,
            "stop_requested_at": self.stop_requested_at if self.stopping else None,
            "current": current,
            "run": run,
            "elapsed_seconds": elapsed,
            "queue": [{"channel_id": cid, "channel_slug": channels.get(cid, {}).get("slug", str(cid)), "trigger": trig}
                      for cid, trig in queue],
            "scheduler_enabled": self.scheduler_enabled,
            "next_due": next_due,
            "login_window_open": self.login_window_open(),
            "session": self.session_status(),
            "sessions": self.all_session_status(),
            "last_error": self.last_error,
            "last_run": self._last_run_summary(),
            "original_endpoint": self._original_endpoint_status(),
            "time": now_iso(),
        }

    def _original_endpoint_status(self) -> dict:
        from .pipeline.media import original_backoff_status

        try:
            return original_backoff_status(self.db)
        except Exception:  # pragma: no cover
            return {"active": False, "until": None, "level": 0}

    def _last_run_summary(self) -> dict | None:
        runs = self.db.list_runs(1)
        if not runs or runs[0]["status"] == "running":
            return None
        r = runs[0]
        return {"id": r["id"], "channel_slug": r["channel_slug"], "status": r["status"], "code": r["code"],
                "finished_at": r["finished_at"], "collected": r["stats"].get("collected", 0),
                "media_downloaded": r["stats"].get("media_downloaded", 0), "failed": r["stats"].get("failed", 0)}

    # ------------------------------------------------------------------ 워커
    def _loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                item = None
                with self._lock:
                    if self._queue:
                        item = self._queue.popleft()
                if item is None and self.scheduler_enabled and not self.login_window_open():
                    item = self._next_due()
                if item is None:
                    self._wake.wait(self.POLL_SECONDS)
                    self._wake.clear()
                    continue
                channel_id, trigger = item
                self._execute(channel_id, trigger)
            except Exception as exc:  # pragma: no cover - 워커가 죽지 않도록 마지막 방어선
                self.last_error = f"WORKER_ERROR: {type(exc).__name__}"
                log.exception("워커 루프 오류. 30초 후 계속합니다.")
                with self._lock:
                    self.current = None
                self._shutdown.wait(30)

    def _next_due(self) -> tuple[int, str] | None:
        now = utcnow()
        due = [item for item in self._scheduled_jobs() if item[0] <= now]
        return (min(due)[1], min(due)[2]) if due else None

    def _scheduled_jobs(self) -> list:
        due_channels = []
        media_work = self.db.media_work_channels()
        for channel in self.db.list_channels():
            if not channel["enabled"]:
                continue
            due = parse_iso(channel.get("next_run_at"))
            if due:
                due_channels.append((due, channel["id"], "schedule"))
            if channel["id"] in media_work:
                media_due = parse_iso(channel.get("next_media_at") or media_work[channel["id"]])
                if media_due:
                    due_channels.append((media_due, channel["id"], "backlog"))
        # 긴 실행 뒤에는 ID가 작은 채널도 다시 기한이 지납니다. 가장 오래
        # 기다린 채널부터 골라 뒤쪽 채널이 계속 밀리지 않게 합니다.
        return due_channels

    def _execute(self, channel_id: int, trigger: str) -> None:
        channel = self.db.get_channel(channel_id)
        if channel is None:
            return
        self.db.queue_run(channel_id, trigger)
        next_field = "next_media_at" if trigger in ("backlog", "backlog_manual") else "next_run_at"
        self._stop_event = threading.Event()
        with self._lock:
            self.current = {"channel_id": channel_id, "channel_slug": channel["slug"], "trigger": trigger,
                            "started_at": now_iso(), "run_id": None}
            self.last_error = None
        try:
            def on_created(run_id: int) -> None:
                with self._lock:
                    if self.current:
                        self.current["run_id"] = run_id

            run = run_channel(self.settings, self.db, channel_id, trigger, self._stop_event, on_run_created=on_created)
            if run["status"] in ("success", "partial", "cancelled"):
                self.last_error = None
            else:
                self.last_error = f"{run['code']}: {run['message']}"
            self._update_session_from_run(run)
        except AppError as exc:
            self.last_error = str(exc)
            if exc.code == "ALREADY_RUNNING":
                log.warning("다른 프로세스가 수집 중이라 %s 실행을 5분 뒤로 미룹니다.", channel["slug"])
                self.db.update_channel(channel_id, **{next_field: iso_after(300)})
            else:
                log.error("실행 실패: %s", exc)
                self.db.update_channel(channel_id, last_error_code=exc.code,
                                       **{next_field: iso_after(int(channel.get("interval_minutes") or 60) * 60)})
        except Exception as exc:  # pragma: no cover
            self.last_error = f"INTERNAL_ERROR: {exc}"
            log.exception("실행 중 예상하지 못한 오류")
            self.db.update_channel(channel_id, last_error_code="INTERNAL_ERROR",
                                   **{next_field: iso_after(int(channel.get("interval_minutes") or 60) * 60)})
        finally:
            if not self._shutdown.is_set():
                self.db.clear_queued_runs(channel_id)
            with self._lock:
                self.current = None
                self.stopping = False
                self.stop_requested_at = None

    def _update_session_from_run(self, run: dict) -> None:
        """실행 결과로 로그인 상태를 추정해 헤더 표시를 갱신합니다."""
        stats = run.get("stats") or {}
        if run.get("code") == "LOGIN_REQUIRED":
            payload = {"checked_at": now_iso(), "logged_in": False,
                       "note": f"실행 #{run['id']}에서 로그인이 필요하다고 확인됨. 로그인 브라우저를 열어 로그인하세요."}
        elif stats.get("logged_in") is not None:
            channel = self.db.get_channel(run["channel_id"]) if run.get("channel_id") else None
            previous = self.session_status((channel or {}).get("site") or "arca")
            payload = {"checked_at": now_iso(), "logged_in": bool(stats["logged_in"]),
                       "nickname": previous.get("nickname") if stats["logged_in"] else None,
                       "note": f"실행 #{run['id']}의 목록 페이지에서 " + ("로그인 상태" if stats["logged_in"] else "비로그인 상태") + "로 관측됨."}
        else:
            return
        channel = self.db.get_channel(run["channel_id"]) if run.get("channel_id") else None
        self.db.set_setting(self._session_key((channel or {}).get("site") or "arca"), json.dumps(payload, ensure_ascii=False))

    # ------------------------------------------------------------------ 브라우저 세션
    def login_window_open(self) -> bool:
        proc = self._login_process
        if proc is not None:
            if proc.poll() is None:
                return True
            self._login_process = None
        # login.bat 등 다른 경로로 연 창도 잠금 파일로 감지합니다.
        from .fetch.browser_fetcher import login_window_locked

        try:
            return login_window_locked(self.settings)
        except OSError:
            return False

    def open_login_window(self, start_url: str = "https://arca.live/") -> dict:
        if self.current:
            return {"ok": False, "reason": "수집이 실행 중입니다. 끝난 뒤 로그인 창을 여세요."}
        if self.login_window_open():
            return {"ok": True, "message": "로그인 창이 이미 열려 있습니다."}
        # --config 는 전역 옵션이라 하위 명령(login) 앞에 와야 합니다.
        command = [sys.executable, "-m", "arca_archive", "--config", str(self.settings.config_path),
                   "login", "--url", start_url]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.settings.log_path.mkdir(parents=True, exist_ok=True)
        log_file = (self.settings.log_path / "login_window.log").open("ab")
        try:
            self._login_process = subprocess.Popen(command, cwd=str(self.settings.root), creationflags=creationflags,
                                                   stdout=log_file, stderr=subprocess.STDOUT)
        except OSError as exc:
            log_file.close()
            return {"ok": False, "reason": f"로그인 창을 열지 못했습니다: {type(exc).__name__}"}
        finally:
            try:
                log_file.close()  # 자식 프로세스가 핸들을 상속했으므로 부모 쪽은 닫아도 됩니다.
            except OSError:
                pass
        # 곧바로 죽으면(인자 오류, 브라우저 시작 실패 등) 사용자에게 바로 알립니다.
        time.sleep(1.5)
        if self._login_process.poll() is not None:
            code = self._login_process.returncode
            self._login_process = None
            tail = self._read_login_log_tail()
            return {"ok": False, "reason": f"로그인 창 프로세스가 바로 종료되었습니다(코드 {code}). {tail}".strip()}
        self.db.set_setting("session_status", json.dumps({"checked_at": now_iso(), "logged_in": None,
                                                          "note": "로그인 창을 열었습니다. 로그인 후 창을 닫고 '세션 확인'을 누르세요."}, ensure_ascii=False))
        return {"ok": True, "message": "로그인 창을 열었습니다. 브라우저에서 직접 로그인한 뒤 창을 닫으세요."}

    def _read_login_log_tail(self) -> str:
        try:
            text = (self.settings.log_path / "login_window.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return " / ".join(lines[-2:])[:300] if lines else ""

    def check_session(self, site: str = "arca") -> dict:
        if self.current:
            return {"ok": False, "reason": "수집이 실행 중입니다. 끝난 뒤 확인하세요."}
        if self.login_window_open():
            return {"ok": False, "reason": "로그인 창이 열려 있습니다. 먼저 닫으세요."}
        if not self._session_lock.acquire(blocking=False):
            return {"ok": False, "reason": "세션 확인이 이미 진행 중입니다."}
        try:
            from .fetch.browser_fetcher import check_session

            result = check_session(self.settings, site)
            payload = {"checked_at": now_iso(), **result}
        except AppError as exc:
            payload = {"checked_at": now_iso(), "logged_in": None, "error": str(exc)}
        finally:
            self._session_lock.release()
        self.db.set_setting(self._session_key(site), json.dumps(payload, ensure_ascii=False))
        return {"ok": True, **payload}

    @staticmethod
    def _session_key(site: str) -> str:
        return "session_status" if site in (None, "", "arca") else f"session_status:{site}"

    def session_status(self, site: str = "arca") -> dict:
        raw = self.db.get_setting(self._session_key(site))
        if not raw:
            return {"logged_in": None, "checked_at": None}
        try:
            return json.loads(raw)
        except ValueError:
            return {"logged_in": None, "checked_at": None}

    def all_session_status(self) -> dict:
        from .sites import all_sites

        return {s.key: self.session_status(s.key) for s in all_sites()}

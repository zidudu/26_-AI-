"""V8 명령행. 예약 작업은 run을 호출하므로 입력 대기가 없습니다."""
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v8.configuration import (DEFAULT, KST, V8Error, addresses, load, parse_time,
                              read_json, require_ready, validate, write_json)
from v8.schedule import cutoff, previous_cutoff
from v8.pipeline import Runner, RUN_PATTERN


def ask(label, default=""):
    value = input(f"{label} (Enter: {default or '없음'}, 취소: /q): ").strip()
    if value.lower() == "/q":
        raise V8Error("CANCELLED", "취소했습니다.")
    return value or default


def yesno(label, default):
    value = ask(label + " [y/n]", "y" if default else "n").lower()
    if value not in ("y", "n"):
        raise V8Error("INVALID_INPUT", "y 또는 n을 입력하세요.")
    return value == "y"


def setup(root):
    path = root / "config_v8.json"
    cfg = load(root) if path.exists() else deepcopy(DEFAULT)
    cfg["schedule"]["time"] = ask("예약 시각 HH:MM / 한국시간", cfg["schedule"]["time"])
    cfg["schedule"]["weekdays_only"] = yesno("평일만 실행", cfg["schedule"]["weekdays_only"])
    validate(cfg)
    boundary = cutoff(datetime.now(KST), cfg["schedule"])
    default_start = previous_cutoff(boundary, cfg["schedule"]).strftime("%Y-%m-%d %H:%M")
    cfg["schedule"]["initial_start"] = ask("최초 수집 시작 YYYY-MM-DD HH:MM", cfg["schedule"]["initial_start"] or default_start)
    if parse_time(cfg["schedule"]["initial_start"]) > datetime.now(KST):
        raise V8Error("FUTURE_START", "최초 수집 시작은 현재 시각 이후로 지정할 수 없습니다.")
    cfg["mail"]["to"] = addresses(ask("받는 사람 / 여러 명은 세미콜론", ";".join(cfg["mail"]["to"])))
    cc = input(f"참조 (Enter: 기존 유지, /none: 비우기, 현재: {';'.join(cfg['mail']['cc']) or '없음'}): ").strip()
    if cc == "/q":
        raise V8Error("CANCELLED", "취소했습니다.")
    if cc:
        cfg["mail"]["cc"] = [] if cc == "/none" else addresses(cc)
    cfg["mail"]["send_partial"] = yesno("재열기 검증을 통과한 부분 완료 PPT도 상태를 표시해 발송", cfg["mail"]["send_partial"])
    cfg = validate(cfg)
    require_ready(cfg)
    if path.exists():
        backup = path.with_name("config_v8_before_" + datetime.now(KST).strftime("%Y%m%d_%H%M%S_%f") + ".json")
        backup.write_bytes(path.read_bytes())
    write_json(path, cfg)
    print(f"[설정 저장] {path}\n[다음] 11_check_v8.bat → 12_run_v8.bat")
    print("설정 저장만 완료했습니다. 예약 등록은 13_register_schedule_v8.bat에서 합니다.")


def choose_run(root):
    rows = []
    for path in (root / "output_v8" / "runs").glob("*/run.json"):
        if RUN_PATTERN.fullmatch(path.parent.name):
            record = read_json(path)
            rows.append(record)
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    if not rows:
        raise V8Error("NO_RUNS", "V8 실행 기록이 없습니다.")
    for i, row in enumerate(rows[:30], 1):
        print(f"{i:2}. {row['id']} / {row['status']} / 메일 {row['mail']['status']}")
    answer = ask("번호 또는 실행 번호", "1")
    if answer.isdigit() and 1 <= int(answer) <= min(len(rows), 30):
        return rows[int(answer) - 1]["id"]
    return answer


def task_command(root, action):
    if os.name != "nt":
        raise V8Error("WINDOWS_REQUIRED", "예약 등록·해제는 Windows 회사 PC에서 실행하세요.")
    cfg = load(root)
    require_ready(cfg)
    completed = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                str(root / "v8" / "schedule.ps1"), "-ProjectRoot", str(root), "-Action", action])
    return completed.returncode


def status(root):
    state_path = root / "output_v8" / "state.json"
    if state_path.exists():
        state = read_json(state_path)
        print("[예약 처리 완료 시각]", state.get("cursor") or "없음")
        print("[진행할 실행]", state.get("active") or "없음")
    paths = list((root / "output_v8" / "runs").glob("*/run.json"))
    if not paths:
        print("[실행 기록] 없음")
    for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)[:15]:
        r = read_json(path)
        print(f"{r['id']} / {r['status']} / 메일 {r['mail']['status']}")
        if r.get("artifact"):
            report = read_json(path.parent / r["artifact"]["summary"])
            if report.get("pptx"):
                print("  PPT:", report["pptx"])
        if r.get("error"):
            print("  최근 오류:", r["error"]["code"], "/", r["error"]["message"])


def _main(argv=None, notice=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="V8 예약 수집·분석·PPT·Outlook 통합 실행")
    parser.add_argument("command", choices=("setup", "check", "run", "retry", "preview", "status", "register", "unregister", "resolve-mail"))
    parser.add_argument("--no-result-window", action="store_true")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--run", dest="identifier")
    parser.add_argument("--prompt", action="store_true")
    parser.add_argument("--decision", choices=("sent", "not-sent"))
    args = parser.parse_args(argv)
    print(f"[V8.2.0] {args.command}", flush=True)
    try:
        if args.command == "status":
            status(ROOT)
            return 0
        if args.command in ("register", "unregister"):
            return task_command(ROOT, args.command)
        from v754.collect.run_lock import RunLock
        with ExitStack() as locks:
            locks.enter_context(RunLock(ROOT / "output_v8" / "v8.lock"))
            if args.command == "setup":
                setup(ROOT)
                return 0
            cfg = load(ROOT)
            require_ready(cfg)
            from v8.backend import Backend
            backend = Backend(ROOT)
            locks.enter_context(RunLock(backend.legacy_out / "v754_export.lock"))
            if args.command == "check":
                print("[기존 환경]", backend.check())
                if os.name != "nt":
                    raise V8Error("WINDOWS_REQUIRED", "실제 실행에는 Windows·Classic Outlook·PowerPoint가 필요합니다.")
                ps = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                                     "if (Test-Path 'Registry::HKEY_CLASSES_ROOT\\Outlook.Application\\CLSID') { exit 0 } else { exit 1 }"],
                                    timeout=30, capture_output=True)
                if ps.returncode:
                    raise V8Error("OUTLOOK_NOT_REGISTERED", "Classic Outlook COM 등록을 확인하지 못했습니다.")
                print(f"[수신자] {'; '.join(cfg['mail']['to'])}")
                print("[확인 완료] 설정·모듈·Office 등록 확인. 웹·AI·메일 호출 없음.")
                return 0
            from v8.vendor.outlook_mail import send_outlook_mail
            runner = Runner(ROOT, cfg, backend, send_outlook_mail)
            if args.prompt:
                args.identifier = choose_run(ROOT)
            if args.command == "resolve-mail":
                if not args.identifier:
                    raise V8Error("RUN_REQUIRED", "실행 번호를 지정하세요.")
                if not args.decision:
                    print("Outlook 보낸편지함·보낼편지함에서 해당 실행 번호를 먼저 확인하세요.")
                    value = ask("1: 이미 발송/발송 대기 중, 2: 미발송을 확인함", "")
                    if value not in ("1", "2"):
                        raise V8Error("INVALID_INPUT", "1 또는 2를 입력하세요.")
                    args.decision = "sent" if value == "1" else "not-sent"
                runner.resolve_mail(args.identifier, args.decision)
                return 0
            code = runner.execute(start=args.start, end=args.end, identifier=args.identifier,
                                  preview=args.command == "preview", retry=args.command == "retry")
            if notice is not None:
                from v8.result_notice import capture_runner_result
                capture_runner_result(runner, notice)
            return code
    except (KeyboardInterrupt, EOFError):
        if notice is not None: notice.update(status="cancelled")
        print("[취소] 저장한 결과는 보존됩니다.")
        return 130
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        if notice is not None:
            notice.update(status="cancelled" if code == "CANCELLED" else "failed",
                          error={"code": code, "message": str(exc) if hasattr(exc, 'code') else '설치·실행 환경을 확인하세요.'})
        if code == "ALREADY_RUNNING":
            if notice is not None: notice.update(status="skipped_busy", error=None)
            print("[실행 중] V8 또는 기존 수집/PPT 작업이 실행 중입니다. 중복 실행을 건너뜁니다.")
            return 0
        print(f"[중단: {code}] {str(exc) if hasattr(exc, 'code') else '설치·실행 환경을 확인하세요.'}")
        return 130 if code == "CANCELLED" else 1


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    values = list(sys.argv[1:] if argv is None else argv)
    # Existing scheduler invokes exactly "run"; no task replacement is needed.
    # Manual BAT already pauses, so it opts out of the additional viewer.
    if not values or values[0] != 'run' or '--no-result-window' in values:
        return _main(values)
    from contextlib import redirect_stdout, redirect_stderr
    from v8.pipeline import Tee
    from v8.result_notice import new_notice, finish_notice
    try:
        path, notice = new_notice(ROOT)
    except OSError:
        print('[결과 기록 준비 실패] 실행 폴더의 쓰기 권한과 디스크 공간을 확인하세요.')
        return 1
    code = 1
    with path.with_suffix('.log').open('w', encoding='utf-8') as log:
        with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
            try:
                code = _main(values, notice)
            finally:
                # _main's ExitStack has already released both execution locks.
                try:
                    finish_notice(path, notice, code)
                except Exception as exc:
                    print('[결과 창 오류]', type(exc).__name__, '/ 실행 로그:', path.with_suffix('.log'))
    return code


if __name__ == "__main__":
    raise SystemExit(main())

"""같은 기간도 새로 수집합니다. 예약 설정·상태·완료 기록을 변경하지 않습니다.

예약 설정 파일에서는 메일 설정과 최초 기간 제안만 읽습니다.
과거 수집 목록은 중복 안내용으로만 읽고 수집/분석/PPT는 V8.2 Backend를 사용합니다.
"""
from contextlib import ExitStack, contextmanager, redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import argparse
import os
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from v8.configuration import KST, V8Error, addresses, load, read_json, write_json
from v8.pipeline import Tee, classify, email_payload, stamp
from v8.schedule import explicit_window, scope
from v8.history import scan_history, announce_history, compare_collected


@contextmanager
def shared_locks(root, backend):
    """예약/기존 V754와 같은 OS 잠금을 사용합니다. 입력 대기 중에는 잡지 않습니다."""
    from v754.collect.run_lock import RunLock
    with ExitStack() as stack:
        stack.enter_context(RunLock(root / "output_v8" / "v8.lock"))
        stack.enter_context(RunLock(backend.legacy_out / "v754_export.lock"))
        yield stack


def run_test(root, cfg, backend, start, end, *, send_mail=False, sender=None, now=None):
    """매번 새 폴더에서 실행합니다. 실패해도 예약 cursor/active는 변경하지 않습니다."""
    root = Path(root).resolve()
    lo, hi = explicit_window(start, end, now or datetime.now(KST))
    if send_mail and (not cfg["mail"]["to"] or sender is None):
        raise V8Error("RECIPIENT_REQUIRED", "메일 발송에는 수신자 설정과 Outlook 모듈이 필요합니다.")
    with shared_locks(root, backend):
        history, prior_keys = scan_history(root, backend.cafe_id, lo, hi, backend.legacy_out)
        identifier = "test_" + datetime.now(KST).strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:8]
        folder = root / "output_v81_period" / identifier
        folder.mkdir(parents=True, exist_ok=False)
        record = {
            "version": "8.2.0", "revision": 1, "id": identifier, "mode": "manual_period",
            "scope": scope(backend.cafe_id, backend.words),
            "start": lo.isoformat(), "end": hi.isoformat(), "created_at": stamp(),
            "status": "running", "stage": "PREPARE", "send_mail_requested": send_mail,
            "mail": {"status": "not_started" if send_mail else "disabled"},
        }

        def save():
            record["updated_at"] = stamp()
            write_json(folder / "test.json", record)

        with (folder / "test.log").open("w", encoding="utf-8") as log:
            with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
                save()
                print(f"[V8.2 기간 지정 실행] {identifier}")
                print(f"[기간] {lo.isoformat()} 이상 ~ {hi.isoformat()} 미만")
                print(f"[결과 폴더] {folder}")
                announce_history(history)
                write_json(folder / "history_overlap.json", {**history, "status": "before_collection"})
                if send_mail:
                    print(f"[이번 실행 수신자] {'; '.join(cfg['mail']['to'])}")
                    print(f"[이번 실행 참조] {'; '.join(cfg['mail']['cc']) or '없음'}")
                print("동일 기간의 기존 결과가 있어도 새로 수집·분석·PPT를 생성합니다.")
                print("기간 지정 실행마다 새 분석에 API 사용량이 발생합니다. 예약의 처리 완료 시각은 바꾸지 않습니다.")
                code = 1
                try:
                    backend.check()
                    collection, export = folder / "collection", folder / "export"
                    collection.mkdir()
                    record["stage"] = "COLLECT"
                    save()
                    backend.collect(collection, lo, hi)
                    if not backend.collection_ok(collection, lo, hi):
                        raise V8Error("COLLECTION_MISMATCH", "지정 기간과 수집 결과가 다릅니다.")
                    # 중복 진단 파일의 문제로 이미 끝난 수집을 취소하지 않습니다.
                    try:
                        compared = compare_collected(collection, prior_keys, history, folder / "history_overlap.json")
                        record["history_overlap"] = {"file": "history_overlap.json",
                            "previously_collected_articles": compared["previously_collected_articles"]}
                    except (OSError, ValueError, KeyError, TypeError, V8Error) as exc:
                        record["history_overlap"] = {"status": "unavailable", "error_type": type(exc).__name__}
                        print("[중복 확인 제한] 실제 게시글 중복 수를 확인하지 못했습니다. 이번 수집·출력은 계속합니다.")
                    export.mkdir()
                    record["stage"] = "EXPORT"
                    save()
                    _, export_code = backend.export(collection, export)
                    if export_code not in (0, 2):
                        raise V8Error("EXPORT_FAILED", f"출력이 완료되지 않았습니다: {export_code}")
                    report = read_json(export / "summary.json")
                    outcome, ppt = classify(report, export, record["scope"], lo, hi)
                    record.update(outcome=outcome, summary="export/summary.json",
                                  selected_articles=report.get("selected_articles", 0),
                                  pptx=str(ppt) if ppt else None)
                    save()
                    code = 2 if outcome == "partial" else 0
                    if outcome == "completed_empty":
                        record["mail"] = {"status": "skipped_empty"}
                        print("[정상 완료] 수집 대상 0건. AI 분석·PPT 생성·메일 발송을 생략했습니다.")
                    elif not send_mail:
                        print("[발송 안 함] 기간 지정 PPT를 저장했습니다. 메일 발송을 선택하지 않았습니다.")
                    elif outcome == "partial" and not cfg["mail"]["send_partial"]:
                        record["mail"] = {"status": "held_partial"}
                        print("[발송 보류] 부분 완료 발송 설정이 n입니다. 기간 지정 PPT는 저장했습니다.")
                    else:
                        record["stage"] = "MAIL"
                        mail_cfg = deepcopy(cfg)
                        mail_cfg["mail"]["subject_prefix"] = "[기간 지정] " + cfg["mail"]["subject_prefix"]
                        payload = email_payload(mail_cfg, record, report, ppt, outcome)
                        record["mail"] = {"status": "sending", "started_at": stamp(), "payload": payload}
                        save()
                        try:
                            result = sender(**payload, display_only=False)
                            if result.get("ok") is not True or result.get("action") != "sent":
                                raise V8Error("MAIL_RESULT_UNEXPECTED", "Outlook 발송 요청 결과가 불명확합니다.")
                            record["mail"].update(status="sent", finished_at=stamp(), result=result)
                            # 발송 직후 기록 저장 실패도 불명확 상태로 처리하여 성공으로 표시하지 않습니다.
                            save()
                        except BaseException as exc:
                            record.update(status="mail_unknown")
                            record["mail"].update(status="unknown", error_type=type(exc).__name__)
                            save()
                            print("[발송 확인 필요] Outlook 보낸편지함·보낼편지함을 확인하세요. 자동 재발송하지 않습니다.")
                            return 3, folder
                        print(f"[Outlook 발송 요청 완료] {payload['subject']}")
                    record.update(status=outcome, stage="COMPLETE")
                    save()
                except BaseException as exc:
                    record.update(status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                                  error={"code": getattr(exc, "code", type(exc).__name__),
                                         "message": str(exc) if hasattr(exc, "code") else "실행 환경 또는 결과 기록을 확인하세요."})
                    save()
                    print(f"[중단] {record['stage']} / {record['error']['code']} / {record['error']['message']}")
                    code = 130 if isinstance(exc, KeyboardInterrupt) else 1
                finally:
                    print(f"[기간 지정 상태] {record['status']} / 메일 {record['mail']['status']}")
                    if record.get("pptx"):
                        print(f"[PPT] {record['pptx']}")
                    print(f"[기록] {folder / 'test.json'}")
                    print(f"[로그] {folder / 'test.log'}")
                    print("예약 설정·처리 완료 기록은 유지했습니다.")
                return code, folder


def ask(label, default):
    value = input(f"{label} (Enter: {default}, 취소: /q): ").strip()
    if value.lower() == "/q":
        raise V8Error("CANCELLED", "취소했습니다.")
    return value or default


def prompt_test_mail(cfg, *, send_mail=False):
    """이번 실행의 메모리 복사본만 바꿉니다. 예약 설정 파일은 저장하지 않습니다."""
    test_cfg = deepcopy(cfg)
    print(f"[기존 메일 수신자] {'; '.join(cfg['mail']['to']) or '없음'}")
    print(f"[기존 참조] {'; '.join(cfg['mail']['cc']) or '없음'}")
    if not send_mail:
        value = ask("이번 결과 메일도 실제 발송 [y/n]", "n").lower()
        if value not in ("y", "n"):
            raise V8Error("INVALID_INPUT", "메일 발송은 y 또는 n을 입력하세요.")
        send_mail = value == "y"
    if not send_mail:
        return test_cfg, False

    print("받는 사람·참조 변경은 이번 실행에만 적용됩니다. 여러 명은 세미콜론(;)으로 구분하세요.")
    to_value = ask("이번 실행 받는 사람", "; ".join(cfg["mail"]["to"]))
    recipients = addresses(to_value)
    if not recipients or any(x.startswith("/") for x in recipients):
        raise V8Error("RECIPIENT_REQUIRED", "메일을 발송하려면 받는 사람을 입력하세요.")
    cc_value = ask("이번 실행 참조 /none: 비우기", "; ".join(cfg["mail"]["cc"]))
    cc = [] if cc_value.lower() == "/none" else addresses(cc_value)
    if any(x.startswith("/") for x in cc):
        raise V8Error("INVALID_RECIPIENT", "참조 주소를 입력하거나 /none으로 비우세요.")
    test_cfg["mail"].update(to=recipients, cc=cc)
    return test_cfg, True


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="같은 기간도 다시 수집하는 V8.2 기간 지정 실행")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--send-mail", action="store_true", help="설정된 수신자에게 기간 지정 PPT를 실제 발송")
    args = parser.parse_args(argv)
    print("[V8.2.0] 기간 지정 실행 / 예약 실행과 별도")
    try:
        if os.name != "nt":
            raise V8Error("WINDOWS_REQUIRED", "Windows 회사 PC에서 실행하세요.")
        cfg = load(ROOT)
        if bool(args.start) != bool(args.end):
            raise V8Error("PERIOD_REQUIRED", "--start와 --end를 함께 지정하세요.")
        send_mail = args.send_mail
        if args.start:
            start, end = args.start, args.end
        else:
            current = datetime.now(KST)
            print("시작은 포함, 종료는 미포함입니다. 날짜·시각은 한국시간입니다.")
            print("이번 입력은 이번 실행에만 적용됩니다. 이미 수집한 기간도 다시 수집합니다.")
            print("새 분석에 API 사용량이 발생합니다. 짧은 기간부터 시험할 수 있습니다.")
            start = ask("수집 시작 YYYY-MM-DD HH:MM", cfg["schedule"]["initial_start"] or
                        (current - timedelta(days=1)).strftime("%Y-%m-%d %H:%M"))
            end = ask("수집 종료 YYYY-MM-DD HH:MM", current.strftime("%Y-%m-%d %H:%M"))
            explicit_window(start, end, datetime.now(KST))
            cfg, send_mail = prompt_test_mail(cfg, send_mail=send_mail)
        # 날짜 검증은 Backend 초기화보다 먼저 실행합니다.
        explicit_window(start, end, datetime.now(KST))
        from v8.backend import Backend
        from v8.vendor.outlook_mail import send_outlook_mail
        code, _ = run_test(ROOT, cfg, Backend(ROOT), start, end,
                           send_mail=send_mail, sender=send_outlook_mail)
        return code
    except (KeyboardInterrupt, EOFError):
        print("[취소] 기간 지정 실행을 종료했습니다.")
        return 130
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        if code == "ALREADY_RUNNING":
            print("[실행 안 함] 예약 또는 다른 수집/PPT 작업이 진행 중입니다. 종료 후 기간 지정 실행을 다시 실행하세요.")
            return 4
        print(f"[중단: {code}] {str(exc) if hasattr(exc, 'code') else '설치·실행 환경을 확인하세요.'}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""한 구간을 수집→출력→발송합니다. 실제 외부 호출은 주입한 backend/sender가 담당합니다."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime
import hashlib
from pathlib import Path
import re
import sys
import uuid

from .configuration import KST, V8Error, read_json, write_json, require_ready
from .schedule import scope, run_id, scheduled_window, explicit_window

TERMINAL = {"completed", "partial", "completed_empty"}
RUN_PATTERN = re.compile(r"run_\d{8}_\d{4}_\d{8}_\d{4}_[a-f0-9]{12}")


def stamp():
    return datetime.now(KST).isoformat()


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(f.read()).hexdigest()


class Tee:
    def __init__(self, console, log):
        self.console, self.log = console, log

    def write(self, text):
        self.console.write(text)
        self.log.write(text)
        self.flush()
        return len(text)

    def flush(self):
        self.console.flush()
        self.log.flush()


def safe_path(folder, relative):
    p = (folder / relative).resolve()
    if not p.is_relative_to(folder.resolve()):
        raise V8Error("BAD_RECORD_PATH", "실행 기록의 파일 경로가 실행 폴더 밖입니다.")
    return p


def classify(report, folder, identity, start, end):
    """코드 2/파일 존재만으로 판정하지 않고 기간·수집 완료·PPT 재열기를 확인합니다."""
    if (report.get("cafe_id") != identity["cafe_id"]
            or set(report.get("keywords", [])) != set(identity["keywords"])
            or report.get("window", {}).get("start") != start.isoformat()
            or report.get("window", {}).get("end") != end.isoformat()):
        raise V8Error("RESULT_MISMATCH", "이번 카페·검색어·기간과 다른 결과입니다.")
    if not all(report.get(k) is True for k in ("collection_complete", "range_search_complete", "articles_verified_complete")):
        raise V8Error("COLLECTION_INCOMPLETE", "수집 완료를 확인할 수 없어 발송하지 않습니다.")
    status = report.get("status")
    if status == "completed_empty" and report.get("selected_articles") == 0 and not report.get("items"):
        return "completed_empty", None
    if status not in ("completed", "partial") or report.get("reopened_structure_verified") is not True:
        raise V8Error("PPT_UNVERIFIED", "PPT 생성·재열기 검증이 끝나지 않았습니다.")
    ppt = Path(report.get("pptx", "")).resolve()
    if ppt.parent != folder.resolve() or ppt.suffix.lower() != ".pptx" or not ppt.is_file() or ppt.stat().st_size == 0:
        raise V8Error("PPT_PATH_INVALID", "이번 출력 폴더의 PPT 파일을 확인할 수 없습니다.")
    if not report.get("slides", 0) or not report.get("items"):
        raise V8Error("PPT_EMPTY", "슬라이드·게시글 정보가 없습니다.")
    issue = (status == "partial" or report.get("capture_failures") or report.get("missing_capture_articles")
             or report.get("warnings") or report.get("selected_articles") != len(report["items"]))
    issue = issue or any(i.get("analysis_issues") or i.get("summary_pending") or i.get("capture_problem")
                         or i.get("metadata_warnings") for i in report["items"])
    return ("partial" if issue else "completed"), ppt


def email_payload(cfg, record, report, ppt, outcome):
    label = "부분 완료" if outcome == "partial" else "완료"
    count = len(report.get("items", []))
    end = datetime.fromisoformat(record["end"])
    missing = set(report.get("missing_capture_articles", [])) | {
        i["id"] for i in report.get("items", []) if i.get("capture_problem")}
    pending = sum(bool(i.get("summary_pending") or i.get("analysis_issues")) for i in report.get("items", []))
    return {
        "to": cfg["mail"]["to"], "cc": cfg["mail"]["cc"],
        "subject": f'{cfg["mail"]["subject_prefix"]} [{label}] {end:%Y-%m-%d} / {count}건',
        "body": (f"동호회 모니터링 결과를 공유드립니다.\n\n"
                 f"수집 기간: {record['start'][:16].replace('T', ' ')} 이상 ~ {record['end'][:16].replace('T', ' ')} 미만 (한국시간)\n"
                 f"검색어: {', '.join(record['scope']['keywords'])}\n"
                 f"처리 상태: {label}\n선정: {report.get('selected_articles', 0)}건 / PPT 포함: {count}건\n"
                 f"캡처 확인 필요: {len(missing)}건 / 요약 확인 필요: {pending}건\n\n"
                 "첨부 PPT를 확인해 주세요. 분류·요약은 원문 검토가 필요한 초안입니다.\n"
                 f"실행 번호: {record['id']}\n"),
        "attachments": [str(ppt)], "timeout": cfg["mail"]["timeout_seconds"],
    }


class Runner:
    """호출자가 V8/기존 V754 OS 잠금을 잡은 상태에서 사용합니다."""
    def __init__(self, root, cfg, backend, sender):
        self.root, self.cfg, self.backend, self.sender = Path(root).resolve(), cfg, backend, sender
        self.out = self.root / "output_v8"
        self.runs = self.out / "runs"
        self.identity = scope(backend.cafe_id, backend.words)
        self.state_path = self.out / "state.json"

    def load_state(self):
        state = read_json(self.state_path) if self.state_path.exists() else {
            "scope": self.identity, "cursor": None, "active": None}
        if state.get("scope") != self.identity:
            raise V8Error("SCOPE_CHANGED", "카페·검색어가 기존 V8 예약 이력과 다릅니다. 기존 설정으로 복구한 뒤 실행하세요. 카페 확장은 V9 범위입니다.")
        return state

    def get_record(self, identifier):
        if not isinstance(identifier, str) or not RUN_PATTERN.fullmatch(identifier):
            raise V8Error("INVALID_RUN", "올바른 V8 실행 번호를 지정하세요.")
        folder = self.runs / identifier
        record = read_json(folder / "run.json")
        if record.get("id") != identifier or record.get("scope") != self.identity:
            raise V8Error("RUN_MISMATCH", "실행 번호·카페·검색어가 현재 설정과 다릅니다.")
        expected = run_id(self.identity, datetime.fromisoformat(record["start"]), datetime.fromisoformat(record["end"]))
        if expected != identifier:
            raise V8Error("RUN_MISMATCH", "실행 기록의 기간이 변경되었습니다.")
        return folder, record

    def save(self, folder, record):
        record["updated_at"] = stamp()
        write_json(folder / "run.json", record)

    def execute(self, *, now=None, start=None, end=None, identifier=None, preview=False, retry=False):
        require_ready(self.cfg)
        now = now or datetime.now(KST)
        state = self.load_state()
        if identifier and (start or end):
            raise V8Error("ARGUMENT_CONFLICT", "실행 번호와 새 기간을 동시에 지정할 수 없습니다.")
        if identifier:
            folder, record = self.get_record(identifier)
        elif not start and not end and state.get("active"):
            folder, record = self.get_record(state["active"])
        else:
            automatic = not (start or end)
            bounds = scheduled_window(self.cfg, state, now) if automatic else explicit_window(start, end, now)
            if not bounds:
                print("[대기] 아직 처리할 예약 구간이 없습니다.")
                return 0
            lo, hi = bounds
            identifier = run_id(self.identity, lo, hi)
            folder = self.runs / identifier
            if (folder / "run.json").exists():
                folder, record = self.get_record(identifier)
            else:
                folder.mkdir(parents=True, exist_ok=True)
                record = {"version": "8.2.0", "id": identifier, "scope": self.identity,
                          "start": lo.isoformat(), "end": hi.isoformat(), "status": "new",
                          "created_at": stamp(), "stage": "PREPARE", "attempts": [],
                          "collection": None, "artifact": None, "mail": {"status": "not_started"}}
                self.save(folder, record)
            if automatic:
                state["active"] = identifier
                write_json(self.state_path, state)
        self.last_folder, self.last_record = folder, record
        with (folder / "run.log").open("a", encoding="utf-8") as log:
            with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
                print(f"\n[V8] {stamp()} / {record['id']}")
                print(f"[기간] {record['start']} 이상 ~ {record['end']} 미만")
                code = self._process(folder, record, preview, retry)
                # 메일 호출이 끝난 뒤 프로세스가 중단돼도 다음 실행에서 커서만 복구합니다.
                if record["status"] in TERMINAL and state.get("active") == record["id"]:
                    state.update(cursor=record["end"], active=None)
                    write_json(self.state_path, state)
                print(f"[상태] {record['status']} / {folder / 'run.json'}")
                return code

    def _artifact(self, folder, record):
        ref = record["artifact"]
        summary = safe_path(folder, ref["summary"])
        if sha(summary) != ref["summary_sha256"]:
            raise V8Error("ARTIFACT_CHANGED", "검증 이후 PPT 결과 기록이 변경됐습니다.")
        report = read_json(summary)
        outcome, ppt = classify(report, summary.parent, self.identity,
                                datetime.fromisoformat(record["start"]), datetime.fromisoformat(record["end"]))
        if ppt and sha(ppt) != ref["ppt_sha256"]:
            raise V8Error("ARTIFACT_CHANGED", "검증 이후 PPT 파일이 변경됐습니다. 이번 실행 결과를 확인하세요.")
        return report, outcome, ppt

    def _process(self, folder, record, preview, retry):
        if record["status"] in TERMINAL:
            print("[기존 완료] 수집·AI·PPT·메일을 다시 실행하지 않습니다.")
            return 2 if record["status"] == "partial" else 0
        if record["mail"]["status"] in ("sending", "unknown"):
            record["mail"]["status"] = "unknown"
            record["status"] = "mail_unknown"
            self.save(folder, record)
            print("[발송 확인 필요] 이전 메일의 발송 여부가 불명확합니다. 18_resolve_mail_v8.bat에서 보낸편지함 확인 결과를 기록하세요.")
            return 3
        if record["status"] in ("failed", "running") and not retry:
            # 실패 호출을 매일 반복하지 않습니다. 사용자가 원인을 해결한 후 17번으로 재시도합니다.
            print("[재시도 대기] 기존 실패/중단 기록이 있습니다. 원인 해결 후 17_retry_v8.bat를 사용하세요.")
            return 1
        try:
            if not record.get("artifact"):
                record.update(status="running", stage="PREPARE")
                self.save(folder, record)
                self.backend.check()
                lo, hi = datetime.fromisoformat(record["start"]), datetime.fromisoformat(record["end"])
                collection = safe_path(folder, record["collection"]) if record.get("collection") else None
                if collection is None:
                    collection = self._recover_collection(folder, record, lo, hi)
                if collection is None:
                    collection = self.new_attempt(folder, record, "collect")
                    record["stage"] = "COLLECT"
                    self.save(folder, record)
                    self.backend.collect(collection, lo, hi)
                if not self.backend.collection_ok(collection, lo, hi):
                    raise V8Error("COLLECTION_MISMATCH", "이번 실행의 수집 결과와 기간이 다릅니다.")
                record["collection"] = str(collection.relative_to(folder))
                self.save(folder, record)
                export = self._recover_export(folder, record, lo, hi)
                if export is None:
                    export = self.new_attempt(folder, record, "export")
                    record["stage"] = "EXPORT"
                    self.save(folder, record)
                    _, code = self.backend.export(collection, export)
                    if code not in (0, 2):
                        raise V8Error("EXPORT_FAILED", f"출력이 완료되지 않았습니다: {code}")
                summary = export / "summary.json"
                report = read_json(summary)
                outcome, ppt = classify(report, export, self.identity, lo, hi)
                record["artifact"] = {"summary": str(summary.relative_to(folder)),
                                      "summary_sha256": sha(summary), "ppt_sha256": sha(ppt) if ppt else None}
                record["outcome"] = outcome
                self.save(folder, record)
            report, outcome, ppt = self._artifact(folder, record)
            if outcome == "completed_empty":
                record.update(status=outcome, stage="COMPLETE", mail={"status": "skipped_empty"})
                self.save(folder, record)
                print("[대상 0건] 완료 기록을 저장했습니다. PPT 생성·메일 발송 없음.")
                return 0
            if outcome == "partial" and not self.cfg["mail"]["send_partial"]:
                record.update(status="mail_held", stage="MAIL")
                self.save(folder, record)
                print("[부분 완료] 설정에 따라 메일을 보류했습니다. PPT는 보존했습니다.")
                return 2
            record["stage"] = "MAIL"
            payload = email_payload(self.cfg, record, report, ppt, outcome)
            if record["mail"]["status"] == "sent":
                # 발송 기록이 먼저 저장된 직후 중단된 경우 외부 호출 없이 마무리합니다.
                record.update(status=outcome, stage="COMPLETE")
                self.save(folder, record)
                return 2 if outcome == "partial" else 0
            if preview:
                result = self.sender(**payload, display_only=True)
                if result.get("ok") is not True or result.get("action") != "displayed":
                    raise V8Error("PREVIEW_FAILED", "메일 작성창 결과를 확인하지 못했습니다.")
                record.update(status="previewed", mail={"status": "displayed", "at": stamp(), "result": result})
                self.save(folder, record)
                print("[미리보기] 발송하지 않았습니다. 작성창을 닫고 12_run_v8.bat를 실행하면 같은 PPT를 발송합니다.")
                return 0
            # Send()가 성공하고 결과 저장 전에 종료될 수 있으므로 먼저 sending을 영속 저장합니다.
            record["mail"] = {"status": "sending", "started_at": stamp(), "payload": payload}
            self.save(folder, record)
            try:
                result = self.sender(**payload, display_only=False)
                if result.get("ok") is not True or result.get("action") != "sent":
                    raise V8Error("MAIL_RESULT_UNEXPECTED", "발송 결과를 확인할 수 없습니다.")
                record["mail"].update(status="sent", finished_at=stamp(), result=result)
                self.save(folder, record)
            except BaseException as exc:
                record["mail"].update(status="unknown", error_type=type(exc).__name__)
                record["status"] = "mail_unknown"
                self.save(folder, record)
                print("[발송 확인 필요] Outlook 호출 이후 결과가 불명확합니다. 자동 재발송은 하지 않습니다.")
                return 3
            record.update(status=outcome, stage="COMPLETE")
            self.save(folder, record)
            print(f"[Outlook 발송 요청 완료] {payload['subject']} / 첨부 {ppt.name}")
            return 2 if outcome == "partial" else 0
        except BaseException as exc:
            record.update(status="failed", error={"code": getattr(exc, "code", type(exc).__name__),
                          "message": str(exc) if hasattr(exc, "code") else "실행 환경 또는 기록을 확인하세요."})
            self.save(folder, record)
            print(f"[실패] {record['stage']} / {record['error']['code']} / {record['error']['message']}")
            return 130 if isinstance(exc, (KeyboardInterrupt, EOFError)) else 1

    def new_attempt(self, folder, record, kind):
        name = kind + "_" + datetime.now(KST).strftime("%Y%m%d_%H%M%S_%f") + "_" + uuid.uuid4().hex[:6]
        child = folder / name
        child.mkdir()
        record["attempts"].append({"kind": kind, "folder": name, "started_at": stamp()})
        self.save(folder, record)
        return child

    def _recover_collection(self, folder, record, lo, hi):
        for attempt in reversed(record["attempts"]):
            if attempt["kind"] == "collect":
                path = safe_path(folder, attempt["folder"])
                if (path / "collection.json").exists():
                    try:
                        if self.backend.collection_ok(path, lo, hi):
                            return path
                    except Exception:
                        pass
        return None

    def _recover_export(self, folder, record, lo, hi):
        for attempt in reversed(record["attempts"]):
            if attempt["kind"] == "export":
                path = safe_path(folder, attempt["folder"])
                if (path / "summary.json").exists():
                    report = read_json(path / "summary.json")
                    if report.get("status") in TERMINAL:
                        classify(report, path, self.identity, lo, hi)
                        return path
        return None

    def resolve_mail(self, identifier, decision):
        folder, record = self.get_record(identifier)
        if record["mail"]["status"] not in ("sending", "unknown", "displayed"):
            raise V8Error("NO_UNCERTAIN_MAIL", "발송 여부 확인이 필요한 메일 기록이 아닙니다.")
        if decision not in ("sent", "not-sent"):
            raise V8Error("INVALID_DECISION", "sent 또는 not-sent를 지정하세요.")
        record.setdefault("mail_history", []).append(record["mail"])
        record["mail"] = {"status": "sent" if decision == "sent" else "not_started",
                          "user_verified_at": stamp(), "user_decision": decision}
        record.update(status="ready", stage="MAIL")
        self.save(folder, record)
        print("[기록 완료] 12_run_v8.bat 또는 실행 번호를 지정한 run으로 이어서 처리하세요.")

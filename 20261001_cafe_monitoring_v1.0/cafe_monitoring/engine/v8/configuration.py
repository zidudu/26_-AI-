"""V8 설정만 관리합니다. 기존 카페·모델·요약 설정은 v754에서 읽습니다."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re

KST = timezone(timedelta(hours=9))
DEFAULT = {
    "version": "8.0.0",
    "schedule": {"time": "09:00", "weekdays_only": True, "initial_start": None,
                 "task_name": "NaverCafe_V8"},
    "mail": {"to": [], "cc": [], "subject_prefix": "[동호회 모니터링]",
             "send_partial": True, "timeout_seconds": 120},
}


class V8Error(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise V8Error("JSON_READ_ERROR", f"기록을 읽지 못했습니다: {path}") from exc


def write_json(path, value):
    # OS 잠금 안에서 사용합니다. 중간 저장 파일을 기존 완료 기록으로 읽지 않습니다.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def parse_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d", value):
        raise V8Error("INVALID_TIME", "시각은 YYYY-MM-DD HH:MM 형식입니다. 한국시간 기준입니다.")
    try:
        return datetime.fromisoformat(value).replace(tzinfo=KST)
    except ValueError as exc:
        raise V8Error("INVALID_TIME", "날짜·시각을 확인하세요.") from exc


def addresses(value):
    if isinstance(value, str):
        value = re.split(r"[;,]", value)
    if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
        raise V8Error("INVALID_RECIPIENT", "수신자는 주소 또는 Outlook에서 확인 가능한 계정 목록입니다.")
    value = list(dict.fromkeys(x.strip() for x in value if x.strip()))
    if any(any(c in x for c in "\r\n\x00;,") for x in value):
        raise V8Error("INVALID_RECIPIENT", "수신자 목록의 구분자·줄바꿈을 확인하세요.")
    return value


def validate(data):
    cfg = deepcopy(data)
    try:
        if set(cfg) != set(DEFAULT) or cfg["version"] != "8.0.0":
            raise ValueError("V8 설정 형식")
        s, m = cfg["schedule"], cfg["mail"]
        if set(s) != set(DEFAULT["schedule"]) or set(m) != set(DEFAULT["mail"]):
            raise ValueError("설정 항목")
        if not isinstance(s["time"], str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", s["time"]):
            raise ValueError("예약 시각 HH:MM")
        if type(s["weekdays_only"]) is not bool or type(m["send_partial"]) is not bool:
            raise ValueError("true/false 설정")
        if s["initial_start"] is not None:
            parse_time(s["initial_start"])
        if not isinstance(s["task_name"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", s["task_name"]):
            raise ValueError("예약 이름(영문·숫자·밑줄·하이픈)")
        m["to"], m["cc"] = addresses(m["to"]), addresses(m["cc"])
        if not isinstance(m["subject_prefix"], str) or not m["subject_prefix"].strip() or any(c in m["subject_prefix"] for c in "\r\n\x00"):
            raise ValueError("메일 제목")
        if type(m["timeout_seconds"]) is not int or not 30 <= m["timeout_seconds"] <= 600:
            raise ValueError("메일 제한 시간(30~600초)")
    except (KeyError, TypeError, ValueError) as exc:
        raise V8Error("CONFIG_ERROR", f"config_v8.json을 확인하세요: {exc}") from exc
    return cfg


def load(root):
    path = Path(root) / "config_v8.json"
    if not path.is_file():
        raise V8Error("SETUP_REQUIRED", "먼저 10_setup_v8.bat로 예약 시각과 수신자를 설정하세요.")
    return validate(read_json(path))


def require_ready(cfg):
    if not cfg["mail"]["to"]:
        raise V8Error("RECIPIENT_REQUIRED", "10_setup_v8.bat에서 수신자를 설정하세요.")
    if not cfg["schedule"]["initial_start"]:
        raise V8Error("START_REQUIRED", "10_setup_v8.bat에서 최초 수집 시작 시각을 설정하세요.")

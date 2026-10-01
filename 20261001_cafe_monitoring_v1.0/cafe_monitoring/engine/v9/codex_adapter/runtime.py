"""Codex CLI 프로세스 실행. shell=True/cmd.exe/PowerShell을 사용하지 않습니다."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any

from .config import CodexOptions
from .contract import CompiledRequest, TRANSPORT_GUARD
from .errors import CodexAdapterError, configuration_error


@dataclass(frozen=True)
class RunOutput:
    final_text: str | None
    events_text: str
    stderr_text: str
    returncode: int
    elapsed_seconds: float
    cli_version: str


def resolve_command(options: CodexOptions) -> tuple[str, ...]:
    if options.command_prefix:
        prefix = options.command_prefix
        if Path(prefix[0]).suffix.lower() in {".cmd", ".bat", ".ps1"}:
            raise configuration_error("명령 셸 래퍼는 실행하지 않습니다. native exe 또는 node.exe + codex.js를 지정하세요.")
        first = shutil.which(prefix[0])
        if not first:
            raise CodexAdapterError("CODEX_NOT_FOUND", "command_prefix 실행 파일을 찾을 수 없습니다.")
        return (first, *prefix[1:])
    found = shutil.which(options.command)
    if not found:
        raise CodexAdapterError("CODEX_NOT_FOUND", "Codex CLI를 찾을 수 없습니다. 같은 계정의 PowerShell에서 codex --version을 확인하세요.")
    path = Path(found)
    if path.suffix.lower() not in {".cmd", ".bat", ".ps1"}:
        return (str(path),)
    # npm 전역 설치의 통상적 위치. 래퍼의 명령 문자열을 파싱/실행하지 않습니다.
    js = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    node = path.parent / "node.exe"
    node_path = str(node) if node.is_file() else shutil.which("node")
    if js.is_file() and node_path:
        return (node_path, str(js))
    raise CodexAdapterError("CODEX_WRAPPER_UNSUPPORTED",
        "Codex 셸 래퍼는 찾았으나 실제 실행 파일을 확인하지 못했습니다. "
        "CodexOptions(command_prefix=(native_exe,)) 또는 (node_exe, codex_js)를 지정하세요.")


def child_environment(options: CodexOptions) -> dict[str, str]:
    env = os.environ.copy()
    remove = {"OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_ORGANIZATION", "OPENAI_PROJECT_ID"}
    for name in list(env):
        if name.upper() in remove:
            env.pop(name, None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"
    if options.codex_home:
        env["CODEX_HOME"] = str(options.codex_home)
    return env


def _decode(data: bytes) -> str:
    return data.decode("utf-8-sig", errors="replace")


def _kill_tree(process: subprocess.Popen) -> bool:
    """우리 프로세스 트리만 종료. 같은 PC의 다른 Codex는 건드리지 않습니다."""
    if process.poll() is not None:
        return True
    ok = True
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        taskkill = system_root / "System32" / "taskkill.exe"
        try:
            result = subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=5, shell=False)
            ok = result.returncode == 0 or process.poll() is not None
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            ok = False
            process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        ok = False
    return ok


class CodexRuntime:
    """여러 Analyzer가 공유할 수 있는 실행기. preflight 및 동시성 게이트를 공유합니다."""
    def __init__(self, options: CodexOptions):
        self.options = options
        self._prefix: tuple[str, ...] | None = None
        self._preflight_lock = threading.Lock()
        self._gate = threading.BoundedSemaphore(options.max_parallel)
        self._version: str | None = None
        self._closed = threading.Event()

    def _temp(self):
        if self.options.temp_root is not None:
            self.options.temp_root.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(prefix="codex-v9-", dir=self.options.temp_root)

    def _process(self, argv: list[str], *, cwd: Path, data: bytes,
                 timeout: float, cancel: threading.Event, model_run: bool,
                 final_path: Path | None = None) -> tuple[int, bytes, bytes]:
        if cancel.is_set() or self._closed.is_set():
            raise CodexAdapterError("CODEX_CANCELLED", "실행 전에 취소되었습니다.")
        kwargs: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
        out_path = cwd / "events.jsonl"
        err_path = cwd / "stderr.txt"
        with out_path.open("w+b") as out, err_path.open("w+b") as err:
            try:
                process = subprocess.Popen(argv, cwd=str(cwd), env=child_environment(self.options),
                    stdin=subprocess.PIPE, stdout=out, stderr=err, shell=False, **kwargs)
            except OSError as exc:
                raise CodexAdapterError("CODEX_START_FAILED", "Codex 프로세스를 시작하지 못했습니다.",
                                        diagnostics={"errno": exc.errno}) from exc
            started = time.monotonic()
            first = True
            try:
                while True:
                    if cancel.is_set() or self._closed.is_set():
                        raise CodexAdapterError("CODEX_CANCELLED", "Codex 실행을 취소했습니다.", uncertain=model_run)
                    if (os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size) > self.options.max_event_bytes:
                        raise CodexAdapterError("CODEX_LOG_LIMIT", "CLI 로그 크기 제한을 초과했습니다.", uncertain=model_run)
                    if final_path and final_path.exists() and final_path.stat().st_size > self.options.max_final_bytes:
                        raise CodexAdapterError("CODEX_OUTPUT_SIZE", "결과 파일 크기 제한을 초과했습니다.", uncertain=model_run)
                    remaining = timeout - (time.monotonic() - started)
                    if remaining <= 0:
                        raise CodexAdapterError("CODEX_TIMEOUT", f"Codex 실행이 {timeout:g}초 제한을 초과했습니다.", uncertain=model_run)
                    try:
                        process.communicate(input=data if first else None, timeout=min(0.2, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        first = False
                if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > self.options.max_event_bytes:
                    raise CodexAdapterError("CODEX_LOG_LIMIT", "CLI 로그 크기 제한을 초과했습니다.", uncertain=model_run)
                out.seek(0); err.seek(0)
                return process.returncode, out.read(self.options.max_event_bytes), err.read(self.options.max_event_bytes)
            except BaseException as exc:
                killed = _kill_tree(process)
                if isinstance(exc, CodexAdapterError):
                    exc.diagnostics["process_tree_cleanup_confirmed"] = killed
                raise
            finally:
                if process.stdin is not None:
                    process.stdin.close()

    def _probe(self, args: list[str]) -> tuple[int, str]:
        assert self._prefix is not None
        try:
            with self._temp() as tmp:
                code, out, err = self._process([*self._prefix, *args], cwd=Path(tmp), data=b"",
                    timeout=self.options.preflight_timeout_seconds, cancel=threading.Event(), model_run=False)
            return code, _decode(out + b"\n" + err)
        except OSError as exc:
            raise CodexAdapterError("CODEX_LOCAL_IO", "사전 검사 임시 파일 처리에 실패했습니다.",
                                    diagnostics={"errno": exc.errno}) from exc

    def preflight(self, *, force: bool = False) -> dict[str, Any]:
        """모델을 호출하지 않습니다. 설치/필수 옵션/인증 방식을 확인합니다."""
        with self._preflight_lock:
            if self._closed.is_set():
                raise CodexAdapterError("CODEX_CLOSED", "Runtime이 이미 종료되었습니다.")
            if self._version is not None and not force:
                return {"cli_version": self._version, "auth_method": "chatgpt", "inference_performed": False}
            self._prefix = resolve_command(self.options)
            code, version_text = self._probe(["--version"])
            match = re.search(r"\b\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?\b", version_text)
            if code != 0 or not match:
                raise CodexAdapterError("CODEX_VERSION", "Codex 버전을 확인하지 못했습니다.")
            version = match.group(0)
            code, help_text = self._probe(["exec", "--help"])
            required = {"--json", "--output-schema", "--output-last-message", "--sandbox", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config"}
            missing = sorted(x for x in required if x not in help_text)
            if code != 0 or missing:
                raise CodexAdapterError("CODEX_CLI_OPTIONS", "현재 Codex에서 필요한 옵션이 확인되지 않았습니다.", diagnostics={"missing_options": missing, "cli_version": version})
            code, root_help = self._probe(["--help"])
            if code != 0 or "--strict-config" not in root_help:
                raise CodexAdapterError("CODEX_CLI_OPTIONS", "--strict-config 지원을 확인하지 못했습니다. 옵션을 자동 제거하지 않습니다.", diagnostics={"cli_version": version})
            code, login_text = self._probe(["-c", 'forced_login_method="chatgpt"',
                "-c", f"cli_auth_credentials_store={json.dumps(self.options.credentials_store)}", "login", "status"])
            if code != 0 or "logged in using chatgpt" not in login_text.lower():
                raise CodexAdapterError("CODEX_LOGIN_REQUIRED", "ChatGPT 방식의 Codex 로그인이 필요합니다. 로그인 상태 출력만으로 회사 계정의 소유자는 확인할 수 없습니다.")
            self._version = version
            return {"cli_version": version, "auth_method": "chatgpt", "inference_performed": False}

    def run(self, compiled: CompiledRequest, *, cancel: threading.Event) -> RunOutput:
        if not self.options.acknowledge_cli_differences:
            raise configuration_error("API의 store 및 생성 시점 token cap과 CLI의 차이를 확인하고 acknowledge_cli_differences=True로 명시하세요.")
        version = self.preflight()["cli_version"]
        while not self._gate.acquire(timeout=0.1):
            if cancel.is_set() or self._closed.is_set():
                raise CodexAdapterError("CODEX_CANCELLED", "실행 대기 중 취소되었습니다.")
        model_may_have_run = False
        try:
            with self._temp() as tmp:
                cwd = Path(tmp)
                instruction_path = cwd / "application_instructions.txt"
                schema_path = cwd / "schema.json"
                final_path = cwd / "final.json"
                instruction_path.write_text(TRANSPORT_GUARD + "\nAPPLICATION INSTRUCTIONS\n" + compiled.instructions,
                                            encoding="utf-8")
                schema_path.write_text(json.dumps(compiled.schema, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                settings = {
                    "forced_login_method": "chatgpt", "cli_auth_credentials_store": self.options.credentials_store,
                    "model_provider": "openai", "model_reasoning_effort": self.options.reasoning_effort,
                    "model_instructions_file": str(instruction_path), "web_search": "disabled",
                    "features.shell_tool": False, "features.unified_exec": False,
                    "features.multi_agent": False, "features.apps": False,
                    "features.memories": False, "features.plugins": False, "features.hooks": False,
                    "project_doc_max_bytes": 0,
                }
                args = ["--strict-config", "-a", "never"]
                for key, value in settings.items():
                    args.extend(["-c", f"{key}={json.dumps(value, ensure_ascii=False)}"])
                args.extend(["exec", "--ignore-user-config", "--ephemeral", "--json", "--sandbox", "read-only",
                             "--skip-git-repo-check", "--model", self.options.model,
                             "--output-schema", str(schema_path), "--output-last-message", str(final_path), "-"])
                assert self._prefix is not None
                started = time.monotonic()
                model_may_have_run = True
                code, out, err = self._process([*self._prefix, *args], cwd=cwd,
                    data=compiled.user_json.encode("utf-8"), timeout=self.options.timeout_seconds,
                    cancel=cancel, model_run=True, final_path=final_path)
                elapsed = time.monotonic() - started
                final = None
                if final_path.exists():
                    if final_path.stat().st_size > self.options.max_final_bytes:
                        raise CodexAdapterError("CODEX_OUTPUT_SIZE", "결과 파일 크기 제한을 초과했습니다.", uncertain=True)
                    try:
                        final = final_path.read_bytes().decode("utf-8-sig")
                    except UnicodeDecodeError as exc:
                        raise CodexAdapterError("CODEX_OUTPUT_ENCODING", "Codex 최종 파일이 올바른 UTF-8이 아닙니다.", stop=False) from exc
                return RunOutput(final, _decode(out), _decode(err), code, elapsed, version)
        except OSError as exc:
            raise CodexAdapterError("CODEX_LOCAL_IO", "어댑터 임시 파일 생성/읽기/정리에 실패했습니다.",
                                    uncertain=model_may_have_run, diagnostics={"errno": exc.errno}) from exc
        finally:
            self._gate.release()

    def close(self) -> None:
        self._closed.set()

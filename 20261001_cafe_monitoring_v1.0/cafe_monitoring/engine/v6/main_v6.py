"""Windows 배치 파일 및 명령행 진입점. 경로는 현재 작업 폴더에 의존하지 않습니다."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from v6 import VERSION
from v6.common import RunLock, V6Error, read_json
from v6.credentials import load_api_key, setup_key
from v6.settings import load_config


def ask_count(default):
    while True:
        value = input(f"이번에 새로 분석할 글 수 1~100 (Enter: {default}, 취소: /q): ").strip()
        if value.lower() == "/q":
            raise KeyboardInterrupt()
        if not value:
            return default
        if value.isdecimal() and 1 <= int(value) <= 100:
            return int(value)
        print("1~100 사이 정수를 입력하세요.")


def print_plan(plan):
    print(f"[원본 확인] 유효 파일 {plan['valid_files']} / 고유 글 {plan['unique_articles']} / 중복·이전 본문 {plan['duplicate_or_older_files']}")
    print(f"[분석 이력] 기존 완료 {plan['cached_articles']} / 실패·미확인 보류 {plan['held_errors']} / 새 대상 {plan['available_new']}")
    print(f"[이번 선정] {plan['selected']} / 다음 실행 대상 {plan['not_selected']}")
    if plan["invalid_files"] or plan["too_long"] or plan["damaged_cache"]:
        print(f"[확인 필요] 원본 제외 {len(plan['invalid_files'])} / 긴 본문 {len(plan['too_long'])} / 손상된 분석 결과 {plan['damaged_cache']}")
    for item in plan["invalid_files"][:5]:
        print(f"  {item['code']} / {item['file']}")
    if plan["unfinished_run_ids"]:
        print("[미완료 실행 있음] 04_resume_v6.bat로 재개하세요.")


def parser():
    p = argparse.ArgumentParser(description="V6 저장 게시글 AI 분석")
    p.add_argument("command", choices=("analyze", "resume", "retry", "recheck", "audit-saved", "convert-saved", "preview", "check", "setup-key", "status"))
    p.add_argument("--config", type=Path)
    p.add_argument("--count", type=int)
    p.add_argument("--prompt", action="store_true")
    p.add_argument("--run-id", help="resume/retry/recheck/audit-saved에서 특정 실행 ID 선택")
    p.add_argument("--preview-only", action="store_true", help="recheck의 대상을 API 호출 없이 확인")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        print(f"[V{VERSION}] {args.command}")
        if args.command == "setup-key":
            setup_key()
            return 0
        cfg = load_config(args.config)
        if args.command == "status":
            status_file = cfg["output_dir"] / "latest_status.json"
            if not status_file.exists():
                print("실행 기록이 없습니다.")
                return 0
            status = read_json(status_file)
            print(f"최근 실행: {status['run_id']} / {status['status']} / {status['code']}")
            print(f"요약 JSON: {status['summary_file']}")
            return 0
        # 설정/키 저장은 SDK가 설치되지 않아도 가능합니다.
        from v6.engine import execute, inspect_work, load_resume, start_run, inspect_recheck, start_recheck
        from v6.prompts import PROMPT_VERSION
        import openai
        import pydantic
        count = cfg["default_count"] if args.count is None else args.count
        if not 1 <= count <= 100:
            raise V6Error("INVALID_COUNT", "분석 개수는 1~100 사이여야 합니다.")
        if args.command == "convert-saved":
            from v6.drafts import convert_saved
            if args.prompt and not args.run_id:
                value = input("초안으로 변환할 실행 ID (Enter: 최근 실행, 취소: /q): ").strip()
                if value.lower() == "/q":
                    raise KeyboardInterrupt()
                args.run_id = value or None
            with RunLock(cfg["output_dir"] / "v6.lock"):
                path, report = convert_saved(cfg, args.run_id)
            print(f"[초안 변환] 저장 {report['saved']} / 응답 없음·읽기 실패 {report['unavailable']} / API 호출 0")
            print("기존 응답을 검토용으로 변환했습니다. 새 지시문으로 다시 분석한 결과는 아닙니다.")
            print("원래 API 응답과 실행 이력은 보존됩니다. 직원 검토 상태: 모두 검토 전")
            print(f"모니터링 초안: {path}")
            return 0 if not report["unavailable"] else 3
        if args.command == "audit-saved":
            from v6.saved_audit import audit_saved_run
            if args.prompt and not args.run_id:
                value = input("점검할 실행 ID (Enter: 최근 실행, 취소: /q): ").strip()
                if value.lower() == "/q":
                    raise KeyboardInterrupt()
                args.run_id = value or None
            with RunLock(cfg["output_dir"] / "v6.lock"):
                path, report = audit_saved_run(cfg, args.run_id)
            print(f"[저장 응답 점검] {report['run_id']} / API 호출 0")
            for item in report["items"]:
                issues = item.get("known_semantic_issues")
                print(f"  {item['article_id']} / 인용 형식 복구 후보 {len(item.get('format_candidates', []))} / 미일치 {len(item.get('unresolved_quotes', []))} / 의미 점검 {len(issues) if issues is not None else '수동 검토'}")
            print("복구 후보는 점검 파일에만 기록했습니다. 원본 응답·완료 이력은 유지됩니다.")
            print("형식 통과는 의미 정확성의 보장이 아닙니다. 원문 검토가 필요합니다.")
            print(f"점검 JSON: {path}")
            return 0
        if args.command == "check":
            print(f"[설정 정상] 카페 {cfg['cafe_id']} / 모델 {cfg['model']} / 기본 {count}개")
            print(f"분석 지시문: {PROMPT_VERSION} / 추론 강도: {cfg['reasoning_effort']}")
            print("결과: 요약·증상·부품·조치 초안 + 원문·캡처 + 검토 메모 / 최종 판단은 직원 검토")
            print(f"Python {sys.version.split()[0]} / openai {openai.__version__} / pydantic {pydantic.__version__}")
            print(f"API 키 설정: {'있음 (값 숨김)' if load_api_key() else '없음 - 02_setup_key_v6.bat 실행'}")
            print("이 확인은 API를 호출하지 않습니다. 키 권한·잔액·모델 연결은 실제 분석에서 확인됩니다.")
            articles, plan, spec = inspect_work(cfg, count)
            print_plan(plan)
            return 0
        if args.command == "preview":
            articles, plan, spec = inspect_work(cfg, count)
            print_plan(plan)
            for i, article in enumerate(articles, 1):
                print(f"  {i}. {article['article_id']} / {article['title']} / {len(article['body'])}자")
            print("API 호출 없이 대상만 확인했습니다.")
            return 0
        if args.command == "analyze" and args.prompt:
            count = ask_count(count)
        if args.command == "recheck":
            if args.prompt and not args.run_id:
                value = input("재검증할 실행 ID (Enter: 최근 이전 기준 실행, 취소: /q): ").strip()
                if value.lower() == "/q":
                    raise KeyboardInterrupt()
                args.run_id = value or None
            articles, plan, spec = inspect_recheck(cfg, args.run_id)
            # 미리보기에서 선택한 실행을 고정해 후속 선정도 같은 원문을 사용합니다.
            args.run_id = plan["recheck_of"]
            print(f"[재검증 원본] {args.run_id} / 원래 대상 {plan['requested']}개")
            print(f"[현재 기준] {spec['prompt_version']} / 모델 {spec['model']} / 추론 {spec['reasoning_effort']}")
            for i, a in enumerate(articles, 1):
                print(f"  {i}. {a['article_id']} / {a['title']} / {len(a['body'])}자")
            print(f"이번 API 대상 {len(articles)} / 동일 기준 완료 {plan['cached_articles']} / 실패·미확인 보류 {plan['held_errors']}")
            if args.preview_only:
                print("API 호출 없이 재검증 대상만 확인했습니다.")
                return 0
        with RunLock(cfg["output_dir"] / "v6.lock"):
            if args.command == "analyze":
                folder, manifest = start_run(cfg, count)
                print_plan(manifest["plan"])
            elif args.command == "recheck":
                folder, manifest = start_recheck(cfg, args.run_id)
                print("[재검증] 원래 제목·본문·작성일을 그대로 사용해 새 실행에 저장합니다.")
            else:
                folder, manifest = load_resume(cfg, args.run_id, retry_errors=args.command == "retry")
                print(f"[재개] 중단 당시 대상·모델·분석 기준을 사용합니다. 실행 ID: {manifest['run_id']}")
                if manifest['spec']['prompt_version'] != PROMPT_VERSION:
                    print("[이전 분석 기준] 이번 수정 기준으로 비교하려면 11_reanalyze_v6.bat를 사용하세요.")
                if args.command == "retry":
                    print("[재시도] 실패·미확인 항목을 포함합니다. 응답이 유실된 요청은 추가 사용량이 발생할 수 있습니다.")
            print(f"모델: {manifest['spec']['model']} / 실행 폴더: {folder}")
            result = execute(cfg, folder, manifest)
            print(f"\n[실행 결과: {result['status']} / {result['code']}] 선정 {result['selected']} / 분석 완료 {result['analyzed']} / 실패 {result['failed']} / 응답 미확인 {result['unknown']} / 미처리 {result['pending']}")
            print(f"이번 호출에서 새로 완료 {result['newly_analyzed_this_invocation']} / 원문 검토 필요 {result['needs_review']}")
            u = result["reported_usage"]
            print(f"누적 토큰: 입력 {u['input_tokens']} / 출력 {u['output_tokens']} / 합계 {u['total_tokens']}")
            print(f"요약 JSON: {folder / 'summary.json'}")
            print(f"분석 JSON: {cfg['output_dir'] / 'analyses'}")
            if result.get("review_file"):
                print(f"모니터링 초안: {result['review_file']}")
            print("저장 완료는 초안 작성 완료입니다. 직원 검토 완료나 내용 정확성 확정이 아닙니다.")
            if result["unknown"]:
                print("응답 미확인 건은 자동 재시도하지 않았습니다. 재시도할 때는 07_retry_v6.bat를 실행하세요.")
            return 130 if result["code"] == "CANCELLED" else (0 if result["status"] == "success" else 3)
    except V6Error as exc:
        print(f"[{exc.code}] {exc}")
        return 4 if exc.code == "ALREADY_RUNNING" else (0 if exc.code == "NOTHING_TO_RESUME" else 1)
    except ModuleNotFoundError:
        print("[DEPENDENCY_MISSING] 01_install_v6.bat를 실행한 뒤 다시 시도하세요.")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\n취소했습니다.")
        return 130
    except (OSError, ValueError, TypeError, KeyError):
        print("[LOCAL_DATA_ERROR] 설정·실행 기록의 손상 또는 파일 접근 오류입니다. 실행 폴더의 summary.json과 events.jsonl을 확인하세요.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

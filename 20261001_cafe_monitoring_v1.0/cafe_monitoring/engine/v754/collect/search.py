"""카페 내부 검색 → 최신순 확인 → 첫 번째 일반 게시글 선택.

2026-09-11 실제 검색 화면에서 확인한 URL/DOM을 사용합니다.
본문 수집은 collector.py가 담당하며 여기서는 다른 글로 임의 대체하지 않습니다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
from urllib.parse import urlencode, urlsplit, parse_qs

from .collector import CollectorError, Target, KST, has_login_redirect, parse_target

ROOT = Path(__file__).resolve().parent


def validate_keyword(keyword: str) -> str:
    if not isinstance(keyword, str):
        raise CollectorError("INVALID_KEYWORD", "검색어는 문자열이어야 합니다.")
    keyword = keyword.strip()
    if not 1 <= len(keyword) <= 100 or any(ord(c) < 32 for c in keyword):
        raise CollectorError("INVALID_KEYWORD", "검색어를 1~100자로 입력하세요. 줄바꿈은 사용할 수 없습니다.")
    return keyword


def search_url(cafe_id: str, keyword: str, page_no: int = 1) -> str:
    if not re.fullmatch(r"[1-9]\d*", cafe_id):
        raise CollectorError("CONFIG_ERROR", "cafe_id는 양의 정수 문자열이어야 합니다.")
    # 실제 검색 및 정렬 조작으로 확인한 웹 페이지 주소입니다. 내부 API가 아닙니다.
    params = {"q": validate_keyword(keyword), "ta": "SUBJECT", "page": str(page_no), "od": "LATEST"}
    return f"https://cafe.naver.com/f-e/cafes/{cafe_id}/menus/0?{urlencode(params)}"


def verify_search_url(url: str, cafe_id: str, keyword: str, page_no: int = 1) -> None:
    try:
        parts = urlsplit(url)
        valid = (parts.scheme == "https" and parts.hostname == "cafe.naver.com"
                 and parts.port in (None, 443) and not parts.username and not parts.password
                 and parts.path.rstrip('/') == f"/f-e/cafes/{cafe_id}/menus/0")
        query = parse_qs(parts.query)
        expected = {"q": keyword, "ta": "SUBJECT", "page": str(page_no), "od": "LATEST"}
        valid = valid and all(query.get(k) == [v] for k, v in expected.items())
    except ValueError:
        valid = False
    if not valid:
        raise CollectorError("SEARCH_CONDITION_MISMATCH", "요청한 카페·검색어·제목 검색·최신순·페이지와 실제 주소가 다릅니다.")


def list_date(raw: str, now: datetime) -> datetime:
    """오늘 글의 시각과 이전 글의 날짜를 비교합니다. 최종 작성일은 본문에서 읽습니다."""
    try:
        if re.fullmatch(r"\d{1,2}:\d{2}", raw):
            hour, minute = map(int, raw.split(':'))
            return now.astimezone(KST).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if re.fullmatch(r"\d{4}\.\d{1,2}\.\d{1,2}\.", raw):
            return datetime(*map(int, raw.rstrip('.').split('.')), tzinfo=KST)
    except ValueError:
        pass
    raise CollectorError("SEARCH_DATE_INVALID", "검색 목록의 작성일 형식이 바뀌었습니다. 최신순 판정을 중단합니다.")


@dataclass(frozen=True)
class SearchSelection:
    target: Target
    # 검색 링크의 일회성 쿼리는 열람 시 메모리에서만 쓰고 저장·로그·repr에서 제외합니다.
    navigation_url: str = field(repr=False)
    metadata: dict


def select_page(snapshot: dict, cfg: dict, keyword: str, now: datetime | None = None, page_no: int = 1) -> list[SearchSelection]:
    """독립 검증 함수. 정렬이나 대상 확인 실패를 '검색 결과 없음'으로 숨기지 않습니다."""
    now = now or datetime.now(KST)
    verify_search_url(snapshot.get("document_url", ""), cfg["cafe_id"], keyword, page_no)
    expected = {"scope": "제목만", "period": "전체기간", "board": "전체 게시판",
                "sort": "최신순", "current_page": str(page_no)}
    values = snapshot.get("query_values", [])
    if not values or any(value != keyword for value in values) or any(snapshot.get(k) != v for k, v in expected.items()):
        raise CollectorError("SEARCH_CONDITION_MISMATCH", "화면의 검색어·범위·기간·정렬·페이지를 확인하지 못했습니다.")
    rows = snapshot.get("rows", [])
    if snapshot.get("empty"):
        if rows:
            raise CollectorError("SEARCH_NOT_READY", "검색 결과와 결과 없음 안내가 함께 표시돼 수집을 중단했습니다.")
        return []
    candidates = []
    for row in rows:
        number = row.get("number", "")
        if number in ("공지", "필독", "광고"):
            continue
        if not re.fullmatch(r"[1-9]\d*", number):
            raise CollectorError("SEARCH_LAYOUT_CHANGED", "검색 목록의 게시글 번호를 읽지 못했습니다.")
        target = parse_target(row.get("href", ""), cfg["cafe_id"], cfg["cafe_slug"])
        if target.article_id != number or not row.get("title", "").strip():
            raise CollectorError("SEARCH_ARTICLE_MISMATCH", "검색 목록의 번호·링크·제목이 일치하지 않습니다.")
        candidates.append((row, target, list_date(row.get("date", ""), now)))
    if not candidates:
        raise CollectorError("SEARCH_NOT_READY", "게시글 목록을 읽지 못했습니다. 결과 없음으로 처리하지 않습니다.")
    dates = [date for _, _, date in candidates]
    if any(a < b for a, b in zip(dates, dates[1:])):
        raise CollectorError("SEARCH_ORDER_MISMATCH", "검색 목록이 작성일 내림차순이 아니어서 최신 글을 확정하지 못했습니다.")
    selections = []
    for rank, (row, target, date) in enumerate(candidates, 1):
        metadata = {
            "keyword": keyword, "scope": "title", "scope_label": "제목만",
            "period": "all", "board": "all", "sort": "latest", "page": page_no,
            "rank_on_page": rank, "result_rank": rank,
            "selection_rule": "first_article_in_latest_search_results",
            "checked_list_rows": len(candidates), "selected_title": row['title'],
            "selected_list_date_raw": row['date'], "selected_list_date": date.isoformat(),
            "selected_article_id": target.article_id,
            "search_url": search_url(cfg['cafe_id'], keyword, page_no), "searched_at": now.isoformat(),
        }
        selections.append(SearchSelection(target, row['href'], metadata))
    return selections


def select_latest(snapshot: dict, cfg: dict, keyword: str, now: datetime | None = None) -> SearchSelection | None:
    candidates = select_page(snapshot, cfg, keyword, now)
    return candidates[0] if candidates else None


def find_latest(page, cfg: dict, keyword: str, timeout_error) -> SearchSelection | None:
    keyword = validate_keyword(keyword)
    timeout_ms = cfg['timeout_seconds'] * 1000
    try:
        response = page.goto(search_url(cfg['cafe_id'], keyword), wait_until='domcontentloaded', timeout=timeout_ms)
        if response and response.status >= 400:
            raise CollectorError("SEARCH_HTTP_ERROR", f"검색 페이지 요청에 HTTP {response.status} 오류가 반환됐습니다.")
        if has_login_redirect(page):
            raise CollectorError("LOGIN_REQUIRED", "로그인이 필요합니다. 02_login.bat 실행 후 다시 검색하세요.")
        verify_search_url(page.url, cfg['cafe_id'], keyword)
        extractor = (ROOT / 'search_snapshot.js').read_text(encoding='utf-8')
        # DOM 껍데기가 아닌 검색 조건과 목록/명시적 빈 결과가 준비될 때까지 기다립니다.
        ready = "(keyword) => { const s = (" + extractor + ")(); return " + (
            "s.query_values.length > 0 && s.query_values.every(v => v === keyword) "
            "&& s.scope && s.sort && s.current_page && (s.rows.length > 0 || s.empty); }"
        )
        page.wait_for_function(ready, arg=keyword, timeout=timeout_ms)
        snapshot = page.evaluate("(" + extractor + ")()")
        if has_login_redirect(page):
            raise CollectorError("LOGIN_REQUIRED", "로그인이 필요합니다. 02_login.bat 실행 후 다시 검색하세요.")
        verify_search_url(page.url, cfg['cafe_id'], keyword)
        return select_latest(snapshot, cfg, keyword)
    except timeout_error:
        if has_login_redirect(page):
            raise CollectorError("LOGIN_REQUIRED", "로그인이 필요합니다. 02_login.bat 실행 후 다시 검색하세요.") from None
        raise CollectorError("SEARCH_NOT_READY", "검색 목록 로딩 시간이 초과됐습니다. 로그인 상태·네트워크·검색 화면을 확인하세요.") from None

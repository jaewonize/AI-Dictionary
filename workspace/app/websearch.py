"""실제 웹 검색(search_web 도구의 live 모드).

1차 호출에서 web_search_20260209 도구로 글과 출처 주소를 모으고,
인용이 붙지 않은 순수 텍스트만 2차 호출에 넘겨 구조화 출력으로 정리한다.
실패하면 예외를 올리지 않고 웹 캐시 결과로 답한다(CostCapExceeded, RateLimitTripped 제외).
"""

from __future__ import annotations

import json
import re
import sys
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app import claude
from app.config import settings

if TYPE_CHECKING:
    from app.tools import ToolContext

WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 5,
    "allowed_callers": ["direct"],
}
MAX_PAUSE_CONTINUATIONS = 3

EXTRACT_SYSTEM = (
    "주어진 웹 검색 요약 글과 출처 목록만 근거로 소개 한 줄(summary)과 국내 행사 목록(events)을 정리합니다. "
    "여러 해나 여러 장소에서 열린 국내 개최 건들은 하나로 합치거나 최근 것만 남기지 말고 각각 별도 항목으로 모두 추출해 시작일 순서로 정렬합니다. "
    "출처에 날까지 있으면 YYYY-MM-DD, 달까지만 있으면 YYYY-MM 형식으로 적고, "
    "출처에서 확인할 수 없는 칸은 추측하지 말고 빈 문자열로 둡니다."
)


class WebEventItem(BaseModel):
    name: str = Field(description="행사 이름. 출처에 없으면 빈 문자열")
    venue: str = Field(description="개최 장소 이름. 출처에 없으면 빈 문자열")
    start: str = Field(description="시작일(YYYY-MM-DD 또는 YYYY-MM). 출처에 없으면 빈 문자열")
    end: str = Field(description="종료일(YYYY-MM-DD 또는 YYYY-MM). 출처에 없으면 빈 문자열")
    url: str = Field(description="출처 웹 주소. 출처에 없으면 빈 문자열")


class WebExtractOut(BaseModel):
    found: bool = Field(description="검색 대상에 관한 소개나 행사를 찾았으면 true, 아니면 false")
    summary: str = Field(description="검색 대상의 한국어 한 줄 소개. 찾지 못했으면 빈 문자열")
    events: list[WebEventItem] = Field(description="국내에서 열렸거나 열릴 행사 목록(시간순)")


class _WebSearchFailed(Exception):
    pass


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


def _g(obj, key: str, default=None):
    if isinstance(obj, dict):
        v = obj.get(key, default)
    else:
        v = getattr(obj, key, default)
    return default if v is None else v


def _load_venue_coords() -> dict[str, tuple[float, float]]:
    coords: dict[str, tuple[float, float]] = {}
    if not settings.web_cache.is_file():
        return coords
    try:
        items = json.loads(settings.web_cache.read_text(encoding="utf-8"))
        if isinstance(items, dict):
            items = items.get("items") or []
    except ValueError:
        return coords
    if not isinstance(items, list):
        return coords
    for it in items:
        if not isinstance(it, dict):
            continue
        for ev in it.get("events") or []:
            if not isinstance(ev, dict):
                continue
            vn = _norm(ev.get("venue"))
            lat, lon = ev.get("lat"), ev.get("lon")
            if vn and isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and not isinstance(lat, bool) and not isinstance(lon, bool):
                coords.setdefault(vn, (float(lat), float(lon)))
    return coords


def _match_venue_coord(venue: str, venue_coords: dict[str, tuple[float, float]]) -> tuple[float, float] | None:
    vn = _norm(venue)
    if not vn:
        return None
    if vn in venue_coords:
        return venue_coords[vn]
    hits = [coord for k, coord in venue_coords.items() if len(k) >= 4 and (k in vn or vn in k)]
    return hits[0] if len(hits) == 1 else None


def _is_error_result_block(block) -> bool:
    content = _g(block, "content", None)
    if content is None:
        return True
    if _g(content, "type", "") == "web_search_tool_result_error" or _g(content, "error_code", None):
        return True
    if isinstance(content, list):
        if not content:
            return False
        return all(
            _g(item, "type", "") == "web_search_tool_result_error" or bool(_g(item, "error_code", None))
            for item in content
        )
    return False


def _collect_text_and_sources(blocks: list) -> tuple[list[str], list[tuple[str, str]], list]:
    texts: list[str] = []
    sources: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    result_blocks: list = []

    def add_src(title: str, url: str) -> None:
        u = (url or "").strip()
        if u and u not in seen_urls:
            seen_urls.add(u)
            sources.append(((title or "").strip(), u))

    for b in blocks:
        btype = _g(b, "type", "")
        if btype == "web_search_tool_result":
            result_blocks.append(b)
            content = _g(b, "content", None)
            if isinstance(content, list):
                for item in content:
                    if _g(item, "type", "") == "web_search_result":
                        add_src(str(_g(item, "title", "")), str(_g(item, "url", "")))
        elif btype == "text":
            txt = str(_g(b, "text", "")).strip()
            if txt:
                texts.append(txt)
            cites = _g(b, "citations", None) or []
            if isinstance(cites, list):
                for c in cites:
                    add_src(str(_g(c, "title", "")), str(_g(c, "url", "")))

    return texts, sources, result_blocks


def search_live(query: str, sentence: str, ctx: ToolContext, fallback_fn) -> tuple[dict, dict]:
    try:
        if ctx.on_progress:
            ctx.on_progress("웹을 검색하는 중이에요")

        user_prompt = (
            f"검색어: {query}\n"
            "이 이름의 소개 한 줄과, 국내에서 열렸거나 열릴 모든 시기(과거 여러 해의 지난 개최들과 최근·예정 개최 포함)의 "
            "행사 이름, 개최 장소(기관·건물 이름), 시작일, 끝일을 빠짐없이 출처와 함께 찾아 주세요. "
            "첫 검색에 최근 행사 하나만 나오면 거기서 멈추지 말고 남은 검색으로 이전 연도들에 국내에서 열린 개최 이력도 추가로 검색하세요. "
            "찾은 행사 가운데 개최 장소 이름이나 시작일·끝일이 비어 있는 것이 있으면 그 행사 이름으로 추가 검색해 장소와 기간을 모두 확인하고, "
            "여러 해나 여러 장소에서 열린 행사는 각각 따로 적어 주세요."
        )
        messages: list[dict] = [{"role": "user", "content": user_prompt}]
        search_recs: list[dict] = []
        all_blocks: list = []

        c = claude.call("search_web", "low", messages, tools=[WEB_SEARCH_TOOL])
        ctx.record_call(c.rec)
        search_recs.append(c.rec)
        all_blocks.extend(c.resp.content or [])

        for _ in range(MAX_PAUSE_CONTINUATIONS):
            if c.stop != "pause_turn":
                break
            messages.append({"role": "assistant", "content": c.resp.content})
            c = claude.call("search_web", "low", messages, tools=[WEB_SEARCH_TOOL])
            ctx.record_call(c.rec)
            search_recs.append(c.rec)
            all_blocks.extend(c.resp.content or [])

        total_reqs = sum(int(r.get("web_search_requests") or 0) for r in search_recs)
        if total_reqs <= 0:
            raise _WebSearchFailed("web_search_requests=0")

        texts, sources, result_blocks = _collect_text_and_sources(all_blocks)
        if result_blocks and all(_is_error_result_block(b) for b in result_blocks):
            raise _WebSearchFailed("모든 웹 검색 결과가 오류 객체임")
        if not texts and not sources:
            raise _WebSearchFailed("모은 검색 글과 출처가 비어 있음")

        if ctx.on_progress:
            ctx.on_progress("검색 결과를 정리하는 중이에요")

        parts = [f"검색어: {query}"]
        if texts:
            parts.append("검색 내용:\n" + "\n\n".join(texts))
        if sources:
            src_lines = [f"- {t}: {u}" if t else f"- {u}" for t, u in sources]
            parts.append("출처 주소:\n" + "\n".join(src_lines))
        parts.append(
            "위 내용만 바탕으로 소개 한 줄과 국내에서 열렸거나 열릴 모든 행사의 이름, 장소, 시작일, 끝일, 출처 주소를 정리해 주세요."
        )

        c2 = claude.call(
            "search_web_extract",
            "low",
            [{"role": "user", "content": "\n\n".join(parts)}],
            system=EXTRACT_SYSTEM,
            output_format=WebExtractOut,
        )
        ctx.record_call(c2.rec)
        parsed = c2.parsed
        if parsed is None:
            raise _WebSearchFailed(f"정리 응답 없음(stop_reason={c2.stop})")

        venue_coords = _load_venue_coords()
        today_s = ctx.today.isoformat()
        evs: list[dict] = []
        for e in _g(parsed, "events", []) or []:
            name = str(_g(e, "name", "") or "").strip()
            venue = str(_g(e, "venue", "") or "").strip()
            start = str(_g(e, "start", "") or "").strip()
            end = str(_g(e, "end", "") or "").strip()
            url = str(_g(e, "url", "") or "").strip()
            end_s = end or start
            status = "upcoming" if end_s and end_s > today_s and start > today_s else "past"
            ev_out: dict = {
                "name": name,
                "venue": venue,
                "start": start,
                "end": end,
                "status": status,
                "url": url,
            }
            matched_coord = _match_venue_coord(venue, venue_coords)
            if matched_coord is not None:
                ev_out["lat"], ev_out["lon"] = matched_coord
            evs.append(ev_out)

        summary = str(_g(parsed, "summary", "") or "").strip()
        raw_found = _g(parsed, "found", None)
        found = bool(raw_found or summary or evs) if raw_found is not None else bool(summary or evs)
        res = {"query": query, "found": found, "summary": summary, "events": evs, "cached": False}
        return res, {"card": "web", "sentence": sentence, "data": res, "cached": False}
    except (claude.CostCapExceeded, claude.RateLimitTripped):
        raise
    except Exception as e:
        print(f"[search_web] 실제 검색 실패로 웹 캐시를 씁니다: {type(e).__name__}: {e}", file=sys.stderr)
        return fallback_fn(query, sentence, ctx)

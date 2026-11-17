"""에이전트 루프가 쓰는 도구 여섯 가지(plan_query, search_web, ask_user, find_candidates, judge_photos, show_answer).

규칙(docs/ARCHITECTURE.md '도구', 'Claude 호출 규칙'):
- 모든 도구 정의는 strict=True 와 additionalProperties=False(anthropic.transform_schema)를 쓴다.
- received_at, saved_at, downloaded_at, origin 과 사진의 위도·경도(lat, lon)는 도구 결과에도,
  도구 안의 Claude 호출에도 넣지 않는다.
- 설명에서 고르기(find_candidates 의 about)는 전체 설명 블록에 1시간 캐시를 걸고 low 로 부른다.
- 사진 다시 보기(judge_photos)는 최대 48장까지 받고, 8장씩 나눠 medium 으로 부른다.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Literal

import anthropic
from pydantic import BaseModel, Field

from app import claude
from app import index as idx_mod
from app.config import settings
from pipeline.common import image_block, prompt

PICK_PROMPT = "pick-v1"
JUDGE_PROMPT = "judge-v1"
MAX_JUDGE_IDS = 48
JUDGE_BATCH = 8
JUDGE_WORKERS = 2
DEFAULT_LIMIT = 48
DEFAULT_RADIUS_M = 1000.0


# ---------------------------------------------------------------- 도구 입력 스키마
class PlanQueryIn(BaseModel):
    target: str = Field(description="찾으려는 사진·문서 대상")
    anchor: str = Field(description="기준이 되는 사건·장소. 없으면 빈 문자열")
    relation: Literal["before", "after", "same_day", "none"] = Field(description="대상과 기준의 시간 관계")
    known: list[str] = Field(description="질문에서 이미 아는 단서 목록")
    unknown: list[str] = Field(description="도구로 확인해야 할 단서 목록")
    sentence: str = Field(description="화면 카드에 보일 한국어 한 문장")


class SearchWebIn(BaseModel):
    query: str = Field(description="찾을 고유명사나 행사 이름")
    sentence: str = Field(description="화면 카드에 보일 한국어 한 문장")


class AskOption(BaseModel):
    id: str = Field(description="선택지 식별자(예: opt_1)")
    label: str = Field(description="버튼에 보일 짧은 이름")
    detail: str = Field(description="기간·장소 등 보조 설명. 없으면 빈 문자열")


class AskUserIn(BaseModel):
    question: str = Field(description="사용자에게 물을 한국어 질문")
    options: list[AskOption] = Field(description="선택지 목록(2개 이상)")


class NearFilter(BaseModel):
    lat: float
    lon: float
    radius_m: float | None = Field(description="반경(미터). 기본값(1000)을 쓰려면 null")


class FindCandidatesIn(BaseModel):
    date_from: str | None = Field(description="시작 날짜 YYYY-MM-DD(포함). 제한하지 않을 때는 null")
    date_to: str | None = Field(description="끝 날짜 YYYY-MM-DD(포함). 제한하지 않을 때는 null")
    after: str | None = Field(description="이 시각 뒤에 찍힌 사진만(초까지 ISO 8601, 경계 시각 제외). 제한하지 않을 때는 null")
    before: str | None = Field(description="이 시각 앞에 찍힌 사진만(초까지 ISO 8601, 경계 시각 제외). 제한하지 않을 때는 null")
    near: NearFilter | None = Field(description="웹 검색 결과의 행사 좌표 근처로 거를 때만 객체로 적고, 아니면 null")
    area: str | None = Field(description="찍은 곳 지명(지역·명소 이름)이나 묶음·방문 코드로 거를 때 적고, 아니면 null")
    text: str | None = Field(description="사진 속 글자나 설명에 들어 있는 정확한 낱말로 거를 때 적고, 아니면 null")
    about: str | None = Field(description="장면·사물·음식·활동·장소 종류 등 의미로 후보를 고를 때 적고, 아니면 null")
    kinds: list[Literal["photo", "document"]] | None = Field(description="찾을 종류. 전체를 볼 때는 null")
    limit: int | None = Field(description="돌려받을 최대 후보 수. 기본값(48)을 쓰려면 null")
    card: Literal["visit_day", "picked", "none"] = Field(description="화면 카드 종류(기준 확인은 visit_day, 대상 찾기는 picked, 연관 수집은 none)")
    sentence: str = Field(description="화면 카드에 보일 한국어 한 문장")


class JudgePhotosIn(BaseModel):
    ids: list[str] = Field(description="다시 볼 사진 id 목록(한 번에 최대 48장)")
    conditions: list[str] = Field(description="사진마다 확인할 조건 목록")
    card: Literal["visit_day", "picked", "none"] = Field(description="화면 카드 종류")
    sentence: str = Field(description="화면 카드에 보일 한국어 한 문장")


class RelatedGroup(BaseModel):
    label: str = Field(description="연관 사진 버튼 이름")
    ids: list[str] = Field(description="연관 사진 id 목록")


class ShowAnswerIn(BaseModel):
    sentence: str = Field(description="결과 요약 한국어 한 문장")
    ids: list[str] = Field(description="찾은 정답 사진 id 목록")
    related: list[RelatedGroup] = Field(description="함께 볼 연관 사진 묶음 목록. 없으면 빈 목록")


# ---------------------------------------------------------------- 내부 구조화 출력 스키마
class _PickedIds(BaseModel):
    ids: list[str]


class _PhotoJudgment(BaseModel):
    index: int
    condition_results: list[bool]
    verdict: Literal["match", "same_visit", "no"]
    reason: str
    read_text: str


class _BatchJudgments(BaseModel):
    items: list[_PhotoJudgment]


def tool_specs() -> list[dict]:
    defs = [
        ("plan_query", "질의를 찾을 대상, 기준 사건, 시간 관계(before/after/same_day/none), 아는 것, 모르는 것으로 정리한다. 맨 먼저 부른다.", PlanQueryIn),
        ("search_web", "모르는 고유명사나 외부 행사의 기간·장소를 찾는다.", SearchWebIn),
        ("ask_user", "실제 후보가 둘 이상이라 사용자의 선택이 꼭 필요할 때 선택지를 보이고 멈춘다.", AskUserIn),
        ("find_candidates", "날짜(date_from/date_to), 시각 경계(after/before), 찍은 곳 지명(area), 글자(text), 장면 의미(about)로 후보를 좁힌다.", FindCandidatesIn),
        ("judge_photos", "남은 후보 사진(최대 48장)을 실제로 다시 보고 조건에 맞는지 정밀 판정한다.", JudgePhotosIn),
        ("show_answer", "최종 답 사진 목록과 연관 사진 묶음을 화면에 낸다. 마지막에 한 번 부른다.", ShowAnswerIn),
    ]
    return [
        {"name": name, "description": desc, "strict": True, "input_schema": anthropic.transform_schema(model)}
        for name, desc, model in defs
    ]


# ---------------------------------------------------------------- 실행 문맥과 보조 함수
@dataclass
class ToolContext:
    today: date
    seen_ids: set[str] = field(default_factory=set)
    last_distances: dict[str, int] = field(default_factory=dict)
    on_call: Callable[[dict], None] | None = None
    on_progress: Callable[[str], None] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record_call(self, rec: dict) -> None:
        if self.on_call:
            with self._lock:
                self.on_call(rec)

    def mark_seen(self, ids: list[str]) -> None:
        with self._lock:
            self.seen_ids.update(ids)


def _default_tzinfo() -> timezone:
    off = settings.default_tz
    sign = 1 if off[0] == "+" else -1
    h, m = off[1:].split(":")
    return timezone(sign * timedelta(hours=int(h), minutes=int(m)))


def _parse_bound(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = idx_mod.parse_time(s.strip())
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_default_tzinfo())
    return dt


def _norm(s: str) -> str:
    return (s or "").lower().replace(" ", "")


def _match_area(row: dict, q: str) -> bool:
    nq = _norm(q)
    if not nq:
        return True
    fields = [row.get("area_ko"), row.get("area_en"), row.get("near_ko"), row.get("near_en"),
              row.get("place_group"), row.get("visit")]
    return any(nq in _norm(str(f)) for f in fields if f)


def _match_text(row: dict, q: str) -> bool:
    raw = (q or "").strip().lower()
    if not raw:
        return True
    hay = "\n".join(str(row.get(k) or "") for k in ("text_in_photo", "scene", "place", "area_ko", "area_en",
                                                    "near_ko", "near_en", "title", "body")).lower()
    if raw in hay or _norm(raw) in _norm(hay):
        return True
    toks = [t for t in raw.split() if t]
    return len(toks) > 1 and all(t in hay or _norm(t) in _norm(hay) for t in toks)


def _catalog_block(rows_by_id: dict[str, dict]) -> str:
    """설명에서 고르기용 고정 블록. 좌표와 유입·수신 시각은 넣지 않는다."""
    lines = []
    for pid in sorted(rows_by_id):
        r = rows_by_id[pid]
        if r.get("kind") == "photo":
            where = idx_mod.place_label(r)
            head = " · ".join(x for x in (pid, where, r.get("place") or "", r.get("photo_type") or "") if x)
            txt = (r.get("text_in_photo") or "").replace("\n", " / ").strip()
            if len(txt) > 300:
                txt = txt[:300] + "…"
            body = r.get("scene") or ""
            if txt:
                body = f"{body} [글자: {txt}]"
            lines.append(f"[{head}] {body}")
        else:
            body = (r.get("body") or "")[:300].replace("\n", " ")
            lines.append(f"[{pid} · document · {r.get('title') or ''}] {body}")
    return "\n".join(lines)


def _candidate_summary(r: dict, dist_m: int | None = None) -> dict:
    """루프에 돌려줄 후보 한 줄 요약. 좌표와 수신·저장·다운로드 시각은 넣지 않는다."""
    if r.get("kind") == "document":
        return {"id": r["id"], "kind": "document", "badge": r.get("badge", ""), "title": r.get("title", ""),
                "written": r.get("written"), "updated": r.get("updated")}
    txt = (r.get("text_in_photo") or "").strip()
    if len(txt) > 300:
        txt = txt[:300] + "…"
    out = {
        "id": r["id"],
        "kind": "photo",
        "time": r.get("time"),
        "time_basis": r.get("time_basis"),
        "place_label": idx_mod.place_label(r),
        "place_group": r.get("place_group") or None,
        "visit": r.get("visit") or None,
        "badge": r.get("badge", ""),
        "place": r.get("place", ""),
        "photo_type": r.get("photo_type", "scene"),
        "scene": r.get("scene", ""),
        "text_in_photo": txt,
    }
    if dist_m is not None:
        out["distance_m"] = dist_m
    return out


# ---------------------------------------------------------------- 도구 실행
def run_plan_query(raw: dict, _ctx: ToolContext) -> tuple[dict, dict]:
    inp = PlanQueryIn.model_validate(raw)
    data = {"target": inp.target, "anchor": inp.anchor, "relation": inp.relation,
            "known": inp.known, "unknown": inp.unknown}
    return data, {"card": "plan", "sentence": inp.sentence, "data": data}


def _search_web_from_cache(query: str, sentence: str, ctx: ToolContext) -> tuple[dict, dict]:
    q = _norm(query)
    items = []
    if settings.web_cache.is_file():
        try:
            items = json.loads(settings.web_cache.read_text(encoding="utf-8"))
            if isinstance(items, dict):
                items = items.get("items") or []
        except ValueError:
            items = []
    hit = None
    for it in items:
        aliases = it.get("aliases") or []
        if any(_norm(a) and (_norm(a) in q or q in _norm(a)) for a in aliases):
            hit = it
            break
    today_s = ctx.today.isoformat()
    if not hit:
        res = {"query": query, "found": False, "summary": "", "events": [], "cached": True}
    else:
        evs = []
        for e in hit.get("events") or []:
            end_s = e.get("end") or e.get("start") or ""
            status = "upcoming" if end_s and end_s > today_s and (e.get("start") or "") > today_s else "past"
            ev_out = {"name": e.get("name", ""), "venue": e.get("venue", ""),
                      "start": e.get("start", ""), "end": e.get("end", ""),
                      "status": e.get("status") or status, "url": e.get("url", "")}
            if e.get("lat") is not None and e.get("lon") is not None:
                ev_out["lat"] = e["lat"]
                ev_out["lon"] = e["lon"]
            evs.append(ev_out)
        res = {"query": query, "found": True, "summary": hit.get("summary", ""), "events": evs, "cached": True}
    return res, {"card": "web", "sentence": sentence, "data": res, "cached": True}


def run_search_web(raw: dict, ctx: ToolContext) -> tuple[dict, dict]:
    inp = SearchWebIn.model_validate(raw)
    if settings.web_search == "live":
        from app import websearch

        return websearch.search_live(inp.query, inp.sentence, ctx, _search_web_from_cache)
    return _search_web_from_cache(inp.query, inp.sentence, ctx)


def run_find_candidates(raw: dict, ctx: ToolContext) -> tuple[dict, dict]:
    nullable_defaults = {
        k: None
        for k in ("date_from", "date_to", "after", "before", "near", "area", "text", "about", "kinds", "limit")
    }
    merged = {**nullable_defaults, **raw}
    if isinstance(merged.get("near"), dict) and "radius_m" not in merged["near"]:
        merged["near"] = {"radius_m": None, **merged["near"]}
    inp = FindCandidatesIn.model_validate(merged)
    idx = idx_mod.load()
    limit = max(1, min(inp.limit or DEFAULT_LIMIT, 200))

    after_dt = _parse_bound(inp.after)
    if inp.after and after_dt is None:
        raise ValueError(f"after 시각 형식이 올바르지 않습니다: {inp.after}")
    before_dt = _parse_bound(inp.before)
    if inp.before and before_dt is None:
        raise ValueError(f"before 시각 형식이 올바르지 않습니다: {inp.before}")

    # 날짜별 전체 사진 장수(집계용)
    day_totals: dict[str, int] = {}
    for r in idx.values():
        if r.get("kind") == "photo" and r.get("time"):
            d = r["time"][:10]
            day_totals[d] = day_totals.get(d, 0) + 1

    kinds = set(inp.kinds) if inp.kinds else {"photo", "document"}
    matched: list[dict] = []
    distances: dict[str, int] = {}

    for r in idx.values():
        if r.get("kind") not in kinds:
            continue
        if inp.date_from or inp.date_to or after_dt or before_dt:
            if r.get("kind") == "photo":
                t_str = r.get("time")
                if not t_str:
                    continue
                d_str = t_str[:10]
                if inp.date_from and d_str < inp.date_from:
                    continue
                if inp.date_to and d_str > inp.date_to:
                    continue
                if after_dt or before_dt:
                    pt = _parse_bound(t_str)
                    if pt is None:
                        continue
                    if after_dt and not (pt > after_dt):
                        continue
                    if before_dt and not (pt < before_dt):
                        continue
            else:
                w_str = r.get("written") or ""
                if (inp.date_from and (not w_str or w_str < inp.date_from)) or (
                    inp.date_to and (not w_str or w_str > inp.date_to)
                ):
                    continue
                if after_dt or before_dt:
                    continue
        if inp.near is not None:
            if r.get("lat") is None or r.get("lon") is None:
                continue
            rad = inp.near.radius_m if inp.near.radius_m is not None else DEFAULT_RADIUS_M
            dm = idx_mod.haversine_m(inp.near.lat, inp.near.lon, r["lat"], r["lon"])
            if dm > rad:
                continue
            distances[r["id"]] = round(dm)
        if inp.area and not _match_area(r, inp.area):
            continue
        if inp.text and not _match_text(r, inp.text):
            continue
        matched.append(r)

    matched.sort(key=lambda r: (r.get("time") or r.get("written") or "", r["id"]))

    if inp.about and inp.about.strip() and matched:
        if ctx.on_progress:
            ctx.on_progress("설명에서 후보를 고르는 중이에요")
        cand_ids = [r["id"] for r in matched]
        cand_set = set(cand_ids)
        cat_text = _catalog_block(idx)
        query_block = (
            f"후보 id 목록 ({len(cand_ids)}개, 이 안에서만 고른다):\n"
            + ", ".join(cand_ids)
            + f"\n\n찾는 조건: {inp.about.strip()}\n"
            + f"관련도가 높은 순서로 최대 {limit}개까지 id를 고르세요."
        )
        c = claude.call(
            "pick_candidates",
            "low",
            [{"role": "user", "content": [
                {"type": "text", "text": cat_text, "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                {"type": "text", "text": query_block},
            ]}],
            system=prompt(PICK_PROMPT),
            output_format=_PickedIds,
        )
        ctx.record_call(c.rec)
        picked = [i for i in (c.parsed.ids if c.parsed else []) if i in cand_set]
        by_id = {r["id"]: r for r in matched}
        matched = [by_id[i] for i in dict.fromkeys(picked)]

    by_date_map: dict[str, list[dict]] = {}
    for r in matched:
        if r.get("kind") == "photo" and r.get("time"):
            by_date_map.setdefault(r["time"][:10], []).append(r)
    by_date = []
    for d in sorted(by_date_map):
        xs = by_date_map[d]
        areas = sorted({idx_mod.place_label(x) for x in xs if idx_mod.place_label(x)})
        by_date.append({"date": d, "matched": len(xs), "day_total": day_totals.get(d, len(xs)), "places": areas})

    shown = matched[:limit]
    ctx.mark_seen([r["id"] for r in shown])
    if distances:
        ctx.last_distances.update(distances)

    res: dict = {
        "matched_count": len(matched),
        "returned_count": len(shown),
        "by_date": by_date,
        "candidates": [_candidate_summary(r, distances.get(r["id"])) for r in shown],
    }
    if inp.date_from and inp.date_to and inp.date_from == inp.date_to:
        day_all = sorted(
            (r for r in idx.values() if r.get("kind") == "photo" and (r.get("time") or "")[:10] == inp.date_from),
            key=lambda r: (r.get("time") or "", r["id"]),
        )
        day_ids = [r["id"] for r in day_all]
        ctx.mark_seen(day_ids)
        res["day_photo_ids"] = day_ids

    ev_dates = [x["date"] for x in by_date]
    from_count = None
    if len(ev_dates) == 1:
        from_count = day_totals.get(ev_dates[0], len(matched))
    elif inp.date_from and inp.date_to and inp.date_from == inp.date_to:
        from_count = day_totals.get(inp.date_from, 0)
    meta = {
        "card": inp.card,
        "sentence": inp.sentence,
        "evidence": {
            "date": ev_dates[0] if len(ev_dates) == 1 else (ev_dates if 1 < len(ev_dates) <= 3 else None),
            "from_count": from_count,
            "cutoff": inp.after,
        },
    }
    return res, meta


def _judge_batch(batch: list[dict], conditions: list[str], ctx: ToolContext) -> list[dict]:
    content: list[dict] = []
    for k, r in enumerate(batch, 1):
        bits = [f"사진 {k}", f"찍은 시각: {r.get('time') or '모름'}"]
        pl = idx_mod.place_label(r)
        if pl:
            bits.append(f"찍은 곳: {pl}")
        if r.get("visit"):
            bits.append(f"방문 묶음: {r['visit']}")
        if r["id"] in ctx.last_distances:
            bits.append(f"기준 지점 거리: 약 {ctx.last_distances[r['id']]}m")
        which = "model_copy" if r.get("model_copy") and (settings.var_dir / r["model_copy"]).is_file() else "screen_copy"
        content.append({"type": "text", "text": " · ".join(bits)})
        content.append(image_block(r, which))
    cond_lines = "\n".join(f"{i}. {c}" for i, c in enumerate(conditions, 1))
    content.append({"type": "text", "text": f"확인할 조건:\n{cond_lines}\n\n사진 1부터 {len(batch)}까지 순서대로 판정하세요."})
    c = claude.call("judge_photos", "medium", [{"role": "user", "content": content}],
                    system=prompt(JUDGE_PROMPT), output_format=_BatchJudgments)
    ctx.record_call(c.rec)
    by_idx = {it.index: it for it in (c.parsed.items if c.parsed else [])}
    out = []
    for k, r in enumerate(batch, 1):
        j = by_idx.get(k)
        out.append({
            "id": r["id"],
            "time": r.get("time"),
            "place_label": idx_mod.place_label(r),
            "visit": r.get("visit") or None,
            "condition_results": j.condition_results if j else [False] * len(conditions),
            "verdict": j.verdict if j else "no",
            "reason": j.reason if j else "판정 결과 없음",
            "read_text": (j.read_text or "").strip() if j else "",
        })
    return out


def run_judge_photos(raw: dict, ctx: ToolContext) -> tuple[dict, dict]:
    inp = JudgePhotosIn.model_validate(raw)
    if not inp.ids:
        raise ValueError("ids 가 비어 있습니다.")
    if len(inp.ids) > MAX_JUDGE_IDS:
        raise ValueError(f"한 번에 최대 {MAX_JUDGE_IDS}장까지만 볼 수 있습니다(요청 {len(inp.ids)}장).")
    idx = idx_mod.load()
    ordered_ids = idx_mod.by_time(inp.ids)
    missing = [i for i in inp.ids if i not in idx or idx[i].get("kind") != "photo"]
    if missing:
        raise ValueError(f"색인에 없는 사진 id 입니다: {missing[:5]}")

    if ctx.on_progress:
        ctx.on_progress(f"후보 사진 {len(ordered_ids)}장을 다시 확인하는 중이에요")

    rows = [idx[i] for i in ordered_ids]
    batches = [rows[i:i + JUDGE_BATCH] for i in range(0, len(rows), JUDGE_BATCH)]
    judged: list[dict] = []
    if len(batches) == 1:
        judged = _judge_batch(batches[0], inp.conditions, ctx)
    else:
        with ThreadPoolExecutor(max_workers=min(JUDGE_WORKERS, len(batches))) as ex:
            for part in ex.map(lambda b: _judge_batch(b, inp.conditions, ctx), batches):
                judged.extend(part)

    ctx.mark_seen(ordered_ids)
    match_ids = [x["id"] for x in judged if x["verdict"] == "match"]
    same_visit_ids = [x["id"] for x in judged if x["verdict"] == "same_visit"]
    accepted = [x for x in judged if x["verdict"] in ("match", "same_visit")]
    # 카드 표시용: 직접 맞은 사진이 있으면 그 장수를 우선하되 같은 방문 사진도 함께 반영 가능
    ev_hits = [x for x in judged if x["verdict"] == "match"] or accepted
    res = {
        "judged_count": len(judged),
        "match_ids": match_ids,
        "same_visit_ids": same_visit_ids,
        "items": judged,
    }
    meta = {
        "card": inp.card,
        "sentence": inp.sentence,
        "evidence": {
            "to_count": len(ev_hits),
            "photo_ids": [x["id"] for x in ev_hits][:8],
            "read_text": [x["read_text"] for x in accepted if x.get("read_text")][:6],
        },
    }
    return res, meta


def run_show_answer(raw: dict, ctx: ToolContext) -> tuple[dict, dict]:
    inp = ShowAnswerIn.model_validate(raw)
    idx = idx_mod.load()
    bad = [i for i in inp.ids if i not in idx]
    unseen = [i for i in inp.ids if i not in ctx.seen_ids]
    for g in inp.related:
        bad.extend(i for i in g.ids if i not in idx)
        unseen.extend(i for i in g.ids if i not in ctx.seen_ids)
    if bad:
        raise ValueError(f"색인에 없는 id 가 포함되어 있습니다: {sorted(set(bad))[:5]}")
    if unseen:
        raise ValueError(f"앞선 도구 결과(find_candidates, judge_photos)에 나오지 않았던 id 입니다: {sorted(set(unseen))[:5]}")

    ans_ids = idx_mod.by_time(inp.ids)
    rel_groups = []
    for g in inp.related:
        g_ids = idx_mod.by_time(g.ids)
        if g_ids:
            rel_groups.append({"label": g.label, "ids": g_ids, "items": [idx_mod.item(idx[i]) for i in g_ids]})

    res = {"shown_count": len(ans_ids), "ids": ans_ids,
           "related": [{"label": g["label"], "count": len(g["ids"])} for g in rel_groups]}
    meta = {
        "result_event": {
            "sentence": inp.sentence,
            "ids": ans_ids,
            "items": [idx_mod.item(idx[i]) for i in ans_ids],
            "related": [{"label": g["label"], "items": g["items"]} for g in rel_groups],
        }
    }
    return res, meta

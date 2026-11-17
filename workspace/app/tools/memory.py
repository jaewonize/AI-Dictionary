"""에이전트 루프의 Hindsight 기억 꺼내기(recall_memory)와 기억 되짚기(reflect_memory) 도구."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import TYPE_CHECKING

import anthropic
from pydantic import BaseModel, Field

from app import hindsight
from app import index as idx_mod
from app.config import settings

if TYPE_CHECKING:
    from app.tools import ToolContext

MAX_MEMORY_HITS = 20
MAX_REFLECT_PHOTOS = 30
REFLECT_PROGRESS_S = 15


class RecallMemoryIn(BaseModel):
    query: str = Field(description="꺼낼 때 쓸 질의. 처음에는 사용자의 질문을 그대로 적는다")


class ReflectMemoryIn(BaseModel):
    query: str = Field(description="되짚을 질문. 사용자의 질문을 그대로 적는다")
    known: str = Field(
        description="앞선 결과로 이미 확인한 것(고른 행사의 이름, 장소, 기간 등)을 한두 문장으로 적는다. 좌표는 적지 않는다. 없으면 빈 글"
    )


def tool_spec() -> dict:
    return {
        "name": "recall_memory",
        "description": (
            "Hindsight 기억 저장소에서 질문과 관련된 기억(사진 설명, 하루 타임라인)을 순위대로 꺼낸다. "
            "사진 기록에서 기준이나 대상을 찾기 전에 부른다. 한 질문에 두 번까지 부를 수 있다."
        ),
        "strict": True,
        "input_schema": anthropic.transform_schema(RecallMemoryIn),
    }


def reflect_tool_spec() -> dict:
    return {
        "name": "reflect_memory",
        "description": (
            "Hindsight 기억 저장소가 은행 안의 AI 모델로 기억을 되짚어, 질문에 맞는 사진 id와 근거를 정리해 돌려준다. "
            "몇 분 걸리고 비용이 든다. recall_memory 뒤에 한 질문에 한 번까지 부른다. "
            "돌려받은 사진은 확인 전의 후보이고, find_candidates나 judge_photos로 확인한 사진만 답에 넣는다."
        ),
        "strict": True,
        "input_schema": anthropic.transform_schema(ReflectMemoryIn),
    }


def _photo_date_time(row: dict) -> tuple[str | None, str | None]:
    dt = idx_mod.parse_time(row.get("time"))
    if not dt:
        return None, None
    return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}", f"{dt.hour:02d}:{dt.minute:02d}"


def run_recall_memory(raw: dict, ctx: ToolContext) -> tuple[dict, dict]:
    inp = RecallMemoryIn.model_validate(raw)
    if ctx.on_progress:
        ctx.on_progress("기억 저장소에서 찾는 중이에요")

    t0 = time.perf_counter()
    try:
        res = hindsight.recall(inp.query)
    except hindsight.RecallUnavailable as e:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        reason = str(e)
        bank = settings.hindsight_bank or ""
        return (
            {"ok": False, "error": f"기억 저장소를 쓰지 못했습니다 ({reason})", "memories": []},
            {
                "memory": {
                    "query": inp.query,
                    "bank": bank,
                    "elapsed_ms": elapsed_ms,
                    "count": 0,
                    "hits": [],
                    "error": reason,
                }
            },
        )

    idx = idx_mod.load()
    raw_hits = res.get("hits") or []
    memories: list[dict] = []
    meta_hits: list[dict] = []
    left_out = 0

    for h in raw_hits:
        kind = h.get("kind") or "other"
        sid = h.get("source_id")
        if kind == "photo":
            if not sid or sid not in idx or idx[sid].get("kind") != "photo":
                left_out += 1
                continue
            if len(memories) >= MAX_MEMORY_HITS:
                left_out += 1
                continue
            d_val, t_val = _photo_date_time(idx[sid])
            mem_id = sid
        elif kind == "timeline":
            if len(memories) >= MAX_MEMORY_HITS:
                left_out += 1
                continue
            mem_id = sid
            d_val = sid
            t_val = None
        elif kind == "doc":
            if len(memories) >= MAX_MEMORY_HITS:
                left_out += 1
                continue
            mem_id = sid
            d_val = (idx.get(sid) or {}).get("written") if sid else None
            t_val = None
        else:
            if len(memories) >= MAX_MEMORY_HITS:
                left_out += 1
                continue
            mem_id = None
            d_val = None
            t_val = None

        memories.append({
            "rank": h["rank"],
            "kind": kind,
            "id": mem_id,
            "date": d_val,
            "time": t_val,
            "text": h["text"],
        })
        meta_hits.append({
            "rank": h["rank"],
            "document_id": h.get("document_id"),
            "kind": kind,
            "source_id": sid,
            "text": h["text"],
        })

    count = len(raw_hits)
    out = {
        "ok": True,
        "bank": res["bank"],
        "count": count,
        "shown": len(memories),
        "left_out": left_out,
        "elapsed_ms": res["elapsed_ms"],
        "memories": memories,
    }
    meta = {
        "memory": {
            "query": res["query"],
            "bank": res["bank"],
            "elapsed_ms": res["elapsed_ms"],
            "count": count,
            "hits": meta_hits,
            "error": None,
        }
    }
    return out, meta


def run_reflect_memory(raw: dict, ctx: ToolContext) -> tuple[dict, dict]:
    inp = ReflectMemoryIn.model_validate(raw)
    if ctx.on_progress:
        ctx.on_progress("기억을 되짚어 보는 중이에요")

    t0 = time.perf_counter()
    ex = ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(hindsight.reflect, inp.query, inp.known)
    try:
        while True:
            try:
                res = fut.result(timeout=REFLECT_PROGRESS_S)
                break
            except FuturesTimeoutError:
                if ctx.on_progress:
                    sec = int(time.perf_counter() - t0)
                    ctx.on_progress(f"기억을 되짚어 보는 중이에요 ({sec}초)")
    except hindsight.ReflectUnavailable as e:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        reason = str(e)
        bank = settings.hindsight_bank or ""
        return (
            {"ok": False, "error": f"기억 되짚기를 하지 못했어요 ({reason})", "photos": []},
            {
                "reflect": {
                    "query": inp.query,
                    "known": inp.known,
                    "bank": bank,
                    "elapsed_ms": elapsed_ms,
                    "count": 0,
                    "photo_ids": [],
                    "left_out": 0,
                    "reasoning": "",
                    "inner_calls": 0,
                    "inner_queries": [],
                    "usage": e.usage,
                    "error": reason,
                }
            },
        )
    finally:
        ex.shutdown(wait=False)

    idx = idx_mod.load()
    raw_ids = res.get("photo_ids") or []
    photos: list[dict] = []
    left_out = 0
    for pid in raw_ids:
        if pid not in idx or idx[pid].get("kind") != "photo":
            left_out += 1
            continue
        if len(photos) >= MAX_REFLECT_PHOTOS:
            left_out += 1
            continue
        d_val, t_val = _photo_date_time(idx[pid])
        photos.append({"id": pid, "date": d_val, "time": t_val})

    shown_ids = [p["id"] for p in photos]
    count = len(raw_ids)
    out = {
        "ok": True,
        "bank": res["bank"],
        "count": count,
        "shown": len(photos),
        "left_out": left_out,
        "elapsed_ms": res["elapsed_ms"],
        "photos": photos,
        "reasoning": res.get("reasoning") or "",
        "note": "되짚기가 고른 사진은 확인 전의 후보입니다. find_candidates나 judge_photos로 확인한 사진만 답에 넣습니다.",
    }
    meta = {
        "reflect": {
            "query": res["query"],
            "known": res["known"],
            "bank": res["bank"],
            "elapsed_ms": res["elapsed_ms"],
            "count": count,
            "photo_ids": shown_ids,
            "left_out": left_out,
            "reasoning": res.get("reasoning") or "",
            "inner_calls": res.get("inner_calls") or 0,
            "inner_queries": res.get("inner_queries") or [],
            "usage": res.get("usage"),
            "error": None,
        }
    }
    return out, meta

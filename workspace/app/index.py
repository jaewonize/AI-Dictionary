"""색인(var/index.jsonl)을 메모리에 올리고, 도구가 쓰는 작은 계산(시각, 거리, 화면 표시)을 둔다."""

from __future__ import annotations

import json
import math
from datetime import datetime
from functools import lru_cache

from app.config import settings

BASIS_LABEL = {"taken": "", "filename": "파일 이름의 시각", "saved": "저장 시각", "inferred": "추정 시각"}


@lru_cache(maxsize=1)
def load() -> dict[str, dict]:
    p = settings.index
    if not p.is_file():
        return {}
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {r["id"]: r for r in rows}


def reload() -> dict[str, dict]:
    load.cache_clear()
    return load()


def photos() -> list[dict]:
    return [r for r in load().values() if r.get("kind") == "photo"]


def parse_time(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def time_label(r: dict) -> str:
    t = parse_time(r.get("time"))
    if not t:
        return "찍은 시각 모름"
    s = f"{t.year}년 {t.month}월 {t.day}일 {t:%H:%M}"
    extra = BASIS_LABEL.get(r.get("time_basis") or "", "")
    return f"{s} ({extra})" if extra else s


def place_label(r: dict) -> str:
    a = r.get("area_ko") or ""
    if r.get("near_ko"):
        a = f"{a} · {r['near_ko']} 부근" if a else f"{r['near_ko']} 부근"
    return a


def item(r: dict) -> dict:
    return {"id": r["id"], "badge": r.get("badge", ""), "time_label": time_label(r), "place_label": place_label(r)}


def by_time(ids: list[str]) -> list[str]:
    idx = load()
    return sorted(dict.fromkeys(i for i in ids if i in idx), key=lambda i: (idx[i].get("time") or "", i))

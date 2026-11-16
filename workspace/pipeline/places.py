"""찍은 곳에 지명 붙이기: 같은 자리 묶음, 같은 방문 묶음, 사람이 흔히 부르는 지명(data/places.json).

`uv run python -m pipeline.places --geonames var/geonames/ID.txt`

1. 노트북 안에서: 찍은 곳이 150m 안으로 이어진 사진을 같은 자리 묶음(place_group)으로, 같은 날 같은 자리에서
   90분 안쪽 간격으로 이어 찍은 사진을 같은 방문(visit)으로 묶는다.
2. 노트북 안에서: 묶음 가운데점 근처의 지명을 공개 지명 데이터(GeoNames 나라별 파일, 탭으로 나눈 글)에서 찾는다.
3. Claude 한 번: 묶음마다 근처 지명 목록과 거리만 보내 흔히 부르는 지명(한국어·영어)을 받는다. 좌표와 사진은 보내지 않는다.
recall 은 뜻과 낱말로 찾으므로 좌표 숫자 대신 지명을 글로 남겨야 '그 동네에서 찍은 사진'으로 찾을 수 있다.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from app import claude
from app.config import settings
from pipeline.common import catalog_photos, haversine_m, prompt

sys.stdout.reconfigure(encoding="utf-8")

PROMPT_VERSION = "places-v1"
GROUP_LINK_M = 150
VISIT_GAP_MIN = 90
SPOT_CLASSES = {"T", "L", "S", "H", "V"}
SKIP_SPOT_CODES = {"STM", "STMI", "STMX", "STMC", "STMM", "STMIX", "DTCH", "PMPO", "DAM"}
LODGING_CODES = {"HTL", "RSRT", "HSTS", "MTL", "INN"}


class GroupName(BaseModel):
    group: str
    area_en: str
    area_ko: str
    near_en: str
    near_ko: str
    note: str


class GroupNames(BaseModel):
    groups: list[GroupName]


def cluster(photos: list[dict]) -> list[list[dict]]:
    parent = list(range(len(photos)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(photos):
        for j in range(i + 1, len(photos)):
            b = photos[j]
            if haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) <= GROUP_LINK_M:
                parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i, p in enumerate(photos):
        groups[find(i)].append(p)
    out = [sorted(g, key=lambda r: r["time"] or "") for g in groups.values()]
    return sorted(out, key=lambda g: g[0]["time"] or "")


def load_geonames(path: Path, photos: list[dict]) -> list[tuple]:
    lat = [p["lat"] for p in photos]
    lon = [p["lon"] for p in photos]
    pad = 0.06
    bb = (min(lat) - pad, max(lat) + pad, min(lon) - pad, max(lon) + pad)
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) < 15:
                continue
            la, lo = float(c[4]), float(c[5])
            if bb[0] <= la <= bb[1] and bb[2] <= lo <= bb[3]:
                rows.append((c[1], c[6], c[7], la, lo))  # name, class, code, lat, lon
    return rows


def nearby(geo: list[tuple], lat: float, lon: float) -> dict:
    def near(pred, limit, max_m):
        cand = [(round(haversine_m(lat, lon, g[3], g[4])), g) for g in geo if pred(g)]
        cand = sorted(c for c in cand if c[0] <= max_m)[:limit]
        return [{"name": g[0], "kind": g[2], "m": m} for m, g in cand]

    return {
        "towns": near(lambda g: g[1] == "P", 6, 2500),
        "districts": near(lambda g: g[1] == "A" and g[2] in ("ADM3", "ADM4"), 3, 5000),
        "spots": near(lambda g: g[1] in SPOT_CLASSES and g[2] not in LODGING_CODES | SKIP_SPOT_CODES, 6, 1500),
        "lodging": near(lambda g: g[2] in LODGING_CODES, 2, 120),
    }


def visits(group: list[dict], gid: str) -> dict[str, str]:
    out, n = {}, 0
    prev = None
    for r in group:
        t = datetime.fromisoformat(r["time"]) if r["time"] else None
        if prev is None or t is None or prev[0] != r["time"][:10] or (t - prev[1]).total_seconds() > VISIT_GAP_MIN * 60:
            n += 1
        out[r["id"]] = f"{gid}-v{n}"
        prev = (r["time"][:10], t) if t else None
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="찍은 곳에 지명 붙이기")
    ap.add_argument("--geonames", required=True, help="GeoNames 나라별 파일(예: var/geonames/ID.txt)")
    ap.add_argument("--no-claude", action="store_true", help="흔히 부르는 지명 없이 묶음과 근처 지명만 만든다")
    args = ap.parse_args()
    photos = [p for p in catalog_photos() if p.get("lat") is not None]
    groups = cluster(photos)
    geo = load_geonames(Path(args.geonames).resolve(), photos)
    print(f"찍은 곳이 있는 사진 {len(photos)}장 · 같은 자리 묶음 {len(groups)}개 · 근처 지명 후보 {len(geo)}개")
    out_groups, photo_map = [], {}
    for i, g in enumerate(groups, 1):
        gid = f"G{i:02d}"
        clat = sum(p["lat"] for p in g) / len(g)
        clon = sum(p["lon"] for p in g) / len(g)
        spread = max(round(haversine_m(clat, clon, p["lat"], p["lon"])) for p in g)
        days = sorted({(p["time"] or "")[:10] for p in g})
        out_groups.append({"group": gid, "photos": len(g), "days": days, "spread_m": spread,
                           "lat": round(clat, 5), "lon": round(clon, 5), "nearby": nearby(geo, clat, clon)})
        for pid, vid in visits(g, gid).items():
            photo_map[pid] = {"place_group": gid, "visit": vid}
    names = {}
    if not args.no_claude:
        listing = []
        for g in out_groups:
            nb = g["nearby"]
            fmt = lambda xs: ", ".join(f"{x['name']}({x['kind']}, {x['m']}m)" for x in xs) or "없음"  # noqa: E731
            listing.append(f"[{g['group']}] 사진 {g['photos']}장, 묶음 반경 약 {g['spread_m']}m\n"
                           f"  마을·동네: {fmt(nb['towns'])}\n  행정 구역: {fmt(nb['districts'])}\n"
                           f"  명소: {fmt(nb['spots'])}")
        c = claude.call("places", "low", [{"role": "user", "content": "아래 묶음마다 지명을 붙여 주세요.\n\n" + "\n\n".join(listing)}],
                        system=prompt(PROMPT_VERSION), output_format=GroupNames)
        if c.parsed is None:
            sys.exit(f"지명 받기 실패: stop_reason={c.stop}")
        names = {x.group: x.model_dump() for x in c.parsed.groups}
        print(f"Claude 지명 받기 1회 · ${c.rec['cost_usd']:.3f}")
    for g in out_groups:
        n = names.get(g["group"], {})
        for k in ("area_en", "area_ko", "near_en", "near_ko", "note"):
            g[k] = n.get(k, "")
        if not g["area_en"] and g["nearby"]["towns"]:
            g["area_en"] = g["nearby"]["towns"][0]["name"]
    gmap = {g["group"]: g for g in out_groups}
    for pid, m in photo_map.items():
        g = gmap[m["place_group"]]
        m.update({k: g[k] for k in ("area_en", "area_ko", "near_en", "near_ko")})
    out = settings.data_dir / "places.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"prompt_version": PROMPT_VERSION, "model": settings.model, "groups": out_groups,
                               "photos": photo_map}, ensure_ascii=False, indent=1), encoding="utf-8")
    for g in out_groups:
        print(f"  {g['group']} {g['photos']:3d}장 {','.join(d[5:] for d in g['days'])}  {g['area_ko']}({g['area_en']})"
              f"{' · ' + g['near_ko'] if g['near_ko'] else ''}")
    print(f"-> {out}")


if __name__ == "__main__":
    main()

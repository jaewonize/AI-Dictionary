"""받은 사진 연계: 다른 사람이 보낸 사진(카카오톡 등)을 사용자가 찍은 사진과 이어 설명한다(data/links.jsonl).

`uv run python -m pipeline.link`                      # 받은 사진 전부(찍은 시각과 찍은 곳이 있으면 그것으로)
`uv run python -m pipeline.link --visual-test N`      # 메타데이터를 가리고 그림 비교만으로 N장 시험, 메타데이터 결과와 견준다

- 메타데이터 경로: 받은 사진의 찍은 시각·찍은 곳으로 ±45분, 300m 안의 사용자 사진을 코드가 찾는다.
- 그림 경로(찍은 시각·찍은 곳이 없는 받은 사진): 받은 시각 이전 72시간의 사용자 사진에서 설명이 비슷한 후보를
  Claude가 글로 고르고(low), 받은 사진과 후보 사진을 함께 보여 같은 곳인지 판정한다(medium). 찍은 시각은 이어진
  사진의 시각으로 추정하고 '추정'이라고 남긴다.
- 그 뒤 Claude가 두 쪽 설명으로 연계 설명(link_note)을 쓴다. 시각 차이와 거리는 코드가 계산한 값만 넘긴다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from app import claude
from app.config import settings
from pipeline.common import append_jsonl, catalog_photos, haversine_m, image_block, latest_by_id, prompt, read_jsonl

sys.stdout.reconfigure(encoding="utf-8")

PROMPT_VERSION = "link-v1"
MATCH_PROMPT = "link-match-v1"
NEAR_MIN, NEAR_M, MAX_LINKED = 45, 300, 6
POOL_HOURS, MAX_VISUAL = 72, 8
RECEIVED = {"kakao_photo", "kakao_download"}


class LinkNote(BaseModel):
    link_note: str
    same_scene: bool
    shared: list[str]


class Picked(BaseModel):
    ids: list[str]


class Verdict(BaseModel):
    candidate: int
    verdict: Literal["same_moment", "same_place", "different"]
    reason: str


class Verdicts(BaseModel):
    verdicts: list[Verdict]


def _t(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


def first_sentences(s: str, n: int = 2) -> str:
    parts = [p.strip() for p in s.replace("\n", " ").split(". ") if p.strip()]
    return ". ".join(parts[:n]).rstrip(".") + "."


def rel_time(a: datetime, b: datetime) -> str:
    mins = round((b - a).total_seconds() / 60)
    if a.date() != b.date():
        days = (b.date() - a.date()).days
        return f"{abs(days)}일 {'뒤' if days > 0 else '전'}"
    if abs(mins) < 2:
        return "거의 같은 시각"
    return f"같은 날 약 {abs(mins)}분 {'뒤' if mins > 0 else '전'}"


def desc_text(d: dict) -> str:
    s = f"장소 종류: {d.get('place', '')}\n{d.get('scene', '')}"
    if (d.get("text_in_photo") or "").strip():
        s += "\n사진 속 글자: " + " / ".join(d["text_in_photo"].split("\n"))[:300]
    return s


def by_metadata(k: dict, own: list[dict]) -> tuple[list[dict], str]:
    kt = _t(k["time"])
    near = []
    for o in own:
        ot = _t(o["time"])
        if not ot:
            continue
        dt = abs((ot - kt).total_seconds()) / 60
        dm = haversine_m(k["lat"], k["lon"], o["lat"], o["lon"])
        if dt <= NEAR_MIN and dm <= NEAR_M:
            near.append((dt, dm, o))
    near.sort(key=lambda x: (x[0], x[1]))
    return [{"photo": o, "minutes": round(dt), "m": round(dm)} for dt, dm, o in near[:MAX_LINKED]], "metadata"


def by_visual(k: dict, own: list[dict], desc: dict) -> tuple[list[dict], str, dict]:
    rt = _t(k.get("received_at"))
    pool = [o for o in own if o["id"] in desc and o.get("time")]
    if rt:
        pool = [o for o in pool if rt - timedelta(hours=POOL_HOURS) <= _t(o["time"]) <= rt]
    listing = "\n".join(f"- {o['id']} · {desc[o['id']].get('place', '')} · {first_sentences(desc[o['id']].get('scene', ''), 1)}"
                        for o in pool)
    c1 = claude.call("link_pick", "low", [{"role": "user", "content":
                     f"받은 사진의 설명:\n{desc_text(desc[k['id']])}\n\n사용자 사진 목록(id · 장소 종류 · 장면 첫 문장):\n{listing}\n\n"
                     f"받은 사진과 같은 장소·같은 때로 보이는 사용자 사진을 가능성이 높은 순서로 최대 {MAX_VISUAL}개 고르세요. id 는 목록에 있는 것만 씁니다."}],
                     system="당신은 사진 설명을 비교해 같은 장소에서 찍은 사진 후보를 고르는 도우미입니다.", output_format=Picked)
    ids = [i for i in (c1.parsed.ids if c1.parsed else []) if i in {o["id"] for o in pool}][:MAX_VISUAL]
    cand = [next(o for o in pool if o["id"] == i) for i in ids]
    trace = {"pool": len(pool), "picked": ids, "verdicts": []}
    if not cand:
        return [], "visual", trace
    content = [{"type": "text", "text": "받은 사진:"}, image_block(k)]
    for i, o in enumerate(cand, 1):
        content += [{"type": "text", "text": f"후보 {i}:"}, image_block(o, "screen_copy")]
    content.append({"type": "text", "text": f"후보 1부터 {len(cand)}까지 각각 받은 사진과 같은 장소·같은 때인지 판정하세요."})
    c2 = claude.call("link_match", "medium", [{"role": "user", "content": content}],
                     system=prompt(MATCH_PROMPT), output_format=Verdicts)
    verdicts = {v.candidate: v for v in (c2.parsed.verdicts if c2.parsed else [])}
    linked = []
    for i, o in enumerate(cand, 1):
        v = verdicts.get(i)
        trace["verdicts"].append({"id": o["id"], "verdict": v.verdict if v else "missing", "reason": v.reason if v else ""})
        if v and v.verdict in ("same_moment", "same_place"):
            linked.append({"photo": o, "verdict": v.verdict})
    return linked, "visual", trace


def write_note(k: dict, linked: list[dict], desc: dict, places: dict, kt: datetime | None) -> LinkNote | None:
    area = places.get(k["id"], {})
    lines = []
    for x in linked:
        o = x["photo"]
        bits = []
        if kt and o.get("time"):
            bits.append(rel_time(kt, _t(o["time"])))
        if "m" in x:
            bits.append(f"약 {x['m']}m 떨어진 곳")
        if "verdict" in x:
            bits.append("그림 비교로 " + ("같은 장소·비슷한 때" if x["verdict"] == "same_moment" else "같은 장소"))
        lines.append(f"- 사용자 사진 {o['id']} ({', '.join(bits)})\n  {first_sentences(desc.get(o['id'], {}).get('scene', ''), 3)}")
    where = f"{area.get('area_ko', '')}" + (f" · {area['near_ko']} 부근" if area.get("near_ko") else "")
    msg = (f"받은 사진({k['badge']}로 받음){' · 찍은 곳: ' + where if where.strip() else ''}\n{desc_text(desc[k['id']])}\n\n"
           f"이어진 사용자 사진:\n" + ("\n".join(lines) if lines else "없음"))
    c = claude.call("link_note", "low", [{"role": "user", "content": msg}], system=prompt(PROMPT_VERSION), output_format=LinkNote)
    return c.parsed


def link_one(k: dict, own: list[dict], desc: dict, places: dict, force_visual: bool = False) -> dict:
    row = {"id": k["id"], "prompt_version": PROMPT_VERSION, "model": settings.model}
    try:
        trace = None
        if k.get("time") and k.get("lat") is not None and not force_visual:
            linked, basis = by_metadata(k, own)
            kt = _t(k["time"])
        else:
            linked, basis, trace = by_visual(k, own, desc)
            times = sorted(_t(x["photo"]["time"]) for x in linked if x["photo"].get("time"))
            kt = times[len(times) // 2] if times else None
            row["inferred_time"] = kt.isoformat() if kt else None
            places = {}  # 찍은 곳을 모르는 사진이므로 지명도 쓰지 않는다
        note = write_note(k, linked, desc, places, kt)
    except (claude.CostCapExceeded, claude.RateLimitTripped):
        raise
    except Exception as e:
        return {**row, "error": f"{type(e).__name__}: {str(e)[:300]}"}
    row.update({"basis": basis, "linked_ids": [x["photo"]["id"] for x in linked],
                "linked": [{k2: v for k2, v in x.items() if k2 != "photo"} | {"id": x["photo"]["id"]} for x in linked],
                "link_note": note.link_note if note else "", "same_scene": note.same_scene if note else None,
                "shared": note.shared if note else []})
    if trace is not None:
        row["visual_trace"] = trace
    return row


def spread_pick(rows: list[dict], truth: dict, n: int) -> list[dict]:
    """시험할 받은 사진을 고른다. 같은 분에 받은 묶음마다 하나씩 돌아가며, 이어진 사진이 많은 것을 먼저 고른다."""
    groups: dict[str, list[dict]] = {}
    for k in sorted(rows, key=lambda r: r.get("received_at") or ""):
        groups.setdefault((k.get("received_at") or "")[:16], []).append(k)
    for g in groups.values():
        g.sort(key=lambda k: -len(truth[k["id"]].get("linked_ids") or []))
    out: list[dict] = []
    while len(out) < n and any(groups.values()):
        for g in groups.values():
            if g and len(out) < n:
                out.append(g.pop(0))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="받은 사진 연계")
    ap.add_argument("--visual-test", type=int, default=0, help="메타데이터를 가리고 그림 비교로 N장 시험")
    args = ap.parse_args()
    photos = catalog_photos()
    desc = latest_by_id(read_jsonl(settings.descriptions))
    pj = settings.data_dir / "places.json"
    places = json.loads(pj.read_text(encoding="utf-8"))["photos"] if pj.is_file() else {}
    received = [p for p in photos if p["source"] in RECEIVED and p["id"] in desc]
    own = [p for p in photos if p["source"] not in RECEIVED and p.get("lat") is not None]
    if args.visual_test:
        truth = {r["id"]: r for r in read_jsonl(settings.links) if not r.get("error")}
        test = spread_pick([k for k in received if k["id"] in truth], truth, args.visual_test)
        print(f"그림 비교 시험 {len(test)}장(메타데이터를 가림)")
        out = []
        for k in test:
            hidden = {**k, "time": None, "lat": None, "lon": None}
            r = link_one(hidden, own, desc, places, force_visual=True)
            t = truth[k["id"]]
            got, want = set(r.get("linked_ids", [])), set(t["linked_ids"])
            it = _t(r.get("inferred_time"))
            err = round(abs((it - _t(k["time"])).total_seconds()) / 60) if it else None
            r["check"] = {"truth_ids": t["linked_ids"], "overlap": len(got & want), "found": len(got),
                          "truth": len(want), "time_error_min": err, "actual_time": k["time"]}
            out.append(r)
            print(f"  {k['id']}: 고른 {len(got)}장 중 메타데이터 결과와 겹침 {len(got & want)}/{len(want)} · 추정 시각 오차 {err}분")
        p = settings.var_dir / "link_visual_test.json"
        p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"-> {p}")
        return
    done = {r["id"] for r in read_jsonl(settings.links) if not r.get("error") and r.get("prompt_version") == PROMPT_VERSION}
    todo = [k for k in received if k["id"] not in done]
    print(f"받은 사진 {len(received)}장 중 {len(todo)}장 연계 · 사용자 사진 {len(own)}장")
    with ThreadPoolExecutor(max_workers=3) as ex:
        for r in ex.map(lambda k: link_one(k, own, desc, places), todo):
            append_jsonl(settings.links, r)
            if r.get("error"):
                print(f"✗ {r['id']} {r['error']}")
            else:
                print(f"  {r['id']} [{r['basis']}] 이어진 사진 {len(r['linked_ids'])}장 · {r['link_note'][:80]}")
    print(f"-> {settings.links}")


if __name__ == "__main__":
    main()

"""색인 만들기: 카탈로그, 사진 설명, 지명, 받은 사진 연계, 문서 본문을 id로 합쳐 var/index.jsonl 을 만든다.

`uv run python -m pipeline.build_index`

받은·저장한·내려받은 시각과 유입 경로는 색인에 남겨 두기만 하고 도구는 후보를 좁히는 데 쓰지 않는다.
"""

from __future__ import annotations

import json
import sys

from app.config import settings
from pipeline.common import latest_by_id, read_jsonl, write_jsonl

sys.stdout.reconfigure(encoding="utf-8")

PHOTO_FIELDS = ("time", "time_basis", "lat", "lon", "width", "height", "screen_copy", "model_copy",
                "received_at", "saved_at", "downloaded_at", "origin")


def main() -> None:
    cat = read_jsonl(settings.catalog)
    if not cat:
        sys.exit("카탈로그가 없다. 먼저 pipeline.collect 를 돌린다")
    desc = latest_by_id(read_jsonl(settings.descriptions))
    pj = settings.data_dir / "places.json"
    places = json.loads(pj.read_text(encoding="utf-8"))["photos"] if pj.is_file() else {}
    links = latest_by_id(read_jsonl(settings.links))
    docs = {r["id"]: r for r in read_jsonl(settings.docs)}
    rows, missing = [], []
    for c in cat:
        base = {"id": c["id"], "kind": c["kind"], "source": c["source"], "badge": c["badge"]}
        if c["kind"] == "photo":
            d = desc.get(c["id"])
            if not d:
                missing.append(c["id"])
            row = {**base, **{k: c.get(k) for k in PHOTO_FIELDS},
                   "place": (d or {}).get("place", ""), "scene": (d or {}).get("scene", ""),
                   "text_in_photo": (d or {}).get("text_in_photo", ""), "photo_type": (d or {}).get("photo_type", "scene")}
            pl = places.get(c["id"], {})
            row.update({k: pl.get(k, "") for k in ("area_ko", "area_en", "near_ko", "near_en", "place_group", "visit")})
            ln = links.get(c["id"])
            if ln:
                row.update({"linked_ids": ln.get("linked_ids", []), "link_note": ln.get("link_note", ""),
                            "link_basis": ln.get("basis")})
                if not row["time"] and ln.get("inferred_time"):
                    row["time"], row["time_basis"] = ln["inferred_time"], "inferred"
            if c["id"] in docs:  # 문서 사진(문서 단계)
                dd = docs[c["id"]]
                row.update({"body": dd.get("body", ""), "written": None, "updated": None,
                            "dates": dd.get("dates", []), "amounts": dd.get("amounts", [])})
            rows.append(row)
        elif c["kind"] == "document" and c["id"] in docs:
            dd = docs[c["id"]]
            rows.append({**base, "title": c.get("title", ""), **{k: dd.get(k) for k in ("body", "written", "updated", "dates", "amounts")}})
    write_jsonl(settings.index, rows)
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"색인 {len(rows)}줄 {kinds} · 연계된 받은 사진 {sum(1 for r in rows if r.get('linked_ids'))}장 -> {settings.index}")
    if missing:
        print(f"주의: 설명이 없는 사진 {len(missing)}장(예: {missing[:3]}). pipeline.describe 를 다시 돌린다")


if __name__ == "__main__":
    main()

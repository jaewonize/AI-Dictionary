"""Hindsight 백업 zip에서 메모리 별자리용 별·낱말·연결과 맞추기 열쇠를 뽑는다."""

from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings

EXCLUDED_SOURCES = frozenset({"갤러리", "캡처", "카카오톡", "다운로드", "카톡 받은 파일"})
FILE_LIKE_EXTS = (".jpg", ".jpeg", ".png", ".heic", ".pdf", ".xlsx", ".docx", ".pptx", ".html")
DIGITS_DASH_RE = re.compile(r"^[\d-]+$")
WS_RE = re.compile(r"\s+")


def split_doc_id(doc_id: str | None) -> tuple[str | None, str | None]:
    if not doc_id or not isinstance(doc_id, str):
        return None, None
    for prefix, kind in (("photo:", "photo"), ("doc:", "doc"), ("timeline:", "timeline")):
        if doc_id.startswith(prefix):
            return kind, doc_id[len(prefix) :]
    return "other", doc_id


def strip_when_suffix(text: str | None) -> str:
    if not text:
        return ""
    return str(text).split(" | When:", 1)[0]


def make_match_key(text: str | None) -> str:
    head = strip_when_suffix(text)
    norm = WS_RE.sub(" ", head).strip()
    return norm[:100]


def make_short_text(text: str | None) -> str:
    head = strip_when_suffix(text).strip()
    return head[:120]


def _default_tz() -> timezone:
    tz_s = settings.default_tz or "+09:00"
    sign = -1 if tz_s.startswith("-") else 1
    parts = tz_s.lstrip("+-").split(":")
    h = int(parts[0]) if parts and parts[0] else 9
    m = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    return timezone(sign * timedelta(hours=h, minutes=m))


def parse_iso_epoch(s: str | None) -> float | None:
    if not s or not isinstance(s, str):
        return None
    raw = s.strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_default_tz())
    return dt.timestamp()


def is_valid_entity(word: str, *, case_lower_user: bool = True) -> bool:
    w = word.strip()
    if not w:
        return False
    if (w.lower() == "user") if case_lower_user else (w == "user"):
        return False
    if w in EXCLUDED_SOURCES:
        return False
    if "/" in w:
        return False
    wl = w.lower()
    if wl.endswith(FILE_LIKE_EXTS):
        return False
    if DIGITS_DASH_RE.match(w):
        return False
    return True


def parse_original_photo_desc(original_text: str) -> tuple[str, str]:
    """옛 백업 문서 본문에서 설명 문단과 사진 속 글자를 뽑는다."""
    marker = "\n사진 속 글자:\n"
    if marker in original_text:
        before, photo_txt = original_text.split(marker, 1)
    else:
        before, photo_txt = original_text, ""
    lines = before.splitlines()
    scene_lines: list[str] = []
    started_meta = False
    for line in lines:
        if line.startswith(("[사진]", "찍은 시각:", "찍은 곳:", "출처:", "장소 종류:")):
            started_meta = True
            continue
        if started_meta or not lines[0].startswith("[사진]"):
            scene_lines.append(line)
    scene = "\n".join(scene_lines).strip()
    return scene, photo_txt.strip()


def read_backup_documents(zip_path: Path) -> tuple[dict, list[dict]]:
    with zipfile.ZipFile(zip_path, "r") as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        doc_names = sorted(
            n for n in zf.namelist() if n.startswith("documents/") and n.endswith(".json")
        )
        docs = [json.loads(zf.read(n).decode("utf-8")) for n in doc_names]
    return manifest, docs


def export_bank(zip_path: Path, *, exclude_label_photo_ids: set[str] | None = None) -> dict:
    """백업 zip에서 별자리 데이터와 통계를 뽑아 돌려준다."""
    manifest, docs = read_backup_documents(zip_path)
    bank_id = manifest.get("source_bank_id") or ""

    raw_stars: list[dict] = []
    word_to_stars: dict[str, list[int]] = {}
    causal_pairs: set[tuple[int, int]] = set()
    causal_skipped = 0
    timeline_days: list[dict] = []
    has_doc_memories = any((d.get("id") or "").startswith("doc:") for d in docs)
    excluded_photos = exclude_label_photo_ids or set()

    for doc in docs:
        doc_id = doc.get("id") or ""
        kind, src_id = split_doc_id(doc_id)
        kind = kind or "other"
        src_id = src_id or ""
        orig_text = doc.get("original_text") or ""
        if kind == "timeline" and src_id:
            photo_cnt = sum(1 for ln in orig_text.splitlines() if ln.startswith("- "))
            timeline_days.append({"date": src_id, "photos": photo_cnt})

        doc_event_date = (doc.get("retain_params") or {}).get("event_date")
        facts = doc.get("facts") or []
        base_idx = len(raw_stars)
        m = len(facts)

        for local_i, fact in enumerate(facts):
            star_idx = base_idx + local_i
            ts_str = fact.get("occurred_start") or doc_event_date or None
            ts_val = parse_iso_epoch(ts_str)
            ts_sec = int(ts_val) if ts_val is not None and ts_val == int(ts_val) else (round(ts_val, 3) if ts_val is not None else None)
            created_epoch = parse_iso_epoch(fact.get("created_at"))
            created_sort = created_epoch if created_epoch is not None else float("inf")

            seen_words: list[str] = []
            seen_set: set[str] = set()
            for ent in fact.get("entities") or []:
                if not isinstance(ent, str):
                    continue
                w = ent.strip()
                if not is_valid_entity(w):
                    continue
                if w not in seen_set:
                    seen_set.add(w)
                    seen_words.append(w)
                    word_to_stars.setdefault(w, []).append(star_idx)

            for rel in fact.get("causal_relations") or []:
                if not isinstance(rel, dict):
                    continue
                tgt = rel.get("target_fact_index")
                if isinstance(tgt, bool) or not isinstance(tgt, int):
                    continue
                if tgt < 0 or tgt >= m:
                    causal_skipped += 1
                elif tgt != local_i:
                    u, v = star_idx, base_idx + tgt
                    causal_pairs.add((u, v) if u < v else (v, u))

            raw_stars.append({
                "idx": star_idx,
                "id": src_id,
                "kind": kind,
                "ts": ts_sec,
                "_created_sort": (0 if created_epoch is not None else 1, created_sort, star_idx),
                "short": make_short_text(fact.get("text")),
                "key": make_match_key(fact.get("text")),
                "_words": seen_words,
            })

    # 저장 순서(order): created_at 오름차순, 같으면 별 번호 순서
    order_sorted = sorted(range(len(raw_stars)), key=lambda i: raw_stars[i]["_created_sort"])
    save_rank = [0] * len(raw_stars)
    for rank, s_idx in enumerate(order_sorted):
        save_rank[s_idx] = rank

    # 전체 낱말 목록(가나다 순서 번호 부여)
    all_words = sorted(word_to_stars.keys())
    word_index = {w: i for i, w in enumerate(all_words)}

    # 레이블 40개: 별이 많이 든 낱말부터, 같으면 가나다 순서
    # (3부 문서 케이스: 백업에 doc: 문서가 있으면 doc: 기억과 문서 사진 기억에서만 나온 낱말 제외)
    label_word_counts: dict[str, int] = {}
    for w, s_list in word_to_stars.items():
        if has_doc_memories:
            allowed = [
                si for si in s_list
                if raw_stars[si]["kind"] != "doc"
                and not (raw_stars[si]["kind"] == "photo" and raw_stars[si]["id"] in excluded_photos)
            ]
            if not allowed:
                continue
            label_word_counts[w] = len(allowed)
        else:
            label_word_counts[w] = len(s_list)

    top_label_words = sorted(label_word_counts.keys(), key=lambda w: (-label_word_counts[w], w))[:40]
    labels = [word_index[w] for w in top_label_words]

    # 4. 시간 연결: 시각 있는 별마다 24시간 미만 다른 별을 시각 차이 작은 순(같으면 별 번호 순) 20개까지
    timed_indices = [s["idx"] for s in raw_stars if s["ts"] is not None]
    time_link_map: dict[tuple[int, int], float] = {}
    for i in timed_indices:
        ti = float(raw_stars[i]["ts"])
        cands: list[tuple[float, int]] = []
        for j in timed_indices:
            if i == j:
                continue
            diff = abs(ti - float(raw_stars[j]["ts"]))
            if diff < 86400.0:
                cands.append((diff, j))
        cands.sort(key=lambda x: (x[0], x[1]))
        for diff, j in cands[:20]:
            pair = (i, j) if i < j else (j, i)
            if pair not in time_link_map:
                w_val = round(max(0.3, 1.0 - (diff / 3600.0) / 24.0), 3)
                time_link_map[pair] = w_val

    time_links = [[u, v, time_link_map[(u, v)]] for u, v in sorted(time_link_map.keys())]

    # 5. 낱말 연결: 낱말마다 별을 시각 순서(시각 없으면 맨 뒤, 같으면 별 번호 순)로 늘어놓고 이웃한 둘씩
    entity_pairs: set[tuple[int, int]] = set()
    for w in all_words:
        s_list = sorted(
            word_to_stars[w],
            key=lambda si: (0 if raw_stars[si]["ts"] is not None else 1, raw_stars[si]["ts"] or 0, si),
        )
        for k in range(len(s_list) - 1):
            u, v = s_list[k], s_list[k + 1]
            if u != v:
                entity_pairs.add((u, v) if u < v else (v, u))
    entity_links = [[u, v] for u, v in sorted(entity_pairs)]

    # 6. 원인 연결
    causal_links = [[u, v] for u, v in sorted(causal_pairs)]

    stars_out = [
        {
            "n": s["idx"],
            "id": s["id"],
            "kind": s["kind"],
            "ts": s["ts"],
            "order": save_rank[s["idx"]],
            "short": s["short"],
            "key": s["key"],
            "words": [word_index[w] for w in s["_words"]],
        }
        for s in raw_stars
    ]

    timeline_days.sort(key=lambda d: d["date"])

    return {
        "bank": bank_id,
        "zip_name": zip_path.name,
        "fact_count_manifest": manifest.get("fact_count"),
        "has_doc_memories": has_doc_memories,
        "stars": stars_out,
        "words": all_words,
        "labels": labels,
        "time_links": time_links,
        "entity_links": entity_links,
        "causal_links": causal_links,
        "causal_skipped": causal_skipped,
        "timeline_days": timeline_days,
        "stats": {
            "memories": len(stars_out),
            "time_links": len(time_links),
            "entity_links": len(entity_links),
            "causal_links": len(causal_links),
            "words": len(all_words),
            "causal_skipped": causal_skipped,
        },
    }

"""브라우저 데모 한 파일(var/demo.html) 빌드 스크립트."""

from __future__ import annotations

import argparse
import base64
import html as html_mod
import io
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from app.config import REPO, WS, settings
from app.events import CARD_TITLES
from demo.export_bank import (
    WS_RE,
    export_bank,
    make_match_key,
    parse_iso_epoch,
    parse_original_photo_desc,
    read_backup_documents,
    split_doc_id,
)
from pipeline.common import latest_by_id, read_jsonl

HERE = Path(__file__).resolve().parent
WEB = WS / "web"
TEMPLATE = HERE / "template.html"
RUNS_DIR = HERE / "runs"
SCENES_DIR = HERE / "scenes"
RECALL_DIR = HERE / "recall"
HINDSIGHT_DIR = WS / "dashboard" / "hindsight"
FONT_FILE = REPO / "fonts" / "PretendardVariable.woff2"

SMALL_EDGE = 160
SMALL_QUALITY = 70
LARGE_EDGE = 640
LARGE_QUALITY = 80
MAX_HTML_BYTES = 20 * 1024 * 1024

BADGE_ORDER = ("갤러리", "캡처", "카카오톡", "다운로드", "카톡 받은 파일")

EVENT_REQUIRED_FIELDS: dict[str, set[str]] = {
    "card": {"no", "kind", "title", "sentence", "evidence", "cached", "data"},
    "ask": {"no", "title", "question", "options"},
    "answer": {"option_id", "label"},
    "result": {"no", "title", "sentence", "items", "related_title", "related"},
    "progress": {"step", "elapsed_ms", "calls", "tokens", "cost_usd"},
    "memory": {"n", "query", "bank", "elapsed_ms", "count", "hits", "error"},
    "reflect": {
        "n",
        "query",
        "known",
        "bank",
        "elapsed_ms",
        "count",
        "photo_ids",
        "left_out",
        "reasoning",
        "inner_calls",
        "inner_queries",
        "usage",
        "error",
    },
    "error": {"message"},
    "done": {"status"},
}

ALLOWED_CARD_KINDS = frozenset({"plan", "web", "visit_day", "picked"})
PREVIEW_CARD_KINDS = frozenset({"docs_found", "contract", "calc"})

DEFAULT_STEPS = [
    {"label": "질문하기", "on": "start"},
    {"label": "찾는 과정 보기", "on": "first_event"},
    {"label": "답 받기", "on": "result"},
]

DEFAULT_NARROW = [
    {"label": "폰 사진", "on": "start", "set": "all"},
    {"label": "근거로 본 사진", "on": {"card": "*", "has": "photo_ids"}, "set": "evidence"},
    {"label": "답에 나온 사진", "on": "result", "set": "result"},
]


def norm_space(s: str | None) -> str:
    return WS_RE.sub(" ", str(s or "")).strip()


def norm_preview_quote(s: str | None) -> str:
    txt = str(s or "")
    txt = txt.replace("|", " ").replace("NaN", " ")
    txt = re.sub(r"-{4,}", " ", txt)
    return WS_RE.sub(" ", txt).strip()


def make_doc_svg_data_uri(doc_id: str, first_line: str = "") -> str:
    ext = Path(doc_id).suffix.lstrip(".").upper() or "DOC"
    fname = Path(doc_id).name
    fname_disp = fname if len(fname) <= 22 else (fname[:20] + "…")
    line_disp = (first_line or "").strip()
    if len(line_disp) > 28:
        line_disp = line_disp[:27] + "…"
    esc_ext = html_mod.escape(ext, quote=True)
    esc_name = html_mod.escape(fname_disp, quote=True)
    esc_line = html_mod.escape(line_disp, quote=True)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 320" width="240" height="320">'
        '<rect width="240" height="320" fill="#eef1f7"/>'
        '<rect x="20" y="16" width="200" height="288" rx="12" fill="#ffffff" stroke="#cfd5e4" stroke-width="2"/>'
        '<rect x="36" y="34" width="64" height="26" rx="6" fill="#2f5bff"/>'
        f'<text x="68" y="52" text-anchor="middle" font-family="sans-serif" font-size="13" font-weight="700" fill="#ffffff">{esc_ext}</text>'
        '<line x1="36" y1="78" x2="204" y2="78" stroke="#e5e9f2" stroke-width="1.5"/>'
        f'<text x="120" y="156" text-anchor="middle" font-family="sans-serif" font-size="13" font-weight="700" fill="#1b2234">{esc_name}</text>'
        '<line x1="36" y1="228" x2="204" y2="228" stroke="#e5e9f2" stroke-width="1.5"/>'
        f'<text x="120" y="256" text-anchor="middle" font-family="sans-serif" font-size="11" fill="#525d78">{esc_line}</text>'
        "</svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def format_ko_time_label(iso_s: str | None) -> str:
    if not iso_s:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_s).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"{dt.year}년 {dt.month}월 {dt.day}일 {dt.hour:02d}:{dt.minute:02d}"


def format_dot_time(iso_s: str | None) -> str:
    if not iso_s:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_s).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return f"{dt.year:04d}.{dt.month:02d}.{dt.day:02d} {dt.hour:02d}:{dt.minute:02d}"


def collect_run_photo_ids(rec: dict) -> list[str]:
    seen: dict[str, None] = {}

    def add(x) -> None:
        pid = x.get("id") if isinstance(x, dict) else x
        if isinstance(pid, str) and pid:
            seen.setdefault(pid, None)

    def scan_events(evs: list[dict]) -> None:
        for ev in evs:
            if not isinstance(ev, dict):
                continue
            t = ev.get("type")
            if t == "card":
                for pid in (ev.get("evidence") or {}).get("photo_ids") or []:
                    add(pid)
            elif t == "result":
                for it in ev.get("items") or []:
                    add(it)
                for grp in ev.get("related") or []:
                    for it in (grp or {}).get("items") or []:
                        add(it)
            elif t == "reflect":
                for pid in ev.get("photo_ids") or []:
                    add(pid)

    scan_events(rec.get("events") or [])
    for br in (rec.get("branches") or {}).values():
        if isinstance(br, dict):
            scan_events(br.get("events") or [])
    return list(seen.keys())


def load_old_backup_quotes(zip_path: Path | None) -> dict[str, str] | None:
    if zip_path is None or not zip_path.is_file():
        return None
    _, docs = read_backup_documents(zip_path)
    quotes: dict[str, str] = {}
    for doc in docs:
        kind, sid = split_doc_id(doc.get("id"))
        if kind == "photo" and sid:
            _, txt = parse_original_photo_desc(doc.get("original_text") or "")
            quotes[sid] = txt
    return quotes


def load_old_backup_descriptions(zip_path: Path) -> dict[str, dict[str, str]]:
    _, docs = read_backup_documents(zip_path)
    out: dict[str, dict[str, str]] = {}
    for doc in docs:
        kind, sid = split_doc_id(doc.get("id"))
        if kind == "photo" and sid:
            scene, txt = parse_original_photo_desc(doc.get("original_text") or "")
            out[sid] = {"scene": scene, "text_in_photo": txt}
    return out


def validate_scene_config(scene: dict, label: str = "") -> None:
    req = {"key", "menu_label", "run", "kind", "doc_photos", "spotlight", "doc_lines", "steps", "narrow"}
    missing = req - set(scene.keys())
    if missing:
        raise ValueError(f"{label} 장면 설정에 필수 칸이 없습니다: {sorted(missing)}")
    if scene["kind"] not in ("photo", "document"):
        raise ValueError(f"{label} 알 수 없는 장면 kind 입니다: {scene['kind']}")
    if not isinstance(scene["steps"], list) or not scene["steps"]:
        raise ValueError(f"{label} steps 목록이 비었습니다")
    if not isinstance(scene["narrow"], list) or not scene["narrow"]:
        raise ValueError(f"{label} narrow 목록이 비었습니다")


def _validate_event_list(
    evs: list[dict],
    *,
    is_branch: bool,
    is_preview: bool,
    label: str,
) -> None:
    if not isinstance(evs, list) or not evs:
        raise ValueError(f"{label}: events 목록이 비었습니다")
    for idx, ev in enumerate(evs, 1):
        if not isinstance(ev, dict):
            raise ValueError(f"{label}: 이벤트 #{idx}가 dict가 아닙니다")
        for base_k in ("type", "run_id", "seq"):
            if base_k not in ev or ev[base_k] is None:
                raise ValueError(f"{label}: 이벤트 #{idx}에 {base_k}가 없습니다")
        if not is_branch and ev["seq"] != idx:
            raise ValueError(f"{label}: 이벤트 seq가 1부터 차례대로 늘지 않습니다 ({ev['seq']} != {idx})")
        if is_branch and int(ev["seq"]) < 100:
            raise ValueError(f"{label}: 갈래 이벤트 seq가 100보다 작습니다 ({ev['seq']})")

        etype = ev["type"]
        if etype not in EVENT_REQUIRED_FIELDS:
            raise ValueError(f"{label}: 알 수 없는 이벤트 type={etype}")
        missing = EVENT_REQUIRED_FIELDS[etype] - set(ev.keys())
        if missing:
            raise ValueError(f"{label}: {etype}(seq={ev['seq']})에 필수 칸이 없습니다: {sorted(missing)}")

        if not is_preview:
            if etype == "card":
                ck = ev["kind"]
                if ck not in ALLOWED_CARD_KINDS:
                    raise ValueError(f"{label}: 허용되지 않은 card kind={ck}")
                if ev["title"] != CARD_TITLES[ck]:
                    raise ValueError(f"{label}: card title 불일치 ({ev['title']} != {CARD_TITLES[ck]})")
            elif etype == "ask":
                if ev["title"] != CARD_TITLES["ask"]:
                    raise ValueError(f"{label}: ask title 불일치 ({ev['title']})")
            elif etype == "result":
                expected_title = CARD_TITLES["result"].format(n=len(ev["items"] or []))
                if ev["title"] != expected_title:
                    raise ValueError(f"{label}: result title 불일치 ({ev['title']} != {expected_title})")
                if ev["related_title"] != CARD_TITLES["related"]:
                    raise ValueError(f"{label}: related_title 불일치 ({ev['related_title']})")
        else:
            if etype == "card" and ev["kind"] not in (ALLOWED_CARD_KINDS | PREVIEW_CARD_KINDS):
                raise ValueError(f"{label}: 설계 미리보기에서 허용되지 않은 card kind={ev['kind']}")


def validate_run_record(
    rec: dict,
    *,
    label: str = "",
    catalog_by_id: dict[str, dict] | None = None,
    old_quote_map: dict[str, str] | None = None,
    doc_text_map: dict[str, str] | None = None,
    scene_doc_lines: dict[str, list[str]] | None = None,
    check_catalog: bool = True,
    check_quotes: bool = True,
) -> list[str]:
    notices: list[str] = []
    if not isinstance(rec, dict):
        raise ValueError(f"{label}: 실행 기록이 dict가 아닙니다")
    demo_meta = rec.get("demo") or {}
    demo_kind = demo_meta.get("kind") if isinstance(demo_meta, dict) else None
    is_script = demo_kind == "대본"
    is_preview = demo_kind == "설계 미리보기"

    evs = rec.get("events") or []
    _validate_event_list(evs, is_branch=False, is_preview=is_preview, label=label)

    branches = rec.get("branches") or {}
    if branches:
        if not isinstance(branches, dict):
            raise ValueError(f"{label}: branches가 dict가 아닙니다")
        for br_key, br_val in branches.items():
            if not isinstance(br_val, dict):
                raise ValueError(f"{label}: 갈래 {br_key}가 dict가 아닙니다")
            _validate_event_list(
                br_val.get("events") or [],
                is_branch=True,
                is_preview=is_preview,
                label=f"{label}[branches.{br_key}]",
            )

    all_evs = list(evs)
    for br_val in branches.values() if isinstance(branches, dict) else []:
        if isinstance(br_val, dict):
            all_evs.extend(br_val.get("events") or [])

    if check_catalog and catalog_by_id is not None:
        for ev in all_evs:
            et = ev["type"]
            if et == "card":
                for pid in (ev.get("evidence") or {}).get("photo_ids") or []:
                    if pid not in catalog_by_id:
                        raise ValueError(f"{label}: 카탈로그에 없는 근거 자료 id={pid}")
            elif et == "result":
                all_items = list(ev.get("items") or [])
                for grp in ev.get("related") or []:
                    all_items.extend((grp or {}).get("items") or [])
                for it in all_items:
                    if not isinstance(it, dict) or not it.get("id"):
                        raise ValueError(f"{label}: 결과 항목에 id가 없습니다")
                    pid = it["id"]
                    if pid not in catalog_by_id:
                        raise ValueError(f"{label}: 카탈로그에 없는 결과 id={pid}")
                    cat_row = catalog_by_id[pid]
                    if it.get("badge") != cat_row.get("badge"):
                        raise ValueError(
                            f"{label}: {pid} 출처 표시 불일치 ({it.get('badge')} != {cat_row.get('badge')})"
                        )
                    if is_script and not is_preview:
                        expected_tl = format_ko_time_label(cat_row.get("time"))
                        if (it.get("time_label") or "") != expected_tl:
                            raise ValueError(
                                f"{label}: {pid} 결과 시각 불일치 ({it.get('time_label')} != {expected_tl})"
                            )
            elif et == "reflect":
                for pid in ev.get("photo_ids") or []:
                    if pid not in catalog_by_id:
                        raise ValueError(f"{label}: 카탈로그에 없는 되짚기 사진 id={pid}")
            elif et == "memory":
                for h in ev.get("hits") or []:
                    if isinstance(h, dict) and h.get("kind") == "photo" and h.get("source_id"):
                        if h["source_id"] not in catalog_by_id:
                            raise ValueError(f"{label}: 카탈로그에 없는 기억 사진 id={h['source_id']}")

    if is_script and not is_preview and check_quotes:
        if old_quote_map is None:
            notices.append("옛 백업 zip이 없어 인용 검사를 건너뜁니다")
        else:
            for ev in all_evs:
                if ev["type"] != "card":
                    continue
                evid = ev.get("evidence") or {}
                quotes = [q for q in (evid.get("read_text") or []) if isinstance(q, str) and q.strip()]
                if not quotes:
                    continue
                pids = [p for p in (evid.get("photo_ids") or []) if isinstance(p, str)]
                present_pids = [p for p in pids if p in old_quote_map]
                if not present_pids:
                    notices.append(f"{label}: 카드 {ev.get('no')}의 근거 사진이 옛 백업에 없어 인용 검사를 건너뜀")
                    continue
                haystacks = [norm_space(old_quote_map[p]) for p in present_pids]
                for q in quotes:
                    nq = norm_space(q)
                    if not any(nq in h for h in haystacks):
                        raise ValueError(f"{label}: 카드 {ev.get('no')} 인용 '{q}'가 근거 사진 속 글자에 없습니다")

    if is_preview and check_quotes:
        def _preview_haystack(item_id: str) -> tuple[str | None, bool]:
            is_doc_item = bool(
                (catalog_by_id and catalog_by_id.get(item_id, {}).get("kind") == "document")
                or (doc_text_map and item_id in doc_text_map)
            )
            if is_doc_item:
                if doc_text_map is None or item_id not in doc_text_map:
                    return None, True
                return norm_preview_quote(doc_text_map[item_id]), True
            if old_quote_map is None:
                return None, False
            if item_id not in old_quote_map:
                return None, False
            return norm_preview_quote(old_quote_map[item_id]), False

        for ev in all_evs:
            if ev["type"] != "card":
                continue
            evid = ev.get("evidence") or {}
            quotes = [q for q in (evid.get("read_text") or []) if isinstance(q, str) and q.strip()]
            if not quotes:
                continue
            pids = [p for p in (evid.get("photo_ids") or []) if isinstance(p, str)]
            hays: list[str] = []
            for p in pids:
                h_txt, _ = _preview_haystack(p)
                if h_txt is not None:
                    hays.append(h_txt)
            if hays:
                for q in quotes:
                    nq = norm_preview_quote(q)
                    if not any(nq in h for h in hays):
                        raise ValueError(f"{label}: 설계 미리보기 카드 {ev.get('no')} 인용 '{q}'가 근거 자료 글자에 없습니다")

        if scene_doc_lines:
            for item_id, lines in scene_doc_lines.items():
                h_txt, is_doc_item = _preview_haystack(item_id)
                if h_txt is None:
                    if is_doc_item and doc_text_map is not None:
                        raise ValueError(f"{label}: doc_lines 자료 '{item_id}'의 문서 글자가 없습니다")
                    continue
                for ln in lines or []:
                    nln = norm_preview_quote(ln)
                    if nln and nln not in h_txt:
                        raise ValueError(f"{label}: doc_lines['{item_id}']의 '{ln}'가 자료 글자에 없습니다")

    return notices


def match_event_condition(cond, events: list[dict]) -> dict | None:
    if cond == "start":
        return {"_virtual": "start"}
    if cond == "first_event":
        return events[0] if events else None
    if cond == "first_answer":
        for ev in events:
            if ev.get("type") == "answer":
                return ev
        for ev in events:
            if ev.get("type") == "result":
                return ev
        return None
    if cond == "result":
        for ev in events:
            if ev.get("type") == "result":
                return ev
        return None
    if isinstance(cond, dict) and "card" in cond:
        want_kind = cond["card"]
        has_field = cond.get("has")
        for ev in events:
            if ev.get("type") != "card":
                continue
            if want_kind != "*" and ev.get("kind") != want_kind:
                continue
            if has_field:
                evid = ev.get("evidence")
                if not isinstance(evid, dict):
                    continue
                val = evid.get(has_field)
                if val is None or val == [] or val == "":
                    continue
            return ev
    return None


def check_scene_conditions(scene: dict, run: dict) -> list[str]:
    evs = run.get("events") or []
    unmatched: list[str] = []
    for st in scene.get("steps") or []:
        if match_event_condition(st.get("on"), evs) is None:
            unmatched.append(f"steps:{st.get('label')}")
    for nr in scene.get("narrow") or []:
        if match_event_condition(nr.get("on"), evs) is None:
            unmatched.append(f"narrow:{nr.get('label')}")
    return unmatched


def compute_narrow_counts(
    scene: dict,
    run: dict,
    catalog_photos_list: list[dict] | None = None,
    catalog_docs_count: int | None = None,
) -> list[int | None]:
    evs = run.get("events") or []
    is_doc_scene = scene.get("kind") == "document"
    counts: list[int | None] = []
    for nr in scene.get("narrow") or []:
        matched = match_event_condition(nr.get("on"), evs)
        if matched is None:
            counts.append(None)
            continue
        s_type = nr.get("set")
        if s_type == "all":
            if is_doc_scene:
                counts.append(catalog_docs_count)
            else:
                counts.append(len(catalog_photos_list) if catalog_photos_list is not None else None)
        elif s_type == "day":
            if catalog_photos_list is None:
                counts.append(None)
            else:
                raw_d = (matched.get("evidence") or {}).get("date")
                dates = set(raw_d if isinstance(raw_d, list) else ([raw_d] if raw_d else []))
                cnt = sum(1 for p in catalog_photos_list if (p.get("time") or "")[:10] in dates)
                counts.append(cnt)
        elif s_type == "evidence":
            evid = matched.get("evidence") or {}
            tc = evid.get("to_count")
            counts.append(int(tc) if tc is not None else len(evid.get("photo_ids") or []))
        elif s_type == "result":
            counts.append(len(matched.get("items") or []))
        else:
            counts.append(None)
    return counts


def _sanitize_hit(h: dict) -> dict:
    kind = h.get("kind")
    sid = h.get("source_id") if "source_id" in h else h.get("id")
    if (not kind or sid is None) and h.get("document_id"):
        dk, ds = split_doc_id(h.get("document_id"))
        kind = kind or dk
        if sid is None:
            sid = ds
    key = h.get("key") if "key" in h and "text" not in h else make_match_key(h.get("text"))
    return {
        "rank": h.get("rank"),
        "kind": kind or "other",
        "id": sid,
        "key": key,
    }


def sanitize_event(ev: dict) -> dict:
    t = ev["type"]
    if t == "card":
        data = ev.get("data")
        if ev.get("kind") == "web" and isinstance(data, dict):
            data = dict(data)
            if isinstance(data.get("events"), list):
                data["events"] = [
                    {k: v for k, v in item.items() if k not in ("lat", "lon")}
                    if isinstance(item, dict)
                    else item
                    for item in data["events"]
                ]
        return {
            "type": "card",
            "run_id": ev["run_id"],
            "seq": ev["seq"],
            "no": ev["no"],
            "kind": ev["kind"],
            "title": ev["title"],
            "sentence": ev["sentence"],
            "evidence": ev["evidence"],
            "cached": bool(ev.get("cached", False)),
            "data": data,
        }
    if t == "memory":
        return {
            "type": "memory",
            "run_id": ev["run_id"],
            "seq": ev["seq"],
            "n": ev["n"],
            "query": ev["query"],
            "bank": ev["bank"],
            "elapsed_ms": ev["elapsed_ms"],
            "count": ev["count"],
            "error": ev["error"],
            "hits": [_sanitize_hit(h) for h in (ev.get("hits") or []) if isinstance(h, dict)],
        }
    if t == "reflect":
        raw_u = ev.get("usage")
        clean_u = (
            {k: raw_u.get(k) for k in ("calls", "cost_usd", "cost_known", "ms", "ok")}
            if isinstance(raw_u, dict)
            else None
        )
        return {
            "type": "reflect",
            "run_id": ev["run_id"],
            "seq": ev["seq"],
            "n": ev["n"],
            "query": ev["query"],
            "known": ev["known"],
            "bank": ev["bank"],
            "elapsed_ms": ev["elapsed_ms"],
            "count": ev["count"],
            "photo_ids": list(ev.get("photo_ids") or []),
            "left_out": ev["left_out"],
            "reasoning": ev["reasoning"],
            "inner_calls": ev["inner_calls"],
            "inner_queries": list(ev.get("inner_queries") or []),
            "usage": clean_u,
            "error": ev["error"],
        }
    if t == "progress":
        return {
            "type": "progress",
            "run_id": ev["run_id"],
            "seq": ev["seq"],
            "step": ev["step"],
            "elapsed_ms": ev["elapsed_ms"],
            "calls": ev["calls"],
            "cost_usd": ev["cost_usd"],
        }
    if t == "result":
        def clean_item(it: dict) -> dict:
            return {"id": it.get("id"), "badge": it.get("badge"), "time_label": it.get("time_label")}

        return {
            "type": "result",
            "run_id": ev["run_id"],
            "seq": ev["seq"],
            "no": ev["no"],
            "title": ev["title"],
            "sentence": ev["sentence"],
            "items": [clean_item(it) for it in (ev.get("items") or []) if isinstance(it, dict)],
            "related_title": ev["related_title"],
            "related": [
                {
                    "label": grp.get("label"),
                    "items": [clean_item(it) for it in (grp.get("items") or []) if isinstance(it, dict)],
                }
                for grp in (ev.get("related") or [])
                if isinstance(grp, dict)
            ],
        }
    return dict(ev)


def sanitize_run_for_page(rec: dict, *, default_kind: str = "저장한 실행") -> dict:
    demo_meta = rec.get("demo")
    if isinstance(demo_meta, dict) and demo_meta.get("kind"):
        clean_demo = {"kind": demo_meta["kind"], **({"note": demo_meta["note"]} if demo_meta.get("note") else {})}
    else:
        clean_demo = {"kind": default_kind}

    raw_sum = rec.get("summary")
    clean_sum = (
        {"calls": raw_sum.get("calls"), "cost_usd": raw_sum.get("cost_usd")}
        if isinstance(raw_sum, dict)
        else None
    )

    clean_calls = []
    for c in rec.get("calls") or []:
        if not isinstance(c, dict):
            continue
        item = {"purpose": c.get("purpose"), "ms": c.get("ms"), "cost_usd": c.get("cost_usd")}
        if c.get("web_search_requests") is not None:
            item["web_search_requests"] = c["web_search_requests"]
        clean_calls.append(item)

    clean_branches: dict[str, dict] = {}
    for bk, bv in (rec.get("branches") or {}).items():
        if isinstance(bv, dict) and isinstance(bv.get("events"), list):
            clean_branches[str(bk)] = {"events": [sanitize_event(e) for e in bv["events"]]}

    out = {
        "run_id": rec.get("run_id"),
        "test": rec.get("test"),
        "query": rec.get("query"),
        "started_at": rec.get("started_at"),
        "elapsed_ms": rec.get("elapsed_ms"),
        "today": rec.get("today"),
        "status": rec.get("status"),
        "error": rec.get("error"),
        "answers": list(rec.get("answers") or []),
        "result_ids": list(rec.get("result_ids") or []),
        "result_sentence": rec.get("result_sentence"),
        "calls": clean_calls,
        "summary": clean_sum,
        "demo": clean_demo,
        "events": [sanitize_event(e) for e in (rec.get("events") or [])],
    }
    if clean_branches:
        out["branches"] = clean_branches
    return out


def sanitize_compare_recall(
    recall_obj: dict | None,
    *,
    expected_bank: str,
    expected_query: str,
) -> tuple[dict | None, str | None]:
    if recall_obj is None:
        return None, None
    rec_bank = recall_obj.get("bank") or ""
    if rec_bank != expected_bank:
        return None, f"비교용 꺼낸 기억 은행 불일치({rec_bank} != {expected_bank})로 쓰지 않음"
    rec_q = norm_space(recall_obj.get("query"))
    exp_q = norm_space(expected_query)
    if rec_q != exp_q:
        return None, "비교용 꺼낸 기억 질의 불일치로 쓰지 않음"
    hits = [_sanitize_hit(h) for h in (recall_obj.get("hits") or []) if isinstance(h, dict)]
    return {
        "bank": rec_bank,
        "query": recall_obj.get("query") or "",
        "elapsed_ms": recall_obj.get("elapsed_ms"),
        "hits": hits,
    }, None


def count_dedup_memories(memory_events: list[dict]) -> int:
    seen: set[tuple] = set()
    for ev in memory_events:
        for h in ev.get("hits") or []:
            seen.add((h.get("kind"), h.get("id"), h.get("key")))
    return len(seen)


def build_pipeline_summary(
    places_obj: dict | None,
    links_rows: list[dict] | None,
    timeline_days_list: list[dict],
) -> dict:
    if places_obj is not None and isinstance(places_obj, dict):
        groups_raw = places_obj.get("groups") or []
        photos_map = places_obj.get("photos") or {}
        groups = len(groups_raw)
        photos_in_groups = sum(int(g.get("photos") or 0) for g in groups_raw if isinstance(g, dict))
        visits = len({
            v.get("visit")
            for v in photos_map.values()
            if isinstance(v, dict) and v.get("visit")
        })
        named = sum(
            1
            for g in groups_raw
            if isinstance(g, dict) and ((g.get("area_ko") or "").strip() or (g.get("near_ko") or "").strip())
        )
        decorated = []
        for g in groups_raw:
            if not isinstance(g, dict):
                continue
            lbl = (g.get("area_ko") or "").strip() or (g.get("near_ko") or "").strip() or "이름 없는 자리"
            p_cnt = int(g.get("photos") or 0)
            d_cnt = len(g.get("days") or [])
            decorated.append((lbl, p_cnt, d_cnt, g.get("group") or ""))
        decorated.sort(key=lambda x: (-x[1], x[0], x[3]))
        top_groups = [{"label": x[0], "photos": x[1], "days": x[2]} for x in decorated[:8]]
    else:
        groups = None
        photos_in_groups = None
        visits = None
        named = None
        top_groups = []

    links_val = (
        sum(1 for r in links_rows if isinstance(r, dict) and bool(r.get("linked_ids")))
        if links_rows is not None
        else None
    )

    days_sorted = sorted(timeline_days_list, key=lambda d: d["date"])
    return {
        "groups": groups,
        "photos_in_groups": photos_in_groups,
        "visits": visits,
        "named": named,
        "links": links_val,
        "timeline_days": len(days_sorted),
        "top_groups": top_groups,
        "days": days_sorted,
    }


def _blurred_lookup(folder: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    if folder.is_dir():
        for p in sorted(folder.iterdir()):
            if p.is_file():
                found.setdefault(p.name.lower(), p)
                found.setdefault(p.stem.lower(), p)
    return found


def _encode_jpeg_uri(im: Image.Image, edge: int, quality: int) -> str:
    copy = im.copy()
    copy.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    copy.save(buf, "JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _rel_display(path: Path) -> str:
    try:
        return path.resolve().relative_to(WS.resolve()).as_posix()
    except ValueError:
        return str(path)


def choose_run_record(run_name: str, *, script_only: bool, passed_dir: Path, runs_dir: Path) -> tuple[dict, str, str]:
    if not script_only:
        passed_path = passed_dir / f"{run_name}.json"
        if passed_path.is_file():
            rec = json.loads(passed_path.read_text(encoding="utf-8"))
            has_result = any(isinstance(e, dict) and e.get("type") == "result" for e in (rec.get("events") or []))
            if rec.get("status") == "answered" and has_result:
                return rec, "저장한 실행", _rel_display(passed_path)
    script_path = runs_dir / f"{run_name}.json"
    if not script_path.is_file():
        raise FileNotFoundError(f"실행 기록 파일이 없습니다: {script_path}")
    rec = json.loads(script_path.read_text(encoding="utf-8"))
    kind = ((rec.get("demo") or {}).get("kind")) or "대본"
    return rec, kind, _rel_display(script_path)


def build_constellation_payload(bank_export: dict) -> dict:
    return {
        "bank": bank_export["bank"],
        "has_doc_memories": bank_export["has_doc_memories"],
        "stars": bank_export["stars"],
        "words": bank_export["words"],
        "labels": bank_export["labels"],
        "time_links": bank_export["time_links"],
        "entity_links": bank_export["entity_links"],
        "causal_links": bank_export["causal_links"],
    }


def build(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="브라우저 데모 HTML 한 파일 만들기")
    ap.add_argument("--script", action="store_true", help="eval/passed 대신 예비 대본으로 만든다")
    ap.add_argument("--runs", default=None, help="추가로 넣을 실행 기록 폴더")
    ap.add_argument("--no-photos", action="store_true", help="--runs 로 더한 실행의 사진을 넣지 않는다")
    ap.add_argument("--out", default=None, help="출력 HTML 경로(기본: var/demo.html)")
    args = ap.parse_args(argv)

    if not settings.catalog.is_file() or not settings.photos_screen.is_dir():
        print("수집 결과가 없습니다. 먼저 uv run python -m pipeline.collect 를 돌리세요.", file=sys.stderr)
        return 1

    catalog_rows = read_jsonl(settings.catalog)
    photo_rows = [r for r in catalog_rows if r.get("kind") == "photo"]
    doc_rows = sorted([r for r in catalog_rows if r.get("kind") == "document"], key=lambda r: r["id"])
    catalog_by_id = {r["id"]: r for r in catalog_rows if r.get("id")}

    # 백업 zip 선택: aidic-mobile.zip -> aidic-scenario.zip
    mobile_zip = HINDSIGHT_DIR / "aidic-mobile.zip"
    scenario_zip = HINDSIGHT_DIR / "aidic-scenario.zip"
    docs_zip = HINDSIGHT_DIR / "aidic-mobile-docs.zip"
    chosen_zip = mobile_zip if mobile_zip.is_file() else (scenario_zip if scenario_zip.is_file() else None)
    if chosen_zip is None:
        print("Hindsight 백업 zip이 없습니다(aidic-mobile.zip, aidic-scenario.zip).", file=sys.stderr)
        return 1

    old_quote_map = load_old_backup_quotes(scenario_zip if scenario_zip.is_file() else None)

    # 장면 설정 읽기(menu_label 순서)
    scene_files = sorted(SCENES_DIR.glob("*.json"))
    scenes_raw = [json.loads(p.read_text(encoding="utf-8")) for p in scene_files]
    for sc in scenes_raw:
        validate_scene_config(sc, label=f"scenes/{sc.get('key')}.json")
    scenes_raw.sort(key=lambda s: str(s.get("menu_label") or ""))

    has_document_case = any(sc.get("kind") == "document" for sc in scenes_raw)
    doc_photo_exclusions: set[str] = set()
    for sc in scenes_raw:
        for pid in sc.get("doc_photos") or []:
            doc_photo_exclusions.add(pid)

    bank_export = export_bank(chosen_zip, exclude_label_photo_ids=doc_photo_exclusions)
    doc_bank_export = (
        export_bank(docs_zip, exclude_label_photo_ids=doc_photo_exclusions)
        if (has_document_case and docs_zip.is_file())
        else None
    )

    # 문서 본문 읽기(문서 케이스가 있을 때): var/docs.jsonl -> markitdown 직접 추출
    doc_text_map: dict[str, str] = {}
    doc_fail_count = 0
    doc_source_label = ""
    if has_document_case and doc_rows:
        if settings.docs.is_file():
            raw_docs = {r["id"]: r for r in read_jsonl(settings.docs) if r.get("id")}
            for dr in doc_rows:
                did = dr["id"]
                if did in raw_docs and (raw_docs[did].get("body") or "").strip():
                    doc_text_map[did] = str(raw_docs[did]["body"])
                else:
                    doc_fail_count += 1
                    doc_text_map[did] = ""
            doc_source_label = "var/docs.jsonl에서 읽음"
        else:
            from markitdown import MarkItDown

            md_conv = MarkItDown()
            for dr in doc_rows:
                did = dr["id"]
                p_doc = settings.phone_dir / did
                try:
                    body_txt = md_conv.convert(str(p_doc)).text_content or ""
                    if not body_txt.strip():
                        doc_fail_count += 1
                    doc_text_map[did] = body_txt
                except Exception:
                    doc_fail_count += 1
                    doc_text_map[did] = ""
            doc_source_label = "markitdown으로 바로 뽑음"

    doc_badge_map: dict[str, int] = {}
    docs_meta: list[dict] = []
    for dr in doc_rows:
        did = dr["id"]
        dbadge = dr.get("badge") or ""
        doc_badge_map[dbadge] = doc_badge_map.get(dbadge, 0) + 1
        docs_meta.append({
            "id": did,
            "badge": dbadge,
            "ext": Path(did).suffix.lstrip(".").upper() or "DOC",
            "chars": len(doc_text_map.get(did) or ""),
        })
    doc_badge_counts = [{"badge": b, "count": doc_badge_map[b]} for b in BADGE_ORDER if doc_badge_map.get(b)]
    for b in sorted(doc_badge_map.keys()):
        if b and b not in BADGE_ORDER:
            doc_badge_counts.append({"badge": b, "count": doc_badge_map[b]})

    # 사진 설명 읽기: data/descriptions.jsonl -> 옛 백업 zip
    desc_rows = read_jsonl(settings.descriptions) if settings.descriptions.is_file() else []
    valid_descs = latest_by_id(desc_rows) if desc_rows else {}
    if valid_descs:
        desc_map = {
            pid: {"scene": r.get("scene") or "", "text_in_photo": r.get("text_in_photo") or ""}
            for pid, r in valid_descs.items()
        }
        desc_source_label = "data/descriptions.jsonl에서 읽음"
    else:
        fb_zip = scenario_zip if scenario_zip.is_file() else chosen_zip
        desc_map = load_old_backup_descriptions(fb_zip)
        desc_source_label = "옛 백업에서 읽음"

    # ① 격자용 사진 목록(찍은 시각 오름차순, 시각 없으면 맨 뒤 id 순서)
    def _photo_sort_key(r: dict) -> tuple:
        ep = parse_iso_epoch(r.get("time"))
        return (0 if ep is not None else 1, ep if ep is not None else 0.0, r["id"])

    sorted_photos = sorted(photo_rows, key=_photo_sort_key)
    badge_counts_map: dict[str, int] = {}
    for r in sorted_photos:
        b = r.get("badge") or ""
        badge_counts_map[b] = badge_counts_map.get(b, 0) + 1
    badge_counts = [{"badge": b, "count": badge_counts_map[b]} for b in BADGE_ORDER if badge_counts_map.get(b)]
    for b in sorted(badge_counts_map.keys()):
        if b and b not in BADGE_ORDER:
            badge_counts.append({"badge": b, "count": badge_counts_map[b]})

    photos_meta: list[dict] = []
    desc_present_count = 0
    for r in sorted_photos:
        pid = r["id"]
        d = desc_map.get(pid)
        has_d = bool(d and (d.get("scene") or d.get("text_in_photo")))
        if has_d:
            desc_present_count += 1
        photos_meta.append({
            "id": pid,
            "badge": r.get("badge") or "",
            "date": (r.get("time") or "")[:10] if r.get("time") else None,
            "time_dot": format_dot_time(r.get("time")),
            "scene": (d.get("scene") or "") if d else "",
            "text": (d.get("text_in_photo") or "") if d else "",
            "has_desc": has_d,
        })
    missing_desc_count = len(sorted_photos) - desc_present_count

    # 색인 줄 수
    index_count: int | None = None
    if settings.index.is_file():
        index_count = sum(1 for ln in settings.index.read_text(encoding="utf-8").splitlines() if ln.strip())

    # ② 에이전트 파이프라인 자료
    places_path = settings.data_dir / "places.json"
    places_obj = json.loads(places_path.read_text(encoding="utf-8")) if places_path.is_file() else None
    links_rows = read_jsonl(settings.links) if settings.links.is_file() else None
    pipeline_summary = build_pipeline_summary(places_obj, links_rows, bank_export["timeline_days"])

    # 케이스별 실행 기록과 비교용 기억 모으기
    case_out_lines: list[str] = []
    recall_out_lines: list[str] = []
    cases_payload: list[dict] = []
    large_needed_ids: dict[str, None] = {}
    doc_svg_first_lines: dict[str, str] = {}

    for sc in scenes_raw:
        key = sc["key"]
        run_name = sc["run"]
        is_doc_sc = sc.get("kind") == "document"
        raw_rec, kind_label, src_disp = choose_run_record(
            run_name,
            script_only=args.script,
            passed_dir=settings.passed_dir,
            runs_dir=RUNS_DIR,
        )
        sc_doc_lines = dict(sc.get("doc_lines") or {})
        q_notices = validate_run_record(
            raw_rec,
            label=f"케이스 {key}({src_disp})",
            catalog_by_id=catalog_by_id,
            old_quote_map=old_quote_map,
            doc_text_map=doc_text_map if is_doc_sc else None,
            scene_doc_lines=sc_doc_lines if is_doc_sc else None,
            check_catalog=True,
            check_quotes=True,
        )
        br_cnt = len(raw_rec.get("branches") or {})
        case_out_lines.append(
            f"케이스 {key}: {kind_label} {src_disp} · 이벤트 {len(raw_rec.get('events') or [])}개 · 갈래 {br_cnt}개"
        )
        case_out_lines.extend(q_notices)
        unmatched = check_scene_conditions(sc, raw_rec)
        if unmatched:
            case_out_lines.append(f"케이스 {key}: 켜지지 않는 단계가 있습니다 -> {', '.join(unmatched)}")

        for pid in sc.get("spotlight") or []:
            if catalog_by_id.get(pid, {}).get("kind") == "document":
                fl = (sc_doc_lines.get(pid) or [""])[0] if sc_doc_lines.get(pid) else ""
                doc_svg_first_lines.setdefault(pid, fl)
            else:
                large_needed_ids.setdefault(pid, None)
        for pid in sc.get("doc_photos") or []:
            large_needed_ids.setdefault(pid, None)
        for pid in collect_run_photo_ids(raw_rec):
            if catalog_by_id.get(pid, {}).get("kind") == "document":
                fl = (sc_doc_lines.get(pid) or [""])[0] if sc_doc_lines.get(pid) else ""
                doc_svg_first_lines.setdefault(pid, fl)
            else:
                large_needed_ids.setdefault(pid, None)
        if is_doc_sc:
            for pid, lns in sc_doc_lines.items():
                if catalog_by_id.get(pid, {}).get("kind") == "document":
                    fl = (lns or [""])[0] if lns else ""
                    doc_svg_first_lines.setdefault(pid, fl)

        clean_run = sanitize_run_for_page(raw_rec, default_kind=kind_label)

        # 비교용 꺼낸 기억
        active_bank_for_case = (doc_bank_export or bank_export)["bank"] if is_doc_sc else bank_export["bank"]
        recall_path = RECALL_DIR / f"{run_name}.json"
        raw_recall = json.loads(recall_path.read_text(encoding="utf-8")) if recall_path.is_file() else None
        clean_recall, recall_skip_reason = sanitize_compare_recall(
            raw_recall,
            expected_bank=active_bank_for_case,
            expected_query=clean_run.get("query") or "",
        )

        mem_evs = [
            e for e in clean_run["events"]
            if e["type"] == "memory" and not e.get("error") and (e.get("hits") or [])
        ]
        agent_mem_part = (
            f"에이전트가 받은 기억 {count_dedup_memories(mem_evs)}개(꺼내기 {len(mem_evs)}번)"
            if mem_evs
            else "에이전트가 받은 기억 없음"
        )
        if clean_recall is not None:
            cmp_hits_cnt = count_dedup_memories([{"hits": clean_recall["hits"]}])
            cmp_part = f"비교용 꺼낸 기억 {cmp_hits_cnt}개 · {_rel_display(recall_path)}"
        elif recall_skip_reason:
            cmp_part = recall_skip_reason
        else:
            cmp_part = "비교용 꺼낸 기억 없음"
        recall_out_lines.append(f"케이스 {key}: {agent_mem_part} · {cmp_part}")

        cases_payload.append({
            "scene": {
                "key": sc["key"],
                "menu_label": sc["menu_label"],
                "run": sc["run"],
                "kind": sc["kind"],
                "doc_photos": list(sc.get("doc_photos") or []),
                "spotlight": list(sc.get("spotlight") or []),
                "doc_lines": dict(sc.get("doc_lines") or {}),
                "steps": sc["steps"],
                "narrow": sc["narrow"],
                "skip_prep": False,
            },
            "run": clean_run,
            "recall": clean_recall,
            "no_photos": False,
        })

    # --runs <폴더> 추가 케이스
    if args.runs:
        extra_dir = Path(args.runs)
        if not extra_dir.is_dir():
            print(f"--runs 폴더가 없습니다: {extra_dir}", file=sys.stderr)
            return 1
        existing_keys = {c["scene"]["key"] for c in cases_payload}
        for p in sorted(extra_dir.glob("*.json")):
            raw_rec = json.loads(p.read_text(encoding="utf-8"))
            validate_run_record(
                raw_rec,
                label=str(p.name),
                catalog_by_id=None,
                old_quote_map=None,
                check_catalog=False,
                check_quotes=False,
            )
            test_id = str(raw_rec.get("test") or p.stem)
            ckey = test_id if test_id not in existing_keys else f"run_{test_id}"
            existing_keys.add(ckey)
            clean_run = sanitize_run_for_page(raw_rec, default_kind="저장한 실행")
            extra_pids = collect_run_photo_ids(raw_rec)
            if not args.no_photos:
                for pid in extra_pids:
                    large_needed_ids.setdefault(pid, None)
            extra_photos_meta = [
                {"id": pid, "badge": "", "date": None, "time_dot": "", "scene": "", "text": "", "has_desc": False}
                for pid in extra_pids
            ]
            cases_payload.append({
                "scene": {
                    "key": ckey,
                    "menu_label": f"실행 기록 {test_id}",
                    "run": ckey,
                    "kind": "photo",
                    "doc_photos": [],
                    "spotlight": [],
                    "doc_lines": {},
                    "steps": DEFAULT_STEPS,
                    "narrow": DEFAULT_NARROW,
                    "skip_prep": True,
                    "only_photos": extra_photos_meta,
                },
                "run": clean_run,
                "recall": None,
                "no_photos": bool(args.no_photos),
            })

    # 관람객 사진 9장(origin == shared_download): 늘 큰 사본 포함 + 서버에서 받지 않는 사진으로 표시
    shared_download_ids = sorted(
        r["id"] for r in photo_rows if r.get("origin") == "shared_download"
    )
    for pid in shared_download_ids:
        large_needed_ids.setdefault(pid, None)
    shared_download_set = set(shared_download_ids)

    blurred_map = _blurred_lookup(settings.blurred_dir)
    small_photos: dict[str, str] = {}
    large_photos: dict[str, str] = {}
    used_blurred = 0
    gaussian_blurred = 0

    for r in sorted_photos:
        pid = r["id"]
        name_low = Path(pid).name.lower()
        blur_hit = blurred_map.get(name_low) or blurred_map.get(Path(name_low).stem)
        is_visitor_photo = pid in shared_download_set

        if blur_hit is not None:
            src_path = blur_hit
            apply_gaussian = False
            if is_visitor_photo:
                used_blurred += 1
        else:
            scr_rel = r.get("screen_copy")
            src_path = (settings.var_dir / scr_rel) if scr_rel else None
            apply_gaussian = is_visitor_photo
            if is_visitor_photo:
                gaussian_blurred += 1

        if src_path is None or not src_path.is_file():
            print(f"화면용 사진 사본이 없습니다: {pid}", file=sys.stderr)
            return 1

        with Image.open(src_path) as raw_im:
            im = ImageOps.exif_transpose(raw_im).convert("RGB")
            if apply_gaussian:
                radius = max(im.size) / 40.0
                im = im.filter(ImageFilter.GaussianBlur(radius=radius))
            small_photos[pid] = _encode_jpeg_uri(im, SMALL_EDGE, SMALL_QUALITY)
            if pid in large_needed_ids:
                large_photos[pid] = _encode_jpeg_uri(im, LARGE_EDGE, LARGE_QUALITY)

    # 글꼴 CSS
    if FONT_FILE.is_file():
        b64_font = base64.b64encode(FONT_FILE.read_bytes()).decode("ascii")
        font_css = (
            '@font-face{font-family:"Pretendard Variable";font-weight:45 930;'
            "font-style:normal;font-display:block;"
            f"src:url(data:font/woff2;base64,{b64_font}) format('woff2');}}"
        )
        font_line = "글꼴 넣음"
    else:
        font_css = ""
        font_line = "글꼴 파일이 없어 컴퓨터의 글꼴로 그립니다"

    doc_svgs: dict[str, str] = {
        did: make_doc_svg_data_uri(did, first_ln)
        for did, first_ln in sorted(doc_svg_first_lines.items())
    }

    page_data = {
        "cases": cases_payload,
        "default_steps": DEFAULT_STEPS,
        "default_narrow": DEFAULT_NARROW,
        "catalog": {
            "total_photos": len(sorted_photos),
            "badges": badge_counts,
            "described_count": desc_present_count,
            "index_count": index_count,
            "photos": photos_meta,
        },
        "catalog_docs": {
            "total_docs": len(doc_rows),
            "badges": doc_badge_counts,
            "docs": docs_meta,
        },
        "doc_svgs": doc_svgs,
        "no_server_photo_ids": shared_download_ids,
        "photos_small": small_photos,
        "photos_large": large_photos,
        "pipeline": pipeline_summary,
        "constellation": build_constellation_payload(bank_export),
        "constellation_docs": build_constellation_payload(doc_bank_export) if doc_bank_export is not None else None,
    }

    data_json = json.dumps(page_data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")

    demo_css = (HERE / "demo.css").read_text(encoding="utf-8") + "\n" + (HERE / "board.css").read_text(encoding="utf-8")
    demo_js = "\n".join(
        (HERE / js_name).read_text(encoding="utf-8")
        for js_name in ("player.js", "phone.js", "live.js", "board.js", "constellation.js")
    )

    html = TEMPLATE.read_text(encoding="utf-8")
    replacements = (
        ("/*__FONT_CSS__*/", font_css),
        ("/*__APP_CSS__*/", (WEB / "app.css").read_text(encoding="utf-8")),
        ("/*__DEMO_CSS__*/", demo_css),
        ("/*__RENDER_JS__*/", (WEB / "render.js").read_text(encoding="utf-8")),
        ("/*__DEMO_JS__*/", demo_js),
        ("/*__DATA__*/", data_json),
    )
    for mark, content in replacements:
        if mark not in html:
            print(f"template.html 에 {mark} 자리가 없습니다", file=sys.stderr)
            return 1
        html = html.replace(mark, content, 1)

    out_path = Path(args.out) if args.out else (settings.var_dir / "demo.html")
    if not out_path.is_absolute():
        out_path = (WS / out_path).resolve()
    encoded_html = html.encode("utf-8")
    if len(encoded_html) > MAX_HTML_BYTES:
        print(
            f"페이지 크기가 20MB를 넘었습니다 ({len(encoded_html) / (1024 * 1024):.2f} MB)",
            file=sys.stderr,
        )
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(encoded_html)

    for ln in case_out_lines:
        print(ln)
    print(
        f"사진 작은 사본 {len(small_photos)}장 · 큰 사본 {len(large_photos)}장 · "
        f"흐린 사본 {used_blurred}장 · 전체를 흐린 사진 {gaussian_blurred}장"
    )
    if missing_desc_count > 0:
        print(f"사진 설명 {desc_present_count}장 · {desc_source_label} · 설명 없는 사진 {missing_desc_count}장")
    else:
        print(f"사진 설명 {desc_present_count}장 · {desc_source_label}")
    if has_document_case and doc_rows:
        print(
            f"문서 {len(doc_rows)}건 글자 · {doc_source_label} · 실패 {doc_fail_count}건 · 문서 그림 {len(doc_svgs)}건"
        )
    print(f"색인 {index_count}줄" if index_count is not None else "색인 줄 없음")

    places_part = (
        f"같은 자리 {pipeline_summary['groups']}곳 · 방문 {pipeline_summary['visits']}번 · 지명 {pipeline_summary['named']}곳"
        if pipeline_summary["groups"] is not None
        else "지명 파일 없음"
    )
    links_part = (
        f"받은 사진 잇기 {pipeline_summary['links']}장"
        if pipeline_summary["links"] is not None
        else "받은 사진 잇기 없음"
    )
    print(f"에이전트 파이프라인: {places_part} · {links_part} · 하루 타임라인 {pipeline_summary['timeline_days']}일")

    st = bank_export["stats"]
    print(
        f"기억 {st['memories']}개 · 시간 연결 {st['time_links']}개 · 낱말 연결 {st['entity_links']}개 · "
        f"원인 연결 {st['causal_links']}개(범위를 벗어나 건너뜀 {st['causal_skipped']}개) · "
        f"낱말 {st['words']}개 · {bank_export['zip_name']}"
    )
    if doc_bank_export is not None:
        dst = doc_bank_export["stats"]
        print(
            f"기억 {dst['memories']}개 · 시간 연결 {dst['time_links']}개 · 낱말 연결 {dst['entity_links']}개 · "
            f"원인 연결 {dst['causal_links']}개(범위를 벗어나 건너뜀 {dst['causal_skipped']}개) · "
            f"낱말 {dst['words']}개 · {doc_bank_export['zip_name']}"
        )
    for ln in recall_out_lines:
        print(ln)
    print(font_line)
    print(f"{out_path} ({len(encoded_html) / 1024:.0f} KB)")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except AttributeError:
            pass
    return build()


if __name__ == "__main__":
    sys.exit(main())

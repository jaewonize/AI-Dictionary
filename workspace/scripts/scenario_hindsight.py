# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "anthropic>=2.8,<3",
#     "google-genai>=2.34,<3",
#     "pillow>=11",
#     "python-dotenv>=1.0",
#     "pydantic>=2.8",
#     "httpx>=0.27",
# ]
# ///
"""hindsight 시험: 사진 기억만(A)과 하루 타임라인을 더한 것(B)으로 같은 질의를 recall·reflect 한다.

workspace/ 에서 차례로 실행한다.
  uv run --script scripts/scenario_hindsight.py collect                 # 폰 폴더의 사진 전부: 찍은 시각·출처, 메타데이터 없는 사본
  uv run --script scripts/scenario_hindsight.py describe                # 지시문 v2 로 사진 설명(이미 한 것은 건너뜀)
  uv run --script scripts/scenario_hindsight.py retain --phase photos   # 사진마다 기억 하나(찍은 시각 = timestamp)
  uv run --script scripts/scenario_hindsight.py ask --phase A --query "…" --answer var/scenario/answer.json
  uv run --script scripts/scenario_hindsight.py retain --phase timeline # 날짜마다 하루 타임라인 기억 하나
  uv run --script scripts/scenario_hindsight.py ask --phase B --query "…" --answer var/scenario/answer.json

규칙: 사진의 XMP(받은·저장한·내려받은 시각, 유입 경로, 장소 이름)는 읽지 않는다. 시각은 EXIF 찍은 시각,
없으면 파일 이름, 그것도 없으면 EXIF 저장 시각을 쓰고 그 기준을 함께 남긴다. 질의와 정답은 인자로만 받는다.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
from PIL import Image
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))
import compare_describe as cd  # noqa: E402  (설정 읽기·사본 만들기·재시도를 같이 쓴다)

OUT = cd.WS / "var" / "scenario"
PHOTOS = OUT / "photos"
THUMBS = OUT / "thumbs"
CATALOG = OUT / "catalog.jsonl"
HS_URL = "http://localhost:8888"
BANK = "aidic-scenario"
PROMPT_VERSION = "describe-v2"
DESC_MODEL = "claude-opus-5-5"
IMG_EXT = {".jpg", ".jpeg", ".png"}
BADGE = {"DCIM/Camera": "갤러리", "DCIM/Screenshots": "캡처", "Pictures/KakaoTalk": "카카오톡",
         "Download": "다운로드", "KakaoTalkDownload": "카톡 받은 파일"}
BASIS_KO = {"taken": "찍은 시각", "filename": "파일 이름의 시각", "saved": "저장 시각"}
_lock = threading.Lock()

# 지시문 v2: 비교 실험(describe-cmp-v1)에서 나온 보강점을 넣었다. 예시는 이 자료와 무관한 것으로만 든다.
SYSTEM_PROMPT_V2 = """당신은 휴대폰 사진 검색용 색인을 만드는 분석기입니다. 사진 한 장을 보고, 나중에 사용자가 기억나는 대로 한국어로 물었을 때 이 사진을 찾을 수 있도록 사진에 보이는 내용을 글로 적습니다.

규칙
- 사진에 실제로 보이는 것만 적습니다. 판단이 필요한 것은 "~로 보인다"라고 쓰고, 보이지 않는 것은 쓰지 않습니다. 맛·재료·온도·층수·바닥 소재처럼 사진으로 확인할 수 없는 속성은 단정하지 않습니다.
- 찍은 날짜, 시각, 장소 이름은 사진 속 글자나 화면에 보일 때만 적습니다. 사람의 신원(이름, 나이)은 추측하지 않습니다.
- place: 장소의 종류를 한두 낱말로 적습니다(예: 카페, 식당, 전시장, 공원, 거리, 집 거실, 주방, 욕실, 사무실, 상점, 문서, 휴대폰 화면).
- scene: 한국어 3~6문장. 장소의 종류와 실내·실외, 주요 사물·음식·음료, 사람(인원과 하는 일. 사람이 없으면 "사람은 보이지 않는다"), 반려동물, 멀리 보이는 지형지물, 그리고 색·조명·인테리어·창밖 풍경처럼 비슷한 다른 사진과 구별되는 특징을 적습니다. 알아볼 수 있는 랜드마크·작품·음식·음료·식물은 일반 명사 대신 이름으로 적고, 확신이 없으면 "~로 보이는"을 붙입니다. 흔히 함께 쓰는 말이 있으면 같이 적습니다(예: 마트·슈퍼마켓).
- 사진 속에 외국어 이름이나 제목이 보이면 scene에 한글 독음을 함께 적습니다(예: 'CLAUDE MONET'은 클로드 모네).
- text_in_photo: 사진 속에 보이는 글자(간판, 메뉴판, 포스터, 작품 설명, 유리·벽의 글씨, 문서, 영수증, 화면)를 원문 그대로 옮깁니다. 덩어리마다 줄을 바꿉니다. 가려지거나 잘린 글자는 채우지 않고 보이는 데까지만 적습니다. 확실하지 않은 숫자는 뺍니다. 원문에 없는 설명은 덧붙이지 않습니다. 글자가 없으면 빈 문자열입니다.
- photo_type: 일반 장면 사진은 scene, 종이 문서·영수증·견적서를 찍은 사진은 document, 휴대폰·컴퓨터 화면 캡처는 screenshot 입니다."""


class PhotoDescriptionV2(BaseModel):
    place: str
    scene: str
    text_in_photo: str
    photo_type: Literal["scene", "document", "screenshot"]


# ---------------------------------------------------------------- 수집
def _exif_time(s: str, offset: str | None) -> str | None:
    m = re.match(r"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})", s or "")
    if not m:
        return None
    off = offset if offset and re.match(r"[+-]\d{2}:\d{2}$", offset) else "+09:00"
    return f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}{off}"


def read_time(path: Path, img: Image.Image) -> tuple[str | None, str | None]:
    exif = img.getexif()
    ifd = exif.get_ifd(0x8769)
    taken = _exif_time(ifd.get(0x9003) or ifd.get(0x9004), ifd.get(0x9011) or ifd.get(0x9012))
    if taken:
        return taken, "taken"
    m = re.search(r"(20\d{2})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})", path.name)
    if m:
        return f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}+09:00", "filename"
    saved = _exif_time(exif.get(0x0132), ifd.get(0x9010))
    if saved:
        return saved, "saved"
    return None, None


def cmd_collect(args) -> None:
    root = cd.phone_dir(cd.load_env())
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in IMG_EXT)
    rows = []
    for i, p in enumerate(files, 1):
        pid = p.relative_to(root).as_posix()
        folder = "/".join(pid.split("/")[:-1])
        with Image.open(p) as im:
            t, basis = read_time(p, im)
        info = cd.clean_copy(p, PHOTOS / f"{i:03d}.jpg", THUMBS / f"{i:03d}.jpg")
        rows.append({"n": i, "id": pid, "badge": BADGE.get(folder, folder), "time": t, "time_basis": basis,
                     "copy": f"{i:03d}.jpg", **{k: info[k] for k in ("width", "height", "frames", "orientation")}})
    OUT.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    by = {}
    for r in rows:
        by[r["time_basis"]] = by.get(r["time_basis"], 0) + 1
    print(f"사진 {len(rows)}장 -> {CATALOG}  시각 기준: {by}")


def load_catalog() -> list[dict]:
    return [json.loads(x) for x in CATALOG.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---------------------------------------------------------------- 설명(v2)
def desc_path() -> Path:
    return OUT / f"desc_{PROMPT_VERSION}.jsonl"


def load_desc() -> dict[str, dict]:
    p = desc_path()
    out = {}
    if p.is_file():
        for x in p.read_text(encoding="utf-8").splitlines():
            if x.strip():
                r = json.loads(x)
                if not r.get("error"):
                    out[r["id"]] = r
    return out


def describe_one(client, jpeg: bytes) -> dict:
    b64 = base64.standard_b64encode(jpeg).decode("ascii")
    t0 = time.perf_counter()
    resp = cd.with_retry(lambda: client.beta.messages.parse(
        model=DESC_MODEL, max_tokens=16000, system=SYSTEM_PROMPT_V2,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": cd.USER_PROMPT}]}],
        output_format=PhotoDescriptionV2, output_config={"effort": "medium"},
        betas=["server-side-fallback-2026-07-01"], fallbacks="default"))
    rec = {"served_model": resp.model, "stop": resp.stop_reason, "in_tok": resp.usage.input_tokens,
           "out_tok": resp.usage.output_tokens, "latency_s": round(time.perf_counter() - t0, 2)}
    if resp.stop_reason != "end_turn" or resp.parsed_output is None:
        rec["error"] = f"stop={resp.stop_reason}"
        return rec
    return {**rec, **resp.parsed_output.model_dump()}


def cmd_describe(args) -> None:
    cat = load_catalog()
    done = load_desc()
    todo = [r for r in cat if r["id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    if not todo:
        print("모두 설명되어 있습니다.")
        return
    client = cd.anthropic_client(cd.load_env())
    print(f"{len(todo)}장 설명 ({DESC_MODEL}, effort medium, {PROMPT_VERSION})")
    ok = fail = 0
    cost = 0.0

    def work(r):
        try:
            rec = describe_one(client, (PHOTOS / r["copy"]).read_bytes())
        except Exception as e:
            rec = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
        return {"id": r["id"], "prompt_version": PROMPT_VERSION, "model": DESC_MODEL, **rec}

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for f in as_completed([ex.submit(work, r) for r in todo]):
            rec = f.result()
            with _lock, desc_path().open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec.get("error"):
                fail += 1
                print(f"✗ {rec['id']} {rec['error']}", flush=True)
            else:
                ok += 1
                cost += (rec["in_tok"] * 4 + rec["out_tok"] * 20) / 1e6
                if ok % 20 == 0:
                    print(f"  {ok}/{len(todo)} 완료 (비용 약 ${cost:.2f})", flush=True)
    print(f"성공 {ok}, 실패 {fail}, 비용 약 ${cost:.2f} -> {desc_path()}")


# ---------------------------------------------------------------- hindsight
def hs(method: str, path: str, **kw):
    with httpx.Client(base_url=HS_URL, timeout=httpx.Timeout(600.0, connect=10.0)) as c:
        r = c.request(method, path, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else {}


def photo_item(r: dict, d: dict) -> dict:
    when = r["time"]
    lines = [f"[사진] 사진 id: {r['id']}",
             f"찍은 시각: {when[:10]} {when[11:16]} ({BASIS_KO.get(r['time_basis'], '시각 모름')})" if when else "찍은 시각: 모름",
             f"출처: {r['badge']} 폴더",
             f"장소 종류: {d['place']}",
             d["scene"]]
    if d["text_in_photo"].strip():
        lines.append("사진 속 글자:\n" + d["text_in_photo"].strip())
    item = {"content": "\n".join(lines), "context": "휴대폰 사진첩에 있는 사진 한 장의 설명",
            "document_id": f"photo:{r['id']}",
            "metadata": {"kind": "photo", "photo_id": r["id"], "source": r["badge"], "time_basis": r["time_basis"] or ""},
            "tags": ["photo"]}
    if when:
        item["timestamp"] = when
    return item


def first_sentence(s: str, n: int = 70) -> str:
    s = s.strip().split(". ")[0].split("다. ")[0]
    return s if len(s) <= n else s[: n - 1] + "…"


def timeline_items(cat: list[dict], desc: dict[str, dict]) -> list[dict]:
    """날짜마다 그날 사진을 찍은 시각 순으로 한 줄씩 적은 기억. 장소나 사건을 코드가 해석하지 않는다."""
    by_date: dict[str, list[dict]] = {}
    for r in cat:
        if r["time"] and r["time_basis"] in ("taken", "filename") and r["id"] in desc:
            by_date.setdefault(r["time"][:10], []).append(r)
    items = []
    wd = "월화수목금토일"
    for date, rs in sorted(by_date.items()):
        rs.sort(key=lambda r: r["time"])
        dt = datetime.fromisoformat(rs[0]["time"])
        head = f"[하루 타임라인] {dt.year}년 {dt.month}월 {dt.day}일({wd[dt.weekday()]})에 찍은 사진 {len(rs)}장을 찍은 시각 순서로 적었다."
        lines = [head]
        for r in rs:
            d = desc[r["id"]]
            line = f"- {r['time'][11:16]} · {d['place']} · 출처 {r['badge']} · 사진 id {r['id']} · {first_sentence(d['scene'])}"
            txt = " ".join(d["text_in_photo"].split())
            if txt:
                line += f" · 사진 속 글자: {txt[:60]}"
            lines.append(line)
        items.append({"content": "\n".join(lines), "timestamp": rs[0]["time"], "context": "하루 동안 찍은 사진의 시간 순서 기록",
                      "document_id": f"timeline:{date}", "metadata": {"kind": "timeline", "date": date}, "tags": ["timeline"]})
    return items


def wait_ops(op_ids: list[str], label: str) -> None:
    pending = set(op_ids)
    t0 = time.time()
    while pending:
        for op in list(pending):
            st = hs("GET", f"/v1/default/banks/{BANK}/operations/{op}")
            s = (st.get("status") or "").lower()
            if s in ("completed", "succeeded", "success", "done", "failed", "error", "cancelled"):
                pending.discard(op)
                if s in ("failed", "error", "cancelled"):
                    print(f"✗ {label} 작업 {op} {s}: {str(st)[:300]}", flush=True)
        if pending:
            print(f"  {label}: 남은 작업 {len(pending)}개, {int(time.time() - t0)}초 지남", flush=True)
            time.sleep(15)


def existing_docs() -> set[str]:
    ids, offset = set(), 0
    while True:
        res = hs("GET", f"/v1/default/banks/{BANK}/documents", params={"limit": 100, "offset": offset})
        docs = res.get("documents") or res.get("items") or (res if isinstance(res, list) else [])
        for d in docs:
            ids.add(d.get("id") or d.get("document_id"))
        if len(docs) < 100:
            return ids
        offset += 100


def cmd_status(args) -> None:
    res = hs("GET", f"/v1/default/banks/{BANK}/operations", params={"limit": 100})
    ops = res.get("operations") or (res if isinstance(res, list) else [])
    by: dict[str, int] = {}
    for o in ops:
        by[o.get("status") or "?"] = by.get(o.get("status") or "?", 0) + 1
    docs = existing_docs()
    print(f"작업 {by} · 문서 {len(docs)}개(사진 {sum(1 for d in docs if d and d.startswith('photo:'))}, "
          f"타임라인 {sum(1 for d in docs if d and d.startswith('timeline:'))})")


def cmd_retain(args) -> None:
    cat = load_catalog()
    desc = load_desc()
    if args.phase == "photos":
        items = [photo_item(r, desc[r["id"]]) for r in cat if r["id"] in desc]
    else:
        items = timeline_items(cat, desc)
    if args.missing_only:
        have = existing_docs()
        items = [it for it in items if it["document_id"] not in have]
    print(f"{args.phase}: 기억 {len(items)}개를 은행 {BANK}에 넣는다")
    if not items:
        return
    ops = []
    for i in range(0, len(items), args.batch):
        res = hs("POST", f"/v1/default/banks/{BANK}/memories", json={"items": items[i:i + args.batch], "async": True})
        ops += [o for o in (res.get("operation_ids") or [res.get("operation_id")]) if o]
    print(f"  비동기 작업 {len(ops)}개 시작")
    wait_ops(ops, args.phase)
    stats = hs("GET", f"/v1/default/banks/{BANK}/memories/list", params={"limit": 1})
    print(f"완료. {str(stats)[:200]}")


REFLECT_SCHEMA = {
    "type": "object",
    "properties": {
        "photo_ids": {"type": "array", "items": {"type": "string"}, "description": "질의에 맞는 사진의 사진 id 목록"},
        "reasoning": {"type": "string", "description": "어떤 기억과 시각을 근거로 골랐는지 한국어 3~5문장"},
    },
    "required": ["photo_ids", "reasoning"],
}


def cmd_ask(args) -> None:
    ans = json.loads(Path(args.answer).read_text(encoding="utf-8"))
    answer, traps = set(ans["answer"]), set(ans.get("traps", []))
    out = {"phase": args.phase, "query": args.query, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "answer": sorted(answer), "traps": sorted(traps)}
    rec = hs("POST", f"/v1/default/banks/{BANK}/memories/recall",
             json={"query": args.query, "budget": "mid", "max_tokens": 8192})
    hits = []
    for i, r in enumerate(rec.get("results", []), 1):
        doc = r.get("document_id") or ""
        pid = doc.split(":", 1)[1] if doc.startswith("photo:") else None
        hits.append({"rank": i, "doc": doc, "photo_id": pid, "kind": (r.get("metadata") or {}).get("kind"),
                     "occurred": r.get("occurred_start"), "text": r.get("text", "")[:240],
                     "label": "answer" if pid in answer else "trap" if pid in traps else ("timeline" if doc.startswith("timeline:") else "other")})
    out["recall"] = hits
    seen_photos = []
    for h in hits:
        if h["photo_id"] and h["photo_id"] not in seen_photos:
            seen_photos.append(h["photo_id"])
    out["recall_photos"] = seen_photos
    out["recall_answer_in_top10"] = len(answer & set(seen_photos[:10]))
    out["recall_trap_in_top10"] = len(traps & set(seen_photos[:10]))
    out["recall_timeline_rank"] = next((h["rank"] for h in hits if h["label"] == "timeline"), None)
    out["reflect"] = []
    for k in range(args.reflect_runs):
        t0 = time.perf_counter()
        try:
            rf = hs("POST", f"/v1/default/banks/{BANK}/reflect",
                    json={"query": args.query + " 해당하는 사진의 사진 id 목록과 근거를 알려 줘.", "budget": "mid",
                          "response_schema": REFLECT_SCHEMA, "include": {"tool_calls": {}}})
        except Exception as e:
            out["reflect"].append({"run": k + 1, "error": str(e)[:300]})
            continue
        so = rf.get("structured_output") or {}
        ids = [re.sub(r"^photo:", "", x.strip()) for x in so.get("photo_ids", [])]
        got = set(ids)
        tc = ((rf.get("trace") or {}).get("tool_calls") or [])
        out["reflect"].append({
            "run": k + 1, "seconds": round(time.perf_counter() - t0, 1), "photo_ids": ids,
            "correct": len(got & answer), "traps": len(got & traps), "other": len(got - answer - traps),
            "precision": round(len(got & answer) / len(got), 2) if got else 0.0,
            "recall": round(len(got & answer) / len(answer), 2) if answer else 0.0,
            "reasoning": so.get("reasoning") or rf.get("text", "")[:800], "tool_calls": len(tc),
            "tool_queries": [((c.get("input") or c.get("arguments") or {}) if isinstance(c, dict) else {}).get("query") for c in tc][:12],
            "error": rf.get("structured_output_error")})
    p = OUT / f"ask_{args.phase}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{args.phase}] recall: 정답 {out['recall_answer_in_top10']}/{len(answer)} · 함정 {out['recall_trap_in_top10']} (상위 10장), "
          f"타임라인 순위 {out['recall_timeline_rank']}")
    for r in out["reflect"]:
        if r.get("error") and not r.get("photo_ids"):
            print(f"  reflect {r['run']}: 오류 {r['error']}")
        else:
            print(f"  reflect {r['run']}: 정답 {r['correct']}/{len(answer)} · 함정 {r['traps']} · 기타 {r['other']} "
                  f"(도구 호출 {r['tool_calls']}회, {r['seconds']}초)")
    print(f"-> {p}")


# ---------------------------------------------------------------- 대시보드 데이터
PHASE_KO = {"A": "사진 기억만", "B": "사진 + 하루 타임라인"}


def _thumb(copy: str) -> str:
    with Image.open(PHOTOS / copy) as im:
        im.load()
        t = im.copy()
    t.thumbnail((240, 240))
    buf = io.BytesIO()
    t.save(buf, "JPEG", quality=72, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def cmd_dash(args) -> None:
    ans = json.loads(Path(args.answer).read_text(encoding="utf-8"))
    answer, traps, exhib = set(ans["answer"]), set(ans.get("traps", [])), set(ans.get("exhibition", []))
    cat = {r["id"]: r for r in load_catalog()}
    desc = load_desc()

    def label(pid: str) -> str:
        return "answer" if pid in answer else "trap" if pid in traps else "exhibition" if pid in exhib else "other"

    runs, rets, used = [], [], set(answer | traps | exhib)
    for ph in ("A", "B"):
        p = OUT / f"ask_{ph}.json"
        if not p.is_file():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        top = d["recall_photos"][:10]
        runs.append({"phase": ph, "phase_label": PHASE_KO[ph], "method": "recall", "method_label": "recall 상위 10장",
                     "correct": len(answer & set(top)), "traps": len(traps & set(top)),
                     "other": len(set(top) - answer - traps), "returned": len(top),
                     "timeline_rank": d.get("recall_timeline_rank"), "seconds": None, "tool_calls": None, "reasoning": ""})
        for i, pid in enumerate(top, 1):
            rets.append({"phase": ph, "method": "recall", "rank": i, "photo_id": pid, "label": label(pid)})
            used.add(pid)
        for r in d.get("reflect", []):
            m = f"reflect{r['run']}"
            ids = [x for x in r.get("photo_ids", []) if x in cat]
            runs.append({"phase": ph, "phase_label": PHASE_KO[ph], "method": m, "method_label": f"reflect {r['run']}회차",
                         "correct": r.get("correct", 0), "traps": r.get("traps", 0), "other": r.get("other", 0),
                         "returned": len(r.get("photo_ids", [])), "timeline_rank": None, "seconds": r.get("seconds"),
                         "tool_calls": r.get("tool_calls"), "reasoning": r.get("reasoning") or r.get("error") or ""})
            for i, pid in enumerate(ids, 1):
                rets.append({"phase": ph, "method": m, "rank": i, "photo_id": pid, "label": label(pid)})
                used.add(pid)
    photos = []
    for pid in sorted(used, key=lambda x: (cat[x]["time"] or "", x)):
        r = cat[pid]
        d = desc.get(pid, {})
        photos.append({"photo_id": pid, "date": (r["time"] or "")[:10], "time": (r["time"] or "")[11:16], "badge": r["badge"],
                       "place": d.get("place", ""), "label": label(pid), "thumb": _thumb(r["copy"]),
                       "scene": first_sentence(d.get("scene", ""), 90)})
    inputs = ["var/scenario/ask_A.json", "var/scenario/ask_B.json", "var/scenario/catalog.jsonl", "answer.json(채점 전용)"]
    meta = lambda: [{"label": Path(__file__).name, "value": Path(__file__).read_text(encoding="utf-8"), "type": "code", "lang": "python"},
                    {"label": "Input", "value": ", ".join(inputs)},
                    {"label": "Made at (UTC)", "value": datetime.now(timezone.utc).isoformat(timespec="seconds")}]
    outdir = OUT / "dash"
    outdir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("scenario_runs", runs), ("scenario_returns", rets), ("scenario_photos", photos)):
        (outdir / f"{name}.json").write_text(json.dumps({"rows": rows, "meta": meta()}, ensure_ascii=False), encoding="utf-8")
        print(f"  {name}.json {len(rows)}행 {(outdir / f'{name}.json').stat().st_size // 1024}KB")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect")
    p = sub.add_parser("describe")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=8)
    p = sub.add_parser("retain")
    p.add_argument("--phase", choices=["photos", "timeline"], required=True)
    p.add_argument("--batch", type=int, default=25)
    p.add_argument("--missing-only", action="store_true")
    sub.add_parser("status")
    p = sub.add_parser("ask")
    p.add_argument("--phase", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--answer", required=True)
    p.add_argument("--reflect-runs", type=int, default=2)
    p = sub.add_parser("dash")
    p.add_argument("--answer", required=True)
    args = ap.parse_args()
    {"collect": cmd_collect, "describe": cmd_describe, "retain": cmd_retain, "ask": cmd_ask, "dash": cmd_dash,
     "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    main()

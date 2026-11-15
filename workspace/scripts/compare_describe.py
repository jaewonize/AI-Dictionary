# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "anthropic>=2.8,<3",
#     "google-genai>=2.34,<3",
#     "pillow>=11",
#     "python-dotenv>=1.0",
#     "pydantic>=2.8",
# ]
# ///
"""사진 설명(색인용 글) 비교 실험: Claude Opus 5.5 와 Gemini 모델을 같은 지시·같은 스키마로 돌려 본다.

workspace/ 에서 차례로 실행한다(Python 경로는 uv 가 고른다).
  uv run --script scripts/compare_describe.py prepare        # 메타데이터를 뺀 사진 사본과 썸네일
  uv run --script scripts/compare_describe.py run            # 모든 변형 실행(이미 성공한 것은 건너뜀)
  uv run --script scripts/compare_describe.py run --variants claude_medium,argon_high --limit 1
  uv run --script scripts/compare_describe.py blind          # 심사용 블라인드 입력(라벨 무작위)
  uv run --script scripts/compare_describe.py judge-gemini   # Gemini 블라인드 심사
  uv run --script scripts/compare_describe.py report         # var/compare/report.html, summary.json

설정은 python-dotenv 로 저장소 맨 위와 workspace/ 의 설정 파일을 읽는다(workspace 쪽이 우선).
Claude 키는 ANTHROPIC_API_KEY, 없으면 CLAUD_API_KEY 를 쓴다. Gemini 는 ADC 와 GOOGLE_CLOUD_PROJECT 를 쓴다.
키 값은 화면·파일 어디에도 쓰지 않는다.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import math
import os
import random
import re
import statistics
import unicodedata
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from PIL import Image, ImageOps
from pydantic import BaseModel

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

WS = Path(__file__).resolve().parents[1]
REPO = WS.parent
OUT = WS / "var" / "compare"
PHOTOS = OUT / "photos"
THUMBS = OUT / "thumbs"
SAMPLES_TXT = Path(__file__).with_name("compare_samples.txt")
SAMPLES_JSON = OUT / "samples.json"
RESULTS = OUT / "results.jsonl"
JUDGE_IN = OUT / "judge_in"
JUDGE_KEY = OUT / "judge_key.json"
JUDGE_GEMINI = OUT / "judge_gemini.jsonl"
JUDGE_CLAUDE_DIR = OUT / "judge_claude"
REPORT = OUT / "report.html"
SUMMARY = OUT / "summary.json"

PROMPT_VERSION = "describe-cmp-v1"
GROUP_SIZE = 4

# 같은 지시를 두 회사 모델에 그대로 준다.
SYSTEM_PROMPT = """당신은 휴대폰 사진 검색용 색인을 만드는 분석기입니다. 사진 한 장을 보고, 나중에 사용자가 기억나는 대로 한국어로 물었을 때 이 사진을 찾을 수 있도록 사진에 보이는 내용을 글로 적습니다.

규칙
- 사진에 실제로 보이는 것만 적습니다. 판단이 필요한 것은 "~로 보인다"라고 쓰고, 보이지 않는 것은 쓰지 않습니다.
- 찍은 날짜, 시각, 장소 이름은 사진 속 글자나 화면에 보일 때만 적습니다. 사람의 신원(이름, 나이)은 추측하지 않습니다.
- scene: 한국어 3~6문장. 장소의 종류(카페, 전시장, 식당, 거리, 집 안 등)와 실내·실외, 주요 사물·음식·음료, 사람(인원과 하는 일), 그리고 색·조명·인테리어·창밖 풍경처럼 비슷한 다른 사진과 구별되는 특징을 적습니다.
- text_in_photo: 사진 속에 보이는 글자(간판, 메뉴판, 포스터, 작품 설명, 유리·벽의 글씨, 문서, 영수증, 화면)를 원문 그대로 옮깁니다. 덩어리마다 줄을 바꿉니다. 읽을 수 없는 부분은 옮기지 않습니다. 글자가 없으면 빈 문자열입니다.
- photo_type: 일반 장면 사진은 scene, 종이 문서·영수증·견적서를 찍은 사진은 document, 휴대폰·컴퓨터 화면 캡처는 screenshot 입니다."""

USER_PROMPT = "이 사진의 색인 항목을 만들어 주세요."


class PhotoDescription(BaseModel):
    scene: str
    text_in_photo: str
    photo_type: Literal["scene", "document", "screenshot"]


# 비교할 변형. claude_medium 과 argon_high 가 본 비교 짝이다.
VARIANTS: dict[str, dict] = {
    "claude_medium": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "medium",
                      "label": "Claude Opus 5.5 · effort medium"},
    "claude_low": {"provider": "anthropic", "model": "claude-opus-5-5", "effort": "low",
                   "label": "Claude Opus 5.5 · effort low"},
    "argon_high": {"provider": "gemini", "model": "gemini-4-argon", "thinking": "MEDIUM", "media": "HIGH",
                   "label": "Gemini 4 Argon · thinking MEDIUM · 해상도 HIGH(=기본)"},
    # 설정 단위 HIGH 는 기본값과 토큰이 같아(이미지당 약 1,120) 사진 조각 단위 ULTRA_HIGH 를 따로 본다.
    "argon_ultra": {"provider": "gemini", "model": "gemini-4-argon", "thinking": "MEDIUM", "media": "ULTRA_HIGH",
                    "label": "Gemini 4 Argon · thinking MEDIUM · 해상도 ULTRA_HIGH"},
    "flash38_high": {"provider": "gemini", "model": "gemini-3.8-flash", "thinking": "MEDIUM", "media": "HIGH",
                     "label": "Gemini 3.8 Flash · thinking MEDIUM · 해상도 HIGH"},
}

# 100만 토큰당 달러. Claude 는 확인된 가격, Gemini 는 공식 가격표를 아직 못 읽어 미확인(보고서에 그렇게 적는다).
PRICES = {
    "claude-opus-5-5": {"in": 4.0, "out": 20.0, "verified": True},
    "gemini-4-argon": {"in": 2.0, "out": 10.0, "verified": False},
    "gemini-3.8-flash": {"in": None, "out": None, "verified": False},
}

_lock = threading.Lock()


# ---------------------------------------------------------------- 설정
def load_env() -> dict[str, str]:
    vals: dict[str, str] = {}
    for p in (REPO / ".env", WS / ".env"):
        if p.is_file():
            vals.update({k: v for k, v in dotenv_values(p).items() if v})
    return vals


def phone_dir(env: dict[str, str]) -> Path:
    p = env.get("AIDIC_PHONE_DIR")
    if p:
        q = Path(p)
        return q if q.is_absolute() else (WS / q).resolve()
    return REPO / "Mobile Storage"


def anthropic_client(env: dict[str, str]):
    import anthropic

    # 셸에 남은 Anthropic 변수는 쓰지 않는다(Claude Code 안에서 돌 때 섞이지 않게).
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        os.environ.pop(k, None)
    key = env.get("ANTHROPIC_API_KEY") or env.get("CLAUD_API_KEY")
    if not key:
        sys.exit("설정 파일에 ANTHROPIC_API_KEY(또는 CLAUD_API_KEY)가 없습니다.")
    return anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com", max_retries=3, timeout=600)


def gemini_client(env: dict[str, str]):
    from google import genai

    project = env.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        sys.exit("설정 파일에 GOOGLE_CLOUD_PROJECT 가 없습니다.")
    return genai.Client(enterprise=True, project=project, location="global")


# ---------------------------------------------------------------- 샘플과 사진 사본
def read_sample_ids() -> list[str]:
    ids = []
    for line in SAMPLES_TXT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            ids.append(line)
    return ids


def clean_copy(src: Path, dst: Path, thumb: Path) -> dict:
    with Image.open(src) as im:
        fmt, mode, frames = im.format, im.mode, getattr(im, "n_frames", 1)
        orientation = im.getexif().get(0x0112, 1)
        im.seek(0)  # MPO: 본 사진(첫 장)만
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, (255, 255, 255))
            rgb.paste(rgba, mask=rgba.getchannel("A"))
        else:
            rgb = im.convert("RGB")
        clean = Image.frombytes("RGB", rgb.size, rgb.tobytes())  # 픽셀만 새 이미지로
    dst.parent.mkdir(parents=True, exist_ok=True)
    clean.save(dst, "JPEG", quality=90)
    t = clean.copy()
    t.thumbnail((520, 520))
    thumb.parent.mkdir(parents=True, exist_ok=True)
    t.save(thumb, "JPEG", quality=82)
    with Image.open(dst) as chk:  # 메타데이터가 비었는지 다시 확인
        if chk.getexif() or any(k in chk.info for k in ("xmp", "XML:com.adobe.xmp", "exif", "comment")):
            raise RuntimeError(f"메타데이터가 남음: {dst}")
    raw = dst.read_bytes()
    if b"Exif\x00\x00" in raw or b"http://ns.adobe.com/xap" in raw:
        raise RuntimeError(f"메타데이터가 남음: {dst}")
    return {"format": fmt, "mode": mode, "frames": frames, "orientation": orientation,
            "width": clean.width, "height": clean.height, "bytes": len(raw)}


def cmd_prepare(args) -> None:
    env = load_env()
    root = phone_dir(env)
    ids = read_sample_ids()
    samples = []
    for i, sid in enumerate(ids, 1):
        key = f"P{i:02d}"
        src = root / sid
        info = clean_copy(src, PHOTOS / f"{key}.jpg", THUMBS / f"{key}.jpg")
        samples.append({"key": key, "id": sid, **info})
        print(f"{key}  {sid:55s} {info['format']:4s} {info['mode']:4s} frames={info['frames']} "
              f"rot={info['orientation']} -> {info['width']}x{info['height']} {info['bytes'] // 1024}KB")
    OUT.mkdir(parents=True, exist_ok=True)
    SAMPLES_JSON.write_text(json.dumps(samples, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(samples)}장 준비 완료 -> {PHOTOS}")


def load_samples() -> list[dict]:
    if not SAMPLES_JSON.is_file():
        sys.exit("먼저 prepare 를 실행하세요.")
    return json.loads(SAMPLES_JSON.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 모델 호출
def _status(e: Exception) -> int | None:
    for attr in ("status_code", "code"):
        v = getattr(e, attr, None)
        if isinstance(v, int):
            return v
    return None


# Gemini(Vertex AI) 호출 규칙. 2026-11-15 Gemini 4 Argon을 동시 8개·짧은 재시도로 부르다 Vertex AI 프로젝트가
# 정지됐고, 재검토 요청에서 아래를 약속했다. 그래서 Gemini 호출은 모두 gemini_call() 을 거쳐 코드로 강제한다.
#   한 번에 1~2개만 보낸다 · 429에는 지수 백오프 · 몇 번 뒤 포기 · 한도에 닿으면 재시도하지 않고 멈춘다(할당량 상향 요청)
# Claude는 이 규칙을 따르지 않는다(429가 나지 않았다). Claude 쪽 재시도는 with_retry 와 Anthropic SDK가 맡는다.
GEMINI_MAX_CONCURRENCY = 2      # 동시 호출 상한. 인자로 더 큰 값을 줘도 이 값을 넘지 않는다
GEMINI_MIN_INTERVAL_S = 2.0     # 요청 시작 사이 최소 간격(분당 30회 이하로 고르게)
MAX_CONSECUTIVE_429 = 5         # Gemini 429가 이만큼 연달아 나면 작업 전체를 멈춘다
_gemini_sem = threading.BoundedSemaphore(GEMINI_MAX_CONCURRENCY)
_gemini_pace = {"next_start": 0.0}
_gemini_pace_lock = threading.Lock()
_breaker = {"consecutive_429": 0, "tripped": False}
_breaker_lock = threading.Lock()


def _transient(e: Exception, st: int | None) -> bool:
    name = type(e).__name__.lower()
    return st in (408, 429, 500, 502, 503, 504) or "timeout" in name or "connection" in name


def with_retry(fn, tries: int = 3, base_delay: float = 5.0):
    """일시 오류만 지수 백오프로 다시 보낸다(Claude 등 일반 호출)."""
    delay = base_delay
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as e:
            st = _status(e)
            if not _transient(e, st) or attempt == tries:
                raise
            wait = delay + random.uniform(0, delay / 2)  # 여러 작업이 같은 순간에 다시 보내지 않게 흩뜨린다
            print(f"   재시도 {attempt}/{tries - 1} ({type(e).__name__} {st}) {wait:.0f}s 뒤", flush=True)
            time.sleep(wait)
            delay *= 2


def _gemini_wait_turn() -> None:
    with _gemini_pace_lock:
        now = time.monotonic()
        start = max(now, _gemini_pace["next_start"])
        _gemini_pace["next_start"] = start + GEMINI_MIN_INTERVAL_S
    if start > now:
        time.sleep(start - now)


def gemini_call(fn, tries: int = 3):
    """Gemini 호출: 동시 2개 상한, 요청 간격, 429 백오프(15초부터), 429 연속 시 작업 전체 중단."""
    delay = 15.0
    for attempt in range(1, tries + 1):
        if _breaker["tripped"]:
            raise RuntimeError("Gemini 429가 연달아 나서 멈춘 상태다. 재시도하지 말고 할당량을 확인·상향한 뒤 다시 돌린다")
        with _gemini_sem:
            _gemini_wait_turn()
            try:
                out = fn()
            except Exception as e:
                st = _status(e)
                err = e
            else:
                with _breaker_lock:
                    _breaker["consecutive_429"] = 0
                return out
        if st == 429:
            with _breaker_lock:
                _breaker["consecutive_429"] += 1
                if _breaker["consecutive_429"] >= MAX_CONSECUTIVE_429:
                    _breaker["tripped"] = True
            if _breaker["tripped"]:
                raise RuntimeError(f"Gemini 429가 연달아 {MAX_CONSECUTIVE_429}번 나서 멈춘다. 재시도하지 말고 할당량을 확인·상향한다") from err
        if not _transient(err, st) or attempt == tries:
            raise err
        wait = delay + random.uniform(0, delay / 2)
        print(f"   Gemini 재시도 {attempt}/{tries - 1} ({type(err).__name__} {st}) {wait:.0f}s 뒤", flush=True)
        time.sleep(wait)  # 기다리는 동안은 동시 호출 자리를 내준다
        delay *= 2


def call_claude(client, v: dict, jpeg: bytes) -> dict:
    b64 = base64.standard_b64encode(jpeg).decode("ascii")
    t0 = time.perf_counter()
    resp = with_retry(lambda: client.beta.messages.parse(
        model=v["model"],
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": USER_PROMPT},
        ]}],
        output_format=PhotoDescription,
        output_config={"effort": v["effort"]},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ))
    latency = time.perf_counter() - t0
    u = resp.usage
    iterations = getattr(u, "iterations", None) or []
    fallback = any(getattr(it, "type", None) == "fallback_message" for it in iterations)
    rec = {
        "served_model": resp.model, "fallback": fallback, "stop": resp.stop_reason,
        "in_tok": u.input_tokens, "out_tok": u.output_tokens, "thought_tok": None,
        "latency_s": round(latency, 2),
    }
    p = PRICES.get(v["model"])
    rec["cost_usd"] = round((u.input_tokens * p["in"] + u.output_tokens * p["out"]) / 1e6, 5) if p else None
    if resp.stop_reason == "refusal":
        sd = getattr(resp, "stop_details", None)
        rec["error"] = f"refusal ({getattr(sd, 'category', None)})"
        return rec
    if resp.stop_reason == "max_tokens":
        rec["error"] = "max_tokens"
        return rec
    parsed = resp.parsed_output
    if parsed is None:
        rec["error"] = "parse failed"
        return rec
    rec.update(parsed.model_dump())
    return rec


def call_gemini(client, v: dict, jpeg: bytes) -> dict:
    from google.genai import types

    cfg = {
        "system_instruction": SYSTEM_PROMPT,
        "response_mime_type": "application/json",
        "response_schema": PhotoDescription,
        "thinking_config": types.ThinkingConfig(thinking_level=v["thinking"]),
        "max_output_tokens": 16000,
    }
    image = types.Part.from_bytes(data=jpeg, mime_type="image/jpeg")
    if v.get("media") == "ULTRA_HIGH":  # 설정 단위에는 없고 사진 조각 단위로만 있다
        image = types.Part(
            inline_data=types.Blob(data=jpeg, mime_type="image/jpeg"),
            media_resolution=types.PartMediaResolution(level=types.PartMediaResolutionLevel.MEDIA_RESOLUTION_ULTRA_HIGH),
        )
    elif v.get("media"):
        cfg["media_resolution"] = getattr(types.MediaResolution, f"MEDIA_RESOLUTION_{v['media']}")
    t0 = time.perf_counter()
    resp = gemini_call(lambda: client.models.generate_content(
        model=v["model"],
        contents=[image, USER_PROMPT],
        config=types.GenerateContentConfig(**cfg),
    ))
    latency = time.perf_counter() - t0
    um = resp.usage_metadata
    cand = (resp.candidates or [None])[0]
    finish = str(getattr(cand, "finish_reason", None))
    in_tok = getattr(um, "prompt_token_count", None) or 0
    out_tok = getattr(um, "candidates_token_count", None) or 0
    th_tok = getattr(um, "thoughts_token_count", None) or 0
    rec = {
        "served_model": getattr(resp, "model_version", None) or v["model"], "fallback": False, "stop": finish,
        "in_tok": in_tok, "out_tok": out_tok + th_tok, "thought_tok": th_tok,
        "latency_s": round(latency, 2),
    }
    p = PRICES.get(v["model"]) or {}
    rec["cost_usd"] = round((in_tok * p["in"] + (out_tok + th_tok) * p["out"]) / 1e6, 5) if p.get("in") else None
    parsed = resp.parsed
    if parsed is None and resp.text:
        try:
            parsed = PhotoDescription.model_validate_json(resp.text)
        except Exception:
            parsed = None
    if parsed is None:
        rec["error"] = f"no parsed output (finish={finish})"
        return rec
    if isinstance(parsed, dict):
        parsed = PhotoDescription.model_validate(parsed)
    rec.update(parsed.model_dump())
    return rec


def load_results() -> list[dict]:
    if not RESULTS.is_file():
        return []
    return [json.loads(x) for x in RESULTS.read_text(encoding="utf-8").splitlines() if x.strip()]


def cmd_run(args) -> None:
    env = load_env()
    samples = load_samples()
    if args.limit:
        samples = samples[: args.limit]
    names = args.variants.split(",") if args.variants else list(VARIANTS)
    for n in names:
        if n not in VARIANTS:
            sys.exit(f"알 수 없는 변형: {n} (가능: {', '.join(VARIANTS)})")
    done = {(r["key"], r["variant"]) for r in load_results() if not r.get("error")}
    jobs = [(s, n) for s in samples for n in names if (s["key"], n) not in done]
    if not jobs:
        print("모두 완료되어 있습니다.")
        return
    clients = {}
    if any(VARIANTS[n]["provider"] == "anthropic" for _, n in jobs):
        clients["anthropic"] = anthropic_client(env)
    if any(VARIANTS[n]["provider"] == "gemini" for _, n in jobs):
        clients["gemini"] = gemini_client(env)
    print(f"{len(jobs)}건 실행 (변형 {len(names)}개 x 사진 {len(samples)}장, 이미 끝난 것 제외)")
    OUT.mkdir(parents=True, exist_ok=True)

    def work(s: dict, n: str) -> dict:
        v = VARIANTS[n]
        jpeg = (PHOTOS / f"{s['key']}.jpg").read_bytes()
        base = {"key": s["key"], "id": s["id"], "variant": n, "provider": v["provider"], "model": v["model"],
                "settings": {k: v[k] for k in ("effort", "thinking", "media") if k in v},
                "prompt_version": PROMPT_VERSION, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            fn = call_claude if v["provider"] == "anthropic" else call_gemini
            rec = fn(clients[v["provider"]], v, jpeg)
        except Exception as e:
            rec = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
        return {**base, **rec}

    ok = fail = 0
    gemini_workers = min(args.gemini_workers, GEMINI_MAX_CONCURRENCY)
    if args.gemini_workers > GEMINI_MAX_CONCURRENCY:
        print(f"Gemini 동시 호출은 {GEMINI_MAX_CONCURRENCY}개를 넘기지 않는다(요청 {args.gemini_workers} → {gemini_workers})")
    # 모델별로 실행 풀을 나눈다: Claude는 차단 없이 돌던 기준(동시 8개), Gemini는 상한 2개
    with ThreadPoolExecutor(max_workers=args.claude_workers) as ex_c, ThreadPoolExecutor(max_workers=gemini_workers) as ex_g:
        futs = [(ex_c if VARIANTS[n]["provider"] == "anthropic" else ex_g).submit(work, s, n) for s, n in jobs]
        for f in as_completed(futs):
            r = f.result()
            with _lock:
                with RESULTS.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            if r.get("error"):
                fail += 1
                print(f"✗ {r['key']} {r['variant']:14s} {r['error']}", flush=True)
            else:
                ok += 1
                print(f"✓ {r['key']} {r['variant']:14s} {r['latency_s']:6.1f}s in={r['in_tok']} out={r['out_tok']} "
                      f"type={r['photo_type']} scene={len(r['scene'])}자 text={len(r['text_in_photo'])}자", flush=True)
    print(f"\n성공 {ok}, 실패 {fail} -> {RESULTS}")


# ---------------------------------------------------------------- 블라인드 심사 입력
def latest_ok(results: list[dict]) -> dict[tuple[str, str], dict]:
    best: dict[tuple[str, str], dict] = {}
    for r in results:
        if not r.get("error"):
            best[(r["key"], r["variant"])] = r  # 뒤에 쓴 것이 이긴다
    return best


def cmd_blind(args) -> None:
    samples = load_samples()
    best = latest_ok(load_results())
    JUDGE_IN.mkdir(parents=True, exist_ok=True)
    key_map: dict[str, dict[str, str]] = {}
    groups: list[list[dict]] = []
    for s in samples:
        items = [(n, best[(s["key"], n)]) for n in VARIANTS if (s["key"], n) in best]
        rng = random.Random(int(hashlib.sha256(s["key"].encode()).hexdigest()[:8], 16))
        rng.shuffle(items)
        labels = "ABCDEFGH"
        key_map[s["key"]] = {labels[i]: n for i, (n, _) in enumerate(items)}
        entry = {
            "key": s["key"],
            "image": str((PHOTOS / f"{s['key']}.jpg").resolve()),
            "entries": [{"label": labels[i], "scene": r["scene"], "text_in_photo": r["text_in_photo"],
                         "photo_type": r["photo_type"]} for i, (_, r) in enumerate(items)],
        }
        if not groups or len(groups[-1]) >= GROUP_SIZE:
            groups.append([])
        groups[-1].append(entry)
    for gi, g in enumerate(groups, 1):
        (JUDGE_IN / f"group_{gi}.json").write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
    JUDGE_KEY.write_text(json.dumps(key_map, ensure_ascii=False, indent=1), encoding="utf-8")
    (JUDGE_IN / "rubric.md").write_text(JUDGE_RUBRIC, encoding="utf-8")  # 두 심사자가 같은 기준을 쓰게
    print(f"심사 입력 {len(groups)}묶음 -> {JUDGE_IN}  (라벨 해독표: {JUDGE_KEY.name})")


JUDGE_RUBRIC = f"""당신은 휴대폰 사진 검색 색인의 품질 심사원입니다. 사진 한 장과, 서로 다른 시스템이 같은 지시로 만든 색인 항목 여러 개(라벨 A, B, C...)를 받습니다. 어떤 시스템이 어떤 라벨인지는 알려 주지 않으며 라벨 순서는 무작위입니다. 문체나 길이가 아니라 사진과 대조한 사실로 판단합니다.

색인 항목이 따라야 했던 지시는 아래와 같습니다.
<지시>
{SYSTEM_PROMPT}
</지시>

항목마다 1~5점(5가 가장 좋음)으로 채점합니다. 기준점: 5 = 문제 없음, 4 = 사소한 누락, 3 = 눈에 띄는 누락이나 사소한 오류 하나, 2 = 분명한 오류, 1 = 대부분 틀림. 사진마다 같은 기준을 지킵니다.
- accuracy: 사진과 어긋나거나 사진에서 확인되지 않는 주장이 없는가. 틀린 주장 하나마다 크게 깎는다.
- text: 사진 속 글자를 빠짐없이 정확히 옮겼는가. 지어낸 글자는 크게 깎는다. 사진에 글자가 없을 때 빈 값이면 5점.
- coverage: 장소 종류, 주요 사물·음식·음료, 사람, 실내외 같은 핵심 요소를 담았는가.
- distinctiveness: 비슷한 다른 사진(같은 종류의 다른 장소, 같은 장소의 다른 컷)과 구별할 특징을 담았는가.
- retrievability: 사용자가 기억나는 대로 한국어로 물었을 때(장소 종류, 먹은 것, 본 것, 보인 글자 등) 이 항목이 걸릴 만한 낱말을 갖췄는가.
- photo_type_correct: photo_type 이 맞으면 true.
- errors: 사진과 어긋나는 구체적 주장이나 잘못 읽은 글자를 짧게 적는다. 사진에서 확인할 수 없는 것은 "확인 불가:"로 시작한다. 없으면 빈 목록.
- note: 이 항목의 강점·약점 한 문장.
마지막으로 색인 용도로 가장 나은 라벨(동점이면 여럿)을 best_labels 에, 항목들이 색인으로 서로 바꿔 써도 될 만큼 비슷한지를 interchangeable 에 한 문장으로 적습니다. 모든 글은 한국어로 씁니다."""


class LabelScore(BaseModel):
    label: str
    accuracy: int
    text: int
    coverage: int
    distinctiveness: int
    retrievability: int
    photo_type_correct: bool
    errors: list[str]
    note: str


class JudgeResult(BaseModel):
    scores: list[LabelScore]
    best_labels: list[str]
    interchangeable: str


def cmd_judge_gemini(args) -> None:
    from google.genai import types

    env = load_env()
    client = gemini_client(env)
    groups = sorted(JUDGE_IN.glob("group_*.json"))
    entries = [e for g in groups for e in json.loads(g.read_text(encoding="utf-8"))]
    done = set()
    if JUDGE_GEMINI.is_file():
        done = {json.loads(x)["key"] for x in JUDGE_GEMINI.read_text(encoding="utf-8").splitlines() if x.strip()}
    todo = [e for e in entries if e["key"] not in done]
    print(f"Gemini 심사 {len(todo)}장 (모델 {args.judge_model})")

    def work(e: dict) -> dict:
        jpeg = Path(e["image"]).read_bytes()
        listing = json.dumps(e["entries"], ensure_ascii=False, indent=1)
        cfg = types.GenerateContentConfig(
            system_instruction=JUDGE_RUBRIC,
            response_mime_type="application/json",
            response_schema=JudgeResult,
            thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
            max_output_tokens=32000,
        )
        resp = gemini_call(lambda: client.models.generate_content(
            model=args.judge_model,
            contents=[types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
                      f"색인 항목들입니다. 라벨마다 채점하세요.\n{listing}"],
            config=cfg,
        ))
        parsed = resp.parsed
        if parsed is None:
            parsed = JudgeResult.model_validate_json(resp.text)
        if isinstance(parsed, dict):
            parsed = JudgeResult.model_validate(parsed)
        return {"key": e["key"], "judge": args.judge_model, **parsed.model_dump()}

    with ThreadPoolExecutor(max_workers=min(args.workers, GEMINI_MAX_CONCURRENCY)) as ex:
        futs = {ex.submit(work, e): e["key"] for e in todo}
        for f in as_completed(futs):
            k = futs[f]
            try:
                r = f.result()
            except Exception as ex_:
                print(f"✗ {k} {type(ex_).__name__}: {str(ex_)[:200]}")
                continue
            with _lock:
                with JUDGE_GEMINI.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"✓ {k} best={r['best_labels']}")


# ---------------------------------------------------------------- 보고서
CRITERIA = ["accuracy", "text", "coverage", "distinctiveness", "retrievability"]


def load_judges() -> dict[str, dict[str, dict]]:
    """{'claude': {key: result}, 'gemini': {key: result}}"""
    out: dict[str, dict[str, dict]] = {"claude": {}, "gemini": {}}
    if JUDGE_GEMINI.is_file():
        for x in JUDGE_GEMINI.read_text(encoding="utf-8").splitlines():
            if x.strip():
                r = json.loads(x)
                out["gemini"][r["key"]] = r
    if JUDGE_CLAUDE_DIR.is_dir():
        for f in sorted(JUDGE_CLAUDE_DIR.glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            for r in data if isinstance(data, list) else [data]:
                out["claude"][r["key"]] = r
    return out


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.fmean(xs), 2) if xs else None


def build_summary(samples, best, judges, key_map) -> dict:
    summary: dict = {"variants": {}, "judge_counts": {j: len(v) for j, v in judges.items()}}
    majority = {}
    for s in samples:
        types_ = [best[(s["key"], n)]["photo_type"] for n in VARIANTS if (s["key"], n) in best]
        majority[s["key"]] = max(set(types_), key=types_.count) if types_ else None
    for n, v in VARIANTS.items():
        rs = [best[(s["key"], n)] for s in samples if (s["key"], n) in best]
        row = {
            "label": v["label"], "ok": len(rs), "n": len(samples),
            "latency_s": mean([r["latency_s"] for r in rs]),
            "in_tok": mean([r["in_tok"] for r in rs]), "out_tok": mean([r["out_tok"] for r in rs]),
            "thought_tok": mean([r.get("thought_tok") for r in rs]),
            "cost_usd_total": round(sum(r["cost_usd"] for r in rs if r.get("cost_usd") is not None), 4)
            if any(r.get("cost_usd") is not None for r in rs) else None,
            "price_verified": PRICES.get(v["model"], {}).get("verified", False),
            "scene_chars": mean([len(r["scene"]) for r in rs]),
            "text_chars": mean([len(r["text_in_photo"]) for r in rs]),
            "type_agree": round(sum(r["photo_type"] == majority[r["key"]] for r in rs) / len(rs), 2) if rs else None,
            "fallbacks": sum(1 for r in rs if r.get("fallback")),
            "judge": {},
        }
        for jn, jd in judges.items():
            per = {c: [] for c in CRITERIA}
            ptc, best_cnt, nerr = [], 0, 0
            for k, res in jd.items():
                lab = next((l for l, vn in key_map.get(k, {}).items() if vn == n), None)
                sc = next((x for x in res.get("scores", []) if x.get("label") == lab), None)
                if not sc:
                    continue
                for c in CRITERIA:
                    per[c].append(sc.get(c))
                ptc.append(1 if sc.get("photo_type_correct") else 0)
                nerr += sum(1 for e in sc.get("errors", []) if not str(e).startswith("확인 불가"))
                best_cnt += 1 if lab in res.get("best_labels", []) else 0
            if per["accuracy"]:
                row["judge"][jn] = {**{c: mean(per[c]) for c in CRITERIA},
                                    "overall": mean([mean(per[c]) for c in CRITERIA]),
                                    "type_ok": mean(ptc), "best": best_cnt, "errors": nerr,
                                    "images": len(per["accuracy"])}
        summary["variants"][n] = row
    return summary


CSS = """
:root{--bg:#fbfbfa;--fg:#1d1d1f;--mut:#6b6b70;--line:#e3e3e0;--card:#fff;--acc:#2f6feb;--warn:#b35c00;--chip:#f0f0ec}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececee;--mut:#9a9aa2;--line:#2c2c31;--card:#1c1c20;--acc:#6ea0ff;--warn:#ffb057;--chip:#26262b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 system-ui,-apple-system,"Segoe UI","Malgun Gothic",sans-serif}
main{max-width:1400px;margin:0 auto;padding:24px 16px 80px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 10px}
.mut{color:var(--mut)}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{font-weight:600;font-size:12px;color:var(--mut);white-space:nowrap}td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.wrap{overflow-x:auto}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:14px 0}
.ph{display:grid;grid-template-columns:260px 1fr;gap:14px}@media (max-width:800px){.ph{grid-template-columns:1fr}}
.ph img{width:100%;border-radius:6px;border:1px solid var(--line)}.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:1px 8px;font-size:12px;margin:0 4px 4px 0}
pre{white-space:pre-wrap;margin:0;font:12.5px/1.5 ui-monospace,Consolas,monospace}.err{color:var(--warn);font-size:12px}
.v{font-weight:600;white-space:nowrap}.main{box-shadow:inset 3px 0 0 var(--acc)}details summary{cursor:pointer;color:var(--mut)}
"""


def esc(s) -> str:
    return html.escape("" if s is None else str(s))


def cmd_report(args) -> None:
    samples = load_samples()
    best = latest_ok(load_results())
    judges = load_judges()
    key_map = json.loads(JUDGE_KEY.read_text(encoding="utf-8")) if JUDGE_KEY.is_file() else {}
    summary = build_summary(samples, best, judges, key_map)
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    h = [f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>사진 설명 비교</title><style>{CSS}</style></head><body><main>",
         f"<h1>사진 설명(색인용 글) 비교 — Claude vs Gemini</h1>"
         f"<div class='mut'>지시 판 {PROMPT_VERSION} · 사진 {len(samples)}장 · 같은 지시문과 같은 출력 스키마(scene, text_in_photo, photo_type) · "
         f"사진은 메타데이터를 뺀 원본 크기 JPEG(품질 90), 찍은 시각·곳은 주지 않음</div>"]

    h.append("<h2>변형별 요약</h2><div class='wrap'><table><tr><th>변형</th><th>성공</th><th>평균 지연(s)</th><th>평균 입력 토큰</th>"
             "<th>평균 출력 토큰(생각 포함)</th><th>총비용($)</th><th>평균 장면 글자수</th><th>평균 사진 속 글자수</th><th>사진 종류 다수결 일치</th></tr>")
    for n, r in summary["variants"].items():
        cost = "—" if r["cost_usd_total"] is None else f"{r['cost_usd_total']:.3f}" + ("" if r["price_verified"] else " (가격 미확인)")
        h.append(f"<tr class='{'main' if n in ('claude_medium', 'argon_high') else ''}'><td class='v'>{esc(r['label'])}</td>"
                 f"<td class='num'>{r['ok']}/{r['n']}</td><td class='num'>{r['latency_s']}</td><td class='num'>{r['in_tok']}</td>"
                 f"<td class='num'>{r['out_tok']}</td><td class='num'>{cost}</td><td class='num'>{r['scene_chars']}</td>"
                 f"<td class='num'>{r['text_chars']}</td><td class='num'>{r['type_agree']}</td></tr>")
    h.append("</table></div>")

    if any(r["judge"] for r in summary["variants"].values()):
        h.append("<h2>블라인드 심사 점수 (1~5, 높을수록 좋음)</h2><div class='mut'>심사자는 라벨만 보고 어느 모델인지 모른 채 사진과 대조해 채점했다. "
                 "claude = Claude 심사 에이전트, gemini = Gemini 4 Argon 심사. 오류 수는 '확인 불가'를 뺀 틀린 주장 수.</div>"
                 "<div class='wrap'><table><tr><th>변형</th><th>심사자</th><th>정확성</th><th>글자</th><th>핵심 요소</th><th>구별력</th>"
                 "<th>검색 유용성</th><th>종합</th><th>종류 정답률</th><th>최고 선정</th><th>오류 수</th></tr>")
        for n, r in summary["variants"].items():
            for jn, js in r["judge"].items():
                h.append(f"<tr class='{'main' if n in ('claude_medium', 'argon_high') else ''}'><td class='v'>{esc(r['label'])}</td><td>{jn}</td>"
                         + "".join(f"<td class='num'>{js[c]}</td>" for c in CRITERIA)
                         + f"<td class='num'><b>{js['overall']}</b></td><td class='num'>{js['type_ok']}</td>"
                           f"<td class='num'>{js['best']}/{js['images']}</td><td class='num'>{js['errors']}</td></tr>")
        h.append("</table></div>")

    h.append("<h2>사진별 결과</h2>")
    for s in samples:
        h.append(f"<div class='card'><div class='ph'><div><a href='photos/{s['key']}.jpg'><img src='thumbs/{s['key']}.jpg' alt='{esc(s['key'])}'></a>"
                 f"<div class='mut' style='margin-top:6px'><b>{esc(s['key'])}</b> {esc(s['id'])}<br>{s['width']}×{s['height']}"
                 f"{' · MPO 첫 장' if s['frames'] > 1 else ''}{' · 회전 보정' if s['orientation'] not in (1, None) else ''}</div></div>"
                 f"<div class='wrap'><table><tr><th>변형</th><th>종류</th><th>장면 설명 (scene)</th><th>사진 속 글자 (text_in_photo)</th><th>심사</th></tr>")
        km = key_map.get(s["key"], {})
        for n, v in VARIANTS.items():
            r = best.get((s["key"], n))
            if not r:
                h.append(f"<tr><td class='v'>{esc(v['label'])}</td><td colspan='4' class='err'>결과 없음</td></tr>")
                continue
            lab = next((l for l, vn in km.items() if vn == n), None)
            jcell = []
            for jn, jd in judges.items():
                res = jd.get(s["key"])
                sc = next((x for x in (res or {}).get("scores", []) if x.get("label") == lab), None) if res else None
                if sc:
                    star = " ★" if lab in res.get("best_labels", []) else ""
                    jcell.append(f"<div><span class='chip'>{jn}{star}</span>"
                                 + " ".join(f"{c[:3]} {sc.get(c)}" for c in CRITERIA)
                                 + "".join(f"<div class='err'>· {esc(e)}</div>" for e in sc.get("errors", []))
                                 + f"<div class='mut' style='font-size:12px'>{esc(sc.get('note'))}</div></div>")
            h.append(f"<tr class='{'main' if n in ('claude_medium', 'argon_high') else ''}'><td class='v'>{esc(v['label'])}"
                     f"<div class='mut' style='font-weight:400;font-size:12px'>{r['latency_s']}s · in {r['in_tok']} · out {r['out_tok']}</div></td>"
                     f"<td>{esc(r['photo_type'])}</td><td>{esc(r['scene'])}</td><td><pre>{esc(r['text_in_photo']) or '<span class=mut>(없음)</span>'}</pre></td>"
                     f"<td style='min-width:220px'>{''.join(jcell) or '<span class=mut>—</span>'}</td></tr>")
        inter = [f"<div class='mut' style='font-size:12px'>{jn}: {esc(jd[s['key']].get('interchangeable'))}</div>"
                 for jn, jd in judges.items() if s["key"] in jd]
        h.append("</table></div></div>" + "".join(inter) + "</div>")
    h.append("</main></body></html>")
    REPORT.write_text("".join(h), encoding="utf-8")
    print(f"보고서 -> {REPORT}\n요약 -> {SUMMARY}")
    for n, r in summary["variants"].items():
        js = "  ".join(f"{jn}:{x['overall']}" for jn, x in r["judge"].items())
        print(f"  {n:14s} ok={r['ok']}/{r['n']} lat={r['latency_s']}s in={r['in_tok']} out={r['out_tok']} "
              f"scene={r['scene_chars']}자 text={r['text_chars']}자 cost={r['cost_usd_total']} {js}")


# ---------------------------------------------------------------- 검색 실험(hindsight recall 근사)
RECALL_QUERIES = OUT / "recall_queries.json"
RECALL_OUT = OUT / "recall.json"
EMBED_CACHE = OUT / "embed_cache.json"
EMBED_MODEL = "gemini-embedding-001"  # 다국어. gemini-embedding-2 는 여러 문장을 보내면 벡터를 하나만 돌려준다
_CJK = r"ㄱ-ㆎ가-힣぀-ヿ一-鿿"
_RUN = re.compile(rf"[a-z0-9]+|[{_CJK}]+")


def tok_bigram(text: str) -> list[str]:
    """pgroonga TokenBigram 근사: 영문·숫자는 단어째, 한글·한자·가나는 2글자씩."""
    out: list[str] = []
    for run in _RUN.findall(unicodedata.normalize("NFKC", text).lower()):
        if run[0].isascii():
            out.append(run)
        elif len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


def tok_native(text: str) -> list[str]:
    """PostgreSQL 기본(english) 사전 근사: 띄어쓰기·문장부호로만 자른 어절. '카페에서'와 '카페'가 다른 낱말이 된다."""
    return _RUN.findall(unicodedata.normalize("NFKC", text).lower())


def bm25_rank(docs: dict[str, list[str]], q: list[str], k1: float = 1.2, b: float = 0.75) -> list[str]:
    n = len(docs)
    avgdl = statistics.fmean(len(d) for d in docs.values()) or 1.0
    df: dict[str, int] = {}
    for toks in docs.values():
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    scores = {}
    for key, toks in docs.items():
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for t in set(q):
            if t in tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(toks) / avgdl))
        scores[key] = s
    # 일치하는 낱말이 하나도 없는 문서는 BM25 결과에 나오지 않는다(키 순서로 끼어들지 않게)
    return sorted((k for k in docs if scores[k] > 0), key=lambda k: (-scores[k], k))


def embed_all(client, texts: list[str], task: str) -> list[list[float]]:
    from google.genai import types

    cache = json.loads(EMBED_CACHE.read_text(encoding="utf-8")) if EMBED_CACHE.is_file() else {}
    keys = [hashlib.sha256(f"{EMBED_MODEL}|{task}|{t}".encode()).hexdigest() for t in texts]
    todo = [(k, t) for k, t in zip(keys, texts) if k not in cache]
    for i in range(0, len(todo), 50):
        chunk = todo[i:i + 50]
        try:
            r = gemini_call(lambda: client.models.embed_content(
                model=EMBED_MODEL, contents=[t for _, t in chunk], config=types.EmbedContentConfig(task_type=task)))
        except Exception:  # task_type 를 받지 않는 모델이면 빼고 다시
            r = gemini_call(lambda: client.models.embed_content(model=EMBED_MODEL, contents=[t for _, t in chunk]))
        embs = list(r.embeddings or [])
        if len(embs) != len(chunk):  # 묶음을 한 덩어리로 본 모델이면 한 문장씩 다시 보낸다
            embs = []
            for _, t in chunk:
                one = gemini_call(lambda: client.models.embed_content(
                    model=EMBED_MODEL, contents=t, config=types.EmbedContentConfig(task_type=task)))
                embs.append(one.embeddings[0])
        for (k, _), e in zip(chunk, embs):
            cache[k] = e.values
    EMBED_CACHE.write_text(json.dumps(cache), encoding="utf-8")
    return [cache[k] for k in keys]


def cos(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def rrf(*rankings: list[str], k: int = 60) -> list[str]:
    s: dict[str, float] = {}
    for rk in rankings:
        for i, key in enumerate(rk, 1):
            s[key] = s.get(key, 0.0) + 1.0 / (k + i)
    return sorted(s, key=lambda x: (-s[x], x))


def score_ranking(rank: list[str], primary: set[str], partial: set[str]) -> dict:
    pos = {k: i for i, k in enumerate(rank, 1)}
    first = min((pos[k] for k in primary if k in pos), default=None)
    gains = {k: 2 for k in primary} | {k: 1 for k in partial if k not in primary}
    dcg = sum(gains.get(k, 0) / math.log2(i + 1) for i, k in enumerate(rank[:5], 1))
    ideal = sorted(gains.values(), reverse=True)[:5]
    idcg = sum(g / math.log2(i + 1) for i, g in enumerate(ideal, 1)) or 1.0
    return {
        "first_rank": first,
        "hit1": 1 if first and first <= 1 else 0,
        "hit3": 1 if first and first <= 3 else 0,
        "hit5": 1 if first and first <= 5 else 0,
        "recall5": sum(1 for k in primary if pos.get(k, 99) <= 5) / len(primary),
        "mrr": 1.0 / first if first else 0.0,
        "ndcg5": dcg / idcg,
    }


def cmd_recall(args) -> None:
    """같은 질의로 변형마다 설명을 검색해 hindsight 의 의미 검색 + BM25 를 흉내 낸다."""
    samples = load_samples()
    best = latest_ok(load_results())
    qs = json.loads(RECALL_QUERIES.read_text(encoding="utf-8"))["queries"]
    client = gemini_client(load_env())
    keys = [s["key"] for s in samples]
    main_g = args.mix_with
    corpora: dict[str, dict[str, str]] = {}
    for n in VARIANTS:
        if all((k, n) in best for k in keys):
            corpora[n] = {k: f"{best[(k, n)]['scene']}\n{best[(k, n)]['text_in_photo']}".strip() for k in keys}
    # 섞은 은행: 사진 절반은 Claude, 절반은 Gemini 설명(두 가지 배정을 모두 재고 평균)
    if "claude_medium" in corpora and main_g in corpora:
        for tag, parity in (("mixA", 0), ("mixB", 1)):
            corpora[f"{tag}_{main_g}"] = {k: corpora["claude_medium" if i % 2 == parity else main_g][k]
                                          for i, k in enumerate(keys)}
    q_emb = embed_all(client, [q["query"] for q in qs], "RETRIEVAL_QUERY")
    arms = ["semantic", "bm25_bigram", "bm25_native", "fused"]
    per_query = []
    agg: dict[str, dict[str, list[dict]]] = {}
    for cname, corp in corpora.items():
        d_emb = dict(zip(corp, embed_all(client, list(corp.values()), "RETRIEVAL_DOCUMENT")))
        big = {k: tok_bigram(t) for k, t in corp.items()}
        nat = {k: tok_native(t) for k, t in corp.items()}
        for q, qe in zip(qs, q_emb):
            prim, part = set(q["primary"]), set(q.get("partial", []))
            ranks = {
                "semantic": sorted(corp, key=lambda k: (-cos(qe, d_emb[k]), k)),
                "bm25_bigram": bm25_rank(big, tok_bigram(q["query"])),
                "bm25_native": bm25_rank(nat, tok_native(q["query"])),
            }
            ranks["fused"] = rrf(ranks["semantic"], ranks["bm25_bigram"])
            for arm in arms:
                sc = score_ranking(ranks[arm], prim, part)
                agg.setdefault(cname, {}).setdefault(arm, []).append({**sc, "kind": q["kind"]})
                per_query.append({"qid": q["qid"], "variant": cname, "arm": arm, "first_rank": sc["first_rank"],
                                  "top3": ranks[arm][:3]})
    metrics = []
    for cname, by_arm in agg.items():
        for arm, rows in by_arm.items():
            row = {"variant": cname, "arm": arm, "queries": len(rows)}
            for m in ("hit1", "hit3", "hit5", "recall5", "mrr", "ndcg5"):
                row[m] = round(statistics.fmean(r[m] for r in rows), 3)
            metrics.append(row)
            for kind in sorted({r["kind"] for r in rows}):
                kr = [r for r in rows if r["kind"] == kind]
                metrics.append({"variant": cname, "arm": f"{arm}:{kind}", "queries": len(kr),
                                **{m: round(statistics.fmean(r[m] for r in kr), 3)
                                   for m in ("hit1", "hit3", "hit5", "recall5", "mrr", "ndcg5")}})
    RECALL_OUT.write_text(json.dumps({"embed_model": EMBED_MODEL, "queries": qs, "metrics": metrics,
                                      "per_query": per_query}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"질의 {len(qs)}개, 은행 {len(corpora)}개 -> {RECALL_OUT}")
    for r in metrics:
        if ":" not in r["arm"] and r["arm"] in ("fused", "bm25_bigram", "bm25_native"):
            print(f"  {r['variant']:22s} {r['arm']:12s} hit@1={r['hit1']:.2f} hit@3={r['hit3']:.2f} "
                  f"hit@5={r['hit5']:.2f} recall@5={r['recall5']:.2f} MRR={r['mrr']:.2f} nDCG@5={r['ndcg5']:.2f}")


# ---------------------------------------------------------------- 대시보드용 데이터 파일
DASH_DIR = OUT / "dash"
FINDINGS_JSON = OUT / "findings.json"
LIST_PRICE = {"claude-opus-5-5": (4.0, 20.0), "gemini-4-argon": (4.0, 20.0)}  # Argon 은 12/1 부터의 정가(미확인)


def _dash_meta(inputs: list[str]) -> list[dict]:
    return [
        {"label": Path(__file__).name, "value": Path(__file__).read_text(encoding="utf-8"), "type": "code", "lang": "python"},
        {"label": "Input", "value": ", ".join(inputs)},
        {"label": "Made at (UTC)", "value": datetime.now(timezone.utc).isoformat(timespec="seconds")},
    ]


def _write_dash(name: str, rows: list[dict], inputs: list[str]) -> None:
    DASH_DIR.mkdir(parents=True, exist_ok=True)
    (DASH_DIR / f"{name}.json").write_text(json.dumps({"rows": rows, "meta": _dash_meta(inputs)}, ensure_ascii=False),
                                           encoding="utf-8")
    print(f"  {name}.json  {len(rows)}행")


def cmd_dashdata(args) -> None:
    samples = load_samples()
    results = load_results()
    best = latest_ok(results)
    judges = load_judges()
    key_map = json.loads(JUDGE_KEY.read_text(encoding="utf-8"))
    summary = build_summary(samples, best, judges, key_map)
    judge_name = {"claude": "Claude 심사(에이전트)", "gemini": "Gemini 4 Argon 심사"}

    rows = []
    for n, v in VARIANTS.items():
        r = summary["variants"][n]
        rs = [best[(s["key"], n)] for s in samples if (s["key"], n) in best]
        lp = LIST_PRICE.get(v["model"])
        rows.append({
            "variant": n, "label": v["label"], "provider": "Anthropic" if v["provider"] == "anthropic" else "Google",
            "model": v["model"], "photos_ok": r["ok"], "photos": r["n"],
            "latency_mean_s": r["latency_s"], "latency_median_s": round(statistics.median(x["latency_s"] for x in rs), 2),
            "in_tok": r["in_tok"], "out_tok": r["out_tok"],
            "cost_per_photo_usd": round(r["cost_usd_total"] / r["ok"], 5) if r["cost_usd_total"] is not None else None,
            "cost_per_photo_list_usd": round((r["in_tok"] * lp[0] + r["out_tok"] * lp[1]) / 1e6, 5) if lp else None,
            "price_basis": "확인된 정가" if v["provider"] == "anthropic" else ("도입가 $2/$10(미확인)" if lp else "가격 미확인"),
            "scene_chars": r["scene_chars"], "text_chars": r["text_chars"], "type_agree": r["type_agree"],
        })
    _write_dash("variants", rows, ["results.jsonl", "samples.json"])

    rows = []
    for n, v in VARIANTS.items():
        for jn, js in summary["variants"][n]["judge"].items():
            rows.append({"variant": n, "label": v["label"], "judge": jn, "judge_label": judge_name[jn],
                         **{c: js[c] for c in CRITERIA}, "overall": js["overall"], "type_ok": js["type_ok"],
                         "best": js["best"], "images": js["images"], "errors": js["errors"]})
        both = [summary["variants"][n]["judge"][j] for j in ("claude", "gemini") if j in summary["variants"][n]["judge"]]
        if len(both) == 2:
            rows.append({"variant": n, "label": v["label"], "judge": "avg", "judge_label": "두 심사 평균",
                         **{c: round((both[0][c] + both[1][c]) / 2, 2) for c in CRITERIA},
                         "overall": round((both[0]["overall"] + both[1]["overall"]) / 2, 2),
                         "type_ok": round((both[0]["type_ok"] + both[1]["type_ok"]) / 2, 2),
                         "best": both[0]["best"] + both[1]["best"], "images": both[0]["images"] + both[1]["images"],
                         "errors": both[0]["errors"] + both[1]["errors"]})
    _write_dash("judge_scores", rows, ["judge_claude/*.json", "judge_gemini.jsonl", "judge_key.json"])

    rec = json.loads(RECALL_OUT.read_text(encoding="utf-8"))
    labels = {n: v["label"] for n, v in VARIANTS.items()}
    for n in list(labels):
        for tag in ("mixA", "mixB"):
            labels[f"{tag}_{n}"] = f"섞은 은행 {tag[-1]} (Claude medium 절반 + {VARIANTS[n]['label'].split(' · ')[0]} 절반)"
    rows = []
    for m in rec["metrics"]:
        arm, _, kind = m["arm"].partition(":")
        rows.append({"bank": m["variant"], "bank_label": labels.get(m["variant"], m["variant"]), "arm": arm,
                     "kind": kind or "all", "queries": m["queries"],
                     **{k: m[k] for k in ("hit1", "hit3", "hit5", "recall5", "mrr", "ndcg5")}})
    _write_dash("recall_metrics", rows, ["recall.json", "recall_queries.json", f"embeddings: {rec['embed_model']}"])

    fused = {(p["qid"], p["variant"]): p for p in rec["per_query"] if p["arm"] == "fused"}
    rows = []
    for q in rec["queries"]:
        row = {"qid": q["qid"], "query": q["query"], "kind": q["kind"], "primary": " ".join(q["primary"]),
               "partial": " ".join(q.get("partial", [])), "why": q.get("why", "")}
        for n in VARIANTS:
            p = fused.get((q["qid"], n))
            row[f"rank_{n}"] = p["first_rank"] if p else None
        rows.append(row)
    _write_dash("recall_queries", rows, ["recall.json", "recall_queries.json"])

    rows = []
    for s in samples:
        km = key_map.get(s["key"], {})
        for n, v in VARIANTS.items():
            r = best.get((s["key"], n))
            if not r:
                continue
            lab = next((l for l, vn in km.items() if vn == n), None)
            row = {"photo": s["key"], "file": s["id"].split("/")[-1], "folder": "/".join(s["id"].split("/")[:-1]),
                   "variant": n, "label": v["label"], "photo_type": r["photo_type"], "scene": r["scene"],
                   "text_in_photo": r["text_in_photo"], "latency_s": r["latency_s"], "in_tok": r["in_tok"],
                   "out_tok": r["out_tok"]}
            for jn, jd in judges.items():
                res = jd.get(s["key"]) or {}
                sc = next((x for x in res.get("scores", []) if x.get("label") == lab), None)
                row[f"{jn}_overall"] = round(statistics.fmean(sc[c] for c in CRITERIA), 2) if sc else None
                row[f"{jn}_accuracy"] = sc["accuracy"] if sc else None
                row[f"{jn}_text"] = sc["text"] if sc else None
                row[f"{jn}_best"] = (lab in res.get("best_labels", [])) if sc else None
                row[f"{jn}_errors"] = " / ".join(sc.get("errors", [])) if sc else ""
                row[f"{jn}_note"] = sc.get("note", "") if sc else ""
            rows.append(row)
    _write_dash("photos", rows, ["results.jsonl", "judge_claude/*.json", "judge_gemini.jsonl", "judge_key.json"])

    if FINDINGS_JSON.is_file():
        f = json.loads(FINDINGS_JSON.read_text(encoding="utf-8"))
        _write_dash("findings", f["rows"], ["reconcile.md", "summary.json"])


def _data_url(img: Image.Image, max_side: int, quality: int) -> str:
    im = img.copy()
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True)  # exif 를 넘기지 않으므로 메타데이터 없이 저장된다
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def cmd_dashimages(args) -> None:
    """대시보드에서 사진을 보이게 화면용 사본(썸네일·큰 그림)을 data URL 로 만든다."""
    rows = []
    for s in load_samples():
        with Image.open(PHOTOS / f"{s['key']}.jpg") as im:
            im.load()
            rows.append({"photo": s["key"], "file": s["id"].split("/")[-1], "folder": "/".join(s["id"].split("/")[:-1]),
                         "width": s["width"], "height": s["height"],
                         "thumb": _data_url(im, 200, 70), "full": _data_url(im, args.max_side, 80)})
    _write_dash("photo_images", rows, ["photos/P01.jpg … P24.jpg (숨은 정보를 지운 사본)"])
    print(f"  크기 {(DASH_DIR / 'photo_images.json').stat().st_size // 1024}KB")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    p = sub.add_parser("run")
    p.add_argument("--variants", default="")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--claude-workers", type=int, default=8, help="Claude 동시 호출 수(차단 없이 돌던 기준 8)")
    p.add_argument("--gemini-workers", type=int, default=2, help=f"Gemini 동시 호출 수(상한 {GEMINI_MAX_CONCURRENCY})")
    sub.add_parser("blind")
    p = sub.add_parser("judge-gemini")
    p.add_argument("--judge-model", default="gemini-4-argon")
    p.add_argument("--workers", type=int, default=2, help=f"Gemini 동시 호출 수(상한 {GEMINI_MAX_CONCURRENCY})")
    sub.add_parser("report")
    p = sub.add_parser("recall")
    p.add_argument("--mix-with", default="argon_ultra")
    sub.add_parser("dashdata")
    p = sub.add_parser("dashimages")
    p.add_argument("--max-side", type=int, default=720)
    args = ap.parse_args()
    {"prepare": cmd_prepare, "run": cmd_run, "blind": cmd_blind, "judge-gemini": cmd_judge_gemini,
     "report": cmd_report, "recall": cmd_recall, "dashdata": cmd_dashdata,
     "dashimages": cmd_dashimages}[args.cmd](args)


if __name__ == "__main__":
    main()

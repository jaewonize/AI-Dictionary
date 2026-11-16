"""미리 분석: 사진마다 Claude가 장면 설명, 사진 속 글자, 사진 종류를 적는다.

`uv run python -m pipeline.describe` (data/descriptions.jsonl 에 이어 쓴다)
`uv run python -m pipeline.describe --limit 20 --effort low --out var/describe_low.jsonl` (effort 견주기)

- Claude에는 메타데이터를 뺀 Claude용 사본만 보낸다. 찍은 시각과 찍은 곳은 알려 주지 않는다.
- id, 원본의 sha256, 모델, effort, 지시문 판이 모두 같은 줄이 이미 있으면 건너뛴다(다시 만들면 돈이 들고 결과가 바뀐다).
- effort 기본값 medium: 11/15 비교 실험(Claude medium/low, Gemini 4 Argon)에서 medium 이 심사 종합 1위였다.
"""

from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app import claude
from app.config import settings
from pipeline.common import append_jsonl, catalog_photos, image_block, prompt, read_jsonl

sys.stdout.reconfigure(encoding="utf-8")

PROMPT_VERSION = "describe-v2"
DEFAULT_EFFORT = "medium"
USER_PROMPT = "이 사진의 색인 항목을 만들어 주세요."


class PhotoDescription(BaseModel):
    place: str
    scene: str
    text_in_photo: str
    photo_type: Literal["scene", "document", "screenshot"]


def done_keys(out: Path, effort: str) -> set[tuple]:
    return {(r["id"], r.get("sha256"), r.get("model"), r.get("effort"), r.get("prompt_version"))
            for r in read_jsonl(out) if not r.get("error")}


def describe_one(row: dict, effort: str, system: str) -> dict:
    base = {"id": row["id"], "sha256": row["sha256"], "model": settings.model, "effort": effort,
            "prompt_version": PROMPT_VERSION}
    try:
        c = claude.call("describe", effort,
                        [{"role": "user", "content": [image_block(row), {"type": "text", "text": USER_PROMPT}]}],
                        system=system, output_format=PhotoDescription)
    except (claude.CostCapExceeded, claude.RateLimitTripped):
        raise
    except Exception as e:  # 실패는 빈 결과로 넘기지 않고 오류로 남긴다
        return {**base, "error": f"{type(e).__name__}: {str(e)[:300]}"}
    rec = {k: c.rec[k] for k in ("served_model", "fallback", "input_tokens", "output_tokens", "cost_usd", "ms")}
    if c.stop != "end_turn" or c.parsed is None:
        return {**base, **rec, "error": f"stop_reason={c.stop}"}
    return {**base, **rec, **c.parsed.model_dump()}


def main() -> None:
    ap = argparse.ArgumentParser(description="사진 미리 분석")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--effort", default=DEFAULT_EFFORT, choices=["low", "medium", "high"])
    ap.add_argument("--out", default="")
    ap.add_argument("--ids", default="", help="쉼표로 나눈 사진 id(이것만 돈다)")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    out = Path(args.out).resolve() if args.out else settings.descriptions
    photos = catalog_photos()
    if not photos:
        sys.exit("카탈로그가 없다. 먼저 pipeline.collect 를 돌린다")
    have = done_keys(out, args.effort)
    todo = [r for r in photos if (r["id"], r["sha256"], settings.model, args.effort, PROMPT_VERSION) not in have]
    if args.ids:
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        todo = [r for r in todo if r["id"] in want]
    elif args.limit:
        step = max(1, len(todo) // args.limit)  # 고르게 뽑는다
        todo = todo[::step][: args.limit]
    if not todo:
        print("모두 설명되어 있다.")
        return
    system = prompt(PROMPT_VERSION)
    print(f"{len(todo)}장 설명 · {settings.model} · effort {args.effort} · {PROMPT_VERSION} · 동시 {args.workers}개 "
          f"· 지금까지 쓴 돈 ${claude.spent_usd():.2f} / 한도 ${settings.cost_cap_usd:.0f}", flush=True)
    lock = threading.Lock()
    stats = {"ok": 0, "fail": 0, "cost": 0.0}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(describe_one, r, args.effort, system) for r in todo]
        try:
            for f in as_completed(futs):
                rec = f.result()
                with lock:
                    append_jsonl(out, rec)
                    if rec.get("error"):
                        stats["fail"] += 1
                        print(f"✗ {rec['id']} {rec['error']}", flush=True)
                    else:
                        stats["ok"] += 1
                        stats["cost"] += rec["cost_usd"]
                        if stats["ok"] % 20 == 0 or stats["ok"] <= 3:
                            print(f"  {stats['ok']}/{len(todo)} 완료 · 이번 비용 ${stats['cost']:.2f}", flush=True)
        except (claude.CostCapExceeded, claude.RateLimitTripped) as e:
            for f in futs:
                f.cancel()
            print(f"멈춤: {e}", flush=True)
    print(f"성공 {stats['ok']}, 실패 {stats['fail']}, 이번 비용 ${stats['cost']:.2f} -> {out}")


if __name__ == "__main__":
    main()

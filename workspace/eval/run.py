"""시험 실행기(docs/ARCHITECTURE.md '질의 한 번이 도는 길', '데이터 파일').

정답 키(settings.answer_key)의 질의를 에이전트 루프(app/loop.py)로 돌리고 채점한다.
  uv run python -m eval.run                 # 전체 질의 1회씩
  uv run python -m eval.run Q01 Q02         # 지정한 질의만
  uv run python -m eval.run all --workers 2 # 첫 질의로 캐시를 데운 뒤 나머지 동시 실행
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

from app import claude, loop
from app import index as idx_mod
from app.config import settings

sys.stdout.reconfigure(encoding="utf-8")


def load_answer_key(path: Path) -> list[dict]:
    if not path.is_file():
        sys.exit(f"정답 키 파일이 없다: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("tests"), list):
        return raw["tests"]
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        out = []
        for k, v in raw.items():
            if k == "forbidden_words" or not isinstance(v, dict):
                continue
            out.append({"id": k, **v})
        return out
    sys.exit(f"정답 키 형식을 읽을 수 없다: {path}")


def _pick_option(options: list[dict], want: str) -> str | None:
    w = want.strip().lower()
    in_label = [o for o in options if w in (o.get("label") or "").lower()]
    if len(in_label) == 1:
        return str(in_label[0]["id"])
    in_detail = [o for o in options if w in (o.get("detail") or "").lower()]
    if len(in_detail) == 1:
        return str(in_detail[0]["id"])
    return None


def _extract_trace(events: list[dict]) -> dict:
    plan = None
    cutoffs: list[str] = []
    cards: list[dict] = []
    for ev in events:
        if ev.get("type") == "card":
            if ev.get("kind") == "plan" and isinstance(ev.get("data"), dict):
                plan = ev["data"]
            evid = ev.get("evidence") or {}
            if evid.get("cutoff") and evid["cutoff"] not in cutoffs:
                cutoffs.append(evid["cutoff"])
            cards.append({"no": ev.get("no"), "kind": ev.get("kind"), "title": ev.get("title"),
                          "sentence": ev.get("sentence"), "evidence": evid if evid else None})
    return {"plan": plan, "cutoffs": cutoffs, "cards": cards}


def grade_one(spec: dict, rec: dict) -> dict:
    got_list = idx_mod.by_time(rec.get("result_ids") or [])
    must_list = idx_mod.by_time(spec.get("answer_ids") or [])
    opt_list = idx_mod.by_time(spec.get("optional_ids") or [])
    trap_list = idx_mod.by_time(spec.get("trap_ids") or [])

    got = set(got_list)
    must = set(must_list)
    opt = set(opt_list)
    allowed = must | opt
    traps = set(trap_list)

    missing = sorted(must - got)
    extra = sorted(got - allowed)
    trap_hits = sorted(got & traps)
    matched_must = sorted(got & must)
    matched_opt = sorted(got & opt)

    answered = rec.get("status") == "answered"
    exact = answered and (got == must) and not trap_hits
    passed = answered and not missing and not extra and not trap_hits

    recall = (len(matched_must) / len(must)) if must else (1.0 if not got else 0.0)
    precision = (len(got & allowed) / len(got)) if got else (1.0 if not must else 0.0)
    trace = _extract_trace(rec.get("events") or [])

    return {
        "id": spec["id"],
        "kind": spec.get("kind", ""),
        "query": spec["query"],
        "note": spec.get("note", ""),
        "passed": passed,
        "exact_match": exact,
        "status": rec.get("status"),
        "error": rec.get("error"),
        "answer_ids": must_list,
        "optional_ids": opt_list,
        "trap_ids": trap_list,
        "got_ids": got_list,
        "matched_must": matched_must,
        "matched_optional": matched_opt,
        "missing_ids": missing,
        "extra_ids": extra,
        "trap_hits": trap_hits,
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "result_sentence": rec.get("result_sentence", ""),
        "plan": trace["plan"],
        "cutoffs": trace["cutoffs"],
        "cards": trace["cards"],
        "calls": rec["summary"]["calls"],
        "loop_calls": rec["summary"]["loop_calls"],
        "input_tokens": rec["summary"]["input_tokens"],
        "output_tokens": rec["summary"]["output_tokens"],
        "cache_read_input_tokens": rec["summary"]["cache_read_input_tokens"],
        "cost_usd": rec["summary"]["cost_usd"],
        "elapsed_ms": rec.get("elapsed_ms", 0),
        "run_id": rec["run_id"],
    }


def run_test_once(spec: dict, today: date | None) -> tuple[dict, dict]:
    tid = spec["id"]
    answers = list(spec.get("answers") or [])
    ans_idx = 0
    state = loop.start(spec["query"], today=today, test=tid)
    while state.status == "waiting" and state.pending_ask:
        if ans_idx >= len(answers):
            state.status = "failed"
            state.error_message = f"예상하지 못한 되묻기가 발생했습니다: {state.pending_ask['question']}"
            state.emit("error", message=state.error_message)
            state.emit("done", status="failed")
            loop.save_run(state)
            break
        want = answers[ans_idx]
        ans_idx += 1
        opt_id = _pick_option(state.pending_ask["options"], want)
        if opt_id is None:
            state.status = "failed"
            state.error_message = f"되묻기 선택지에서 '{want}'을(를) 하나로 특정하지 못했습니다."
            state.emit("error", message=state.error_message)
            state.emit("done", status="failed")
            loop.save_run(state)
            break
        state = loop.resume(state, opt_id)

    rec = loop.save_run(state)
    graded = grade_one(spec, rec)
    if graded["passed"]:
        settings.passed_dir.mkdir(parents=True, exist_ok=True)
        (settings.passed_dir / f"{tid}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return graded, rec


def main() -> int:
    ap = argparse.ArgumentParser(description="에이전트 루프 질의 채점")
    ap.add_argument("targets", nargs="*", default=["all"], help="돌릴 질의 id 목록(기본 all)")
    ap.add_argument("--times", type=int, default=1, help="질의마다 반복 횟수")
    ap.add_argument("--workers", type=int, default=1, help="동시 실행 수(기본 1)")
    ap.add_argument("--today", default="", help="오늘 날짜 고정(YYYY-MM-DD)")
    args = ap.parse_args()

    if not idx_mod.load():
        sys.exit(f"색인이 없다({settings.index}). 먼저 pipeline.build_index 를 돌린다")

    specs = load_answer_key(settings.answer_key)
    if "all" not in args.targets:
        want = set(args.targets)
        specs = [s for s in specs if s["id"] in want]
        if not specs:
            sys.exit(f"정답 키에 해당 id 가 없다: {args.targets}")

    td = date.fromisoformat(args.today) if args.today else None
    jobs = [s for s in specs for _ in range(max(1, args.times))]
    spent0 = claude.spent_usd()
    print(f"질의 {len(jobs)}건 채점 시작 · 모델 {settings.model} · 동시 {args.workers}개 "
          f"· 누적 비용 ${spent0:.2f} / 한도 ${settings.cost_cap_usd:.0f}", flush=True)

    results_by_order: list[dict | None] = [None] * len(jobs)

    def _do(idx_job: tuple[int, dict]) -> tuple[int, dict]:
        i, sp = idx_job
        g, _ = run_test_once(sp, td)
        mark = "✓" if g["passed"] else "✗"
        print(f"  {mark} [{g['id']}] 정답 {len(g['matched_must'])}/{len(g['answer_ids'])} "
              f"(고른 {len(g['got_ids'])}장, 빠짐 {len(g['missing_ids'])}, 여분 {len(g['extra_ids'])}, 함정 {len(g['trap_hits'])}) "
              f"· 호출 {g['calls']}회 · ${g['cost_usd']:.3f} · {g['elapsed_ms']/1000:.1f}s", flush=True)
        return i, g

    if jobs:
        # 동시 실행이 2 이상일 때도 첫 질의를 먼저 돌려 1시간 설명 캐시를 데운다.
        if args.workers > 1 and len(jobs) > 1:
            i0, g0 = _do((0, jobs[0]))
            results_by_order[i0] = g0
            rest = [(i, sp) for i, sp in enumerate(jobs[1:], 1)]
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                futs = [ex.submit(_do, item) for item in rest]
                for f in as_completed(futs):
                    i, g = f.result()
                    results_by_order[i] = g
        else:
            for i, sp in enumerate(jobs):
                _, g = _do((i, sp))
                results_by_order[i] = g

    results = [r for r in results_by_order if r is not None]
    run_passed_n = sum(1 for r in results if r["passed"])
    run_exact_n = sum(1 for r in results if r["exact_match"])
    run_calls = sum(r["calls"] for r in results)
    run_cost = round(sum(r["cost_usd"] for r in results), 6)

    out_path = settings.var_dir / "eval_results.json"
    merged_results = results
    if "all" not in args.targets and args.times == 1 and out_path.is_file():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            by_id = {r["id"]: r for r in (prev.get("results") or []) if isinstance(r, dict) and "id" in r}
            for r in results:
                by_id[r["id"]] = r
            all_order = [s["id"] for s in load_answer_key(settings.answer_key)]
            ordered = [by_id.pop(qid) for qid in all_order if qid in by_id]
            merged_results = ordered + list(by_id.values())
        except Exception:
            merged_results = results

    passed_n = sum(1 for r in merged_results if r["passed"])
    exact_n = sum(1 for r in merged_results if r["exact_match"])
    total_calls = sum(r["calls"] for r in merged_results)
    total_cost = round(sum(r["cost_usd"] for r in merged_results), 6)

    out_doc = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.model,
        "workers": args.workers,
        "times": args.times,
        "total": len(merged_results),
        "passed": passed_n,
        "exact": exact_n,
        "avg_recall": round(sum(r["recall"] for r in merged_results) / len(merged_results), 4) if merged_results else 0.0,
        "avg_precision": round(sum(r["precision"] for r in merged_results) / len(merged_results), 4) if merged_results else 0.0,
        "total_calls": total_calls,
        "total_cost_usd": total_cost,
        "results": merged_results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_doc, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"이번 실행: 통과 {run_passed_n}/{len(results)} (정확 일치 {run_exact_n}/{len(results)}) "
          f"· 이번 호출 {run_calls}회 · 이번 비용 ${run_cost:.2f}", flush=True)
    print(f"전체 집계: 통과 {passed_n}/{len(merged_results)} (정확 일치 {exact_n}/{len(merged_results)}) "
          f"· 평균 재현율 {out_doc['avg_recall']*100:.1f}% · 평균 정밀도 {out_doc['avg_precision']*100:.1f}% "
          f"· 집계 호출 {total_calls}회 · 집계 비용 ${total_cost:.2f} -> {out_path}", flush=True)
    return 0 if run_passed_n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

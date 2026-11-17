"""수용표 검사기(분석 8, 9).

실행 기록 하나를 정답 키와 대조해 기록으로 확인할 수 있는 일곱 줄을 검사하고,
--save 를 주면 일곱 줄이 모두 맞을 때만 settings.passed_dir 에 저장한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import index as idx_mod
from app.config import settings
from eval.run import _pick_option, load_answer_key

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def _ws(s: str | None) -> str:
    return " ".join(str(s or "").split())


def _default_tz() -> timezone:
    off = settings.default_tz or "+09:00"
    sign = 1 if off[0] == "+" else -1
    h, m = off[1:].split(":")
    return timezone(sign * timedelta(hours=int(h), minutes=int(m)))


def _parse_tz(s: str | None) -> datetime | None:
    if not s:
        return None
    dt = idx_mod.parse_time(str(s).strip())
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_default_tz())
    return dt


def _check_query_and_choice(spec: dict, rec: dict, events: list[dict]) -> tuple[str, str]:
    answers = list(spec.get("answers") or [])
    q_match = _ws(rec.get("query")) == _ws(spec.get("query"))
    if not answers:
        if q_match and rec.get("status") == "answered":
            return "해당 없음", f"질의 일치, 되묻기 없음 · 실행 {rec.get('run_id')}"
        return "✗", f"질의 일치={q_match}, status={rec.get('status')}"

    want = answers[0]
    ask_idx = None
    ask_ev = None
    for i, ev in enumerate(events):
        if ev.get("type") == "ask" and len(ev.get("options") or []) >= 2:
            ask_idx = i
            ask_ev = ev
            break

    if ask_idx is None or ask_ev is None:
        return "✗", f"선택지 2개 이상인 ask 없음 · 실행 {rec.get('run_id')}"

    expected_opt_id = _pick_option(ask_ev["options"], want)
    if expected_opt_id is None:
        return "✗", f"선택지에서 '{want}' 특정 실패 · 실행 {rec.get('run_id')}"

    ans_idx = None
    ans_ev = None
    for i in range(ask_idx + 1, len(events)):
        if events[i].get("type") == "answer":
            ans_idx = i
            ans_ev = events[i]
            break

    if ans_idx is None or ans_ev is None:
        return "✗", f"ask 뒤 answer 이벤트 없음 · 실행 {rec.get('run_id')}"

    chosen_id = str(ans_ev.get("option_id"))
    chosen_opt = next((o for o in ask_ev["options"] if str(o.get("id")) == chosen_id), {})
    tail = events[ans_idx + 1:]
    has_card = any(e.get("type") == "card" for e in tail)
    has_result = any(e.get("type") == "result" for e in tail)
    ok = (
        q_match
        and chosen_id == str(expected_opt_id)
        and has_card
        and has_result
        and rec.get("status") == "answered"
    )
    detail_str = chosen_opt.get("detail") or ""
    evid = (
        f"질의='{rec.get('query')}', 선택='{chosen_opt.get('label', ans_ev.get('label', ''))}'"
        + (f" ({detail_str})" if detail_str else "")
        + f", 실행={rec.get('run_id')}"
    )
    return ("✓" if ok else "✗"), evid


def _check_final_photos(spec: dict, rec: dict) -> tuple[str, str]:
    must = set(spec.get("answer_ids") or [])
    opt = set(spec.get("optional_ids") or [])
    traps = set(spec.get("trap_ids") or [])
    got = set(rec.get("result_ids") or [])
    allowed = must | opt

    missing = sorted(must - got)
    extra = sorted(got - allowed)
    trap_hits = sorted(got & traps)
    ok = (rec.get("status") == "answered") and not missing and not extra and not trap_hits
    evid = f"결과 {len(got)}장={sorted(got)} (필수 {len(must)}장, 빠짐 {len(missing)}, 여분 {len(extra)}, 함정 {len(trap_hits)})"
    return ("✓" if ok else "✗"), evid


def _check_cutoff_evidence(spec: dict, rec: dict, events: list[dict], idx: dict[str, dict]) -> tuple[str, str]:
    acc = spec.get("accept") or {}
    after_id = acc.get("after_id")
    if not after_id:
        return "해당 없음", "accept.after_id 없음"

    after_dt = _parse_tz((idx.get(after_id) or {}).get("time"))
    if after_dt is None:
        return "✗", f"기준 사진({after_id}) 시각 없음"

    res_ids = list(rec.get("result_ids") or [])
    if not res_ids:
        return "✗", "결과 사진이 없음"

    res_dts: list[datetime] = []
    for pid in res_ids:
        dt = _parse_tz((idx.get(pid) or {}).get("time"))
        if dt is None or not (dt > after_dt):
            return "✗", f"결과 사진 {pid} 시각이 기준({after_dt.isoformat()}) 뒤가 아님"
        res_dts.append(dt)

    earliest_res = min(res_dts)

    for ev in events:
        if ev.get("type") == "card" and ev.get("kind") == "picked":
            cut_raw = (ev.get("evidence") or {}).get("cutoff")
            cut_dt = _parse_tz(cut_raw)
            if cut_dt is not None and after_dt <= cut_dt < earliest_res:
                return "✓", f"카드 {ev.get('no')} cutoff={cut_raw}"

    for t in rec.get("tools") or []:
        if t.get("name") == "find_candidates" and t.get("ok") is True:
            cut_raw = (t.get("input") or {}).get("after")
            cut_dt = _parse_tz(cut_raw)
            if cut_dt is not None and after_dt <= cut_dt < earliest_res:
                return "✓", f"도구 #{t.get('i')} find_candidates.after={cut_raw}"

    return "✗", f"범위 [{after_dt.isoformat()}, {earliest_res.isoformat()}) 안의 cutoff/after 근거 없음"


def _check_live_web_search(rec: dict, events: list[dict]) -> tuple[str, str]:
    web_cards = [e for e in events if e.get("type") == "card" and e.get("kind") == "web"]
    has_live_card = any(e.get("cached") is False for e in web_cards)
    reqs = sum(
        int(c.get("web_search_requests") or 0)
        for c in (rec.get("calls") or [])
        if isinstance(c, dict) and c.get("purpose") == "search_web"
    )
    ok = has_live_card and reqs >= 1
    cached_vals = [e.get("cached") for e in web_cards]
    return ("✓" if ok else "✗"), f"web 카드 cached={cached_vals}, 검색 요청 수={reqs}"


def _check_related_group(rel_spec: dict, events: list[dict]) -> tuple[str, str]:
    want_ids = set(rel_spec.get("ids") or [])
    res_evs = [e for e in events if e.get("type") == "result"]
    if not res_evs:
        return "✗", "result 이벤트 없음"
    last_res = res_evs[-1]
    for g in last_res.get("related") or []:
        if not isinstance(g, dict):
            continue
        g_ids = {
            (it.get("id") if isinstance(it, dict) else it)
            for it in (g.get("items") or [])
            if (it.get("id") if isinstance(it, dict) else it)
        }
        if g_ids == want_ids:
            return "✓", f"묶음 '{g.get('label')}' ({len(g_ids)}장: {sorted(g_ids)})"
    return "✗", f"기대 사진 {len(want_ids)}장과 일치하는 연관 묶음 없음"


def _check_hindsight_connection(rec: dict, events: list[dict], idx: dict[str, dict], bank: str) -> tuple[str, str]:
    tools = [t for t in (rec.get("tools") or []) if isinstance(t, dict)]
    mem_events_by_n = {e.get("n"): e for e in events if e.get("type") == "memory" and e.get("n") is not None}
    ref_events_by_n = {e.get("n"): e for e in events if e.get("type") == "reflect" and e.get("n") is not None}

    # (가) recall_memory 확인
    first_recall_idx: int | None = None
    first_recall_tool: dict | None = None
    for idx_t, t in enumerate(tools):
        if (
            t.get("name") == "recall_memory"
            and t.get("ok") is True
            and t.get("bank") == bank
            and int(t.get("count") or 0) >= 1
            and t.get("memory_n") in mem_events_by_n
        ):
            first_recall_idx = idx_t
            first_recall_tool = t
            break
    cond_a = first_recall_idx is not None

    # (나) 기억을 쓴 호출
    mem_using_indices: list[int] = []
    mem_used_dates: set[str] = set()
    if first_recall_idx is not None:
        for k in range(first_recall_idx + 1, len(tools)):
            t = tools[k]
            if not t.get("ok") or t.get("name") not in ("find_candidates", "judge_photos"):
                continue
            prior_dates: set[str] = set()
            prior_ids: set[str] = set()
            for prev in tools[:k]:
                if prev.get("ok") is True and prev.get("name") in ("recall_memory", "reflect_memory"):
                    prior_dates.update(d for d in (prev.get("out_dates") or []) if d)
                    prior_ids.update(pid for pid in (prev.get("out_ids") or []) if pid)

            inp = t.get("input") or {}
            if t["name"] == "find_candidates":
                df, dt_to = inp.get("date_from"), inp.get("date_to")
                matched_d = {d for d in (df, dt_to) if d and d in prior_dates}
                if matched_d:
                    mem_using_indices.append(k)
                    mem_used_dates.update(matched_d)
            elif t["name"] == "judge_photos":
                j_ids = [pid for pid in (inp.get("ids") or []) if pid in prior_ids]
                if j_ids:
                    mem_using_indices.append(k)
                    for pid in j_ids:
                        tm = (idx.get(pid) or {}).get("time") or ""
                        if len(tm) >= 10:
                            mem_used_dates.add(tm[:10])
    cond_b = len(mem_using_indices) >= 1

    # (다) 결과 사진이 첫 '기억을 쓴 호출' 이후 확인된 사진·날짜 안에 있는지
    res_ids = list(rec.get("result_ids") or [])
    cond_c = False
    if cond_b and res_ids:
        first_use_k = mem_using_indices[0]
        verified_ids: set[str] = set()
        for t in tools[first_use_k:]:
            if t.get("ok") is True and t.get("name") in ("find_candidates", "judge_photos"):
                verified_ids.update(t.get("out_ids") or [])
        all_in_verified = all(pid in verified_ids for pid in res_ids)
        all_dates_ok = all(
            len((idx.get(pid) or {}).get("time") or "") >= 10
            and ((idx.get(pid) or {})["time"][:10] in mem_used_dates)
            for pid in res_ids
        )
        cond_c = all_in_verified and all_dates_ok

    # (라) reflect_memory 확인 및 후속 도구 사용 확인
    cond_d = False
    used_ref_tool: dict | None = None
    hs_ok = any(isinstance(h, dict) and h.get("ok") is True for h in (rec.get("hindsight") or []))
    if first_recall_idx is not None and hs_ok:
        for ref_k in range(first_recall_idx + 1, len(tools)):
            t = tools[ref_k]
            if (
                t.get("name") == "reflect_memory"
                and t.get("ok") is True
                and t.get("bank") == bank
                and int(t.get("count") or 0) >= 1
                and t.get("reflect_n") in ref_events_by_n
            ):
                ref_ids = set(t.get("out_ids") or [])
                ref_dates = set(t.get("out_dates") or [])
                used_after = False
                for t2 in tools[ref_k + 1:]:
                    if t2.get("ok") is not True:
                        continue
                    inp2 = t2.get("input") or {}
                    if t2.get("name") == "judge_photos" and (set(inp2.get("ids") or []) & ref_ids):
                        used_after = True
                        break
                    if t2.get("name") == "find_candidates" and (
                        (inp2.get("date_from") in ref_dates) or (inp2.get("date_to") in ref_dates)
                    ):
                        used_after = True
                        break
                if used_after:
                    cond_d = True
                    used_ref_tool = t
                    break

    ok = cond_a and cond_b and cond_c and cond_d
    parts = [
        f"(가)={'✓' if cond_a else '✗'}",
        f"(나)={'✓' if cond_b else '✗'}",
        f"(다)={'✓' if cond_c else '✗'}",
        f"(라)={'✓' if cond_d else '✗'}",
    ]
    if first_recall_tool:
        parts.append(f"recall #{first_recall_tool.get('i')}(count={first_recall_tool.get('count')})")
    if used_ref_tool:
        parts.append(
            f"reflect #{used_ref_tool.get('i')}(count={used_ref_tool.get('count')}, cost={used_ref_tool.get('cost_usd')})"
        )
    if mem_using_indices:
        use_nums = [tools[k].get("i") for k in mem_using_indices]
        parts.append(f"기억 사용 도구={use_nums}(날짜={sorted(mem_used_dates)})")
    return ("✓" if ok else "✗"), ", ".join(parts)


def evaluate_acceptance(test_id: str, run_path: Path, *, save: bool = False) -> tuple[int, str]:
    bank = settings.hindsight_bank
    if not bank:
        return 2, "은행 이름이 없다. 명령 앞에 AIDIC_HINDSIGHT_BANK를 붙인다"

    specs = load_answer_key(settings.answer_key)
    spec = next((s for s in specs if s.get("id") == test_id), None)
    if spec is None:
        return 1, f"정답 키에 시험 '{test_id}' 가 없다"

    rec = json.loads(run_path.read_text(encoding="utf-8"))
    events = [e for e in (rec.get("events") or []) if isinstance(e, dict)]
    idx = idx_mod.load()

    where = "시험 실행기" if rec.get("test") else "서버"
    rows: list[tuple[str, str, str]] = []

    r1, e1 = _check_query_and_choice(spec, rec, events)
    rows.append(("질의와 선택", r1, e1))

    r2, e2 = _check_final_photos(spec, rec)
    rows.append(("최종 사진", r2, e2))

    r3, e3 = _check_cutoff_evidence(spec, rec, events, idx)
    rows.append(("기준 뒤의 근거", r3, e3))

    r4, e4 = _check_live_web_search(rec, events)
    rows.append(("실제 웹 검색", r4, e4))

    rel_specs = list(((spec.get("accept") or {}).get("related")) or [])
    if rel_specs:
        for rs in rel_specs:
            rr, er = _check_related_group(rs, events)
            rows.append((f"후속 조회: {rs.get('name', '')}", rr, er))
    else:
        rows.append(("후속 조회 1", "해당 없음", "accept.related 없음"))
        rows.append(("후속 조회 2", "해당 없음", "accept.related 없음"))

    rows.append(("두 가지 재생 방식", "브라우저", "검증 9와 검증 13의 (나)에서 확인"))

    r7, e7 = _check_hindsight_connection(rec, events, idx, bank)
    rows.append(("기억 꺼내기와 되짚기 도구 연결", r7, e7))

    record_rows = [r for r in rows if r[1] != "브라우저"]
    passed_n = sum(1 for _, status, _ in record_rows if status in ("✓", "해당 없음"))
    total_n = len(record_rows)

    out_lines = [
        f"수용표 {spec['id']} · 실행 {rec.get('run_id')} · 실행한 곳 {where}",
        "| 확인 대상 | 결과 | 근거 |",
        "| --- | --- | --- |",
    ]
    for name, status, evid in rows:
        safe_evid = str(evid).replace("|", "\\|").replace("\n", " ")
        out_lines.append(f"| {name} | {status} | {safe_evid} |")
    out_lines.append(f"기록으로 보는 일곱 줄 가운데 {passed_n}줄 맞음")

    all_passed = passed_n == total_n
    if save and all_passed:
        settings.passed_dir.mkdir(parents=True, exist_ok=True)
        (settings.passed_dir / f"{spec['id']}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )

    return (0 if all_passed else 1), "\n".join(out_lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="수용표 검사기")
    ap.add_argument("test", help="시험 id")
    ap.add_argument("--run", required=True, help="실행 기록 JSON 경로")
    ap.add_argument("--save", action="store_true", help="모두 맞으면 passed_dir 에 저장")
    args = ap.parse_args(argv)

    code, msg = evaluate_acceptance(args.test, Path(args.run), save=args.save)
    if code == 2:
        print(msg, file=sys.stderr)
        return 2
    print(msg)
    return code


if __name__ == "__main__":
    sys.exit(main())

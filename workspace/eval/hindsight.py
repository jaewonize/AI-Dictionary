"""Hindsight 채널 적재·검색·채점 실행기(docs/ARCHITECTURE.md '구성 요소와 배치'의 Hindsight 기억 저장소).

색인(settings.index)의 사진 기억과 하루 타임라인을 Hindsight 은행(settings.hindsight_bank)에 적재하고,
정답 키(settings.answer_key)의 같은 질의로 recall(상위 10장)과 reflect를 돌려 기본 색인 경로와 비교한다.

  uv run python -m eval.hindsight retain --limit 10 --batch 10 --verify-recall
  uv run python -m eval.hindsight retain --batch 10
  uv run python -m eval.hindsight retain --kinds timeline
  uv run python -m eval.hindsight export-bank --out dashboard/hindsight/aidic-mobile.zip
  uv run python -m eval.hindsight recall --run eval/passed/A.json --out demo/recall/A.json
  uv run python -m eval.hindsight eval
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app import claude
from app import index as idx_mod
from app.config import settings
from eval.run import load_answer_key

sys.stdout.reconfigure(encoding="utf-8")

BASIS_KO = {
    "taken": "찍은 시각",
    "filename": "파일 이름의 시각",
    "saved": "저장 시각",
    "inferred": "추정 시각",
}

REFLECT_SCHEMA = {
    "type": "object",
    "properties": {
        "photo_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "질의 조건에 맞는 사진의 사진 id 목록(고른 순서대로)",
        },
        "reasoning": {
            "type": "string",
            "description": "어떤 기억·시각·장소를 근거로 골랐는지 한국어 2~5문장 요약",
        },
    },
    "required": ["photo_ids", "reasoning"],
    "additionalProperties": False,
}


def _hs_base() -> tuple[str, str]:
    url = settings.hindsight_url
    bank = settings.hindsight_bank
    if not url or not bank:
        sys.exit("AIDIC_HINDSIGHT_URL 과 AIDIC_HINDSIGHT_BANK 설정이 필요하다.")
    return url, bank


def hs_request(url: str, method: str, path: str, *, allow_404: bool = False, **kw) -> dict:
    with httpx.Client(base_url=url, timeout=httpx.Timeout(600.0, connect=10.0)) as c:
        r = c.request(method, path, **kw)
        if allow_404 and r.status_code == 404:
            return {"_not_found": True}
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else {}


def make_photo_memory_item(row: dict) -> dict:
    """색인 사진 한 줄을 Hindsight 기억 항목 하나로 바꾼다. 좌표와 수신·저장 시각은 넣지 않는다."""
    when = row.get("time") or ""
    where_ko = idx_mod.place_label(row)
    where_en_bits = [x for x in (row.get("area_en"), row.get("near_en")) if x]
    where_full = f"{where_ko} ({', '.join(where_en_bits)})" if where_ko and where_en_bits else where_ko
    badge = row.get("badge") or row.get("source") or ""
    lines = [
        f"[사진] 사진 id: {row['id']}",
        (
            f"찍은 시각: {when[:10]} {when[11:19]} ({BASIS_KO.get(row.get('time_basis') or '', '시각 모름')})"
            if when
            else "찍은 시각: 모름"
        ),
        f"찍은 곳: {where_full}" if where_full else "찍은 곳: 모름",
        f"출처: {badge} 폴더",
        f"장소 종류: {row.get('place') or ''}",
        row.get("scene") or "",
    ]
    txt = (row.get("text_in_photo") or "").strip()
    if txt:
        lines.append("사진 속 글자:\n" + txt)

    item: dict = {
        "content": "\n".join(lines),
        "context": "휴대폰 사진첩에 있는 사진 한 장의 설명",
        "document_id": f"photo:{row['id']}",
        "metadata": {
            "kind": "photo",
            "photo_id": row["id"],
            "source": badge,
            "date": when[:10] if when else "",
            "place": where_ko,
            "place_group": row.get("place_group") or "",
            "time_basis": row.get("time_basis") or "",
        },
        "tags": ["photo"],
    }
    if when:
        item["timestamp"] = when
    return item


def _timeline_first_sentence(s: str, n: int = 70) -> str:
    s = " ".join((s or "").splitlines()).strip().split(". ")[0].split("다. ")[0]
    return s if len(s) <= n else s[: n - 1] + "…"


def make_timeline_memory_items(photo_rows: list[dict]) -> list[dict]:
    """시각 기준이 taken 이나 filename 인 사진을 날짜별로 묶어 하루 타임라인 기억 항목을 만든다."""
    by_date: dict[str, list[dict]] = {}
    for r in photo_rows:
        if r.get("time") and r.get("time_basis") in ("taken", "filename"):
            by_date.setdefault(r["time"][:10], []).append(r)

    items: list[dict] = []
    wd = "월화수목금토일"
    for date_s, rs in sorted(by_date.items()):
        rs.sort(key=lambda r: (r["time"], r["id"]))
        dt = datetime.fromisoformat(rs[0]["time"])
        head = f"[하루 타임라인] {dt.year}년 {dt.month}월 {dt.day}일({wd[dt.weekday()]})에 찍은 사진 {len(rs)}장을 찍은 시각 순서로 적었다."
        lines = [head]
        for r in rs:
            pl = idx_mod.place_label(r)
            badge = r.get("badge") or r.get("source") or ""
            parts = [r["time"][11:16], r.get("place") or ""]
            if pl:
                parts.append(pl)
            parts.extend([f"출처 {badge}", f"사진 id {r['id']}", _timeline_first_sentence(r.get("scene") or "")])
            line = "- " + " · ".join(parts)
            txt = " ".join((r.get("text_in_photo") or "").split())
            if txt:
                line += f" · 사진 속 글자: {txt[:60]}"
            lines.append(line)
        items.append({
            "content": "\n".join(lines),
            "timestamp": rs[0]["time"],
            "context": "하루 동안 찍은 사진의 시간 순서 기록",
            "document_id": f"timeline:{date_s}",
            "metadata": {"kind": "timeline", "date": date_s},
            "tags": ["timeline"],
        })
    return items


def make_doc_memory_item(row: dict) -> dict:
    """색인 문서 한 줄을 Hindsight 기억 항목 하나로 바꾼다."""
    doc_id = str(row["id"])
    written = str(row.get("written") or "")
    updated = row.get("updated")
    badge = row.get("badge") or row.get("source") or ""
    lines = [
        f"[문서] 문서 id: {doc_id}",
        f"작성일: {written}",
    ]
    if updated:
        lines.append(f"갱신일: {updated}")
    lines.append(f"출처: {badge} 폴더")
    lines.append(row.get("body") or "")
    return {
        "content": "\n".join(lines),
        "context": "휴대폰에 저장된 문서 한 건의 본문",
        "document_id": f"doc:{doc_id}",
        "metadata": {
            "kind": "document",
            "doc_id": doc_id,
            "source": str(row.get("source") or ""),
            "written": written,
        },
        "tags": ["document"],
        "timestamp": f"{written}T09:00:00{settings.default_tz}",
    }


def assert_no_secret_leak(items: list[dict], photo_rows: list[dict]) -> None:
    """기억 본문과 메타데이터에 GPS 좌표나 수신·저장·다운로드 시각 필드가 새지 않는지 검증한다."""
    dumped = json.dumps(items, ensure_ascii=False)
    for forbidden_key in ('"lat"', '"lon"', '"received_at"', '"saved_at"', '"downloaded_at"'):
        if forbidden_key in dumped:
            raise AssertionError(f"기억 항목에 금지 필드가 포함되었다: {forbidden_key}")
    coord_tokens: set[str] = set()
    for r in photo_rows:
        for k in ("lat", "lon"):
            v = r.get(k)
            if isinstance(v, (int, float)):
                s = f"{v:.4f}"
                coord_tokens.add(s[:-1])  # 소수점 셋째 자리까지
                coord_tokens.add(f"{v:.3f}")
    for tok in coord_tokens:
        if len(tok) >= 6 and tok in dumped:
            raise AssertionError(f"기억 항목에 좌표 문자열이 포함되었다: {tok}")


def load_photo_rows() -> list[dict]:
    idx = idx_mod.load()
    if not idx:
        sys.exit(f"색인이 없다({settings.index}). 먼저 pipeline.build_index 를 돌린다.")
    rows = [r for r in idx.values() if r.get("kind") == "photo"]
    rows.sort(key=lambda r: (r.get("time") or "", r["id"]))
    return rows


def load_doc_rows() -> list[dict]:
    idx = idx_mod.load()
    if not idx:
        sys.exit(f"색인이 없다({settings.index}). 먼저 pipeline.build_index 를 돌린다.")
    rows = [r for r in idx.values() if r.get("kind") == "document"]
    rows.sort(key=lambda r: (r.get("written") or "", r["id"]))
    return rows


def existing_docs(url: str, bank: str) -> set[str]:
    ids: set[str] = set()
    offset = 0
    while True:
        res = hs_request(url, "GET", f"/v1/default/banks/{bank}/documents",
                         allow_404=True, params={"limit": 100, "offset": offset})
        if res.get("_not_found"):
            return set()
        docs = res.get("documents") or res.get("items") or (res if isinstance(res, list) else [])
        for d in docs:
            doc_id = d.get("id") or d.get("document_id")
            if doc_id:
                ids.add(doc_id)
        if len(docs) < 100:
            return ids
        offset += 100


def bank_stats(url: str, bank: str) -> dict:
    res = hs_request(url, "GET", f"/v1/default/banks/{bank}/stats", allow_404=True)
    if res.get("_not_found"):
        return {
            "total_documents": 0,
            "total_nodes": 0,
            "total_links": 0,
            "total_observations": 0,
            "pending_operations": 0,
            "failed_operations": 0,
        }
    return res


def _cost_from_hs_tokens(input_tokens: int, output_tokens: int, cached_tokens: int, model: str) -> float:
    """Hindsight AnthropicLLM 의 토큰 집계(input = 비캐시 + cache_read + cache_write, cached = cache_read)로 비용 계산."""
    p = claude.PRICES.get(model) or claude.PRICES["claude-opus-5-5"]
    non_cached = max(0, int(input_tokens) - int(cached_tokens))
    cost = (non_cached * p["in"] + int(cached_tokens) * p["cr"] + int(output_tokens) * p["out"]) / 1e6
    return round(cost, 6)


def bank_llm_usage(url: str, bank: str, operation: str | None = None) -> dict:
    params: dict = {"call_type": "llm", "period": "30d"}
    if operation:
        params["operation"] = operation
    res = hs_request(url, "GET", f"/v1/default/banks/{bank}/llm-requests/stats",
                     allow_404=True, params=params)
    if res.get("_not_found"):
        return {
            "calls": 0, "success": 0, "errors": 0,
            "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
            "cost_usd": 0.0, "provider": "anthropic", "model": settings.model,
        }
    calls = success = inp = out = cached = 0
    for b in res.get("buckets") or []:
        calls += int(b.get("total") or 0)
        st = b.get("statuses") or {}
        success += int(st.get("success") or 0)
        tok = b.get("tokens") or {}
        inp += int(tok.get("input") or 0)
        out += int(tok.get("output") or 0)
        cached += int(tok.get("cached") or 0)
    provider = "anthropic"
    model = settings.model
    sample = hs_request(url, "GET", f"/v1/default/banks/{bank}/llm-requests",
                        allow_404=True, params={"call_type": "llm", "limit": 1})
    items = sample.get("items") or []
    if items:
        provider = items[0].get("provider") or provider
        model = items[0].get("model") or model
    return {
        "calls": calls,
        "success": success,
        "errors": max(0, calls - success),
        "input_tokens": inp,
        "output_tokens": out,
        "cached_tokens": cached,
        "cost_usd": _cost_from_hs_tokens(inp, out, cached, model),
        "provider": provider,
        "model": model,
    }


def _usage_delta(before: dict, after: dict) -> dict:
    model = after.get("model") or before.get("model") or settings.model
    inp = max(0, int(after["input_tokens"]) - int(before["input_tokens"]))
    out = max(0, int(after["output_tokens"]) - int(before["output_tokens"]))
    cached = max(0, int(after["cached_tokens"]) - int(before["cached_tokens"]))
    return {
        "calls": max(0, int(after["calls"]) - int(before["calls"])),
        "errors": max(0, int(after["errors"]) - int(before["errors"])),
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cached,
        "cost_usd": _cost_from_hs_tokens(inp, out, cached, model),
        "model": model,
    }


def _check_budget(url: str, bank: str, stage_cap_usd: float) -> dict:
    total_hs = bank_llm_usage(url, bank)
    base_spent = claude.spent_usd()
    if total_hs["cost_usd"] >= stage_cap_usd:
        raise RuntimeError(
            f"Hindsight 단계 누적 비용 ${total_hs['cost_usd']:.2f} 이 단계 상한 ${stage_cap_usd:.2f} 에 도달해 멈췄다."
        )
    if base_spent + total_hs["cost_usd"] >= settings.cost_cap_usd:
        raise RuntimeError(
            f"전체 누적 비용 ${base_spent + total_hs['cost_usd']:.2f} 이 총 한도 ${settings.cost_cap_usd:.0f} 에 도달해 멈췄다."
        )
    return total_hs


def wait_ops(url: str, bank: str, op_ids: list[str], label: str, timeout_s: float = 600.0) -> tuple[int, int, list[str]]:
    pending = set(op_ids)
    ok = fail = 0
    errors: list[str] = []
    t0 = time.time()
    while pending:
        for op in list(pending):
            st = hs_request(url, "GET", f"/v1/default/banks/{bank}/operations/{op}")
            s = (st.get("status") or "").lower()
            if s in ("completed", "succeeded", "success", "done"):
                pending.discard(op)
                ok += 1
            elif s in ("failed", "error", "cancelled"):
                pending.discard(op)
                fail += 1
                msg = f"{op}: {s} ({str(st.get('error') or st)[:240]})"
                errors.append(msg)
                print(f"  ✗ {label} 실패 {msg}", flush=True)
        if pending:
            elapsed = time.time() - t0
            if elapsed > timeout_s:
                for op in list(pending):
                    fail += 1
                    errors.append(f"{op}: timeout after {int(elapsed)}s")
                print(f"  ✗ {label} 시간 초과({int(elapsed)}s): 미완료 {len(pending)}개", flush=True)
                break
            time.sleep(5)
    return ok, fail, errors


def _first_sentence(text: str) -> str:
    s = (text or "").strip()
    for sep in ("다. ", "다.\n", ". ", "\n"):
        if sep in s:
            head = s.split(sep, 1)[0].strip()
            if sep.startswith("다"):
                head += "다"
            return head[:90]
    return s[:90]


def _recall_photos(url: str, bank: str, query: str, max_tokens: int = 8192) -> tuple[list[str], list[dict], int]:
    t0 = time.perf_counter()
    rec = hs_request(
        url,
        "POST",
        f"/v1/default/banks/{bank}/memories/recall",
        json={"query": query, "budget": "mid", "max_tokens": max_tokens},
    )
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    raw_results = rec.get("results") or []
    hits: list[dict] = []
    seen_photos: list[str] = []
    for rank_i, r in enumerate(raw_results, 1):
        doc = r.get("document_id") or ""
        pid = doc.split(":", 1)[1] if doc.startswith("photo:") else None
        if pid and pid not in seen_photos:
            seen_photos.append(pid)
        hits.append({
            "fact_rank": rank_i,
            "document_id": doc,
            "photo_id": pid,
            "occurred_start": r.get("occurred_start"),
            "text": (r.get("text") or "")[:240],
        })
    return seen_photos, hits, elapsed_ms


def _server_info(url: str) -> dict:
    ver = hs_request(url, "GET", "/version")
    feats = ver.get("features") or {}
    return {
        "api_version": ver.get("api_version"),
        "observations": bool(feats.get("observations", False)),
        "llm_trace": bool(feats.get("llm_trace", False)),
        "llm_provider": "anthropic",
        "llm_model": settings.model,
        "embeddings_provider": "local",
        "embeddings_model": "BAAI/bge-m3",
        "reranker_model": "hindsight-ai/hs-rerank-1",
        "text_search_extension": "pgroonga",
        "vector_extension": "pgvector",
        "max_concurrent": 2,
        "max_retries": 8,
    }


def _safe_doc_memory_items(doc_rows: list[dict], photo_rows: list[dict]) -> list[dict]:
    items: list[dict] = []
    for r in doc_rows:
        it = make_doc_memory_item(r)
        try:
            assert_no_secret_leak([it], photo_rows)
        except AssertionError as e:
            print(f"  [보호 검사 제외] 문서 {r['id']} 제외 ({e})", flush=True)
            continue
        items.append(it)
    return items


def cmd_retain(args: argparse.Namespace) -> int:
    url, bank = _hs_base()
    photo_rows = load_photo_rows()
    kinds = getattr(args, "kinds", "photo") or "photo"
    if kinds == "photo":
        target_rows = photo_rows[: args.limit] if args.limit > 0 else photo_rows
        all_target_items = [make_photo_memory_item(r) for r in target_rows]
        assert_no_secret_leak(all_target_items, photo_rows)
    elif kinds == "timeline":
        target_rows = []
        tl_items = make_timeline_memory_items(photo_rows)
        all_target_items = tl_items[: args.limit] if args.limit > 0 else tl_items
        assert_no_secret_leak(all_target_items, photo_rows)
    elif kinds == "document":
        target_rows = []
        doc_rows = load_doc_rows()
        doc_items = _safe_doc_memory_items(doc_rows, photo_rows)
        all_target_items = doc_items[: args.limit] if args.limit > 0 else doc_items
    elif kinds == "all":
        target_rows = photo_rows[: args.limit] if args.limit > 0 else photo_rows
        photo_and_tl = [make_photo_memory_item(r) for r in photo_rows] + make_timeline_memory_items(photo_rows)
        assert_no_secret_leak(photo_and_tl, photo_rows)
        doc_items = _safe_doc_memory_items(load_doc_rows(), photo_rows)
        combined = photo_and_tl + doc_items
        all_target_items = combined[: args.limit] if args.limit > 0 else combined
    else:
        sys.exit(f"알 수 없는 --kinds 값: {kinds}")
    target_doc_ids = {it["document_id"] for it in all_target_items}

    srv = _server_info(url)
    have = existing_docs(url, bank)
    todo = [it for it in all_target_items if it["document_id"] not in have]

    print(
        f"[retain] 은행={bank} · 목표={len(all_target_items)}장 (전체 사진 {len(photo_rows)}장) "
        f"· 기존 적재={len(have)}건 · 이번 적재 대상={len(todo)}장 · 배치={args.batch}장 "
        f"· observations={srv['observations']}",
        flush=True,
    )

    t0 = time.perf_counter()
    total_batch_fail = 0
    all_errors: list[str] = []
    batches = [todo[i:i + args.batch] for i in range(0, len(todo), args.batch)]

    for bi, batch in enumerate(batches, 1):
        _check_budget(url, bank, args.stage_cap_usd)
        bt0 = time.perf_counter()
        res = hs_request(
            url,
            "POST",
            f"/v1/default/banks/{bank}/memories",
            json={"items": batch, "async": True},
        )
        ops = [o for o in (res.get("operation_ids") or [res.get("operation_id")]) if o]
        _, fail_n, errs = wait_ops(url, bank, ops, f"batch-{bi}/{len(batches)}")
        total_batch_fail += fail_n
        all_errors.extend(errs)

        docs_now = existing_docs(url, bank)
        done_docs_n = len(target_doc_ids & docs_now)
        st_now = bank_stats(url, bank)
        u_ret = bank_llm_usage(url, bank, operation="retain")
        u_all = bank_llm_usage(url, bank)
        print(
            f"  [{bi}/{len(batches)}] 적재 문서 {done_docs_n}/{len(all_target_items)}장 "
            f"(전체 은행 {len(docs_now)}건, 실패 작업 {total_batch_fail}) "
            f"· 추출 사실 {st_now.get('total_nodes', 0)}개 "
            f"· retain LLM {u_ret['calls']}회(오류 {u_ret['errors']}) "
            f"· retain 누적 ${u_ret['cost_usd']:.4f} (은행 전체 ${u_all['cost_usd']:.4f}) "
            f"· {time.perf_counter() - bt0:.1f}s",
            flush=True,
        )

    docs_final = existing_docs(url, bank)
    done_docs_final = len(target_doc_ids & docs_final)
    missing_docs = sorted(target_doc_ids - docs_final)
    st_final = bank_stats(url, bank)
    u_retain = bank_llm_usage(url, bank, operation="retain")
    u_total = bank_llm_usage(url, bank)
    srv["llm_provider"] = u_retain.get("provider") or srv["llm_provider"]
    srv["llm_model"] = u_retain.get("model") or srv["llm_model"]

    verify_checks: list[dict] = []
    if args.verify_recall and target_rows:
        probe_indices = [0] if len(target_rows) == 1 else [0, len(target_rows) - 1]
        for idx_i in probe_indices:
            prow = target_rows[idx_i]
            probe_q = _first_sentence(prow.get("scene") or "")
            if not probe_q:
                continue
            r_photos, r_hits, r_ms = _recall_photos(url, bank, probe_q, max_tokens=4096)
            rank = (r_photos.index(prow["id"]) + 1) if prow["id"] in r_photos else None
            chk = {
                "target_photo_id": prow["id"],
                "probe_query": probe_q,
                "found_rank": rank,
                "returned_photos": r_photos[:5],
                "fact_hits": len(r_hits),
                "top_fact_preview": r_hits[0]["text"] if r_hits else "",
                "elapsed_ms": r_ms,
            }
            verify_checks.append(chk)
            print(
                f"  [recall 점검] 목표={prow['id']} · 순위={rank} · 반환 사진={len(r_photos)}장 "
                f"(상위 {r_photos[:3]}) · 사실 조각={len(r_hits)}개 · {r_ms/1000:.1f}s",
                flush=True,
            )

    retain_doc = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bank": bank,
        "url": url,
        "total_index_photos": len(photo_rows),
        "target_photos": len(all_target_items),
        "inserted_memories": done_docs_final,
        "failed_count": len(missing_docs) + int(st_final.get("failed_operations") or 0),
        "batch_failures": total_batch_fail,
        "missing_documents": missing_docs,
        "errors": all_errors,
        "stats": {
            "total_documents": int(st_final.get("total_documents") or done_docs_final),
            "total_nodes": int(st_final.get("total_nodes") or 0),
            "total_links": int(st_final.get("total_links") or 0),
            "total_observations": int(st_final.get("total_observations") or 0),
            "failed_operations": int(st_final.get("failed_operations") or 0),
        },
        "llm_usage": u_retain,
        "bank_total_llm_usage": u_total,
        "settings": srv,
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "verify_recall": verify_checks,
    }

    out_path = settings.var_dir / "hindsight_retain.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(retain_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.verify_recall:
        pilot_path = settings.var_dir / "hindsight_pilot.json"
        pilot_path.write_text(json.dumps(retain_doc, ensure_ascii=False, indent=1), encoding="utf-8")

    print(
        f"[retain 완료] 적재 {done_docs_final}/{len(all_target_items)}장 · 미적재 {len(missing_docs)}건 "
        f"· 사실 {retain_doc['stats']['total_nodes']}개 · LLM {u_retain['calls']}회 "
        f"· retain 비용 ${u_retain['cost_usd']:.4f} -> {out_path}",
        flush=True,
    )
    return 0 if not missing_docs and total_batch_fail == 0 else 1


def _first_rank(ordered_ids: list[str], target_set: set[str]) -> int | None:
    for i, pid in enumerate(ordered_ids, 1):
        if pid in target_set:
            return i
    return None


def _grade_ids(spec: dict, chosen_ids: list[str], *, answered: bool, sentence: str, error: str | None) -> dict:
    got_list = idx_mod.by_time(chosen_ids)
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

    exact = answered and (got == must) and not trap_hits
    passed = answered and not missing and not extra and not trap_hits
    rec_val = (len(matched_must) / len(must)) if must else (1.0 if not got else 0.0)
    prec_val = (len(got & allowed) / len(got)) if got else (1.0 if not must else 0.0)

    return {
        "passed": passed,
        "exact_match": exact,
        "status": "answered" if answered else "failed",
        "error": error,
        "answer_ids": must_list,
        "optional_ids": opt_list,
        "trap_ids": trap_list,
        "got_ids": got_list,
        "matched_must": matched_must,
        "matched_optional": matched_opt,
        "missing_ids": missing,
        "extra_ids": extra,
        "trap_hits": trap_hits,
        "recall": round(rec_val, 4),
        "precision": round(prec_val, 4),
        "result_sentence": sentence,
    }


def cmd_eval(args: argparse.Namespace) -> int:
    url, bank = _hs_base()
    idx = idx_mod.load()
    if not idx:
        sys.exit(f"색인이 없다({settings.index}).")

    specs = load_answer_key(settings.answer_key)
    if "all" not in args.targets:
        want = set(args.targets)
        specs = [s for s in specs if s["id"] in want]
        if not specs:
            sys.exit(f"정답 키에 해당 id 가 없다: {args.targets}")

    docs_now = existing_docs(url, bank)
    photo_docs_n = sum(1 for d in docs_now if d.startswith("photo:"))
    if photo_docs_n == 0:
        sys.exit(f"은행 {bank} 에 사진 기억이 없다. 먼저 retain 을 실행한다.")

    without_by_id: dict[str, dict] = {}
    without_path = settings.var_dir / "eval_results.json"
    if without_path.is_file():
        try:
            raw_wo = json.loads(without_path.read_text(encoding="utf-8"))
            for r in raw_wo.get("results") or []:
                if isinstance(r, dict) and "id" in r:
                    without_by_id[r["id"]] = r
        except Exception:
            without_by_id = {}

    out_path = settings.var_dir / "hindsight_eval.json"
    prev_by_id: dict[str, dict] = {}
    if "all" not in args.targets and out_path.is_file():
        try:
            prev_doc = json.loads(out_path.read_text(encoding="utf-8"))
            for r in prev_doc.get("results") or []:
                if isinstance(r, dict) and "id" in r:
                    prev_by_id[r["id"]] = r
        except Exception:
            prev_by_id = {}

    srv = _server_info(url)
    u_start = bank_llm_usage(url, bank)
    print(
        f"[hindsight eval] 은행={bank} (사진 기억 {photo_docs_n}장) · 질의 {len(specs)}건 "
        f"· 모델 {srv['llm_model']} · 시작 시 은행 누적 비용 ${u_start['cost_usd']:.4f}",
        flush=True,
    )

    run_results: list[dict] = []
    t_eval0 = time.perf_counter()

    for qi, spec in enumerate(specs, 1):
        _check_budget(url, bank, args.stage_cap_usd)
        qid = spec["id"]
        query = spec["query"]
        must_set = set(spec.get("answer_ids") or [])
        opt_set = set(spec.get("optional_ids") or [])
        allowed_set = must_set | opt_set
        trap_set = set(spec.get("trap_ids") or [])

        # 1) recall (LLM 0회, $0)
        rec_photos, rec_hits, rec_ms = _recall_photos(url, bank, query, max_tokens=8192)
        top10 = rec_photos[:10]
        rec_first_must = _first_rank(rec_photos, must_set)
        rec_first_allowed = _first_rank(rec_photos, allowed_set)
        rec_must_top10 = [p for p in top10 if p in must_set]
        rec_opt_top10 = [p for p in top10 if p in opt_set]
        rec_trap_top10 = [p for p in top10 if p in trap_set]
        rec_extra_top10 = [p for p in top10 if p not in allowed_set]
        rec_recall10 = round(len(rec_must_top10) / len(must_set), 4) if must_set else 1.0

        # 2) reflect (LLM 추론 + 구조화 출력)
        u_ref_before = bank_llm_usage(url, bank, operation="reflect")
        t_rf0 = time.perf_counter()
        rf_err: str | None = None
        ranked_reflect_ids: list[str] = []
        invalid_reflect_ids: list[str] = []
        reasoning = ""
        recall_tool_calls = 0
        tool_queries: list[str] = []
        answered = False

        try:
            rf = hs_request(
                url,
                "POST",
                f"/v1/default/banks/{bank}/reflect",
                json={
                    "query": (
                        f"{query}\n"
                        "조건에 맞는 사진의 정확한 사진 id 목록을 빠짐없이 적고, "
                        "어떤 기억·시각·장소를 근거로 골랐는지 함께 알려 줘."
                    ),
                    "budget": "mid",
                    "response_schema": REFLECT_SCHEMA,
                    "include": {"tool_calls": {}},
                },
            )
            so = rf.get("structured_output") or {}
            so_err = rf.get("structured_output_error")
            raw_ids = [re.sub(r"^photo:", "", str(x).strip()) for x in (so.get("photo_ids") or []) if str(x).strip()]
            for pid in raw_ids:
                if pid in idx and idx[pid].get("kind") == "photo":
                    if pid not in ranked_reflect_ids:
                        ranked_reflect_ids.append(pid)
                else:
                    invalid_reflect_ids.append(pid)
            reasoning = (so.get("reasoning") or rf.get("text") or "").strip()[:1000]
            tc = ((rf.get("trace") or {}).get("tool_calls") or [])
            recall_tool_calls = len(tc)
            for c in tc[:12]:
                if isinstance(c, dict):
                    q_arg = ((c.get("input") or c.get("arguments") or {}) if isinstance(c.get("input") or c.get("arguments"), dict) else {}).get("query")
                    if q_arg:
                        tool_queries.append(str(q_arg))
            if so_err and not so:
                rf_err = f"structured_output_error: {so_err}"
            else:
                answered = True
        except Exception as e:
            rf_err = f"{type(e).__name__}: {str(e)[:260]}"

        rf_ms = int((time.perf_counter() - t_rf0) * 1000)
        u_ref_after = bank_llm_usage(url, bank, operation="reflect")
        delta = _usage_delta(u_ref_before, u_ref_after)

        graded = _grade_ids(spec, ranked_reflect_ids, answered=answered, sentence=reasoning, error=rf_err)
        rf_first_must = _first_rank(ranked_reflect_ids, must_set)
        rf_first_allowed = _first_rank(ranked_reflect_ids, allowed_set)
        rf_time_first_must = _first_rank(graded["got_ids"], must_set)

        wo = without_by_id.get(qid)
        wo_got = (wo.get("got_ids") or []) if wo else []
        wo_summary = {
            "available": wo is not None,
            "passed": bool(wo.get("passed")) if wo else None,
            "exact_match": bool(wo.get("exact_match")) if wo else None,
            "got_ids": wo_got,
            "matched_must_count": len(wo.get("matched_must") or []) if wo else 0,
            "trap_hits_count": len(wo.get("trap_hits") or []) if wo else 0,
            "extra_ids_count": len(wo.get("extra_ids") or []) if wo else 0,
            "first_must_rank": _first_rank(wo_got, must_set) if wo else None,
            "first_allowed_rank": _first_rank(wo_got, allowed_set) if wo else None,
            "calls": int(wo.get("calls") or 0) if wo else 0,
            "cost_usd": float(wo.get("cost_usd") or 0.0) if wo else 0.0,
        }

        entry = {
            "id": qid,
            "kind": spec.get("kind", ""),
            "query": query,
            "note": spec.get("note", ""),
            **graded,
            "ranked_got_ids": ranked_reflect_ids,
            "invalid_ids": invalid_reflect_ids,
            "first_must_rank": rf_first_must,
            "first_allowed_rank": rf_first_allowed,
            "time_ordered_first_must_rank": rf_time_first_must,
            "recall_tool_calls": recall_tool_calls,
            "tool_queries": tool_queries,
            "calls": delta["calls"],
            "input_tokens": delta["input_tokens"],
            "output_tokens": delta["output_tokens"],
            "cache_read_input_tokens": delta["cache_read_input_tokens"],
            "cost_usd": delta["cost_usd"],
            "elapsed_ms": rf_ms,
            "recall_channel": {
                "top10_ids": top10,
                "total_photo_hits": len(rec_photos),
                "fact_hits": len(rec_hits),
                "first_must_rank": rec_first_must,
                "first_allowed_rank": rec_first_allowed,
                "matched_must_top10": rec_must_top10,
                "matched_optional_top10": rec_opt_top10,
                "trap_hits_top10": rec_trap_top10,
                "extra_top10": rec_extra_top10,
                "recall_at_10": rec_recall10,
                "elapsed_ms": rec_ms,
                "calls": 0,
                "cost_usd": 0.0,
            },
            "without_hindsight": wo_summary,
        }
        run_results.append(entry)
        prev_by_id[qid] = entry

        mark = "✓" if entry["passed"] else "✗"
        wo_mark = ("✓" if wo_summary["passed"] else "✗") if wo_summary["available"] else "—"
        print(
            f"  [{qi}/{len(specs)}] {mark} [{qid}] "
            f"reflect 정답 {len(entry['matched_must'])}/{len(entry['answer_ids'])} "
            f"(고른 {len(entry['got_ids'])}장, 빠짐 {len(entry['missing_ids'])}, 여분 {len(entry['extra_ids'])}, 함정 {len(entry['trap_hits'])}, "
            f"첫정답순위={rf_first_must}) "
            f"· recall@10 정답 {len(rec_must_top10)}/{len(entry['answer_ids'])}(첫정답순위={rec_first_must}, {rec_ms/1000:.1f}s) "
            f"· 기존경로={wo_mark}(첫정답순위={wo_summary['first_must_rank']}) "
            f"· LLM {entry['calls']}회 · ${entry['cost_usd']:.4f} · {rf_ms/1000:.1f}s",
            flush=True,
        )

        _write_eval_doc(out_path, url, bank, srv, prev_by_id, t_eval0)

    final_doc = _write_eval_doc(out_path, url, bank, srv, prev_by_id, t_eval0)
    time.sleep(0.5)
    final_doc = _reconcile_files(url, bank, srv, out_path, final_doc.get("elapsed_s") or 0.0)
    print(
        f"[hindsight eval 완료] reflect 통과 {final_doc['passed']}/{final_doc['total']} "
        f"(정확 일치 {final_doc['exact']}/{final_doc['total']}) "
        f"· recall 상위10 첫정답 적중 {final_doc['recall_summary']['top10_hit_queries']}/{final_doc['total']} "
        f"(1위 {final_doc['recall_summary']['rank1_queries']}개) "
        f"· reflect LLM {final_doc['total_calls']}회 · 비용 ${final_doc['total_cost_usd']:.4f} -> {out_path}",
        flush=True,
    )
    return 0


def _fetch_reflect_traces(url: str, bank: str) -> list[dict]:
    items: list[dict] = []
    offset = 0
    while True:
        res = hs_request(
            url, "GET", f"/v1/default/banks/{bank}/llm-requests",
            allow_404=True,
            params={"call_type": "llm", "operation": "reflect", "limit": 500, "offset": offset},
        )
        batch = res.get("items") or []
        items.extend(batch)
        if len(batch) < 500:
            break
        offset += 500
    by_trace: dict[str, list[dict]] = {}
    for x in items:
        tid = x.get("trace_id") or x.get("id") or ""
        if tid:
            by_trace.setdefault(tid, []).append(x)
    traces: list[dict] = []
    for tid, xs in by_trace.items():
        started = min((x.get("started_at") or "") for x in xs)
        model = next((x.get("model") for x in xs if x.get("model")), settings.model)
        inp = sum(int(x.get("input_tokens") or 0) for x in xs)
        out = sum(int(x.get("output_tokens") or 0) for x in xs)
        cached = sum(int(x.get("cached_tokens") or 0) for x in xs)
        traces.append({
            "trace_id": tid,
            "started_at": started,
            "calls": len(xs),
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_input_tokens": cached,
            "cost_usd": _cost_from_hs_tokens(inp, out, cached, model),
        })
    traces.sort(key=lambda t: t["started_at"])
    return traces


def _reconcile_files(url: str, bank: str, srv: dict, eval_path: Path, elapsed_s: float) -> dict:
    ret_path = settings.var_dir / "hindsight_retain.json"
    if ret_path.is_file():
        ret_doc = json.loads(ret_path.read_text(encoding="utf-8"))
        docs_now = existing_docs(url, bank)
        photo_docs_n = sum(1 for d in docs_now if d.startswith("photo:"))
        st_now = bank_stats(url, bank)
        ret_doc["inserted_memories"] = photo_docs_n
        ret_doc["stats"] = {
            "total_documents": int(st_now.get("total_documents") or photo_docs_n),
            "total_nodes": int(st_now.get("total_nodes") or 0),
            "total_links": int(st_now.get("total_links") or 0),
            "total_observations": int(st_now.get("total_observations") or 0),
            "failed_operations": int(st_now.get("failed_operations") or 0),
        }
        ret_doc["llm_usage"] = bank_llm_usage(url, bank, operation="retain")
        ret_doc["bank_total_llm_usage"] = bank_llm_usage(url, bank)
        ret_path.write_text(json.dumps(ret_doc, ensure_ascii=False, indent=1), encoding="utf-8")

    ev_doc = json.loads(eval_path.read_text(encoding="utf-8"))
    results = ev_doc.get("results") or []
    traces = _fetch_reflect_traces(url, bank)
    if results and len(traces) >= len(results):
        aligned = traces[-len(results):]
        for r, tr in zip(results, aligned):
            r["trace_id"] = tr["trace_id"]
            r["calls"] = tr["calls"]
            r["input_tokens"] = tr["input_tokens"]
            r["output_tokens"] = tr["output_tokens"]
            r["cache_read_input_tokens"] = tr["cache_read_input_tokens"]
            r["cost_usd"] = tr["cost_usd"]
    by_id = {r["id"]: r for r in results}
    fixed = _write_eval_doc(eval_path, url, bank, srv, by_id, time.perf_counter() - elapsed_s)
    fixed["elapsed_s"] = elapsed_s
    eval_path.write_text(json.dumps(fixed, ensure_ascii=False, indent=1), encoding="utf-8")
    return fixed


def cmd_reconcile(_args: argparse.Namespace) -> int:
    url, bank = _hs_base()
    srv = _server_info(url)
    eval_path = settings.var_dir / "hindsight_eval.json"
    if not eval_path.is_file():
        sys.exit(f"파일이 없다: {eval_path}")
    prev = json.loads(eval_path.read_text(encoding="utf-8"))
    fixed = _reconcile_files(url, bank, srv, eval_path, float(prev.get("elapsed_s") or 0.0))
    print(
        f"[reconcile 완료] reflect 호출 {fixed['total_calls']}회 "
        f"(은행 집계 {fixed['llm_usage']['reflect']['calls']}회) "
        f"· 비용 ${fixed['total_cost_usd']:.4f} (은행 집계 ${fixed['llm_usage']['reflect']['cost_usd']:.4f})",
        flush=True,
    )
    return 0


def _write_eval_doc(out_path: Path, url: str, bank: str, srv: dict, by_id: dict[str, dict], t0: float) -> dict:
    all_order = [s["id"] for s in load_answer_key(settings.answer_key)]
    merged = [by_id[qid] for qid in all_order if qid in by_id] + [
        v for k, v in by_id.items() if k not in set(all_order)
    ]
    n = len(merged)
    passed_n = sum(1 for r in merged if r["passed"])
    exact_n = sum(1 for r in merged if r["exact_match"])
    total_calls = sum(int(r.get("calls") or 0) for r in merged)
    total_cost = round(sum(float(r.get("cost_usd") or 0.0) for r in merged), 6)

    rec_ranks = [r["recall_channel"]["first_must_rank"] for r in merged]
    rf_ranks = [r["first_must_rank"] for r in merged]
    wo_ranks = [r["without_hindsight"]["first_must_rank"] for r in merged if r.get("without_hindsight", {}).get("available")]

    def _mrr(ranks: list[int | None]) -> float:
        if not ranks:
            return 0.0
        return round(sum((1.0 / rk) if rk else 0.0 for rk in ranks) / len(ranks), 4)

    u_retain = bank_llm_usage(url, bank, operation="retain")
    u_reflect = bank_llm_usage(url, bank, operation="reflect")
    u_total = bank_llm_usage(url, bank)
    st_now = bank_stats(url, bank)
    srv["llm_provider"] = u_total.get("provider") or srv["llm_provider"]
    srv["llm_model"] = u_total.get("model") or srv["llm_model"]

    better_ids = [
        r["id"] for r in merged
        if r.get("without_hindsight", {}).get("available") and r["passed"] and not r["without_hindsight"]["passed"]
    ]
    worse_ids = [
        r["id"] for r in merged
        if r.get("without_hindsight", {}).get("available") and not r["passed"] and r["without_hindsight"]["passed"]
    ]
    both_pass_ids = [
        r["id"] for r in merged
        if r.get("without_hindsight", {}).get("available") and r["passed"] and r["without_hindsight"]["passed"]
    ]
    both_fail_ids = [
        r["id"] for r in merged
        if r.get("without_hindsight", {}).get("available") and not r["passed"] and not r["without_hindsight"]["passed"]
    ]

    doc = {
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bank": bank,
        "model": srv["llm_model"],
        "settings": srv,
        "bank_stats": {
            "total_documents": int(st_now.get("total_documents") or 0),
            "total_nodes": int(st_now.get("total_nodes") or 0),
            "total_links": int(st_now.get("total_links") or 0),
            "total_observations": int(st_now.get("total_observations") or 0),
            "failed_operations": int(st_now.get("failed_operations") or 0),
        },
        "llm_usage": {
            "retain": u_retain,
            "reflect": u_reflect,
            "total": u_total,
        },
        "total": n,
        "passed": passed_n,
        "failed": n - passed_n,
        "exact": exact_n,
        "avg_recall": round(sum(r["recall"] for r in merged) / n, 4) if n else 0.0,
        "avg_precision": round(sum(r["precision"] for r in merged) / n, 4) if n else 0.0,
        "total_calls": total_calls,
        "total_cost_usd": total_cost,
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "recall_summary": {
            "rank1_queries": sum(1 for rk in rec_ranks if rk == 1),
            "top3_hit_queries": sum(1 for rk in rec_ranks if rk is not None and rk <= 3),
            "top10_hit_queries": sum(1 for rk in rec_ranks if rk is not None and rk <= 10),
            "found_any_queries": sum(1 for rk in rec_ranks if rk is not None),
            "mrr": _mrr(rec_ranks),
            "avg_recall_at_10": round(sum(r["recall_channel"]["recall_at_10"] for r in merged) / n, 4) if n else 0.0,
            "calls": 0,
            "cost_usd": 0.0,
        },
        "reflect_rank_summary": {
            "rank1_queries": sum(1 for rk in rf_ranks if rk == 1),
            "found_any_queries": sum(1 for rk in rf_ranks if rk is not None),
            "mrr": _mrr(rf_ranks),
        },
        "without_hindsight_rank_summary": {
            "rank1_queries": sum(1 for rk in wo_ranks if rk == 1),
            "found_any_queries": sum(1 for rk in wo_ranks if rk is not None),
            "mrr": _mrr(wo_ranks),
        },
        "comparison": {
            "reflect_better_ids": better_ids,
            "reflect_worse_ids": worse_ids,
            "both_passed_ids": both_pass_ids,
            "both_failed_ids": both_fail_ids,
        },
        "results": merged,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def cmd_import_bank(args: argparse.Namespace) -> int:
    url, bank = _hs_base()
    target_bank = args.target_bank or bank
    zip_path = Path(args.zip_file) if args.zip_file else (settings.var_dir / f"{bank}.zip")
    if not zip_path.is_file():
        sys.exit(f"은행 백업 파일이 없다: {zip_path}")

    docs_now = existing_docs(url, target_bank)
    if docs_now:
        print(f"[import-bank] 은행 {target_bank} 에 이미 문서 {len(docs_now)}건이 있어 건너뜁니다.", flush=True)
        return 0

    print(f"[import-bank] {zip_path} -> 은행 {target_bank} 복원 시작 (LLM 호출 없음, $0)...", flush=True)
    created_bootstrap = False
    with httpx.Client(base_url=url, timeout=httpx.Timeout(600.0, connect=10.0)) as c:
        r_banks = c.get("/v1/default/banks")
        banks_payload = r_banks.json() if (r_banks.status_code < 400 and r_banks.content) else {}
        bank_items = (
            banks_payload.get("banks")
            or banks_payload.get("items")
            or (banks_payload if isinstance(banks_payload, list) else [])
        )
        existing_bank_ids = [
            str(b.get("bank_id") or b.get("id"))
            for b in bank_items
            if isinstance(b, dict) and (b.get("bank_id") or b.get("id"))
        ]
        host_bank = next((bid for bid in existing_bank_ids if bid != target_bank), None)
        if not host_bank:
            host_bank = "_import_host"
            r_put = c.put(f"/v1/default/banks/{host_bank}", json={})
            if r_put.status_code >= 400:
                raise RuntimeError(f"임시 호스트 은행 생성 실패 ({r_put.status_code}): {r_put.text[:300]}")
            created_bootstrap = True

        with zip_path.open("rb") as fh:
            r = c.post(
                f"/v1/default/banks/{host_bank}/transfer/import",
                params={"mode": "restore", "target_bank_id": target_bank},
                files={"file": (zip_path.name, fh, "application/zip")},
            )
        if r.status_code >= 400:
            if created_bootstrap:
                c.delete(f"/v1/default/banks/{host_bank}")
            raise RuntimeError(f"import 실패 ({r.status_code}): {r.text[:400]}")
        res = r.json()

    op_id = res.get("operation_id") or (res.get("operation_ids") or [None])[0]
    if op_id:
        _, fail_n, errs = wait_ops(url, host_bank, [op_id], "import-bank", timeout_s=1200.0)
        if created_bootstrap:
            try:
                hs_request(url, "DELETE", f"/v1/default/banks/{host_bank}", allow_404=True)
            except Exception:
                pass
        if fail_n:
            sys.exit(f"은행 복원 실패: {errs}")
    elif created_bootstrap:
        try:
            hs_request(url, "DELETE", f"/v1/default/banks/{host_bank}", allow_404=True)
        except Exception:
            pass

    docs_after = existing_docs(url, target_bank)
    st_after = bank_stats(url, target_bank)
    print(
        f"[import-bank 완료] 은행={target_bank} · 문서 {len(docs_after)}건 "
        f"· 사실 노드 {st_after.get('total_nodes', 0)}개 복원 완료",
        flush=True,
    )
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    url, bank = _hs_base()
    srv = _server_info(url)
    docs = existing_docs(url, bank)
    st = bank_stats(url, bank)
    u_ret = bank_llm_usage(url, bank, operation="retain")
    u_ref = bank_llm_usage(url, bank, operation="reflect")
    u_all = bank_llm_usage(url, bank)
    print(json.dumps({
        "bank": bank,
        "documents": len(docs),
        "photo_documents": sum(1 for d in docs if d.startswith("photo:")),
        "stats": st,
        "settings": srv,
        "llm_usage": {"retain": u_ret, "reflect": u_ref, "total": u_all},
    }, ensure_ascii=False, indent=2))
    return 0


EXPORT_POLL_S = 2.0


def cmd_export_bank(args: argparse.Namespace) -> int:
    url, bank = _hs_base()
    out_path = Path(args.out)
    try:
        with httpx.Client(base_url=url, timeout=httpx.Timeout(600.0, connect=10.0)) as c:
            r = c.post(f"/v1/default/banks/{bank}/transfer/export")
            if r.status_code >= 400:
                print(f"export 요청 실패 ({r.status_code}): {r.text[:300]}", file=sys.stderr)
                return 1
            res = r.json() if r.content else {}
            op_id = res.get("operation_id")
            if not op_id:
                print("export 응답에 operation_id 가 없다.", file=sys.stderr)
                return 1

            t0 = time.time()
            st: dict = {}
            while True:
                r_op = c.get(f"/v1/default/banks/{bank}/operations/{op_id}")
                if r_op.status_code == 404:
                    print("not_found", file=sys.stderr)
                    return 1
                if r_op.status_code >= 400:
                    print(f"operation 조회 실패 ({r_op.status_code})", file=sys.stderr)
                    return 1
                st = r_op.json() if r_op.content else {}
                s = (st.get("status") or "").lower()
                if s == "completed":
                    break
                if s in ("failed", "cancelled", "not_found"):
                    print(str(st.get("error_message") or s), file=sys.stderr)
                    return 1
                if time.time() - t0 > 600.0:
                    print(f"export 시간 초과 (status={s})", file=sys.stderr)
                    return 1
                time.sleep(EXPORT_POLL_S)

            meta = st.get("result_metadata") or {}
            dl_url = (meta.get("download_url") or "").strip()
            if not dl_url:
                print("result_metadata.download_url 이 없다.", file=sys.stderr)
                return 1
            r_dl = c.get(dl_url)
            if r_dl.status_code >= 400:
                print(f"zip 내려받기 실패 ({r_dl.status_code})", file=sys.stderr)
                return 1
            data = r_dl.content
    except Exception as e:
        print(f"export 실패 ({type(e).__name__}): {e}", file=sys.stderr)
        return 1

    if not data.startswith(b"PK"):
        print("내려받은 바이트가 zip(PK) 형식이 아니다.", file=sys.stderr)
        return 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    print(f"[export-bank 완료] 은행={bank} · {len(data)} bytes -> {out_path}", flush=True)
    return 0


def cmd_recall(args: argparse.Namespace) -> int:
    from app import hindsight as app_hs

    run_path = Path(args.run)
    if not run_path.is_file():
        print(f"실행 기록 파일이 없다: {run_path}", file=sys.stderr)
        return 1
    run_doc = json.loads(run_path.read_text(encoding="utf-8"))
    query = str(run_doc.get("query") or "")
    try:
        res = app_hs.recall(query)
    except app_hs.RecallUnavailable as e:
        print(str(e), file=sys.stderr)
        return 1

    out_doc = {**res, "run": run_path.stem}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    hits = res.get("hits") or []
    photos_n = len({h["source_id"] for h in hits if h.get("kind") == "photo" and h.get("source_id")})
    timelines_n = len({h["source_id"] for h in hits if h.get("kind") == "timeline" and h.get("source_id")})
    docs_n = len({h["source_id"] for h in hits if h.get("kind") == "doc" and h.get("source_id")})
    doc_part = f" · 문서 {docs_n}건" if docs_n > 0 else ""
    sec = (res.get("elapsed_ms") or 0) / 1000
    print(
        f"[recall] 은행={res['bank']} · 꺼낸 기억 {len(hits)}개 · 사진 {photos_n}장 · 하루 타임라인 {timelines_n}개{doc_part} · {sec:.1f}초 -> {out_path}",
        flush=True,
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Hindsight 채널 적재·검색·채점 실행기")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ret = sub.add_parser("retain", help="사진·타임라인·문서 기억을 Hindsight 은행에 적재")
    p_ret.add_argument("--kinds", choices=["photo", "timeline", "document", "all"], default="photo", help="적재 종류(기본 photo)")
    p_ret.add_argument("--limit", type=int, default=0, help="앞에서부터 넣을 사진 수(0이면 전체)")
    p_ret.add_argument("--batch", type=int, default=10, help="배치당 사진 수(기본 10)")
    p_ret.add_argument("--verify-recall", action="store_true", help="적재 후 recall 동작 확인")
    p_ret.add_argument("--stage-cap-usd", type=float, default=19.5, help="이 단계 최대 허용 비용(기본 $19.5)")

    p_exp = sub.add_parser("export-bank", help="Hindsight 은행을 zip 백업으로 내보내기")
    p_exp.add_argument("--out", required=True, help="저장할 zip 경로")

    p_rec = sub.add_parser("recall", help="실행 기록의 질의로 기억을 한 번 꺼내 파일로 저장")
    p_rec.add_argument("--run", required=True, help="실행 기록 JSON 경로")
    p_rec.add_argument("--out", required=True, help="저장할 JSON 경로")

    p_ev = sub.add_parser("eval", help="정답 키 질의로 recall·reflect 채점 및 비교")
    p_ev.add_argument("targets", nargs="*", default=["all"], help="돌릴 질의 id 목록(기본 all)")
    p_ev.add_argument("--stage-cap-usd", type=float, default=19.5, help="이 단계 최대 허용 비용(기본 $19.5)")

    sub.add_parser("status", help="은행 상태 및 누적 토큰·비용 조회")
    sub.add_parser("reconcile", help="은행 LLM trace 기준으로 문서·비용 정합성 동기화")
    p_imp = sub.add_parser("import-bank", help="bali-trip.zip 백업을 Hindsight 은행으로 무비용 복원")
    p_imp.add_argument("--zip-file", default="", help="복원할 zip 경로(기본 var/bali/bali-trip.zip)")
    p_imp.add_argument("--target-bank", default="", help="대상 은행 이름(기본 AIDIC_HINDSIGHT_BANK)")

    args = ap.parse_args()
    if args.cmd == "retain":
        return cmd_retain(args)
    if args.cmd == "export-bank":
        return cmd_export_bank(args)
    if args.cmd == "recall":
        return cmd_recall(args)
    if args.cmd == "eval":
        return cmd_eval(args)
    if args.cmd == "reconcile":
        return cmd_reconcile(args)
    if args.cmd == "import-bank":
        return cmd_import_bank(args)
    return cmd_status(args)


if __name__ == "__main__":
    sys.exit(main())

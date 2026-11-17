"""Hindsight 기억 저장소 연동: 기억 꺼내기(recall)와 기억 되짚기(reflect), 되짚기 비용 장부."""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone

import httpx

from app import claude
from app.config import settings

RECALL_TIMEOUT_S = 180.0
STATS_TIMEOUT_S = 30.0
CONNECT_TIMEOUT_S = 10.0

REFLECT_CAP_USD = 25.0
REFLECT_RESERVE_USD = 1.5
REFLECT_TIMEOUT_S = 600
REFLECT_STATS_RETRY_SLEEP_S = 1.0

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

_ledger_lock = threading.Lock()


class RecallUnavailable(Exception):
    pass


class ReflectUnavailable(Exception):
    def __init__(self, message: str, usage: dict | None = None) -> None:
        super().__init__(message)
        self.usage = usage


def hindsight_ledger_path():
    return settings.var_dir / "hindsight_calls.jsonl"


def reflect_spent_usd() -> float:
    p = hindsight_ledger_path()
    if not p.is_file():
        return 0.0
    total = 0.0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            total += REFLECT_RESERVE_USD
            continue
        if not isinstance(obj, dict):
            total += REFLECT_RESERVE_USD
            continue
        c = obj.get("cost_usd")
        if c is None or isinstance(c, bool) or not isinstance(c, (int, float)):
            total += REFLECT_RESERVE_USD
        else:
            total += float(c)
    return total


def _append_reflect_ledger(usage: dict) -> None:
    row = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "purpose": "reflect_memory",
        **usage,
    }
    with _ledger_lock:
        p = hindsight_ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _cost_from_tokens(inp: int, out: int, cached: int, model: str) -> float:
    prices = claude.PRICES.get(model) or claude.PRICES["claude-opus-5-5"]
    non_cached = max(0, int(inp) - int(cached))
    cost = (non_cached * prices["in"] + int(cached) * prices["cr"] + int(out) * prices["out"]) / 1e6
    return round(cost, 6)


def _kind_and_source(doc_id: str | None) -> tuple[str | None, str, str | None]:
    if not isinstance(doc_id, str) or not doc_id:
        return None, "other", None
    for prefix, kind in (("photo:", "photo"), ("timeline:", "timeline"), ("doc:", "doc")):
        if doc_id.startswith(prefix):
            return doc_id, kind, doc_id[len(prefix):]
    return doc_id, "other", None


def _clean_recall_text(raw: str | None) -> str:
    head = str(raw or "").split(" | When:", 1)[0]
    return re.sub(r"\s+", " ", head).strip()[:240]


def recall(query: str) -> dict:
    url = settings.hindsight_url
    bank = settings.hindsight_bank
    if not url or not bank:
        raise RecallUnavailable("기억 저장소의 주소나 은행이 정해지지 않았어요")

    t0 = time.perf_counter()
    try:
        with httpx.Client(base_url=url, timeout=httpx.Timeout(RECALL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)) as c:
            r = c.post(
                f"/v1/default/banks/{bank}/memories/recall",
                json={"query": query, "budget": "mid", "max_tokens": 8192},
            )
    except Exception as e:
        raise RecallUnavailable(f"기억 저장소가 답하지 않았어요 ({type(e).__name__})") from e

    if r.status_code >= 400:
        raise RecallUnavailable(f"기억 저장소가 답하지 않았어요 (HTTP {r.status_code})")

    try:
        body = r.json() if r.content else {}
    except Exception as e:
        raise RecallUnavailable(f"기억 저장소가 답하지 않았어요 ({type(e).__name__})") from e

    if not isinstance(body, dict):
        raise RecallUnavailable("기억 저장소가 답하지 않았어요 (InvalidJSON)")

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    raw_results = body.get("results") if isinstance(body.get("results"), list) else []
    hits: list[dict] = []
    for rank, item in enumerate(raw_results, 1):
        if not isinstance(item, dict):
            continue
        doc_id, kind, source_id = _kind_and_source(item.get("document_id"))
        hits.append({
            "rank": rank,
            "document_id": doc_id,
            "kind": kind,
            "source_id": source_id,
            "type": item.get("type"),
            "occurred_start": item.get("occurred_start"),
            "text": _clean_recall_text(item.get("text")),
        })

    return {
        "bank": bank,
        "query": query,
        "budget": "mid",
        "max_tokens": 8192,
        "elapsed_ms": elapsed_ms,
        "hits": hits,
    }


def _read_reflect_stats(url: str, bank: str) -> dict:
    with httpx.Client(base_url=url, timeout=httpx.Timeout(STATS_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)) as c:
        r = c.get(
            f"/v1/default/banks/{bank}/llm-requests/stats",
            params={"call_type": "llm", "operation": "reflect", "period": "30d"},
        )
        if r.status_code == 404:
            return {
                "not_found": True,
                "calls": 0,
                "success": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "model": settings.model,
            }
        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code}")
        data = r.json() if r.content else {}
        if not isinstance(data, dict):
            raise RuntimeError("InvalidJSON")

        calls = success = inp = out = cached = 0
        for b in data.get("buckets") or []:
            if not isinstance(b, dict):
                continue
            calls += int(b.get("total") or 0)
            st = b.get("statuses") or {}
            if isinstance(st, dict):
                success += int(st.get("success") or 0)
            tok = b.get("tokens") or {}
            if isinstance(tok, dict):
                inp += int(tok.get("input") or 0)
                out += int(tok.get("output") or 0)
                cached += int(tok.get("cached") or 0)

        model = settings.model
        r2 = c.get(
            f"/v1/default/banks/{bank}/llm-requests",
            params={"call_type": "llm", "limit": 1},
        )
        if r2.status_code == 404:
            pass
        elif r2.status_code >= 400:
            raise RuntimeError(f"HTTP {r2.status_code}")
        else:
            d2 = r2.json() if r2.content else {}
            if isinstance(d2, dict):
                items = d2.get("items") or []
                if isinstance(items, list) and items and isinstance(items[0], dict):
                    model = items[0].get("model") or model

        return {
            "not_found": False,
            "calls": calls,
            "success": success,
            "input_tokens": inp,
            "output_tokens": out,
            "cached_tokens": cached,
            "model": model,
        }


def _stats_delta(before: dict, after: dict) -> dict:
    model = after.get("model") or before.get("model") or settings.model
    calls = max(0, int(after["calls"]) - int(before["calls"]))
    inp = max(0, int(after["input_tokens"]) - int(before["input_tokens"]))
    out = max(0, int(after["output_tokens"]) - int(before["output_tokens"]))
    cached = max(0, int(after["cached_tokens"]) - int(before["cached_tokens"]))
    return {
        "model": model,
        "calls": calls,
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cached,
        "cost_usd": _cost_from_tokens(inp, out, cached, model),
    }


def reflect(query: str, known: str) -> dict:
    t_func0 = time.perf_counter()
    url = settings.hindsight_url
    bank = settings.hindsight_bank
    if not url or not bank:
        raise ReflectUnavailable("기억 저장소의 주소나 은행이 정해지지 않았어요", usage=None)

    if reflect_spent_usd() + REFLECT_RESERVE_USD > REFLECT_CAP_USD:
        raise ReflectUnavailable("기억 되짚기 비용이 상한 25달러에 가까워 되짚지 않았어요", usage=None)

    try:
        before = _read_reflect_stats(url, bank)
    except RuntimeError as e:
        raise ReflectUnavailable(f"기억 저장소가 답하지 않았어요 ({e})", usage=None) from e
    except Exception as e:
        raise ReflectUnavailable(f"기억 저장소가 답하지 않았어요 ({type(e).__name__})", usage=None) from e

    q_lines = [query]
    if known:
        q_lines.append(f"이미 확인한 것: {known}")
    q_lines.append("조건에 맞는 사진의 정확한 사진 id 목록을 빠짐없이 적고, 어떤 기억·시각·장소를 근거로 골랐는지 함께 알려 줘.")
    full_query = "\n".join(q_lines)

    req_t0 = time.perf_counter()
    ok = False
    err_msg: str | None = None
    immediate_reject = False
    uncertain_stream = False
    photo_ids: list[str] = []
    reasoning = ""
    inner_calls = 0
    inner_queries: list[str] = []

    resp = None
    try:
        with httpx.Client(base_url=url, timeout=httpx.Timeout(float(REFLECT_TIMEOUT_S), connect=CONNECT_TIMEOUT_S)) as c:
            resp = c.post(
                f"/v1/default/banks/{bank}/reflect",
                json={
                    "query": full_query,
                    "budget": "mid",
                    "response_schema": REFLECT_SCHEMA,
                    "include": {"tool_calls": {}},
                },
            )
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        immediate_reject = True
        err_msg = f"기억 저장소가 답하지 않았어요 ({type(e).__name__})"
    except (httpx.TimeoutException, httpx.TransportError) as e:
        uncertain_stream = True
        err_msg = f"기억 저장소가 답하지 않았어요 ({type(e).__name__})"
    except Exception as e:
        err_msg = f"기억 저장소가 답하지 않았어요 ({type(e).__name__})"

    req_ms = int((time.perf_counter() - req_t0) * 1000)

    if resp is not None:
        if resp.status_code >= 400:
            if 400 <= resp.status_code < 500 and resp.status_code not in (408, 429) and req_ms < 10_000:
                immediate_reject = True
            err_msg = f"기억 저장소가 답하지 않았어요 (HTTP {resp.status_code})"
        else:
            try:
                rf = resp.json() if resp.content else None
                so = rf.get("structured_output") if isinstance(rf, dict) else None
                if (
                    not isinstance(rf, dict)
                    or not isinstance(so, dict)
                    or not so
                    or not isinstance(so.get("photo_ids"), list)
                ):
                    err_msg = "기억 저장소가 답을 정리하지 못했어요"
                else:
                    seen_ids: set[str] = set()
                    for raw_pid in so["photo_ids"]:
                        if not isinstance(raw_pid, str):
                            continue
                        cleaned = re.sub(r"^photo:", "", raw_pid.strip()).strip()
                        if cleaned and cleaned not in seen_ids:
                            seen_ids.add(cleaned)
                            photo_ids.append(cleaned)
                    raw_r = so.get("reasoning")
                    if not isinstance(raw_r, str) or not raw_r:
                        raw_t = rf.get("text")
                        raw_r = raw_t if isinstance(raw_t, str) else ""
                    reasoning = raw_r.strip()[:600]

                    tr = rf.get("trace")
                    tcs = tr.get("tool_calls") if isinstance(tr, dict) else None
                    if isinstance(tcs, list):
                        inner_calls = len(tcs)
                        for tc in tcs:
                            if not isinstance(tc, dict):
                                continue
                            inp_obj = tc.get("input") if isinstance(tc.get("input"), dict) else (
                                tc.get("arguments") if isinstance(tc.get("arguments"), dict) else None
                            )
                            if inp_obj and isinstance(inp_obj.get("query"), str) and inp_obj["query"]:
                                inner_queries.append(inp_obj["query"][:120])
                                if len(inner_queries) >= 8:
                                    break
                    else:
                        inner_calls = 0
                        inner_queries = []
                    ok = True
            except Exception:
                ok = False
                err_msg = "기억 저장소가 답을 정리하지 못했어요"

    model_name = before.get("model") or settings.model
    calls_n = 0
    inp_tok = 0
    out_tok = 0
    cached_tok = 0
    cost_usd: float | None = None
    cost_known = False

    if uncertain_stream:
        try:
            _read_reflect_stats(url, bank)
        except Exception:
            pass
    elif not before.get("not_found"):
        after = None
        try:
            after = _read_reflect_stats(url, bank)
        except Exception:
            after = None

        if after is not None and not after.get("not_found"):
            delta = _stats_delta(before, after)
            if delta["calls"] == 0 and not immediate_reject:
                for _ in range(3):
                    time.sleep(REFLECT_STATS_RETRY_SLEEP_S)
                    try:
                        after_retry = _read_reflect_stats(url, bank)
                    except Exception:
                        after = None
                        break
                    if after_retry.get("not_found"):
                        after = None
                        break
                    after = after_retry
                    delta = _stats_delta(before, after)
                    if delta["calls"] > 0:
                        break

            if after is not None and not after.get("not_found"):
                model_name = delta["model"]
                if delta["calls"] > 0:
                    calls_n = delta["calls"]
                    inp_tok = delta["input_tokens"]
                    out_tok = delta["output_tokens"]
                    cached_tok = delta["cache_read_input_tokens"]
                    cost_usd = delta["cost_usd"]
                    cost_known = True
                elif immediate_reject:
                    calls_n = 0
                    inp_tok = 0
                    out_tok = 0
                    cached_tok = 0
                    cost_usd = 0.0
                    cost_known = True

    usage = {
        "bank": bank,
        "model": model_name,
        "calls": calls_n,
        "input_tokens": inp_tok,
        "output_tokens": out_tok,
        "cache_read_input_tokens": cached_tok,
        "cost_usd": cost_usd,
        "cost_known": cost_known,
        "ms": req_ms,
        "ok": ok,
    }
    _append_reflect_ledger(usage)

    if not ok:
        raise ReflectUnavailable(err_msg or "기억 저장소가 답을 정리하지 못했어요", usage=usage)

    elapsed_ms = int((time.perf_counter() - t_func0) * 1000)
    return {
        "bank": bank,
        "query": query,
        "known": known,
        "budget": "mid",
        "elapsed_ms": elapsed_ms,
        "photo_ids": photo_ids,
        "reasoning": reasoning,
        "inner_calls": inner_calls,
        "inner_queries": inner_queries,
        "usage": usage,
    }

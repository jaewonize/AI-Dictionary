"""Claude 호출은 모두 여기를 거친다: 클라이언트, 가격표, 비용 장부, 누적 비용 한도, 429 연속 차단.

규칙(docs/ARCHITECTURE.md 'Claude 호출 규칙'):
- 모델은 settings.model(claude-opus-5-5) 하나. effort 는 output_config.effort 에 꼭 적는다. thinking 칸은 보내지 않는다.
- client.beta.messages(create, parse) + betas server-side-fallback-2026-07-01 + fallbacks "default".
- temperature, top_p, top_k, 강제 tool_choice, 답 미리 채우기는 보내지 않는다.
- 스트리밍 없이, max_tokens 16000.
- 호출마다 토큰과 비용을 var/claude_calls.jsonl 장부에 남긴다. 장부 합계가 한도(AIDIC_COST_CAP_USD)를 넘으면
  새 호출을 막는다. RateLimitError(429)가 연달아 MAX_CONSECUTIVE_429 번 나면 이 프로세스의 호출을 모두 멈춘다.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import anthropic

from app.config import settings

FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16000
TIMEOUT_S = 300.0       # 요청 하나의 시간 제한(시연용). SDK 기본 10분은 너무 길다
MAX_RETRIES = 2         # SDK 가 408, 409, 429, 5xx, 연결 오류, 시간 초과를 다시 보내는 횟수
MAX_CONSECUTIVE_429 = 5

# 100만 토큰당 달러. 캐시 쓰기는 5분짜리와 1시간짜리 값이 다르다.
PRICES = {
    "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cw5m": 5.0, "cw1h": 8.0, "cr": 0.2},
    "claude-opus-5": {"in": 5.0, "out": 25.0, "cw5m": 6.25, "cw1h": 10.0, "cr": 0.5},
    "claude-opus-4-8": {"in": 5.0, "out": 25.0, "cw5m": 6.25, "cw1h": 10.0, "cr": 0.5},
}
WEB_SEARCH_PER_1000 = 10.0


class CostCapExceeded(RuntimeError):
    pass


class RateLimitTripped(RuntimeError):
    pass


_lock = threading.Lock()
_state = {"spent": None, "consecutive_429": 0, "tripped": False}
_client: anthropic.Anthropic | None = None


def ledger_path():
    return settings.var_dir / "claude_calls.jsonl"


def spent_usd() -> float:
    with _lock:
        if _state["spent"] is None:
            total = 0.0
            p = ledger_path()
            if p.is_file():
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        try:
                            total += float(json.loads(line).get("cost_usd") or 0)
                        except ValueError:
                            pass
            _state["spent"] = total
        return _state["spent"]


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
            os.environ.pop(k, None)  # 셸에 남은 값은 쓰지 않는다
        key = settings.anthropic_key()
        if not key:
            raise SystemExit("설정 파일에 ANTHROPIC_API_KEY 가 없다. workspace/ 의 설정 파일에 키를 넣는다(env.example 참고).")
        _client = anthropic.Anthropic(api_key=key, base_url="https://api.anthropic.com",
                                      max_retries=MAX_RETRIES, timeout=TIMEOUT_S)
    return _client


def _price(model: str) -> dict:
    return PRICES.get(model) or PRICES["claude-opus-5-5"]


def _g(o, k, default=0):
    v = getattr(o, k, None) if not isinstance(o, dict) else o.get(k)
    return default if v is None else v


def usage_record(resp) -> dict:
    """응답의 usage 로 토큰과 비용을 계산한다. 대체 모델이 답했으면 시도별 토큰과 그 모델 가격을 쓴다."""
    u = resp.usage
    cc = _g(u, "cache_creation", None)
    rec = {
        "served_model": resp.model,
        "input_tokens": _g(u, "input_tokens"),
        "output_tokens": _g(u, "output_tokens"),
        "cache_read_input_tokens": _g(u, "cache_read_input_tokens"),
        "cache_write_5m": _g(cc, "ephemeral_5m_input_tokens") if cc is not None else 0,
        "cache_write_1h": _g(cc, "ephemeral_1h_input_tokens") if cc is not None else 0,
        "web_search_requests": _g(_g(u, "server_tool_use", None) or {}, "web_search_requests"),
        "fallback": False,
    }
    if not rec["cache_write_5m"] and not rec["cache_write_1h"]:
        rec["cache_write_5m"] = _g(u, "cache_creation_input_tokens")
    iterations = _g(u, "iterations", None) or []
    attempts = []
    for it in iterations:
        t = _g(it, "type", "")
        if t == "fallback_message":
            rec["fallback"] = True
        attempts.append({
            "type": t, "model": _g(it, "model", None) or resp.model,
            "input_tokens": _g(it, "input_tokens"), "output_tokens": _g(it, "output_tokens"),
            "cache_read_input_tokens": _g(it, "cache_read_input_tokens"),
            "cache_write": _g(it, "cache_creation_input_tokens"),
        })
    if attempts and rec["fallback"]:
        cost = 0.0
        for a in attempts:
            p = _price(a["model"])
            cost += (a["input_tokens"] * p["in"] + a["output_tokens"] * p["out"] + a["cache_read_input_tokens"] * p["cr"]
                     + a["cache_write"] * p["cw5m"]) / 1e6
        rec["attempts"] = attempts
    else:
        p = _price(resp.model)
        cost = (rec["input_tokens"] * p["in"] + rec["output_tokens"] * p["out"] + rec["cache_read_input_tokens"] * p["cr"]
                + rec["cache_write_5m"] * p["cw5m"] + rec["cache_write_1h"] * p["cw1h"]) / 1e6
    cost += rec["web_search_requests"] * WEB_SEARCH_PER_1000 / 1000
    rec["cost_usd"] = round(cost, 6)
    return rec


@dataclass
class Call:
    resp: object
    rec: dict

    @property
    def parsed(self):
        return getattr(self.resp, "parsed_output", None)

    @property
    def stop(self) -> str:
        return self.resp.stop_reason


def _before() -> None:
    if _state["tripped"]:
        raise RateLimitTripped(f"429가 연달아 {MAX_CONSECUTIVE_429}번 나서 호출을 멈췄다. 잠시 뒤 다시 돌린다")
    if spent_usd() >= settings.cost_cap_usd:
        raise CostCapExceeded(f"누적 비용 ${spent_usd():.2f} 이 한도 ${settings.cost_cap_usd:.0f} 에 닿아 호출을 멈췄다")


def _after(rec: dict) -> None:
    with _lock:
        _state["consecutive_429"] = 0
        _state["spent"] = (_state["spent"] or 0.0) + rec["cost_usd"]
        p = ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def call(purpose: str, effort: str, messages: list, *, system=None, tools=None, output_format=None,
         output_schema: dict | None = None, cache_control: dict | None = None, extra: dict | None = None) -> Call:
    """Claude 한 번 부르기. output_format(Pydantic 클래스)이면 parse, 아니면 create."""
    _before()
    kw = dict(model=settings.model, max_tokens=MAX_TOKENS, messages=messages,
              betas=[FALLBACK_BETA], fallbacks="default")
    oc = {"effort": effort}
    if output_schema is not None:
        oc["format"] = {"type": "json_schema", "schema": output_schema}
    kw["output_config"] = oc
    if system is not None:
        kw["system"] = system
    if tools:
        kw["tools"] = tools
    if cache_control is not None:
        kw["cache_control"] = cache_control
    if extra:
        kw.update(extra)
    t0 = time.perf_counter()
    try:
        if output_format is not None:
            resp = client().beta.messages.parse(output_format=output_format, **kw)
        else:
            resp = client().beta.messages.create(**kw)
    except anthropic.RateLimitError:
        with _lock:
            _state["consecutive_429"] += 1
            if _state["consecutive_429"] >= MAX_CONSECUTIVE_429:
                _state["tripped"] = True
        raise
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "purpose": purpose,
           "model": settings.model, "effort": effort, "ms": int((time.perf_counter() - t0) * 1000),
           "stop_reason": resp.stop_reason, **usage_record(resp)}
    if resp.stop_reason == "refusal":
        sd = getattr(resp, "stop_details", None)
        rec["refusal_category"] = getattr(sd, "category", None) if sd else None
    _after(rec)
    return Call(resp, rec)


def text_of(resp) -> str:
    return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()

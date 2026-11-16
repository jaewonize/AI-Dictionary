"""에이전트 루프(docs/ARCHITECTURE.md '질의 한 번이 도는 길', 'Claude 호출 규칙', '카드 만드는 규칙').

서버(app/main.py)와 시험 실행기(eval/run.py)가 똑같이 이 루프를 쓴다.
"""

from __future__ import annotations

import itertools
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable

_run_seq = itertools.count(1)

from app import claude
from app import index as idx_mod
from app.config import settings
from app.events import CARD_NO, CARD_TITLES, EventLog, empty_evidence
from app.tools import (
    AskUserIn,
    ToolContext,
    run_find_candidates,
    run_judge_photos,
    run_plan_query,
    run_search_web,
    run_show_answer,
    tool_specs,
)
from pipeline.common import prompt

LOOP_PROMPT = "loop-v1"
MAX_LOOP_CALLS = 16

TOOL_RUNNERS = {
    "plan_query": run_plan_query,
    "search_web": run_search_web,
    "find_candidates": run_find_candidates,
    "judge_photos": run_judge_photos,
    "show_answer": run_show_answer,
}


@dataclass
class RunState:
    run_id: str
    query: str
    today: date
    started_at: str
    t0: float
    system: str
    tools: list[dict]
    event_log: EventLog
    ctx: ToolContext
    test: str | None = None
    messages: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    answers: list[str] = field(default_factory=list)
    pending_ask: dict | None = None
    shown_result: dict | None = None
    active_card: dict | None = None
    used_card_nos: set[int] = field(default_factory=set)
    next_extra_no: int = 10
    nudged: bool = False
    loop_calls: int = 0
    status: str = "running"
    error_message: str | None = None
    on_event: Callable[[dict], None] | None = None

    def emit(self, type_: str, **fields) -> dict:
        ev = self.event_log.make(type_, **fields)
        if self.on_event:
            self.on_event(ev)
        return ev

    def emit_progress(self, step: str) -> None:
        toks = sum((c.get("input_tokens") or 0) + (c.get("output_tokens") or 0) + (c.get("cache_read_input_tokens") or 0)
                   for c in self.calls)
        cost = round(sum(float(c.get("cost_usd") or 0) for c in self.calls), 6)
        self.emit("progress", step=step, elapsed_ms=int((time.perf_counter() - self.t0) * 1000),
                  calls=len(self.calls), tokens=toks, cost_usd=cost)


def _alloc_no(state: RunState, kind: str) -> int:
    base = CARD_NO.get(kind, 10)
    if base not in state.used_card_nos:
        state.used_card_nos.add(base)
        return base
    state.next_extra_no += 1
    return state.next_extra_no


def _merge_evidence(dst: dict, src: dict | None) -> None:
    if not src:
        return
    for k in ("date", "from_count", "to_count", "cutoff"):
        if src.get(k) is not None:
            dst[k] = src[k]
    if src.get("photo_ids"):
        dst["photo_ids"] = list(dict.fromkeys([*(dst.get("photo_ids") or []), *src["photo_ids"]]))[:8]
    if src.get("read_text"):
        dst["read_text"] = list(dict.fromkeys([*(dst.get("read_text") or []), *src["read_text"]]))[:6]


def _handle_card_meta(state: RunState, meta: dict) -> None:
    if "result_event" in meta:
        state.active_card = None
        re = meta["result_event"]
        state.shown_result = re
        state.emit(
            "result",
            no=_alloc_no(state, "result"),
            title=CARD_TITLES["result"].format(n=len(re["items"])),
            sentence=re["sentence"],
            items=re["items"],
            related_title=CARD_TITLES["related"],
            related=re["related"],
        )
        return

    card = meta.get("card")
    if not card or card == "none":
        return

    if card in ("plan", "web"):
        state.active_card = None
        state.emit(
            "card",
            no=_alloc_no(state, card),
            kind=card,
            title=CARD_TITLES[card],
            sentence=meta.get("sentence", ""),
            evidence=None,
            cached=bool(meta.get("cached", False)),
            data=meta.get("data"),
        )
        return

    if card in ("visit_day", "picked"):
        if state.active_card and state.active_card["kind"] == card:
            cur = state.active_card
            _merge_evidence(cur["evidence"], meta.get("evidence"))
            cur["sentence"] = meta.get("sentence") or cur["sentence"]
        else:
            ev = empty_evidence()
            _merge_evidence(ev, meta.get("evidence"))
            cur = {"kind": card, "no": _alloc_no(state, card), "evidence": ev,
                   "sentence": meta.get("sentence", "")}
            state.active_card = cur
        state.emit(
            "card",
            no=cur["no"],
            kind=card,
            title=CARD_TITLES[card],
            sentence=cur["sentence"],
            evidence=dict(cur["evidence"]),
            cached=False,
            data=None,
        )


def new_run(query: str, *, today: date | None = None, test: str | None = None,
            on_event: Callable[[dict], None] | None = None) -> RunState:
    now = datetime.now(timezone.utc)
    prefix = f"{test}_" if test else ""
    run_id = f"{prefix}{now.strftime('%Y%m%d_%H%M%S')}_{next(_run_seq):03d}"
    td = today or datetime.now().date()
    state = RunState(
        run_id=run_id,
        query=query,
        today=td,
        started_at=now.isoformat(timespec="seconds"),
        t0=time.perf_counter(),
        system=prompt(LOOP_PROMPT),
        tools=tool_specs(),
        event_log=EventLog(run_id=run_id),
        ctx=ToolContext(today=td),
        test=test,
        on_event=on_event,
    )
    state.ctx.on_call = lambda rec: state.calls.append(rec)
    state.ctx.on_progress = state.emit_progress
    state.messages.append({"role": "user", "content": f"오늘 날짜: {td.isoformat()}\n질문: {query}"})
    return state


def _fail(state: RunState, msg: str) -> RunState:
    state.status = "failed"
    state.error_message = msg
    state.emit("error", message=msg)
    state.emit("done", status="failed")
    save_run(state)
    return state


def _step_until_pause_or_done(state: RunState) -> RunState:
    if not idx_mod.load():
        return _fail(state, "찾아보기 파일이 아직 준비되지 않았어요.")

    while True:
        if state.loop_calls >= MAX_LOOP_CALLS:
            if state.shown_result is not None:
                state.status = "answered"
                state.emit("done", status="answered")
                save_run(state)
                return state
            return _fail(state, f"단계가 너무 길어져 멈췄어요({MAX_LOOP_CALLS}회 초과).")

        state.emit_progress("질문을 살펴보고 다음 단계를 고르는 중이에요")
        state.loop_calls += 1
        try:
            c = claude.call(
                "loop",
                "medium",
                state.messages,
                system=state.system,
                tools=state.tools,
                cache_control={"type": "ephemeral"},
            )
            state.calls.append(c.rec)
        except Exception as e:
            return _fail(state, f"AI 모델 호출에 실패했어요({type(e).__name__}: {str(e)[:200]}).")

        # 생각 블록을 포함해 응답 content 그대로 대화에 남긴다.
        state.messages.append({"role": "assistant", "content": c.resp.content})

        stop = c.stop
        if stop in ("refusal", "max_tokens"):
            return _fail(state, f"AI 모델이 응답을 끝내지 못했어요({stop}).")

        if stop == "tool_use":
            tool_uses = [b for b in c.resp.content if getattr(b, "type", None) == "tool_use"]
            tool_results: list[dict] = []
            ask_block = None
            ask_inp: AskUserIn | None = None

            for b in tool_uses:
                name = b.name
                raw_in = b.input if isinstance(b.input, dict) else {}
                if name == "ask_user":
                    try:
                        ask_inp = AskUserIn.model_validate(raw_in)
                        if len(ask_inp.options) < 2:
                            raise ValueError("ask_user 선택지는 2개 이상이어야 합니다.")
                        ask_block = b
                    except Exception as e:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": b.id,
                            "is_error": True,
                            "content": f"입력 오류: {e}",
                        })
                    continue

                runner = TOOL_RUNNERS.get(name)
                if not runner:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "is_error": True,
                        "content": f"알 수 없는 도구입니다: {name}",
                    })
                    continue

                state.emit_progress(f"{name} 실행 중")
                try:
                    out, meta = runner(raw_in, state.ctx)
                    _handle_card_meta(state, meta)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "content": json.dumps(out, ensure_ascii=False),
                    })
                except (claude.CostCapExceeded, claude.RateLimitTripped) as e:
                    return _fail(state, str(e))
                except Exception as e:
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": b.id,
                        "is_error": True,
                        "content": f"도구 실행 오류: {e}",
                    })

            if ask_block is not None and ask_inp is not None:
                state.active_card = None
                opts = [o.model_dump() for o in ask_inp.options]
                state.pending_ask = {
                    "tool_use_id": ask_block.id,
                    "question": ask_inp.question,
                    "options": opts,
                    "sibling_results": tool_results,
                }
                state.status = "waiting"
                state.emit("ask", no=_alloc_no(state, "ask"), title=CARD_TITLES["ask"],
                           question=ask_inp.question, options=opts)
                state.emit("done", status="waiting")
                save_run(state)
                return state

            state.messages.append({"role": "user", "content": tool_results})
            continue

        if stop == "end_turn":
            if state.shown_result is not None:
                state.status = "answered"
                state.emit("done", status="answered")
                save_run(state)
                return state
            if not state.nudged:
                state.nudged = True
                state.messages.append({
                    "role": "user",
                    "content": "아직 show_answer나 ask_user를 부르지 않았습니다. 찾은 결과를 show_answer로 내거나 선택이 필요하면 ask_user를 불러 마무리해 주세요.",
                })
                continue
            return _fail(state, "AI 모델이 답 보이기 도구를 부르지 않고 차례를 마쳤어요.")

        return _fail(state, f"알 수 없는 종료 상태입니다: {stop}")


def start(query: str, *, today: date | None = None, test: str | None = None,
          on_event: Callable[[dict], None] | None = None) -> RunState:
    state = new_run(query, today=today, test=test, on_event=on_event)
    return _step_until_pause_or_done(state)


def resume(state: RunState, option_id: str) -> RunState:
    if state.status != "waiting" or not state.pending_ask:
        raise ValueError("되묻기 응답을 기다리는 실행이 아닙니다.")
    ask = state.pending_ask
    opt = next((o for o in ask["options"] if str(o["id"]) == str(option_id)), None)
    if opt is None:
        raise ValueError(f"없는 선택지 id 입니다: {option_id}")
    state.pending_ask = None
    state.status = "running"
    state.answers.append(opt["label"])
    state.emit("answer", option_id=opt["id"], label=opt["label"])
    ans_result = {
        "type": "tool_result",
        "tool_use_id": ask["tool_use_id"],
        "content": json.dumps({"chosen_id": opt["id"], "label": opt["label"], "detail": opt.get("detail", "")},
                              ensure_ascii=False),
    }
    state.messages.append({"role": "user", "content": [*ask["sibling_results"], ans_result]})
    return _step_until_pause_or_done(state)


def run_record(state: RunState) -> dict:
    total_cost = round(sum(float(c.get("cost_usd") or 0) for c in state.calls), 6)
    total_in = sum(int(c.get("input_tokens") or 0) for c in state.calls)
    total_out = sum(int(c.get("output_tokens") or 0) for c in state.calls)
    total_cr = sum(int(c.get("cache_read_input_tokens") or 0) for c in state.calls)
    return {
        "run_id": state.run_id,
        "test": state.test,
        "query": state.query,
        "started_at": state.started_at,
        "elapsed_ms": int((time.perf_counter() - state.t0) * 1000),
        "status": state.status,
        "error": state.error_message,
        "answers": state.answers,
        "result_ids": state.shown_result["ids"] if state.shown_result else [],
        "result_sentence": state.shown_result["sentence"] if state.shown_result else "",
        "calls": state.calls,
        "summary": {
            "calls": len(state.calls),
            "loop_calls": state.loop_calls,
            "input_tokens": total_in,
            "output_tokens": total_out,
            "cache_read_input_tokens": total_cr,
            "cost_usd": total_cost,
        },
        "events": state.event_log.events,
    }


def save_run(state: RunState) -> dict:
    rec = run_record(state)
    out = settings.runs_dir / f"{state.run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec

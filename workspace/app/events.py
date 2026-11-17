"""화면으로 보내는 SSE 이벤트. 서버, 시험 실행기, 실행 기록, 재생 페이지가 이 모양을 그대로 쓴다.

이벤트 하나는 'data: ' 로 시작하는 JSON 한 줄과 빈 줄이다. JSON에는 type, run_id, seq 와 아래 필드를 담는다.

card     no, kind, title, sentence, evidence, cached, data
         kind: plan(1번) web(2번) visit_day(4번) picked(5번)
         evidence: photo_ids, read_text, date, from_count, to_count, cutoff
         data: kind마다 다른 자세한 값(plan은 target/anchor/relation/known/unknown, web은 query/found/summary/events)
ask      no(3), title, question, options[{id, label, detail}]
answer   option_id, label      되묻기에서 고른 답(화면과 재생 페이지가 사용자 말풍선으로 그린다)
result   no(6), title, sentence, items[{id, badge, time_label}], related_title, related[{label, items[...]}]
memory   n, query, bank, elapsed_ms, count, hits[{rank, document_id, kind, source_id, text}], error      기억 꺼내기 도구가 끝날 때(화면은 그리지 않아도 된다)
reflect  n, query, known, bank, elapsed_ms, count, photo_ids, left_out, reasoning, inner_calls, inner_queries, usage{bank, model, calls, input_tokens, output_tokens, cache_read_input_tokens, cost_usd, cost_known, ms, ok}, error      기억 되짚기 도구가 끝날 때(화면은 그리지 않아도 된다)
progress step, elapsed_ms, calls, tokens, cost_usd
error    message
done     status(answered, waiting, failed)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

CARD_TITLES = {
    "plan": "찾을 내용을 정리했어요",
    "web": "웹에서 찾아봤어요",
    "ask": "어느 쪽인지 알려주세요",
    "visit_day": "다녀온 날을 찾았어요",
    "picked": "조건에 맞는 사진만 골랐어요",
    "result": "{n}장 찾았어요",
    "related": "연관된 사진을 보실래요?",
}
CARD_NO = {"plan": 1, "web": 2, "ask": 3, "visit_day": 4, "picked": 5, "result": 6, "related": 7}


def empty_evidence() -> dict:
    return {"photo_ids": [], "read_text": [], "date": None, "from_count": None, "to_count": None, "cutoff": None}


@dataclass
class EventLog:
    run_id: str
    seq: int = 0
    events: list[dict] = field(default_factory=list)

    def make(self, type_: str, **fields) -> dict:
        self.seq += 1
        ev = {"type": type_, "run_id": self.run_id, "seq": self.seq, **fields}
        self.events.append(ev)
        return ev


def sse(ev: dict) -> str:
    return "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"

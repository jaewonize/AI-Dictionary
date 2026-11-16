"""도구·루프·채점기 단위 시험(Claude API 호출 없음)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app import index as idx_mod
from app import loop
from app.tools import ToolContext, _catalog_block, run_find_candidates, run_plan_query, run_show_answer, tool_specs
from eval.run import grade_one


def _walk_objects(schema: dict) -> list[dict]:
    found = []
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            found.append(schema)
        for v in schema.values():
            found.extend(_walk_objects(v))
    elif isinstance(schema, list):
        for x in schema:
            found.extend(_walk_objects(x))
    return found


def test_tool_specs_strict_schema():
    specs = tool_specs()
    assert [s["name"] for s in specs] == [
        "plan_query", "search_web", "ask_user", "find_candidates", "judge_photos", "show_answer"
    ]
    for s in specs:
        assert s["strict"] is True
        raw_json = json.dumps(s["input_schema"], ensure_ascii=False)
        assert "{default:" not in raw_json
        objs = _walk_objects(s["input_schema"])
        assert objs
        for o in objs:
            assert o.get("additionalProperties") is False
            props = set((o.get("properties") or {}).keys())
            reqs = set(o.get("required") or [])
            assert reqs == props, f"{s['name']} object missing required keys: {props - reqs}"
            for forbidden in ("minimum", "maximum", "maxItems", "minLength"):
                assert forbidden not in o


def test_find_candidates_time_area_and_no_gps_leak(monkeypatch):
    fake_rows = {
        "p1.jpg": {
            "id": "p1.jpg", "kind": "photo", "source": "gallery", "badge": "갤러리",
            "time": "2025-05-01T10:00:00+08:00", "time_basis": "taken",
            "lat": -12.3456, "lon": 123.4567,
            "area_ko": "갑동네", "area_en": "Gap", "near_ko": "푸른 해변", "near_en": "Blue Beach",
            "place_group": "G01", "visit": "G01-v1",
            "place": "카페", "photo_type": "scene", "scene": "아침 커피 잔이 놓여 있다.", "text_in_photo": "MORNING",
            "received_at": "2025-05-01T12:00:00+08:00", "saved_at": None, "downloaded_at": None, "origin": None,
        },
        "p2.jpg": {
            "id": "p2.jpg", "kind": "photo", "source": "gallery", "badge": "갤러리",
            "time": "2025-05-01T11:30:00+08:00", "time_basis": "taken",
            "lat": -12.3460, "lon": 123.4570,
            "area_ko": "갑동네", "area_en": "Gap", "near_ko": "", "near_en": "",
            "place_group": "G02", "visit": "G02-v1",
            "place": "미술관", "photo_type": "scene", "scene": "전시실 벽에 그림이 걸려 있다.", "text_in_photo": "",
            "received_at": None, "saved_at": None, "downloaded_at": None, "origin": None,
        },
        "p3.jpg": {
            "id": "p3.jpg", "kind": "photo", "source": "kakao_photo", "badge": "카카오톡",
            "time": "2025-05-01T13:15:00+08:00", "time_basis": "taken",
            "lat": -12.3500, "lon": 123.4600,
            "area_ko": "을동네", "area_en": "Eul", "near_ko": "", "near_en": "",
            "place_group": "G03", "visit": "G03-v1",
            "place": "식당", "photo_type": "scene", "scene": "점심 국수 그릇이 보인다.", "text_in_photo": "NOODLE HOUSE",
            "received_at": "2025-05-01T18:00:00+08:00", "saved_at": None, "downloaded_at": None, "origin": None,
        },
    }
    monkeypatch.setattr(idx_mod, "load", lambda: fake_rows)
    ctx = ToolContext(today=date(2025, 5, 10))

    # 경계 시각(11:30:00)은 제외하고 그 뒤만 나와야 한다.
    out, meta = run_find_candidates({
        "date_from": "2025-05-01",
        "date_to": "2025-05-01",
        "after": "2025-05-01T11:30:00+08:00",
        "card": "picked",
        "sentence": "기준 시각 뒤의 사진을 찾았어요.",
    }, ctx)
    assert [c["id"] for c in out["candidates"]] == ["p3.jpg"]
    assert out["day_photo_ids"] == ["p1.jpg", "p2.jpg", "p3.jpg"]
    assert meta["evidence"]["cutoff"] == "2025-05-01T11:30:00+08:00"

    # 좌표와 수신·저장 시각이 후보 결과나 설명 블록에 새지 않는지 확인
    dumped = json.dumps(out, ensure_ascii=False)
    for secret in ("-12.34", "123.45", "-12.35", "123.46", "received_at", "saved_at", "18:00:00"):
        assert secret not in dumped
    cat_blk = _catalog_block(fake_rows)
    for secret in ("-12.34", "123.45", "18:00:00"):
        assert secret not in cat_blk

    # 지명(띄어쓰기 무시) 필터 확인
    out2, _ = run_find_candidates({
        "area": "푸른해변",
        "card": "picked",
        "sentence": "해변 부근 사진을 찾았어요.",
    }, ctx)
    assert [c["id"] for c in out2["candidates"]] == ["p1.jpg"]

    # show_answer: 앞선 결과에 없던 id 거절 및 시간순 정렬 확인
    with pytest.raises(ValueError):
        run_show_answer({"sentence": "답", "ids": ["unknown.jpg"], "related": []}, ctx)
    ans_out, _ = run_show_answer({"sentence": "2장 찾았어요", "ids": ["p3.jpg", "p1.jpg"], "related": []}, ctx)
    assert ans_out["ids"] == ["p1.jpg", "p3.jpg"]


def test_card_merge_and_grading(monkeypatch):
    fake_rows = {
        "a.jpg": {"id": "a.jpg", "kind": "photo", "time": "2025-05-01T10:00:00+08:00"},
        "b.jpg": {"id": "b.jpg", "kind": "photo", "time": "2025-05-01T11:00:00+08:00"},
    }
    monkeypatch.setattr(idx_mod, "load", lambda: fake_rows)
    st = loop.new_run("질문", today=date(2025, 5, 10), test="T1")
    _, m_plan = run_plan_query({
        "target": "식당 사진", "anchor": "산책", "relation": "after",
        "known": ["산책 뒤"], "unknown": ["산책 시각"], "sentence": "정리했어요"
    }, st.ctx)
    loop._handle_card_meta(st, m_plan)
    loop._handle_card_meta(st, {
        "card": "picked", "sentence": "후보 추림",
        "evidence": {"date": "2025-05-01", "from_count": 10, "cutoff": "2025-05-01T10:30:00+08:00"},
    })
    loop._handle_card_meta(st, {
        "card": "picked", "sentence": "사진 확인 완료",
        "evidence": {"to_count": 1, "photo_ids": ["b.jpg"], "read_text": ["MENU"]},
    })
    picked_cards = [e for e in st.event_log.events if e["type"] == "card" and e["kind"] == "picked"]
    assert len(picked_cards) == 2
    assert picked_cards[0]["no"] == picked_cards[1]["no"] == 5
    assert picked_cards[1]["sentence"] == "사진 확인 완료"
    assert picked_cards[1]["evidence"]["from_count"] == 10
    assert picked_cards[1]["evidence"]["to_count"] == 1
    assert picked_cards[1]["evidence"]["cutoff"] == "2025-05-01T10:30:00+08:00"

    st.status = "answered"
    st.shown_result = {"ids": ["b.jpg"], "sentence": "1장 찾았어요", "items": [], "related": []}
    rec = loop.run_record(st)
    g = grade_one({"id": "T1", "kind": "sequence", "query": "질문", "answer_ids": ["b.jpg"], "trap_ids": ["a.jpg"]}, rec)
    assert g["passed"] is True
    assert g["exact_match"] is True
    assert g["plan"]["relation"] == "after"


def test_hindsight_memory_item_and_secret_guard():
    from eval.hindsight import _cost_from_hs_tokens, assert_no_secret_leak, make_photo_memory_item

    row = {
        "id": "1700000000000.jpg",
        "kind": "photo",
        "source": "kakao_photo",
        "badge": "카카오톡",
        "time": "2025-05-01T14:22:10+08:00",
        "time_basis": "taken",
        "lat": -12.3456,
        "lon": 123.4567,
        "area_ko": "갑동네",
        "area_en": "Gap",
        "near_ko": "푸른 해변",
        "near_en": "Blue Beach",
        "place_group": "G01",
        "place": "해변 카페",
        "scene": "나무 탁자 위에 음료 잔이 놓여 있다.",
        "text_in_photo": "OCEAN CAFE",
        "received_at": "2025-05-03T09:11:00+08:00",
    }
    item = make_photo_memory_item(row)
    assert item["document_id"] == "photo:1700000000000.jpg"
    assert item["timestamp"] == "2025-05-01T14:22:10+08:00"
    assert "갑동네 · 푸른 해변 부근 (Gap, Blue Beach)" in item["content"]
    assert "OCEAN CAFE" in item["content"]
    assert "2025-05-03" not in json.dumps(item, ensure_ascii=False)
    assert_no_secret_leak([item], [row])

    bad_item = {**item, "content": item["content"] + " (-12.3456, 123.4567)"}
    with pytest.raises(AssertionError):
        assert_no_secret_leak([bad_item], [row])

    # Hindsight 토큰 비용: input_tokens(10000) 중 cached_tokens(8000)이면 비캐시 2000만 기본 단가 적용
    cost = _cost_from_hs_tokens(10_000, 500, 8_000, "claude-opus-5-5")
    assert cost == round((2_000 * 4.0 + 8_000 * 0.2 + 500 * 20.0) / 1e6, 6)


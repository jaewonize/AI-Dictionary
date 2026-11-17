"""1부 새 코드 단위 시험(웹 검색, 기억 저장·백업·꺼내기·되짚기, 기억 도구, 루프 연결, 수용표, 시나리오 값)."""

from __future__ import annotations

import copy
import json
import re
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app import claude, hindsight, loop, websearch
from app import index as idx_mod
from app.config import WS, settings
from app.tools import ToolContext, run_search_web, run_show_answer
from app.tools import memory as memory_mod
from eval import accept as accept_mod
from eval import hindsight as eval_hs


def _disable_hindsight(monkeypatch) -> None:
    monkeypatch.setitem(settings.values, "AIDIC_HINDSIGHT_URL", "http://from-file:8888")
    monkeypatch.setitem(settings.values, "AIDIC_HINDSIGHT_BANK", "bank-from-file")
    monkeypatch.delenv("AIDIC_HINDSIGHT_URL", raising=False)
    monkeypatch.delenv("AIDIC_HINDSIGHT_BANK", raising=False)
    monkeypatch.delitem(settings.values, "AIDIC_HINDSIGHT_URL", raising=False)
    monkeypatch.delitem(settings.values, "AIDIC_HINDSIGHT_BANK", raising=False)


def _enable_hindsight(monkeypatch, bank: str = "test-bank", url: str = "http://127.0.0.1:8888") -> None:
    monkeypatch.setenv("AIDIC_HINDSIGHT_URL", url)
    monkeypatch.setenv("AIDIC_HINDSIGHT_BANK", bank)


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


# ---------------------------------------------------------------- 1. 웹 검색
def test_web_search_live_pause_fallback_and_cache(monkeypatch, tmp_path):
    cache_file = tmp_path / "web_cache.json"
    cache_file.write_text(
        json.dumps([
            {
                "aliases": ["가상작가", "가상 작가"],
                "summary": "캐시된 작가 소개입니다.",
                "events": [
                    {
                        "name": "초기전",
                        "venue": "가상미술관",
                        "start": "2024-03-01",
                        "end": "2024-05-31",
                        "url": "https://example.org/1",
                        "lat": 35.1234,
                        "lon": 129.5678,
                    },
                    {
                        "name": "미래전",
                        "venue": "미래전시관",
                        "start": "2026-08-01",
                        "end": "2026-09-30",
                        "url": "https://example.org/2",
                    },
                ],
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setitem(settings.__dict__, "data_dir", tmp_path)

    # cache 모드 확인
    monkeypatch.setenv("AIDIC_WEB_SEARCH", "cache")
    ctx_cache = ToolContext(today=date(2025, 6, 1))
    res_c, meta_c = run_search_web({"query": "가상 작가 전시", "sentence": "웹에서 찾았어요"}, ctx_cache)
    assert res_c["cached"] is True
    assert meta_c["cached"] is True
    assert [e["status"] for e in res_c["events"]] == ["past", "upcoming"]
    assert res_c["events"][0]["lat"] == 35.1234
    assert "lat" not in res_c["events"][1]

    # live 모드: pause_turn 후 2차 정리 호출, 같은 장소 이름에만 좌표 부착
    monkeypatch.setenv("AIDIC_WEB_SEARCH", "live")
    calls_made: list[tuple[str, str, bool]] = []

    def fake_call(purpose, effort, messages, *, system=None, tools=None, output_format=None, **kw):
        calls_made.append((purpose, effort, bool(tools)))
        if purpose == "search_web" and len(calls_made) == 1:
            resp = SimpleNamespace(
                stop_reason="pause_turn",
                content=[SimpleNamespace(type="text", text="중간 검색 글")],
            )
            return claude.Call(resp, {"purpose": "search_web", "web_search_requests": 1, "cost_usd": 0.01, "ms": 100})
        if purpose == "search_web":
            resp = SimpleNamespace(
                stop_reason="end_turn",
                content=[
                    SimpleNamespace(
                        type="web_search_tool_result",
                        content=[SimpleNamespace(type="web_search_result", title="공식 안내", url="https://example.org/a")],
                    ),
                    SimpleNamespace(type="text", text="최종 검색 요약"),
                ],
            )
            return claude.Call(resp, {"purpose": "search_web", "web_search_requests": 2, "cost_usd": 0.02, "ms": 120})
        assert purpose == "search_web_extract"
        assert not tools
        parsed = websearch.WebExtractOut(
            found=True,
            summary="실시간으로 찾은 한 줄 소개입니다.",
            events=[
                websearch.WebEventItem(
                    name="가을 회고전", venue="가상미술관", start="2024-09-01", end="2024-11-30", url="https://example.org/a"
                ),
                websearch.WebEventItem(
                    name="내년 특별전", venue="다른전시관", start="2026-02", end="2026-04", url="https://example.org/b"
                ),
            ],
        )
        resp = SimpleNamespace(stop_reason="end_turn", content=[], parsed_output=parsed)
        return claude.Call(resp, {"purpose": "search_web_extract", "web_search_requests": 0, "cost_usd": 0.005, "ms": 80})

    monkeypatch.setattr(claude, "call", fake_call)
    recorded: list[dict] = []
    progress_steps: list[str] = []
    ctx_live = ToolContext(today=date(2025, 6, 1), on_call=recorded.append, on_progress=progress_steps.append)
    res_l, meta_l = run_search_web({"query": "가상작가", "sentence": "찾아봤어요"}, ctx_live)

    assert [c[0] for c in calls_made] == ["search_web", "search_web", "search_web_extract"]
    assert calls_made[0][2] is True and calls_made[2][2] is False
    assert set(res_l.keys()) == set(res_c.keys())
    assert res_l["cached"] is False
    assert meta_l["cached"] is False
    assert [e["status"] for e in res_l["events"]] == ["past", "upcoming"]
    assert res_l["events"][0]["lat"] == 35.1234
    assert "lat" not in res_l["events"][1]
    assert len(recorded) == 3
    assert "웹을 검색하는 중이에요" in progress_steps
    assert "검색 결과를 정리하는 중이에요" in progress_steps

    # 검색 수 0 이면 웹 캐시로 대체(cached=True)
    def fake_zero_search(purpose, effort, messages, **kw):
        resp = SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="검색 안 함")])
        return claude.Call(resp, {"purpose": purpose, "web_search_requests": 0, "cost_usd": 0.001, "ms": 50})

    monkeypatch.setattr(claude, "call", fake_zero_search)
    res_fb, meta_fb = run_search_web({"query": "가상작가", "sentence": "찾아봤어요"}, ToolContext(today=date(2025, 6, 1)))
    assert res_fb["cached"] is True
    assert meta_fb["cached"] is True
    assert res_fb["summary"] == "캐시된 작가 소개입니다."
    assert websearch._match_venue_coord("가상미술관 별관", {"가상미술관": (35.1234, 128.5678)}) == (35.1234, 128.5678)
    assert websearch._match_venue_coord("없는장소", {"가상미술관": (35.1234, 128.5678)}) is None


# ---------------------------------------------------------------- 2. 백업 명령
def test_export_bank_command(monkeypatch, tmp_path):
    _enable_hindsight(monkeypatch, bank="exp-bank", url="http://hs.local")
    monkeypatch.setattr(eval_hs, "EXPORT_POLL_S", 0.0)
    calls: list[tuple[str, str]] = []
    poll_count = 0

    class FakeClientOK:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, path, **kw):
            calls.append(("POST", path))
            return httpx.Response(202, json={"operation_id": "op-1", "status": "pending"})

        def get(self, path, **kw):
            nonlocal poll_count
            calls.append(("GET", path))
            if "/operations/" in path:
                poll_count += 1
                if poll_count == 1:
                    return httpx.Response(200, json={"status": "processing"})
                return httpx.Response(
                    200,
                    json={"status": "completed", "result_metadata": {"download_url": "/v1/default/files/download/k1"}},
                )
            return httpx.Response(200, content=b"PK\x03\x04fake-zip-bytes")

    monkeypatch.setattr(eval_hs.httpx, "Client", FakeClientOK)
    out_zip = tmp_path / "out.zip"
    rc = eval_hs.cmd_export_bank(SimpleNamespace(out=str(out_zip)))
    assert rc == 0
    assert out_zip.read_bytes() == b"PK\x03\x04fake-zip-bytes"
    assert calls == [
        ("POST", "/v1/default/banks/exp-bank/transfer/export"),
        ("GET", "/v1/default/banks/exp-bank/operations/op-1"),
        ("GET", "/v1/default/banks/exp-bank/operations/op-1"),
        ("GET", "/v1/default/files/download/k1"),
    ]

    # 실패 상태면 파일을 쓰지 않고 0이 아닌 값 반환
    class FakeClientFail(FakeClientOK):
        def get(self, path, **kw):
            return httpx.Response(200, json={"status": "failed", "error_message": "백업 생성 실패"})

    monkeypatch.setattr(eval_hs.httpx, "Client", FakeClientFail)
    fail_zip = tmp_path / "fail.zip"
    assert eval_hs.cmd_export_bank(SimpleNamespace(out=str(fail_zip))) != 0
    assert not fail_zip.exists()

    # 받은 바이트가 PK 로 시작하지 않으면 쓰지 않고 실패
    class FakeClientNotZip(FakeClientOK):
        def get(self, path, **kw):
            if "/operations/" in path:
                return httpx.Response(
                    200, json={"status": "completed", "result_metadata": {"download_url": "/v1/default/files/download/k2"}}
                )
            return httpx.Response(200, content=b"HTML error page")

    monkeypatch.setattr(eval_hs.httpx, "Client", FakeClientNotZip)
    bad_zip = tmp_path / "bad.zip"
    assert eval_hs.cmd_export_bank(SimpleNamespace(out=str(bad_zip))) != 0
    assert not bad_zip.exists()


# ---------------------------------------------------------------- 3. 하루 타임라인
def test_timeline_items_and_default_retain_photo_only(monkeypatch, tmp_path):
    fake_rows = [
        {
            "id": "d1_b.jpg", "kind": "photo", "badge": "갤러리",
            "time": "2025-04-10T14:20:00+09:00", "time_basis": "taken",
            "lat": 37.1111, "lon": 127.2222,
            "area_ko": "푸른동", "near_ko": "숲공원",
            "place": "공원", "scene": "나무 벤치 옆에 산책로가 이어진다. 두 번째 문장이다.",
            "text_in_photo": "PARK   SIGN\nOPEN",
            "received_at": "2025-04-11T09:00:00+09:00",
        },
        {
            "id": "d1_a.jpg", "kind": "photo", "badge": "다운로드",
            "time": "2025-04-10T09:05:00+09:00", "time_basis": "filename",
            "lat": None, "lon": None,
            "area_ko": "", "near_ko": "",
            "place": "식당", "scene": "아침 수프 그릇이 놓여 있다.",
            "text_in_photo": "",
            "saved_at": "2025-04-12T10:00:00+09:00",
        },
        {
            "id": "d1_saved.jpg", "kind": "photo", "badge": "다운로드",
            "time": "2025-04-10T11:00:00+09:00", "time_basis": "saved",
            "lat": None, "lon": None,
            "area_ko": "푸른동", "near_ko": "",
            "place": "상점", "scene": "저장 시각만 있는 사진이다.",
            "text_in_photo": "",
        },
        {
            "id": "d2_a.jpg", "kind": "photo", "badge": "카카오톡",
            "time": "2025-04-12T18:30:00+09:00", "time_basis": "taken",
            "lat": 35.9999, "lon": 128.8888,
            "area_ko": "강변동", "near_ko": "",
            "place": "카페", "scene": "저녁 창가에 차가 놓여 있다.",
            "text_in_photo": "",
            "downloaded_at": "2025-04-13T08:00:00+09:00",
        },
    ]

    tl = eval_hs.make_timeline_memory_items(fake_rows)
    assert [it["document_id"] for it in tl] == ["timeline:2025-04-10", "timeline:2025-04-12"]
    first = tl[0]
    assert first["timestamp"] == "2025-04-10T09:05:00+09:00"
    assert first["metadata"] == {"kind": "timeline", "date": "2025-04-10"}
    assert first["tags"] == ["timeline"]
    assert first["context"] == "하루 동안 찍은 사진의 시간 순서 기록"
    lines = first["content"].splitlines()
    assert lines[0] == "[하루 타임라인] 2025년 4월 10일(목)에 찍은 사진 2장을 찍은 시각 순서로 적었다."
    # 지명이 빈 d1_a.jpg 는 지명 칸이 빠지고 시각 순서상 먼저 온다
    assert lines[1] == "- 09:05 · 식당 · 출처 다운로드 · 사진 id d1_a.jpg · 아침 수프 그릇이 놓여 있다."
    assert "푸른동 · 숲공원 부근" in lines[2]
    assert "사진 속 글자: PARK SIGN OPEN" in lines[2]
    assert "d1_saved.jpg" not in first["content"]
    eval_hs.assert_no_secret_leak(tl, fake_rows)
    dumped = json.dumps(tl, ensure_ascii=False)
    for secret in ("2025-04-11", "2025-04-13", "37.111", "127.222"):
        assert secret not in dumped

    # --kinds 를 주지 않은 retain 은 사진 기억만 보낸다
    _enable_hindsight(monkeypatch, bank="ret-bank", url="http://hs.local")
    monkeypatch.setitem(settings.__dict__, "var_dir", tmp_path)
    monkeypatch.setattr(eval_hs, "load_photo_rows", lambda: fake_rows)
    monkeypatch.setattr(eval_hs, "_server_info", lambda u: {"observations": False, "llm_provider": "anthropic", "llm_model": "claude-opus-5-5"})
    posted_batches: list[list[dict]] = []
    stored_docs: set[str] = set()

    def fake_hs_req(url, method, path, **kw):
        if method == "POST" and path.endswith("/memories"):
            items = kw["json"]["items"]
            posted_batches.append(items)
            stored_docs.update(it["document_id"] for it in items)
            return {"operation_ids": ["op1"]}
        if "/operations/" in path:
            return {"status": "completed"}
        if path.endswith("/documents"):
            return {"documents": [{"id": d} for d in sorted(stored_docs)]}
        if path.endswith("/stats") and "llm-requests" not in path:
            return {"total_documents": len(stored_docs), "total_nodes": 4}
        return {"buckets": [], "items": []}

    monkeypatch.setattr(eval_hs, "hs_request", fake_hs_req)
    rc = eval_hs.main_args if hasattr(eval_hs, "main_args") else eval_hs.cmd_retain(
        SimpleNamespace(limit=0, batch=10, verify_recall=False, stage_cap_usd=19.5)
    )
    assert rc == 0
    sent_ids = [it["document_id"] for batch in posted_batches for it in batch]
    assert all(did.startswith("photo:") for did in sent_ids)
    assert len(sent_ids) == 4


# ---------------------------------------------------------------- 4. 꺼내기와 되짚기 함수
def test_recall_and_reflect_functions_and_ledger(monkeypatch, tmp_path):
    monkeypatch.setitem(settings.__dict__, "var_dir", tmp_path)
    monkeypatch.setattr(hindsight, "REFLECT_STATS_RETRY_SLEEP_S", 0.0)

    claude_called = 0

    def forbid_claude(*a, **kw):
        nonlocal claude_called
        claude_called += 1
        raise AssertionError("claude.call 이 불리면 안 됩니다")

    monkeypatch.setattr(claude, "call", forbid_claude)

    # 주소나 은행이 비었을 때(settings.values 에 먼저 넣은 뒤 지움)
    _disable_hindsight(monkeypatch)
    with pytest.raises(hindsight.RecallUnavailable):
        hindsight.recall("질의")
    with pytest.raises(hindsight.ReflectUnavailable) as exc_empty:
        hindsight.reflect("질의", "")
    assert exc_empty.value.usage is None

    _enable_hindsight(monkeypatch, bank="mob-bank", url="http://hs.local")

    # recall 정상 동작 검사
    recall_reqs: list[tuple[str, str, dict]] = []
    long_fact = "가" * 300 + " | When: 2025-01-01 | Involving: user"

    class FakeRecallClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, path, *, json=None, **kw):
            recall_reqs.append(("POST", path, json))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"document_id": "photo:p1.jpg", "type": "world", "occurred_start": "2025-01-01T10:00:00Z", "text": long_fact, "entities": ["x"], "chunk_id": "c1"},
                        {"document_id": "timeline:2025-01-01", "type": "world", "occurred_start": "2025-01-01T00:00:00Z", "text": "하루   기록\n요약 | When: 2025-01-01", "metadata": {"k": 1}},
                        {"document_id": "doc:d1.pdf", "type": "world", "occurred_start": None, "text": "문서 내용"},
                        {"document_id": "misc:1", "type": "observation", "occurred_start": None, "text": "기타"},
                        {"type": "observation", "text": "문서 id 없음"},
                    ]
                },
            )

    monkeypatch.setattr(hindsight.httpx, "Client", FakeRecallClient)
    r_out = hindsight.recall("테스트 질문")
    assert len(recall_reqs) == 1
    assert recall_reqs[0] == ("POST", "/v1/default/banks/mob-bank/memories/recall", {"query": "테스트 질문", "budget": "mid", "max_tokens": 8192})
    hits = r_out["hits"]
    assert [h["rank"] for h in hits] == [1, 2, 3, 4, 5]
    assert [(h["kind"], h["source_id"]) for h in hits] == [
        ("photo", "p1.jpg"),
        ("timeline", "2025-01-01"),
        ("doc", "d1.pdf"),
        ("other", None),
        ("other", None),
    ]
    assert len(hits[0]["text"]) == 240
    assert "When:" not in hits[0]["text"]
    assert hits[1]["text"] == "하루 기록 요약"
    for h in hits:
        assert set(h.keys()) == {"rank", "document_id", "kind", "source_id", "type", "occurred_start", "text"}

    # eval.hindsight recall 명령 검사
    run_json = tmp_path / "T_run.json"
    run_json.write_text(json.dumps({"query": "테스트 질문"}, ensure_ascii=False), encoding="utf-8")
    recall_out = tmp_path / "sub" / "recall_out.json"
    assert eval_hs.cmd_recall(SimpleNamespace(run=str(run_json), out=str(recall_out))) == 0
    saved_rec = json.loads(recall_out.read_text(encoding="utf-8"))
    assert saved_rec["run"] == "T_run"
    assert len(saved_rec["hits"]) == 5

    # recall 오류들(연결 실패, 시간 초과, 500) 및 eval.hindsight recall 실패 시 미생성
    for err_kind in ("connect", "timeout", "500"):
        class FakeErrClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, path, **kw):
                if err_kind == "connect":
                    raise httpx.ConnectError("conn fail")
                if err_kind == "timeout":
                    raise httpx.ReadTimeout("timeout")
                return httpx.Response(500, text="secret internal body")

        monkeypatch.setattr(hindsight.httpx, "Client", FakeErrClient)
        with pytest.raises(hindsight.RecallUnavailable) as ei:
            hindsight.recall("질문")
        assert "secret internal body" not in str(ei.value)

    bad_recall_out = tmp_path / "bad_recall.json"
    assert eval_hs.cmd_recall(SimpleNamespace(run=str(run_json), out=str(bad_recall_out))) != 0
    assert not bad_recall_out.exists()

    # reflect 정상 동작(재시도로 집계 반영, known 있을 때와 빈 글일 때)
    reflect_http_log: list[tuple[str, str, object]] = []
    stats_seq = [
        # 첫 reflect 호출: 앞 집계(0) -> 뒤 첫 집계(0, 재시도 유도) -> 뒤 둘째 집계(3 calls)
        {"buckets": [{"total": 0, "statuses": {"success": 0}, "tokens": {"input": 0, "output": 0, "cached": 0}}]},
        {"buckets": [{"total": 0, "statuses": {"success": 0}, "tokens": {"input": 0, "output": 0, "cached": 0}}]},
        {"buckets": [{"total": 3, "statuses": {"success": 3}, "tokens": {"input": 10000, "output": 500, "cached": 8000}}]},
        # 둘째 reflect 호출(known 빈 글): 앞 집계(3) -> 뒤 집계(5)
        {"buckets": [{"total": 3, "statuses": {"success": 3}, "tokens": {"input": 10000, "output": 500, "cached": 8000}}]},
        {"buckets": [{"total": 5, "statuses": {"success": 5}, "tokens": {"input": 15000, "output": 800, "cached": 12000}}]},
    ]

    class FakeReflectClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, path, *, params=None, **kw):
            reflect_http_log.append(("GET", path, params))
            if path.endswith("/stats"):
                return httpx.Response(200, json=stats_seq.pop(0))
            return httpx.Response(200, json={"items": [{"model": "claude-opus-5-5"}]})

        def post(self, path, *, json=None, **kw):
            reflect_http_log.append(("POST", path, json))
            return httpx.Response(
                200,
                json={
                    "structured_output": {
                        "photo_ids": ["photo:a.jpg", "b.jpg", "photo:a.jpg", "  ", 123],
                        "reasoning": "근" * 700,
                    },
                    "trace": {
                        "tool_calls": [
                            {"input": {"query": "내부질의1"}},
                            {"arguments": {"query": "내부질의2"}},
                        ]
                    },
                },
            )

    monkeypatch.setattr(hindsight.httpx, "Client", FakeReflectClient)
    rf1 = hindsight.reflect("원래 질문", "장소 가, 2025년 봄")
    assert rf1["photo_ids"] == ["a.jpg", "b.jpg"]
    assert len(rf1["reasoning"]) == 600
    assert rf1["inner_calls"] == 2
    assert rf1["inner_queries"] == ["내부질의1", "내부질의2"]
    expected_cost1 = round(((10000 - 8000) * 4.0 + 8000 * 0.2 + 500 * 20.0) / 1e6, 6)
    assert rf1["usage"]["cost_usd"] == expected_cost1
    assert rf1["usage"]["cost_known"] is True
    assert rf1["usage"]["ok"] is True

    post1_body = next(x[2] for x in reflect_http_log if x[0] == "POST")
    assert "이미 확인한 것: 장소 가, 2025년 봄" in post1_body["query"]
    assert post1_body["budget"] == "mid"
    assert post1_body["response_schema"] == hindsight.REFLECT_SCHEMA
    assert post1_body["include"] == {"tool_calls": {}}

    reflect_http_log.clear()
    rf2 = hindsight.reflect("원래 질문", "")
    post2_body = next(x[2] for x in reflect_http_log if x[0] == "POST")
    assert "이미 확인한 것:" not in post2_body["query"]
    assert rf2["usage"]["calls"] == 2

    # 즉시 거절(400, ConnectError)에서 집계 차이 0 이면 cost_usd=0, cost_known=True
    for reject_mode in ("400", "connect"):
        class FakeRejectClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, path, **kw):
                if path.endswith("/stats"):
                    return httpx.Response(200, json={"buckets": []})
                return httpx.Response(200, json={"items": [{"model": "claude-opus-5-5"}]})

            def post(self, path, **kw):
                if reject_mode == "connect":
                    raise httpx.ConnectError("refused")
                return httpx.Response(400, text="bad req")

        monkeypatch.setattr(hindsight.httpx, "Client", FakeRejectClient)
        with pytest.raises(hindsight.ReflectUnavailable) as rj_exc:
            hindsight.reflect("질문", "")
        assert rj_exc.value.usage is not None
        assert rj_exc.value.usage["cost_usd"] == 0.0
        assert rj_exc.value.usage["cost_known"] is True
        assert rj_exc.value.usage["ok"] is False

    # 시간 초과면 집계 차이가 있어도 cost_usd=None, cost_known=False
    to_stats = [
        {"buckets": [{"total": 0, "statuses": {"success": 0}, "tokens": {"input": 0, "output": 0, "cached": 0}}]},
        {"buckets": [{"total": 4, "statuses": {"success": 4}, "tokens": {"input": 9000, "output": 400, "cached": 1000}}]},
    ]

    class FakeTimeoutClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, path, **kw):
            if path.endswith("/stats"):
                return httpx.Response(200, json=to_stats.pop(0) if to_stats else {"buckets": []})
            return httpx.Response(200, json={"items": []})

        def post(self, path, **kw):
            raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(hindsight.httpx, "Client", FakeTimeoutClient)
    with pytest.raises(hindsight.ReflectUnavailable) as to_exc:
        hindsight.reflect("질문", "")
    assert to_exc.value.usage["cost_usd"] is None
    assert to_exc.value.usage["cost_known"] is False
    assert to_exc.value.usage["calls"] == 0

    # 500, structured_output 비정상, 200 비-JSON 본문, 집계 404 검사
    for bad_case in ("500", "bad_so", "non_json", "stats_404"):
        class FakeCasesClient:
            def __init__(self, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, path, **kw):
                if bad_case == "stats_404":
                    return httpx.Response(404)
                if path.endswith("/stats"):
                    return httpx.Response(200, json={"buckets": []})
                return httpx.Response(200, json={"items": []})

            def post(self, path, **kw):
                if bad_case == "500":
                    return httpx.Response(500, text="err500")
                if bad_case == "bad_so":
                    return httpx.Response(200, json={"structured_output": "not-a-dict"})
                if bad_case == "non_json":
                    return httpx.Response(200, content=b"not json at all")
                return httpx.Response(200, json={"structured_output": {"photo_ids": ["p1.jpg"], "reasoning": "근거"}})

        monkeypatch.setattr(hindsight.httpx, "Client", FakeCasesClient)
        if bad_case == "stats_404":
            rf_404 = hindsight.reflect("질문", "")
            assert rf_404["usage"]["cost_usd"] is None
            assert rf_404["usage"]["cost_known"] is False
        else:
            with pytest.raises(hindsight.ReflectUnavailable) as bc_exc:
                hindsight.reflect("질문", "")
            assert bc_exc.value.usage is not None
            assert bc_exc.value.usage["ok"] is False

    # 장부 줄 확인 및 상한 초과 시 요청 미전송 확인(cost_usd=None 줄은 1.5 로 셈)
    ledger_file = tmp_path / "hindsight_calls.jsonl"
    assert ledger_file.is_file()
    ledger_file.write_text(
        json.dumps({"cost_usd": hindsight.REFLECT_CAP_USD - 2.5}) + "\n"
        + json.dumps({"cost_usd": None}) + "\n",
        encoding="utf-8",
    )
    assert round(hindsight.reflect_spent_usd(), 2) == round(hindsight.REFLECT_CAP_USD - 1.0, 2)

    sent_any = False

    class ForbiddenClient:
        def __init__(self, **kw):
            nonlocal sent_any
            sent_any = True

    monkeypatch.setattr(hindsight.httpx, "Client", ForbiddenClient)
    with pytest.raises(hindsight.ReflectUnavailable) as cap_exc:
        hindsight.reflect("질문", "")
    assert sent_any is False
    assert cap_exc.value.usage is None
    assert claude_called == 0
    assert not (tmp_path / "claude_calls.jsonl").exists()


# ---------------------------------------------------------------- 5. 기억 꺼내기와 되짚기 도구
def test_memory_and_reflect_tools(monkeypatch):
    for spec in (memory_mod.tool_spec(), memory_mod.reflect_tool_spec()):
        assert spec["strict"] is True
        for obj in _walk_objects(spec["input_schema"]):
            assert obj.get("additionalProperties") is False
            assert set(obj.get("required") or []) == set((obj.get("properties") or {}).keys())

    fake_idx = {
        f"p{i:02d}.jpg": {
            "id": f"p{i:02d}.jpg", "kind": "photo", "badge": "갤러리",
            "time": f"2025-07-01T10:{i:02d}:00+09:00",
            "lat": 37.5555, "lon": 126.9999,
            "received_at": "2025-07-02T00:00:00+09:00",
            "saved_at": "2025-07-02T01:00:00+09:00",
            "downloaded_at": "2025-07-02T02:00:00+09:00",
        }
        for i in range(35)
    }
    monkeypatch.setattr(idx_mod, "load", lambda: fake_idx)
    _enable_hindsight(monkeypatch, bank="tool-bank")

    raw_hits = [
        {"rank": 1, "document_id": "timeline:2025-07-01", "kind": "timeline", "source_id": "2025-07-01", "type": "world", "occurred_start": None, "text": "타임라인 요약"},
        {"rank": 2, "document_id": "photo:missing.jpg", "kind": "photo", "source_id": "missing.jpg", "type": "world", "occurred_start": None, "text": "없는 사진"},
    ]
    for i in range(22):
        raw_hits.append({
            "rank": i + 3,
            "document_id": f"photo:p{i:02d}.jpg",
            "kind": "photo",
            "source_id": f"p{i:02d}.jpg",
            "type": "world",
            "occurred_start": None,
            "text": f"사진 설명 {i}",
        })

    monkeypatch.setattr(
        hindsight,
        "recall",
        lambda q: {"bank": "tool-bank", "query": q, "budget": "mid", "max_tokens": 8192, "elapsed_ms": 250, "hits": raw_hits},
    )
    ctx = ToolContext(today=date(2025, 7, 10))
    out, meta = memory_mod.run_recall_memory({"query": "질문"}, ctx)
    assert out["ok"] is True
    assert out["shown"] == 20
    assert out["count"] == 24
    assert out["left_out"] == 4  # missing 1 + 20개 초과 3
    assert out["memories"][0] == {"rank": 1, "kind": "timeline", "id": "2025-07-01", "date": "2025-07-01", "time": None, "text": "타임라인 요약"}
    assert out["memories"][1]["id"] == "p00.jpg"
    assert out["memories"][1]["date"] == "2025-07-01"
    assert out["memories"][1]["time"] == "10:00"
    assert len(meta["memory"]["hits"]) == 20
    assert ctx.seen_ids == set()
    dumped = json.dumps([out, meta], ensure_ascii=False)
    for forbidden in ("37.555", "126.999", "2025-07-02", "received_at", "saved_at", "downloaded_at"):
        assert forbidden not in dumped

    # seen_ids 에 들어가지 않으므로 show_answer 거절
    with pytest.raises(ValueError):
        run_show_answer({"sentence": "답", "ids": ["p00.jpg"], "related": []}, ctx)

    # RecallUnavailable 처리
    def raise_recall(_q):
        raise hindsight.RecallUnavailable("서버 꺼짐")

    monkeypatch.setattr(hindsight, "recall", raise_recall)
    out_err, meta_err = memory_mod.run_recall_memory({"query": "질문"}, ctx)
    assert out_err["ok"] is False
    assert "서버 꺼짐" in out_err["error"]
    assert meta_err["memory"]["error"] == "서버 꺼짐"

    # reflect_memory 도구: 30장 제한, 진행 표시, ReflectUnavailable usage 전달
    monkeypatch.setattr(memory_mod, "REFLECT_PROGRESS_S", 0.01)
    prog: list[str] = []
    ctx_rf = ToolContext(today=date(2025, 7, 10), on_progress=prog.append)

    def slow_reflect(q, k):
        time.sleep(0.035)
        ids = ["gone.jpg"] + [f"p{i:02d}.jpg" for i in range(32)]
        return {
            "bank": "tool-bank", "query": q, "known": k, "budget": "mid", "elapsed_ms": 40,
            "photo_ids": ids, "reasoning": "되짚기 요약", "inner_calls": 3, "inner_queries": ["q1"],
            "usage": {"bank": "tool-bank", "model": "claude-opus-5-5", "calls": 3, "input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 0, "cost_usd": 0.01, "cost_known": True, "ms": 40, "ok": True},
        }

    monkeypatch.setattr(hindsight, "reflect", slow_reflect)
    rf_out, rf_meta = memory_mod.run_reflect_memory({"query": "질문", "known": "단서"}, ctx_rf)
    assert rf_out["ok"] is True
    assert rf_out["shown"] == 30
    assert rf_out["left_out"] == 3  # gone.jpg 1 + 초과 2
    assert any(s.startswith("기억을 되짚어 보는 중이에요 (") for s in prog)
    assert ctx_rf.seen_ids == set()
    dumped_rf = json.dumps([rf_out, rf_meta], ensure_ascii=False)
    for forbidden in ("37.555", "126.999", "2025-07-02"):
        assert forbidden not in dumped_rf

    fake_usage = {"bank": "tool-bank", "model": "claude-opus-5-5", "calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cost_usd": None, "cost_known": False, "ms": 50, "ok": False}

    def fail_reflect(_q, _k):
        raise hindsight.ReflectUnavailable("시간 초과", usage=fake_usage)

    monkeypatch.setattr(hindsight, "reflect", fail_reflect)
    rf_err, rf_err_meta = memory_mod.run_reflect_memory({"query": "질문", "known": ""}, ctx_rf)
    assert rf_err["ok"] is False
    assert rf_err_meta["reflect"]["usage"] == fake_usage


# ---------------------------------------------------------------- 6. 루프 연결
def test_loop_hindsight_integration_and_limits(monkeypatch, tmp_path):
    monkeypatch.setitem(settings.__dict__, "var_dir", tmp_path)
    fake_idx = {
        "c1.jpg": {"id": "c1.jpg", "kind": "photo", "badge": "갤러리", "time": "2025-08-03T13:00:00+09:00", "lat": 37.5, "lon": 127.0, "scene": "차와 케이크", "place": "카페"},
    }
    monkeypatch.setattr(idx_mod, "load", lambda: fake_idx)

    # 끈 상태 확인
    _disable_hindsight(monkeypatch)
    st_off = loop.new_run("질문", today=date(2025, 8, 10))
    assert [t["name"] for t in st_off.tools] == ["plan_query", "search_web", "ask_user", "find_candidates", "judge_photos", "show_answer"]
    assert "recall_memory" not in st_off.system

    # 켠 상태 확인
    _enable_hindsight(monkeypatch, bank="loop-bank")
    st_on = loop.new_run("질문", today=date(2025, 8, 10))
    assert [t["name"] for t in st_on.tools] == [
        "plan_query", "search_web", "ask_user", "recall_memory", "reflect_memory", "find_candidates", "judge_photos", "show_answer"
    ]
    assert "recall_memory" in st_on.system and "reflect_memory" in st_on.system

    monkeypatch.setattr(
        hindsight,
        "recall",
        lambda q: {
            "bank": "loop-bank", "query": q, "budget": "mid", "max_tokens": 8192, "elapsed_ms": 120,
            "hits": [{"rank": 1, "document_id": "photo:c1.jpg", "kind": "photo", "source_id": "c1.jpg", "type": "world", "occurred_start": None, "text": "차와 케이크"}],
        },
    )
    monkeypatch.setattr(
        hindsight,
        "reflect",
        lambda q, k: {
            "bank": "loop-bank", "query": q, "known": k, "budget": "mid", "elapsed_ms": 200,
            "photo_ids": ["c1.jpg"], "reasoning": "근거 요약", "inner_calls": 2, "inner_queries": ["q"],
            "usage": {"bank": "loop-bank", "model": "claude-opus-5-5", "calls": 2, "input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 0, "cost_usd": 0.42, "cost_known": True, "ms": 200, "ok": True},
        },
    )

    turn = 0
    seen_tool_results: list[list[dict]] = []

    def fake_loop_claude(purpose, effort, messages, **kw):
        nonlocal turn
        turn += 1
        if messages and isinstance(messages[-1].get("content"), list):
            seen_tool_results.append(copy.deepcopy(messages[-1]["content"]))
        if turn == 1:
            blocks = [
                SimpleNamespace(type="tool_use", id="tu1", name="plan_query", input={"target": "사진", "anchor": "", "relation": "none", "known": [], "unknown": [], "sentence": "정리"}),
                SimpleNamespace(type="tool_use", id="tu2", name="recall_memory", input={"query": "질문 1"}),
                SimpleNamespace(type="tool_use", id="tu3", name="recall_memory", input={"query": "질문 2"}),
                SimpleNamespace(type="tool_use", id="tu4", name="recall_memory", input={"query": "질문 3"}),
                SimpleNamespace(type="tool_use", id="tu5", name="reflect_memory", input={"query": "질문", "known": ""}),
                SimpleNamespace(type="tool_use", id="tu6", name="reflect_memory", input={"query": "질문", "known": ""}),
            ]
            return claude.Call(SimpleNamespace(stop_reason="tool_use", content=blocks), {"purpose": "loop", "cost_usd": 0.10, "input_tokens": 100, "output_tokens": 50})
        if turn == 2:
            blocks = [
                SimpleNamespace(
                    type="tool_use",
                    id="tu7",
                    name="find_candidates",
                    input={"date_from": "2025-08-03", "date_to": "2025-08-03", "near": {"lat": 37.5, "lon": 127.0, "radius_m": 500}, "card": "picked", "sentence": "후보 찾음"},
                ),
                SimpleNamespace(type="tool_use", id="tu8", name="show_answer", input={"sentence": "답", "ids": ["c1.jpg"], "related": []}),
            ]
            return claude.Call(SimpleNamespace(stop_reason="tool_use", content=blocks), {"purpose": "loop", "cost_usd": 0.10, "input_tokens": 100, "output_tokens": 50})
        return claude.Call(SimpleNamespace(stop_reason="end_turn", content=[]), {"purpose": "loop", "cost_usd": 0.05, "input_tokens": 50, "output_tokens": 10})

    monkeypatch.setattr(claude, "call", fake_loop_claude)
    done_state = loop.start("질문", today=date(2025, 8, 10), test="LT")
    rec = loop.run_record(done_state)

    assert rec["status"] == "answered"
    assert rec["today"] == "2025-08-10"
    assert round(rec["summary"]["cost_usd"], 2) == 0.25  # reflect 비용 0.42 는 포함되지 않음
    assert len(rec["hindsight"]) == 1
    assert rec["hindsight"][0]["cost_usd"] == 0.42

    mem_evs = [e for e in rec["events"] if e["type"] == "memory"]
    ref_evs = [e for e in rec["events"] if e["type"] == "reflect"]
    assert [e["n"] for e in mem_evs] == [1, 2]
    assert [e["n"] for e in ref_evs] == [1]

    # 첫 도구 결과 묶음에서 꺼낸 기억과 되짚기 결과가 모델에 전달되고, 3번째 recall 과 2번째 reflect 는 is_error
    first_tr = seen_tool_results[0]
    assert "c1.jpg" in first_tr[1]["content"]
    assert first_tr[3].get("is_error") is True
    assert "c1.jpg" in first_tr[4]["content"]
    assert first_tr[5].get("is_error") is True

    # 카드 번호를 memory/reflect 가 쓰지 않아 plan=1, picked=5, result=6 그대로
    card_nos = [(e["type"], e.get("kind"), e.get("no")) for e in rec["events"] if e["type"] in ("card", "result")]
    assert card_nos == [("card", "plan", 1), ("card", "picked", 5), ("result", None, 6)]

    recall_traces = [t for t in rec["tools"] if t["name"] == "recall_memory"]
    assert [t["memory_n"] for t in recall_traces] == [1, 2, None]
    assert recall_traces[0]["out_ids"] == ["c1.jpg"]
    assert recall_traces[0]["out_dates"] == ["2025-08-03"]

    reflect_traces = [t for t in rec["tools"] if t["name"] == "reflect_memory"]
    assert [t["reflect_n"] for t in reflect_traces] == [1, None]
    assert reflect_traces[0]["cost_usd"] == 0.42
    assert reflect_traces[0]["out_ids"] == ["c1.jpg"]
    assert reflect_traces[0]["out_dates"] == ["2025-08-03"]

    fc_trace = next(t for t in rec["tools"] if t["name"] == "find_candidates")
    assert fc_trace["input"]["near"] == {"radius_m": 500}
    assert "lat" not in json.dumps(fc_trace)

    # 꺼내기와 되짚기가 모두 실패해도 답까지 이어지는지 확인
    monkeypatch.setattr(hindsight, "recall", lambda _q: (_ for _ in ()).throw(hindsight.RecallUnavailable("꺼짐")))
    monkeypatch.setattr(hindsight, "reflect", lambda _q, _k: (_ for _ in ()).throw(hindsight.ReflectUnavailable("꺼짐", usage=None)))
    turn = 0
    seen_tool_results.clear()

    def fake_fail_resilient(purpose, effort, messages, **kw):
        nonlocal turn
        turn += 1
        if turn == 1:
            return claude.Call(
                SimpleNamespace(
                    stop_reason="tool_use",
                    content=[
                        SimpleNamespace(type="tool_use", id="u1", name="recall_memory", input={"query": "질문"}),
                        SimpleNamespace(type="tool_use", id="u2", name="reflect_memory", input={"query": "질문", "known": ""}),
                    ],
                ),
                {"purpose": "loop", "cost_usd": 0.05},
            )
        if turn == 2:
            return claude.Call(
                SimpleNamespace(
                    stop_reason="tool_use",
                    content=[
                        SimpleNamespace(type="tool_use", id="u3", name="find_candidates", input={"date_from": "2025-08-03", "date_to": "2025-08-03", "card": "picked", "sentence": "찾음"}),
                        SimpleNamespace(type="tool_use", id="u4", name="show_answer", input={"sentence": "답", "ids": ["c1.jpg"], "related": []}),
                    ],
                ),
                {"purpose": "loop", "cost_usd": 0.05},
            )
        return claude.Call(SimpleNamespace(stop_reason="end_turn", content=[]), {"purpose": "loop", "cost_usd": 0.02})

    monkeypatch.setattr(claude, "call", fake_fail_resilient)
    st_resilient = loop.start("질문", today=date(2025, 8, 10))
    assert st_resilient.status == "answered"


# ---------------------------------------------------------------- 7. 수용표 검사
def test_accept_checker(monkeypatch, tmp_path):
    ak_path = tmp_path / "answer_key.json"
    passed_dir = tmp_path / "passed"
    monkeypatch.setitem(settings.__dict__, "answer_key", ak_path)
    monkeypatch.setitem(settings.__dict__, "passed_dir", passed_dir)

    fake_idx = {
        "anc.jpg": {"id": "anc.jpg", "kind": "photo", "time": "2025-05-10T11:30:00+09:00"},
        "ans1.jpg": {"id": "ans1.jpg", "kind": "photo", "time": "2025-05-10T13:00:00+09:00"},
        "ans2.jpg": {"id": "ans2.jpg", "kind": "photo", "time": "2025-05-10T13:30:00+09:00"},
        "trap_early.jpg": {"id": "trap_early.jpg", "kind": "photo", "time": "2025-05-10T09:40:00+09:00"},
        "trap_late.jpg": {"id": "trap_late.jpg", "kind": "photo", "time": "2025-05-10T14:00:00+09:00"},
        "ref_only.jpg": {"id": "ref_only.jpg", "kind": "photo", "time": "2025-05-11T15:00:00+09:00"},
    }
    monkeypatch.setattr(idx_mod, "load", lambda: fake_idx)

    ak_path.write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "id": "Q1",
                        "kind": "sequence",
                        "query": "공연 보고 나서 들른 찻집 사진",
                        "answers": ["시민회관"],
                        "answer_ids": ["ans1.jpg", "ans2.jpg"],
                        "optional_ids": [],
                        "trap_ids": ["trap_early.jpg", "trap_late.jpg"],
                        "accept": {
                            "after_id": "anc.jpg",
                            "related": [
                                {"name": "공연장 사진", "ids": ["anc.jpg"]},
                                {"name": "공연 전 사진", "ids": ["trap_early.jpg"]},
                            ],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    base_rec = {
        "run_id": "Q1_001",
        "test": "Q1",
        "query": "공연 보고 나서 들른 찻집 사진",
        "status": "answered",
        "result_ids": ["ans1.jpg", "ans2.jpg"],
        "calls": [{"purpose": "search_web", "web_search_requests": 2, "cost_usd": 0.02}],
        "tools": [
            {"i": 1, "name": "recall_memory", "input": {"query": "공연 보고 나서 들른 찻집 사진"}, "ok": True, "error": None, "out_ids": ["anc.jpg"], "out_dates": ["2025-05-10"], "memory_n": 1, "bank": "acc-bank", "count": 3, "elapsed_ms": 100},
            {"i": 2, "name": "reflect_memory", "input": {"query": "공연 보고 나서 들른 찻집 사진", "known": "시민회관"}, "ok": True, "error": None, "out_ids": ["ref_only.jpg"], "out_dates": ["2025-05-11"], "reflect_n": 1, "bank": "acc-bank", "count": 1, "elapsed_ms": 500, "cost_usd": 0.55},
            {"i": 3, "name": "find_candidates", "input": {"date_from": "2025-05-10", "date_to": "2025-05-10", "after": "2025-05-10T11:30:00+09:00"}, "ok": True, "error": None, "out_ids": ["ans1.jpg", "ans2.jpg", "anc.jpg", "trap_early.jpg", "trap_late.jpg"], "out_dates": []},
            {"i": 4, "name": "judge_photos", "input": {"ids": ["ref_only.jpg", "ans1.jpg", "ans2.jpg"]}, "ok": True, "error": None, "out_ids": ["ans1.jpg", "ans2.jpg"], "out_dates": []},
        ],
        "hindsight": [{"bank": "acc-bank", "model": "claude-opus-5-5", "calls": 5, "cost_usd": 0.55, "cost_known": True, "ms": 500, "ok": True}],
        "events": [
            {"type": "card", "run_id": "Q1_001", "seq": 1, "no": 2, "kind": "web", "title": "웹에서 찾아봤어요", "sentence": "s", "evidence": None, "cached": False, "data": {}},
            {"type": "ask", "run_id": "Q1_001", "seq": 2, "no": 3, "title": "어느 쪽인지 알려주세요", "question": "어느 공연?", "options": [{"id": "o1", "label": "시민회관", "detail": "2025.5"}, {"id": "o2", "label": "별빛홀", "detail": "2024.9"}]},
            {"type": "done", "run_id": "Q1_001", "seq": 3, "status": "waiting"},
            {"type": "answer", "run_id": "Q1_001", "seq": 4, "option_id": "o1", "label": "시민회관"},
            {"type": "memory", "run_id": "Q1_001", "seq": 5, "n": 1, "query": "q", "bank": "acc-bank", "elapsed_ms": 100, "count": 3, "hits": [], "error": None},
            {"type": "reflect", "run_id": "Q1_001", "seq": 6, "n": 1, "query": "q", "known": "시민회관", "bank": "acc-bank", "elapsed_ms": 500, "count": 1, "photo_ids": ["ref_only.jpg"], "left_out": 0, "reasoning": "r", "inner_calls": 5, "inner_queries": [], "usage": {"ok": True, "cost_usd": 0.55}, "error": None},
            {"type": "card", "run_id": "Q1_001", "seq": 7, "no": 5, "kind": "picked", "title": "조건에 맞는 사진만 골랐어요", "sentence": "s", "evidence": {"cutoff": "2025-05-10T11:30:00+09:00"}, "cached": False, "data": None},
            {
                "type": "result", "run_id": "Q1_001", "seq": 8, "no": 6, "title": "2장 찾았어요", "sentence": "s",
                "items": [{"id": "ans1.jpg"}, {"id": "ans2.jpg"}],
                "related_title": "연관된 사진을 보실래요?",
                "related": [
                    {"label": "공연장 보기", "items": [{"id": "anc.jpg"}]},
                    {"label": "이전 사진", "items": [{"id": "trap_early.jpg"}]},
                ],
            },
            {"type": "done", "run_id": "Q1_001", "seq": 9, "status": "answered"},
        ],
    }

    def run_check(rec_obj: dict, save: bool = False) -> tuple[int, str, dict[str, str]]:
        rp = tmp_path / "cand.json"
        rp.write_text(json.dumps(rec_obj, ensure_ascii=False), encoding="utf-8")
        code, out = accept_mod.evaluate_acceptance("Q1", rp, save=save)
        table: dict[str, str] = {}
        for line in out.splitlines():
            if line.startswith("| ") and not line.startswith("| 확인 대상") and not line.startswith("| ---"):
                cols = [c.strip() for c in line.strip("|").split("|")]
                table[cols[0]] = cols[1]
        return code, out, table

    # 은행 이름이 비어 있으면 끝 값 2, 저장 안 함
    _disable_hindsight(monkeypatch)
    c_no_bank, _, _ = run_check(base_rec, save=True)
    assert c_no_bank == 2
    assert not (passed_dir / "Q1.json").exists()

    _enable_hindsight(monkeypatch, bank="acc-bank")

    # 모두 맞는 기록 -> 끝 값 0, --save 시 그대로 저장
    c_ok, out_ok, tbl_ok = run_check(base_rec, save=True)
    assert c_ok == 0
    assert "기록으로 보는 일곱 줄 가운데 7줄 맞음" in out_ok
    assert tbl_ok["두 가지 재생 방식"] == "브라우저"
    assert json.loads((passed_dir / "Q1.json").read_text(encoding="utf-8")) == base_rec
    (passed_dir / "Q1.json").unlink()

    # 선택지의 detail 에만 answers 낱말이 있는 기록 -> 통과
    detail_rec = copy.deepcopy(base_rec)
    detail_rec["events"][1]["options"] = [
        {"id": "o1", "label": "첫 번째 공연", "detail": "2025.5 · 시민회관 대강당"},
        {"id": "o2", "label": "두 번째 공연", "detail": "2024.9 · 별빛홀"},
    ]
    c_det, _, tbl_det = run_check(detail_rec)
    assert c_det == 0 and tbl_det["질의와 선택"] == "✓"

    # 1) 최종 사진에 함정(trap_late.jpg) 섞임: 다른 6줄은 모두 ✓ 유지
    m_trap = copy.deepcopy(base_rec)
    m_trap["result_ids"] = ["ans1.jpg", "ans2.jpg", "trap_late.jpg"]
    c1, _, t1 = run_check(m_trap, save=True)
    assert c1 == 1 and not (passed_dir / "Q1.json").exists()
    assert [k for k, v in t1.items() if v == "✗"] == ["최종 사진"]

    # 2) (나) 위반: recall/reflect 가 돌려준 날짜·id 를 뒤 도구가 하나도 쓰지 않음
    m_no_use = copy.deepcopy(base_rec)
    m_no_use["tools"][0]["out_ids"] = []
    m_no_use["tools"][0]["out_dates"] = ["2025-01-01"]
    m_no_use["tools"][1]["out_ids"] = []
    m_no_use["tools"][1]["out_dates"] = ["2025-01-02"]
    c2, _, t2 = run_check(m_no_use, save=True)
    assert c2 == 1 and [k for k, v in t2.items() if v == "✗"] == ["기억 꺼내기와 되짚기 도구 연결"]

    # 3) (가) 위반: recall_memory 가 ok=False 뿐
    m_rec_fail = copy.deepcopy(base_rec)
    m_rec_fail["tools"][0]["ok"] = False
    c3, _, t3 = run_check(m_rec_fail)
    assert c3 == 1 and [k for k, v in t3.items() if v == "✗"] == ["기억 꺼내기와 되짚기 도구 연결"]

    # 4) (라) 위반: reflect_memory 가 없는 기록과 ok=False 뿐인 기록
    m_no_ref = copy.deepcopy(base_rec)
    m_no_ref["tools"] = [t for t in m_no_ref["tools"] if t["name"] != "reflect_memory"]
    c4a, _, t4a = run_check(m_no_ref)
    assert c4a == 1 and [k for k, v in t4a.items() if v == "✗"] == ["기억 꺼내기와 되짚기 도구 연결"]

    m_ref_fail = copy.deepcopy(base_rec)
    m_ref_fail["tools"][1]["ok"] = False
    c4b, _, t4b = run_check(m_ref_fail)
    assert c4b == 1 and [k for k, v in t4b.items() if v == "✗"] == ["기억 꺼내기와 되짚기 도구 연결"]

    # 5) (라) 위반: 되짚기가 고른 사진·날짜(꺼내기와 겹치지 않음)를 뒤 도구가 하나도 쓰지 않음
    m_ref_unused = copy.deepcopy(base_rec)
    m_ref_unused["tools"][3]["input"]["ids"] = ["ans1.jpg", "ans2.jpg"]
    c5, _, t5 = run_check(m_ref_unused)
    assert c5 == 1 and [k for k, v in t5.items() if v == "✗"] == ["기억 꺼내기와 되짚기 도구 연결"]

    # 6) 실제 웹 검색 위반: cached=True 뿐
    m_web_cached = copy.deepcopy(base_rec)
    m_web_cached["events"][0]["cached"] = True
    c6, _, t6 = run_check(m_web_cached)
    assert c6 == 1 and [k for k, v in t6.items() if v == "✗"] == ["실제 웹 검색"]

    # 7) 후속 조회 하나 불일치
    m_rel_bad = copy.deepcopy(base_rec)
    m_rel_bad["events"][7]["related"][1]["items"] = [{"id": "ans1.jpg"}]
    c7, _, t7 = run_check(m_rel_bad)
    assert c7 == 1 and [k for k, v in t7.items() if v == "✗"] == ["후속 조회: 공연 전 사진"]


# ---------------------------------------------------------------- 8. 시나리오 값 검사
def test_no_scenario_values_in_app_and_accept():
    ak_file = WS / "eval" / "answer_key.json"
    if not ak_file.is_file():
        pytest.skip("정답 키 파일이 아직 없다")

    raw_ak = json.loads(ak_file.read_text(encoding="utf-8"))
    tests = raw_ak.get("tests") if isinstance(raw_ak, dict) else raw_ak
    spec_a = next((s for s in (tests or []) if isinstance(s, dict) and s.get("id") == "A"), None)
    if spec_a is None:
        pytest.skip("정답 키에 A 가 없다")

    forbidden: set[str] = set()

    def add_tok(val: str | None) -> None:
        if not val or not isinstance(val, str):
            return
        s = val.strip()
        if len(s) >= 2:
            forbidden.add(s)

    add_tok(spec_a.get("query"))
    for w in spec_a.get("answers") or []:
        add_tok(w)
    for pid in [*(spec_a.get("answer_ids") or []), *(spec_a.get("trap_ids") or [])]:
        add_tok(pid)
        add_tok(Path(pid).stem)
    acc = spec_a.get("accept") or {}
    add_tok(acc.get("after_id"))
    if acc.get("after_id"):
        add_tok(Path(acc["after_id"]).stem)
    for grp in acc.get("related") or []:
        for pid in (grp or {}).get("ids") or []:
            add_tok(pid)
            add_tok(Path(pid).stem)
    note = str(spec_a.get("note") or "")
    for dt_str in re.findall(r"\d{4}-\d{2}-\d{2}", note):
        add_tok(dt_str)
        add_tok(dt_str.replace("-", ""))

    wc_file = WS / "data" / "web_cache.json"
    if wc_file.is_file():
        wc_items = json.loads(wc_file.read_text(encoding="utf-8"))
        if isinstance(wc_items, dict):
            wc_items = wc_items.get("items") or []
        for it in wc_items or []:
            if not isinstance(it, dict):
                continue
            for al in it.get("aliases") or []:
                add_tok(al)
            for ev in it.get("events") or []:
                if isinstance(ev, dict):
                    add_tok(ev.get("venue"))

    targets = [
        *sorted((WS / "app").rglob("*.py")),
        *sorted((WS / "app").rglob("*.md")),
        WS / "eval" / "accept.py",
    ]
    leaks: list[tuple[str, str]] = []
    for path in targets:
        text = path.read_text(encoding="utf-8")
        for tok in sorted(forbidden):
            if tok in text:
                leaks.append((path.relative_to(WS).as_posix(), tok))
    assert not leaks, f"코드·지시문에 시나리오 값이 들어 있습니다: {leaks}"


# ---------------------------------------------------------------- 3부 추가 검사 (수집·문서 추출·문서 기억)
def test_part3_collect_screenshot_filename(tmp_path):
    import zipfile
    from PIL import Image
    from pipeline.collect import read_photo_meta

    img_path = tmp_path / "Screenshot_20250412-083015.png"
    Image.new("RGB", (16, 16), (255, 255, 255)).save(img_path, "PNG")
    with Image.open(img_path) as im:
        meta = read_photo_meta(img_path, im, "screenshot")
    assert meta["time"] == "2025-04-12T08:30:15+09:00"
    assert meta["time_basis"] == "filename"

    for legacy_name in ("IMG_20250412_083015.jpg", "PXL_20250412_083015.jpg", "20250412_083015.jpg"):
        p = tmp_path / legacy_name
        Image.new("RGB", (16, 16), (200, 200, 200)).save(p, "JPEG")
        with Image.open(p) as im2:
            m2 = read_photo_meta(p, im2, "gallery")
        assert m2["time"] == "2025-04-12T08:30:15+09:00"
        assert m2["time_basis"] == "filename"
    assert zipfile is not None


def test_part3_extract_docs_html_and_docx(tmp_path):
    import zipfile
    from pipeline.extract_docs import extract_one_document

    html_file = tmp_path / "sample_notice.html"
    html_file.write_text(
        '<!doctype html><html><head>'
        '<meta name="description" content="작성일 2025-05-10 | 키워드=비밀키워드 | 선정업체=비밀업체 | 버전=1차">'
        '<title>정답표시제목</title></head>'
        '<body><h1>안내문 본문</h1><p>엘리베이터 점검 안내입니다.</p></body></html>',
        encoding="utf-8",
    )
    h_row = extract_one_document(html_file, "Download/sample_notice.html")
    assert h_row["written"] == "2025-05-10"
    assert h_row["updated"] is None
    assert h_row["dates"] == [] and h_row["amounts"] == []
    assert "엘리베이터 점검 안내" in h_row["body"]
    for secret_attr in ("키워드=", "선정업체=", "버전=", "비밀키워드", "비밀업체"):
        assert secret_attr not in h_row["body"]

    # 최소 유효 .docx 생성 (core.xml 에 정답 속성을 심고 본문에는 옮기지 않는지 확인)
    docx_file = tmp_path / "sample_memo.docx"
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '</Relationships>'
    )
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:title>숨김제목</dc:title>'
        '<dc:creator>숨김작성자</dc:creator>'
        '<cp:keywords>키워드=숨김</cp:keywords>'
        '<dc:description>작성일 2025-05-10 | 선정업체=숨김업체 | 버전=최종</dc:description>'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">2025-05-14T09:00:00Z</dcterms:modified>'
        '</cp:coreProperties>'
    )
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>회의 정리 본문입니다.</w:t></w:r></w:p></w:body>'
        '</w:document>'
    )
    with zipfile.ZipFile(docx_file, "w") as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("docProps/core.xml", core_xml)
        zf.writestr("word/document.xml", doc_xml)

    d_row = extract_one_document(docx_file, "Download/sample_memo.docx")
    assert d_row["written"] == "2025-05-10"
    assert d_row["updated"] == "2025-05-14"
    assert "회의 정리 본문" in d_row["body"]
    for secret_attr in ("키워드=", "선정업체=", "버전=", "숨김작성자", "숨김업체"):
        assert secret_attr not in d_row["body"]


def test_part3_make_doc_memory_item():
    from eval.hindsight import make_doc_memory_item

    row_with_upd = {
        "id": "Download/sample_doc.pdf",
        "source": "download",
        "badge": "다운로드",
        "written": "2025-06-01",
        "updated": "2025-06-05",
        "body": "본문 첫 줄\n본문 둘째 줄",
    }
    it = make_doc_memory_item(row_with_upd)
    assert it["document_id"] == "doc:Download/sample_doc.pdf"
    assert it["context"] == "휴대폰에 저장된 문서 한 건의 본문"
    assert it["tags"] == ["document"]
    assert it["timestamp"] == "2025-06-01T09:00:00+09:00"
    assert it["metadata"] == {
        "kind": "document",
        "doc_id": "Download/sample_doc.pdf",
        "source": "download",
        "written": "2025-06-01",
    }
    lines = it["content"].splitlines()
    assert lines[0] == "[문서] 문서 id: Download/sample_doc.pdf"
    assert lines[1] == "작성일: 2025-06-01"
    assert lines[2] == "갱신일: 2025-06-05"
    assert lines[3] == "출처: 다운로드 폴더"
    assert "\n".join(lines[4:]) == "본문 첫 줄\n본문 둘째 줄"

    row_no_upd = {**row_with_upd, "updated": None}
    it2 = make_doc_memory_item(row_no_upd)
    assert "갱신일:" not in it2["content"]

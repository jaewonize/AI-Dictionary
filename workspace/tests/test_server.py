"""서버 단위 시험(Claude API 호출 없음). 루프의 start, resume 을 가짜로 바꿔 경로와 이벤트 흐름만 본다."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import index as idx_mod
from app import loop, main
from app.config import settings

OPTIONS = [{"id": "opt_1", "label": "가", "detail": ""}, {"id": "opt_2", "label": "나", "detail": ""}]


def _events(text: str) -> list[dict]:
    return [json.loads(c[len("data: "):]) for c in text.split("\n\n") if c.startswith("data: ")]


def _fake_start(query, *, today=None, test=None, on_event=None):
    st = loop.new_run(query, on_event=on_event)
    st.pending_ask = {"tool_use_id": "t1", "question": "어느 쪽?", "options": OPTIONS, "sibling_results": []}
    st.status = "waiting"
    st.emit("ask", no=3, title="알려주세요", question="어느 쪽?", options=OPTIONS)
    st.emit("done", status="waiting")
    return st


def _fake_resume(state, option_id):
    state.pending_ask = None
    state.status = "answered"
    state.emit("answer", option_id=option_id, label="가")
    state.emit("done", status="answered")
    return state


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "_running", False)
    monkeypatch.setattr(main, "_waiting", None)
    monkeypatch.setattr(loop, "start", _fake_start)
    monkeypatch.setattr(loop, "resume", _fake_resume)
    return TestClient(main.app)


def test_ask_then_answer_flow(client):
    r = client.post("/api/ask", json={"query": "질문"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    evs = _events(r.text)
    assert [e["type"] for e in evs] == ["ask", "done"]
    assert evs[-1]["status"] == "waiting"
    run_id = evs[0]["run_id"]

    # 다른 run_id 는 409, 없는 선택지는 400 이고 기다리는 실행은 그대로 남는다.
    assert client.post("/api/answer", json={"run_id": "x", "option_id": "opt_1"}).status_code == 409
    assert client.post("/api/answer", json={"run_id": run_id, "option_id": "opt_9"}).status_code == 400

    r2 = client.post("/api/answer", json={"run_id": run_id, "option_id": "opt_1"})
    assert r2.status_code == 200
    evs2 = _events(r2.text)
    assert [e["type"] for e in evs2] == ["answer", "done"]
    assert evs2[-1]["status"] == "answered"
    assert {e["run_id"] for e in evs2} == {run_id}

    # 끝난 실행에는 다시 답할 수 없다.
    assert client.post("/api/answer", json={"run_id": run_id, "option_id": "opt_1"}).status_code == 409


def test_ask_rejected_while_running_and_new_ask_drops_waiting(client, monkeypatch):
    assert client.post("/api/ask", json={"query": "   "}).status_code == 400

    monkeypatch.setattr(main, "_running", True)
    assert client.post("/api/ask", json={"query": "질문"}).status_code == 409
    monkeypatch.setattr(main, "_running", False)

    first = _events(client.post("/api/ask", json={"query": "첫 질문"}).text)[0]["run_id"]
    second = _events(client.post("/api/ask", json={"query": "둘째 질문"}).text)[0]["run_id"]
    assert first != second
    assert client.post("/api/answer", json={"run_id": first, "option_id": "opt_1"}).status_code == 409
    assert client.post("/api/answer", json={"run_id": second, "option_id": "opt_1"}).status_code == 200


def test_worker_failure_becomes_error_card_and_frees_server(client, monkeypatch):
    def boom(query, *, today=None, test=None, on_event=None):
        raise SystemExit("설정 파일에 키가 없다")

    monkeypatch.setattr(loop, "start", boom)
    evs = _events(client.post("/api/ask", json={"query": "질문"}).text)
    assert [e["type"] for e in evs] == ["error", "done"]
    assert evs[-1]["status"] == "failed"

    monkeypatch.setattr(loop, "start", _fake_start)
    assert client.post("/api/ask", json={"query": "다시"}).status_code == 200


def test_static_photo_and_health(client, monkeypatch, tmp_path):
    assert "AI" in client.get("/").text
    assert client.get("/web/live.js").status_code == 200

    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    rows = {"d/a.jpg": {"id": "d/a.jpg", "kind": "photo", "screen_copy": "photos/a.jpg"},
            "gone.jpg": {"id": "gone.jpg", "kind": "photo", "screen_copy": "photos/gone.jpg"}}
    monkeypatch.setattr(idx_mod, "load", lambda: rows)
    monkeypatch.setitem(settings.__dict__, "var_dir", tmp_path)

    ok = client.get("/api/photo", params={"id": "d/a.jpg"})
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/jpeg"
    assert ok.content == b"\xff\xd8\xff\xd9"
    assert client.get("/api/photo", params={"id": "photos/a.jpg"}).status_code == 404  # 색인에 없는 id
    assert client.get("/api/photo", params={"id": "gone.jpg"}).status_code == 404      # 사본 파일이 없음

    h = client.get("/api/health").json()
    assert h == {"index": 2, "model": settings.model, "web_search": settings.web_search}

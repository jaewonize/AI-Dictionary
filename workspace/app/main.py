"""서버(docs/ARCHITECTURE.md '서버와 화면 사이', '질의 한 번이 도는 길').

질의마다 에이전트 루프(app/loop.py)를 돌리고, 루프가 내는 이벤트를 나오는 대로 SSE로 화면에 보낸다.
실행은 한 번에 하나만 돈다. 되묻기에서 기다리는 실행은 새 질의가 오면 버린다.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable, Iterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import index as idx_mod
from app import loop
from app.config import WS, settings
from app.events import sse

WEB_DIR = WS / "web"

app = FastAPI(title="AI ( ) Dictionary", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")

_lock = threading.Lock()
_running = False                      # 루프가 돌고 있는가
_waiting: loop.RunState | None = None  # 되묻기에서 답을 기다리는 실행


class AskIn(BaseModel):
    query: str


class AnswerIn(BaseModel):
    run_id: str
    option_id: str | int


def _stream(work: Callable[[Callable[[dict], None]], loop.RunState]) -> StreamingResponse:
    """work 를 다른 스레드에서 돌리고, 그 안에서 나온 이벤트를 차례로 내보낸다.

    done 이벤트는 실행 상태를 고친 뒤에 보낸다. 화면이 done 을 받자마자 다음 요청을 보내도 409 가 나지 않게 한다.
    """
    q: queue.Queue[dict | None] = queue.Queue()

    def worker() -> None:
        global _running, _waiting
        state = None
        try:
            state = work(q.put)
        except BaseException as e:  # 키가 없을 때의 SystemExit 까지 오류 카드로 보인다
            q.put({"type": "error", "message": f"서버에서 실행하지 못했어요({type(e).__name__}: {str(e)[:200]})."})
            q.put({"type": "done", "status": "failed"})
        finally:
            with _lock:
                _waiting = state if state is not None and state.status == "waiting" else None
                _running = False
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def events() -> Iterator[str]:
        held = None
        while True:
            ev = q.get()
            if ev is None:
                break
            if ev.get("type") == "done":
                held = ev
                continue
            yield sse(ev)
        if held is not None:
            yield sse(held)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
def home() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.post("/api/ask")
def ask(body: AskIn) -> StreamingResponse:
    global _running, _waiting
    query = body.query.strip()
    if not query:
        raise HTTPException(400, "질의가 비어 있다")
    with _lock:
        if _running:
            raise HTTPException(409, "돌고 있는 실행이 있다")
        _running = True
        _waiting = None
    return _stream(lambda emit: loop.start(query, on_event=emit))


@app.post("/api/answer")
def answer(body: AnswerIn) -> StreamingResponse:
    global _running, _waiting
    option_id = str(body.option_id)
    with _lock:
        state = _waiting
        if _running or state is None or state.run_id != body.run_id:
            raise HTTPException(409, "되묻기에서 기다리는 실행이 아니다")
        options = (state.pending_ask or {}).get("options") or []
        if not any(str(o["id"]) == option_id for o in options):
            raise HTTPException(400, "없는 선택지다")
        _running = True
        _waiting = None

    def work(emit: Callable[[dict], None]) -> loop.RunState:
        state.on_event = emit
        return loop.resume(state, option_id)

    return _stream(work)


@app.get("/api/photo")
def photo(id: str) -> FileResponse:
    copy = (idx_mod.load().get(id) or {}).get("screen_copy")
    path = settings.var_dir / copy if copy else None
    if path is None or not path.is_file():
        raise HTTPException(404, "색인에 없는 사진이다")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/health")
def health() -> dict:
    return {"index": len(idx_mod.load()), "model": settings.model, "web_search": settings.web_search}

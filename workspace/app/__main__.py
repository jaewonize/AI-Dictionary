"""서버를 띄운다: uv run python -m app (workspace/ 에서). 127.0.0.1 에만 묶는다."""

from __future__ import annotations

import uvicorn

from app import claude
from app import index as idx_mod
from app.config import settings


def main() -> None:
    claude.client()  # 설정 파일에 키가 없으면 여기서 바로 멈춘다
    n = len(idx_mod.load())
    print(f"색인 {n}줄 · 모델 {settings.model} · http://localhost:{settings.port}", flush=True)
    if not n:
        print(f"색인이 없다({settings.index}). 질의에는 오류 카드로 답한다. 먼저 pipeline.build_index 를 돌린다", flush=True)
    uvicorn.run("app.main:app", host="127.0.0.1", port=settings.port)


if __name__ == "__main__":
    main()

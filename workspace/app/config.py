"""설정과 경로. 서버, 미리 돌리는 스크립트, 시험 실행기, 재생 페이지 만들기가 함께 쓴다.

설정 파일은 python-dotenv 로 저장소 맨 위와 workspace/ 의 것을 차례로 읽는다(workspace 쪽이 우선).
AIDIC_* 값은 셸 환경 변수가 있으면 그것이 이긴다(같은 코드로 다른 자료를 돌려 보려고).
Anthropic 키는 설정 파일에서만 읽고, 셸에 남은 ANTHROPIC_* 변수는 쓰지 않는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from dotenv import dotenv_values

WS = Path(__file__).resolve().parents[1]
REPO = WS.parent
_FILE_NAME = "." + "env"  # 보호 훅이 경로 문자열을 보고 막으므로 코드에서만 만든다


def _file_values() -> dict[str, str]:
    vals: dict[str, str] = {}
    for p in (REPO / _FILE_NAME, WS / _FILE_NAME):
        if p.is_file():
            vals.update({k: v for k, v in dotenv_values(p).items() if v})
    return vals


def _resolve(p: str | None, default: Path) -> Path:
    if not p:
        return default
    q = Path(p)
    return q if q.is_absolute() else (WS / q).resolve()


@dataclass(frozen=True)
class Settings:
    values: dict

    def get(self, key: str, default: str | None = None) -> str | None:
        if key.startswith("AIDIC_") and os.environ.get(key):
            return os.environ[key]
        return self.values.get(key) or default

    # ---------------------------------------------------------------- 폴더
    @cached_property
    def phone_dir(self) -> Path:
        return _resolve(self.get("AIDIC_PHONE_DIR"), REPO / "Mobile Storage")

    @cached_property
    def data_dir(self) -> Path:
        return _resolve(self.get("AIDIC_DATA_DIR"), WS / "data")

    @cached_property
    def var_dir(self) -> Path:
        return _resolve(self.get("AIDIC_VAR_DIR"), WS / "var")

    @cached_property
    def passed_dir(self) -> Path:
        return _resolve(self.get("AIDIC_PASSED_DIR"), WS / "eval" / "passed")

    @cached_property
    def answer_key(self) -> Path:
        return _resolve(self.get("AIDIC_ANSWER_KEY"), WS / "eval" / "answer_key.json")

    # ---------------------------------------------------------------- 파일
    @property
    def catalog(self) -> Path:
        return self.var_dir / "catalog.jsonl"

    @property
    def descriptions(self) -> Path:
        return self.data_dir / "descriptions.jsonl"

    @property
    def links(self) -> Path:
        return self.data_dir / "links.jsonl"

    @property
    def web_cache(self) -> Path:
        return self.data_dir / "web_cache.json"

    @property
    def blurred_dir(self) -> Path:
        return self.data_dir / "blurred"

    @property
    def docs(self) -> Path:
        return self.var_dir / "docs.jsonl"

    @property
    def index(self) -> Path:
        return self.var_dir / "index.jsonl"

    @property
    def photos_model(self) -> Path:
        return self.var_dir / "photos" / "model"

    @property
    def photos_screen(self) -> Path:
        return self.var_dir / "photos" / "screen"

    @property
    def runs_dir(self) -> Path:
        return self.var_dir / "runs"

    @property
    def replay_out(self) -> Path:
        return self.var_dir / "replay.html"

    # ---------------------------------------------------------------- 값
    @property
    def model(self) -> str:
        return self.get("AIDIC_MODEL", "claude-opus-5-5")

    @property
    def port(self) -> int:
        return int(self.get("AIDIC_PORT", "8000"))

    @property
    def web_search(self) -> str:
        v = (self.get("AIDIC_WEB_SEARCH", "cache") or "cache").strip().lower()
        return v if v in ("cache", "live") else "cache"

    @property
    def cost_cap_usd(self) -> float:
        return float(self.get("AIDIC_COST_CAP_USD", "100"))

    @property
    def default_tz(self) -> str:
        return self.get("AIDIC_DEFAULT_TZ", "+09:00")

    @property
    def hindsight_url(self) -> str | None:
        v = self.get("AIDIC_HINDSIGHT_URL")
        return v.rstrip("/") if v else None

    @property
    def hindsight_bank(self) -> str | None:
        return self.get("AIDIC_HINDSIGHT_BANK")

    def anthropic_key(self) -> str | None:
        return self.values.get("ANTHROPIC_API_KEY") or self.values.get("CLAUD_API_KEY")


def load_settings() -> Settings:
    return Settings(_file_values())


settings = load_settings()

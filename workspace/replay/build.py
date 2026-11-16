"""재생 페이지 만들기. eval/passed/ 의 통과한 실행 기록을 서버 없이, 인터넷 없이 열리는 HTML 한 파일로 만든다.

    uv run python -m replay.build        (workspace/ 에서)

web/app.css 와 web/render.js 를 template.html 에 그대로 넣고, 이벤트에 나온 사진을 data URI 로 넣는다.
사진은 var/index.jsonl 의 screen_copy(화면용 사본)를 쓰되, data/blurred/ 에 같은 이름의 파일(얼굴을 흐린 사본)이 있으면 그것을 쓴다.
"""

from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageOps

from app.config import settings

HERE = Path(__file__).resolve().parent
WEB = HERE.parent / "web"
TEMPLATE = HERE / "template.html"
LONG_EDGE = 640
QUALITY = 80
RUN_FIELDS = ("run_id", "test", "query", "started_at", "status", "answers", "events")


def load_runs(folder: Path) -> list[dict]:
    runs = []
    if not folder.is_dir():
        return runs
    for p in sorted(folder.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"건너뜀 {p.name}: 읽지 못함 ({e})", file=sys.stderr)
            continue
        if not isinstance(rec, dict) or not isinstance(rec.get("events"), list):
            print(f"건너뜀 {p.name}: events 목록이 없음", file=sys.stderr)
            continue
        runs.append({k: rec.get(k) for k in RUN_FIELDS})
    return runs


def photo_ids(events: list[dict]) -> list[str]:
    """이벤트에 나온 사진 id 를 처음 나온 순서대로 모은다."""
    seen: dict[str, None] = {}

    def add(x) -> None:
        pid = x.get("id") if isinstance(x, dict) else x
        if isinstance(pid, str) and pid:
            seen.setdefault(pid, None)

    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "card":
            for pid in (ev.get("evidence") or {}).get("photo_ids") or []:
                add(pid)
        elif ev.get("type") == "result":
            for it in ev.get("items") or []:
                add(it)
            for group in ev.get("related") or []:
                for it in (group or {}).get("items") or []:
                    add(it)
    return list(seen)


def load_index(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("id"):
                rows[row["id"]] = row
    return rows


def blurred_files(folder: Path) -> dict[str, Path]:
    """흐린 사본을 파일 이름(소문자)과 확장자 뺀 이름(소문자)으로 찾을 수 있게 둔다."""
    found: dict[str, Path] = {}
    if folder.is_dir():
        for p in sorted(folder.iterdir()):
            if p.is_file():
                found.setdefault(p.name.lower(), p)
                found.setdefault(p.stem.lower(), p)
    return found


def source_path(pid: str, index: dict[str, dict], blurred: dict[str, Path]) -> Path | None:
    name = Path(pid).name.lower()
    hit = blurred.get(name) or blurred.get(Path(name).stem)
    if hit:
        return hit
    copy = (index.get(pid) or {}).get("screen_copy")
    if copy:
        p = settings.var_dir / copy
        if p.is_file():
            return p
    return None


def data_uri(path: Path) -> str:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((LONG_EDGE, LONG_EDGE), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=QUALITY, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def build() -> int:
    runs = load_runs(settings.passed_dir)
    if not runs:
        print(f"재생할 실행 기록이 없습니다: {settings.passed_dir}", file=sys.stderr)
        print("시험 실행기로 통과한 실행을 먼저 만드세요 (uv run python -m eval.run A).", file=sys.stderr)
        return 1

    ids: dict[str, None] = {}
    for run in runs:
        for pid in photo_ids(run["events"]):
            ids.setdefault(pid, None)

    index = load_index(settings.index)
    blurred = blurred_files(settings.blurred_dir)
    photos: dict[str, str] = {}
    missing: list[str] = []
    used_blurred = 0
    for pid in ids:
        src = source_path(pid, index, blurred)
        if src is None:
            missing.append(pid)
            continue
        try:
            photos[pid] = data_uri(src)
        except OSError as e:
            print(f"사진을 읽지 못함 {pid}: {e}", file=sys.stderr)
            missing.append(pid)
            continue
        used_blurred += src.parent == settings.blurred_dir

    # '<' 를 모두 < 로 바꿔 JSON 안의 글이 script 태그를 닫지 못하게 한다
    data = json.dumps({"runs": runs, "photos": photos}, ensure_ascii=False).replace("<", "\\u003c")
    html = TEMPLATE.read_text(encoding="utf-8")
    for mark, text in (
        ("/*__APP_CSS__*/", (WEB / "app.css").read_text(encoding="utf-8")),
        ("/*__RENDER_JS__*/", (WEB / "render.js").read_text(encoding="utf-8")),
        ("/*__DATA__*/", data),
    ):
        if mark not in html:
            raise SystemExit(f"template.html 에 {mark} 자리가 없습니다")
        html = html.replace(mark, text, 1)

    out = settings.replay_out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    print(f"실행 {len(runs)}개, 사진 {len(photos)}장(흐린 사본 {used_blurred}장)을 넣었습니다.")
    if missing:
        print(f"사진 {len(missing)}장을 찾지 못해 빈 칸으로 둡니다: {', '.join(missing[:10])}", file=sys.stderr)
    print(f"{out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="backslashreplace")
        except AttributeError:
            pass
    return build()


if __name__ == "__main__":
    sys.exit(main())

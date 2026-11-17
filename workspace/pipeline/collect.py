"""수집: 폰 폴더를 읽어 카탈로그(var/catalog.jsonl)와 메타데이터를 뺀 사진 사본을 만든다.

`uv run python -m pipeline.collect` (workspace/에서)

- 사진과 문서는 폴더가 아니라 파일 형식으로 가른다. 동영상은 카탈로그에 kind=video 로만 적고 사본을 만들지 않는다.
- 시각은 EXIF 찍은 시각(+시간대)을 쓴다. 없으면 파일 이름의 시각(카카오톡 사진은 파일 이름이 받은 시각이라 쓰지 않는다),
  그것도 없으면 EXIF 저장 시각을 쓰고 그 기준(time_basis)을 남긴다.
- XMP 에서는 유입 경로와 받은·저장한·내려받은 시각(poc:*)만 옮긴다. 장소 이름, 기기, 메모는 옮기지 않는다.
- 사본은 회전을 반영한 픽셀만 새 이미지로 옮겨 JPEG(품질 90)로 저장하고, 다시 열어 메타데이터가 비었는지 확인한다.
  Claude용은 긴 변 2048픽셀 이하로(넘으면 줄인다), 화면용은 긴 변 1280픽셀이다.
- 폰 폴더의 원본은 고치지 않는다. 함께 들어 있는 정답 표 네 파일은 수집에서 뺀다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageOps

from app.config import settings

sys.stdout.reconfigure(encoding="utf-8")

IMAGE_FORMATS = {"JPEG", "PNG", "MPO", "WEBP", "HEIF", "GIF", "BMP"}
DOC_EXT = {".pdf", ".docx", ".xlsx", ".pptx", ".html", ".htm", ".md", ".txt", ".hwp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv"}
ANSWER_TABLES = {"image_file_mapping.md", "image_file_mapping.xlsx", "DOCUMENTS.md", "DOCUMENTS.xlsx"}
MODEL_LONG_EDGE = 2048
SCREEN_LONG_EDGE = 1280

FOLDER_SOURCE = [
    ("DCIM/Camera", "gallery"),
    ("DCIM/Screenshots", "screenshot"),
    ("Pictures/KakaoTalk", "kakao_photo"),
    ("KakaoTalkDownload", "kakao_download"),
    ("Download", "download"),
]
BADGE = {"gallery": "갤러리", "screenshot": "캡처", "kakao_photo": "카카오톡", "download": "다운로드",
         "kakao_download": "카톡 받은 파일"}
KAKAO_MS_NAME = re.compile(r"^(1[5-9]\d{11})(?:_\d+)?\.(?:jpe?g|png)$", re.I)  # 카카오톡이 받은 사진에 붙이는 밀리초 이름
CAMERA_NAME = re.compile(r"^(?:IMG_|PXL_|Screenshot[_-])?(20\d{2})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})", re.I)
SCREENSHOT_NAME = re.compile(r"^Screenshot[_-]", re.I)
POC = {"SourceType": "origin", "ReceivedAt": "received_at", "SavedAt": "saved_at", "DownloadedAt": "downloaded_at"}
ORIGIN_MAP = {"user_supplied_download": "shared_download", "web_download": "web_download",
              "shared_download": "shared_download"}


def source_of(pid: str) -> str:
    folder = pid.rsplit("/", 1)[0] if "/" in pid else ""
    for prefix, src in FOLDER_SOURCE:
        if folder == prefix or folder.startswith(prefix + "/"):
            return src
    name = pid.rsplit("/", 1)[-1]
    if KAKAO_MS_NAME.match(name):
        return "kakao_photo"
    if SCREENSHOT_NAME.match(name):
        return "screenshot"
    return "gallery"


def _tz(offset: str | None) -> str:
    return offset if offset and re.fullmatch(r"[+-]\d{2}:\d{2}", offset.strip()) else settings.default_tz


def _exif_time(s, offset: str | None) -> str | None:
    m = re.match(r"(\d{4}):(\d{2}):(\d{2}) (\d{2}):(\d{2}):(\d{2})", str(s or ""))
    if not m or m[1] == "0000":
        return None
    return f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}{_tz(offset)}"


def _gps(exif) -> tuple[float | None, float | None]:
    g = exif.get_ifd(0x8825)
    if not g or 2 not in g or 4 not in g:
        return None, None
    try:
        def deg(v, ref):
            d = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
            return -d if str(ref).upper() in ("S", "W") else d
        lat, lon = deg(g[2], g.get(1, "N")), deg(g[4], g.get(3, "E"))
    except (TypeError, ValueError, ZeroDivisionError, IndexError):
        return None, None
    if abs(lat) < 1e-9 and abs(lon) < 1e-9:
        return None, None
    return round(lat, 6), round(lon, 6)


def _xmp_poc(info: dict) -> dict:
    raw = info.get("xmp") or info.get("XML:com.adobe.xmp") or b""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    out = {}
    for key, field in POC.items():
        m = re.search(rf'poc:{key}\s*=\s*"([^"]*)"', text) or re.search(rf"<poc:{key}>([^<]*)</poc:{key}>", text)
        if m:
            out[field] = m[1].strip()
    if "origin" in out:
        out["origin"] = ORIGIN_MAP.get(out["origin"], out["origin"])
    for k in ("received_at", "saved_at", "downloaded_at"):
        if k in out:
            out[k] = _iso_ms(out[k])
    return out


def _iso_ms(s: str) -> str | None:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        h, m = settings.default_tz[1:].split(":")
        sign = 1 if settings.default_tz[0] == "+" else -1
        dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=int(h), minutes=int(m))))
    return dt.isoformat(timespec="milliseconds" if dt.microsecond else "seconds")


def _tzinfo(offset: str) -> timezone:
    sign = 1 if offset[0] == "+" else -1
    h, m = offset[1:].split(":")
    return timezone(sign * timedelta(hours=int(h), minutes=int(m)))


def read_photo_meta(path: Path, im: Image.Image, source: str) -> dict:
    exif = im.getexif()
    ifd = exif.get_ifd(0x8769)
    offset = ifd.get(0x9011) or ifd.get(0x9012) or ifd.get(0x9010)
    t = _exif_time(ifd.get(0x9003) or ifd.get(0x9004), ifd.get(0x9011) or ifd.get(0x9012))
    basis = "taken" if t else None
    if not t and source != "kakao_photo":
        m = CAMERA_NAME.match(path.name)
        if m:
            t, basis = f"{m[1]}-{m[2]}-{m[3]}T{m[4]}:{m[5]}:{m[6]}{settings.default_tz}", "filename"
    if not t:
        t = _exif_time(exif.get(0x0132), ifd.get(0x9010))
        basis = "saved" if t else None
    lat, lon = _gps(exif)
    meta = {"time": t, "time_basis": basis, "lat": lat, "lon": lon, "orientation": exif.get(0x0112, 1),
            "received_at": None, "saved_at": None, "downloaded_at": None, "origin": None}
    meta.update(_xmp_poc(im.info))
    if source == "kakao_photo" and not meta["received_at"]:
        m = KAKAO_MS_NAME.match(path.name)
        if m:
            tz = _tzinfo(_tz(offset))
            meta["received_at"] = datetime.fromtimestamp(int(m[1]) / 1000, tz).isoformat(timespec="milliseconds")
    return meta


def copy_name(pid: str) -> str:
    return pid if pid.lower().endswith((".jpg", ".jpeg")) else pid + ".jpg"


def _save_clean(img: Image.Image, dst: Path, long_edge: int) -> tuple[int, int]:
    im = img
    if max(im.size) > long_edge:
        im = im.copy()
        im.thumbnail((long_edge, long_edge), Image.Resampling.LANCZOS)
    clean = Image.frombytes("RGB", im.size, im.tobytes())  # 픽셀만 새 이미지로 옮긴다
    dst.parent.mkdir(parents=True, exist_ok=True)
    clean.save(dst, "JPEG", quality=90)
    with Image.open(dst) as chk:
        if chk.getexif() or any(k in chk.info for k in ("xmp", "XML:com.adobe.xmp", "exif", "comment", "icc_profile")):
            raise RuntimeError(f"사본에 메타데이터가 남았다: {dst}")
    raw = dst.read_bytes()
    if b"Exif\x00\x00" in raw or b"http://ns.adobe.com/xap" in raw or b"MotionPhoto" in raw:
        raise RuntimeError(f"사본에 메타데이터가 남았다: {dst}")
    return clean.size


def _rgb(im: Image.Image) -> Image.Image:
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        rgb = Image.new("RGB", rgba.size, (255, 255, 255))
        rgb.paste(rgba, mask=rgba.getchannel("A"))
        return rgb
    return im.convert("RGB")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_one(path: Path, root: Path) -> dict:
    pid = path.relative_to(root).as_posix()
    src = source_of(pid)
    row = {"id": pid, "source": src, "badge": BADGE[src], "sha256": sha256(path)}
    ext = path.suffix.lower()
    if ext in VIDEO_EXT:
        return {**row, "kind": "video", "note": "동영상은 이번 범위에서 설명하지 않는다"}
    if ext in DOC_EXT:
        return {**row, "kind": "document", "title": path.name}
    try:
        im = Image.open(path)
    except Exception:
        return {**row, "kind": "other", "note": "열 수 없는 파일"}
    with im:
        if im.format not in IMAGE_FORMATS:
            return {**row, "kind": "other", "note": f"모르는 형식 {im.format}"}
        meta = read_photo_meta(path, im, src)
        im.seek(0)  # MPO: 본 사진(첫 장)만
        up = _rgb(ImageOps.exif_transpose(im))
    name = copy_name(pid)
    mw, mh = _save_clean(up, settings.photos_model / name, MODEL_LONG_EDGE)
    _save_clean(up, settings.photos_screen / name, SCREEN_LONG_EDGE)
    rel = lambda p: p.relative_to(settings.var_dir).as_posix()  # noqa: E731
    return {**row, "kind": "photo", **meta, "width": up.width, "height": up.height,
            "model_width": mw, "model_height": mh,
            "model_copy": rel(settings.photos_model / name), "screen_copy": rel(settings.photos_screen / name)}


def main() -> None:
    ap = argparse.ArgumentParser(description="폰 폴더 수집")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    root = settings.phone_dir
    if not root.is_dir():
        sys.exit(f"폰 폴더가 없다: {root}")
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.relative_to(root).as_posix() not in ANSWER_TABLES)
    print(f"폰 폴더 {root} · 파일 {len(files)}개")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        rows = list(ex.map(lambda p: collect_one(p, root), files))
    settings.var_dir.mkdir(parents=True, exist_ok=True)
    with settings.catalog.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    kinds, basis, src = {}, {}, {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        if r["kind"] == "photo":
            basis[r["time_basis"]] = basis.get(r["time_basis"], 0) + 1
            src[r["source"]] = src.get(r["source"], 0) + 1
    gps = sum(1 for r in rows if r.get("lat") is not None)
    print(f"종류 {kinds} · 시각 기준 {basis} · 출처 {src} · 찍은 곳 있음 {gps}")
    print(f"-> {settings.catalog}")


if __name__ == "__main__":
    main()

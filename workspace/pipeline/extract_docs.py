"""문서 글자 뽑기: 카탈로그의 문서 127건과 문서 사진의 글자를 뽑아 var/docs.jsonl 을 만든다.

`uv run python -m pipeline.extract_docs` (workspace/에서)
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from markitdown import MarkItDown
from pypdf import PdfReader

from app.config import settings
from pipeline.common import latest_by_id, read_jsonl, write_jsonl

sys.stdout.reconfigure(encoding="utf-8")

KST = timezone(timedelta(hours=9))
FORBIDDEN_ATTR_TOKENS = ("키워드=", "선정업체=", "버전=")


def _parse_pdf_date(raw: object) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    if s.startswith("D:"):
        s = s[2:]
    m = re.match(
        r"^(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:([Zz]|[+-])(\d{2})'?(\d{2})'?)?",
        s,
    )
    if not m:
        return None
    yr = int(m.group(1))
    mo = int(m.group(2) or "01")
    dy = int(m.group(3) or "01")
    hr = int(m.group(4) or "00")
    mi = int(m.group(5) or "00")
    sc = int(m.group(6) or "00")
    tz_sign = m.group(7)
    if not tz_sign:
        dt = datetime(yr, mo, dy, hr, mi, sc, tzinfo=KST)
    elif tz_sign.upper() == "Z":
        dt = datetime(yr, mo, dy, hr, mi, sc, tzinfo=timezone.utc).astimezone(KST)
    else:
        sign = 1 if tz_sign == "+" else -1
        off_h = int(m.group(8) or "0")
        off_m = int(m.group(9) or "0")
        tz = timezone(sign * timedelta(hours=off_h, minutes=off_m))
        dt = datetime(yr, mo, dy, hr, mi, sc, tzinfo=tz).astimezone(KST)
    return dt.date().isoformat()


def _parse_iso_to_kst_date(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    m_plain = re.match(r"^(20\d{2}-\d{2}-\d{2})$", s)
    if m_plain:
        return m_plain.group(1)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        else:
            dt = dt.astimezone(KST)
        return dt.date().isoformat()
    except ValueError:
        m = re.search(r"(20\d{2}-\d{2}-\d{2})", s)
        return m.group(1) if m else None


def extract_doc_dates(path: Path) -> tuple[str | None, str | None]:
    """문서 파일에서 작성일(written)과 갱신일(updated, 작성일과 다를 때만)을 읽는다."""
    ext = path.suffix.lower()
    if ext in (".docx", ".xlsx", ".pptx"):
        with zipfile.ZipFile(path, "r") as zf:
            xml_bytes = zf.read("docProps/core.xml")
        root = ET.fromstring(xml_bytes)
        ns = {
            "dc": "http://purl.org/dc/elements/1.1/",
            "dcterms": "http://purl.org/dc/terms/",
        }
        desc_el = root.find("dc:description", ns)
        desc_txt = (desc_el.text or "").strip() if desc_el is not None else ""
        m_w = re.search(r"작성일\s*(20\d{2}-\d{2}-\d{2})", desc_txt)
        written = m_w.group(1) if m_w else _parse_iso_to_kst_date(desc_txt)

        mod_el = root.find("dcterms:modified", ns)
        mod_txt = (mod_el.text or "").strip() if mod_el is not None else ""
        mod_date = _parse_iso_to_kst_date(mod_txt)
        updated = mod_date if (mod_date and written and mod_date != written) else None
        return written, updated

    if ext in (".html", ".htm"):
        txt = path.read_text(encoding="utf-8", errors="replace")
        m_meta = re.search(
            r'<meta\s+[^>]*name=["\']description["\'][^>]*content=["\']([^"\']*)["\']',
            txt,
            re.I,
        ) or re.search(
            r'<meta\s+[^>]*content=["\']([^"\']*)["\'][^>]*name=["\']description["\']',
            txt,
            re.I,
        )
        content = m_meta.group(1).strip() if m_meta else ""
        m_w = re.match(r"^작성일\s*(20\d{2}-\d{2}-\d{2})", content)
        written = m_w.group(1) if m_w else None
        return written, None

    if ext == ".pdf":
        reader = PdfReader(str(path))
        meta = reader.metadata or {}
        written = _parse_pdf_date(meta.get("/CreationDate"))
        mod_date = _parse_pdf_date(meta.get("/ModDate"))
        updated = mod_date if (mod_date and written and mod_date != written) else None
        return written, updated

    return None, None


def extract_one_document(path: Path, doc_id: str, converter: MarkItDown | None = None) -> dict:
    """문서 파일 한 건에서 본문과 작성일·갱신일만 뽑고 정답 속성은 옮기지 않는다."""
    md = converter or MarkItDown()
    body = md.convert(str(path)).text_content or ""
    written, updated = extract_doc_dates(path)
    return {
        "id": doc_id,
        "body": body,
        "written": written,
        "updated": updated,
        "dates": [],
        "amounts": [],
    }


def main() -> None:
    cat = read_jsonl(settings.catalog)
    if not cat:
        sys.exit("카탈로그가 없다. 먼저 pipeline.collect 를 돌린다")

    doc_cat = [r for r in cat if r.get("kind") == "document"]
    if len(doc_cat) != 127:
        sys.exit(f"멈춤: 카탈로그의 문서 수가 127건이 아니다 ({len(doc_cat)}건)")

    md = MarkItDown()
    doc_rows: list[dict] = []
    for c in doc_cat:
        doc_id = c["id"]
        path = settings.phone_dir / doc_id
        if not path.is_file():
            sys.exit(f"멈춤: 문서 파일이 없다 -> {doc_id}")
        row = extract_one_document(path, doc_id, converter=md)
        if not row["body"].strip():
            sys.exit(f"멈춤: 본문이 빈 문서가 있다 -> {doc_id}")
        for tok in FORBIDDEN_ATTR_TOKENS:
            if tok in row["body"]:
                sys.exit(f"멈춤: 문서 본문에 금지 속성 표기({tok})가 들어갔다 -> {doc_id}")
        w = row["written"]
        if not w or not ("2023-03-02" <= w <= "2026-11-03"):
            sys.exit(f"멈춤: 작성일이 없거나 범위(2023-03-02~2026-11-03) 밖이다 -> {doc_id}: {w}")
        doc_rows.append(row)

    updated_cnt = sum(1 for r in doc_rows if r["updated"] is not None)
    if updated_cnt != 15:
        sys.exit(f"멈춤: 갱신일 있는 문서 수가 15건이 아니다 ({updated_cnt}건)")

    desc_map = latest_by_id(read_jsonl(settings.descriptions))
    doc_photo_rows: list[dict] = []
    for c in cat:
        if c.get("kind") != "photo":
            continue
        pid = c["id"]
        d = desc_map.get(pid)
        if d and d.get("photo_type") in ("document", "screenshot"):
            doc_photo_rows.append({
                "id": pid,
                "body": d.get("text_in_photo") or "",
                "written": None,
                "updated": None,
                "dates": [],
                "amounts": [],
            })

    all_rows = doc_rows + doc_photo_rows
    write_jsonl(settings.docs, all_rows)
    print(f"문서 {len(doc_rows)}건 · 문서 사진 {len(doc_photo_rows)}장 · 갱신일 있는 문서 {updated_cnt}건")


if __name__ == "__main__":
    main()

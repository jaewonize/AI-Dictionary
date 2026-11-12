#!/usr/bin/env python3
"""갤러리 목업에 채울 사진을 Wikimedia Commons에서 받는다.

CC0(저작권 제한 없음) 사진만 고른다. 출처는 photos/CREDITS.tsv 에 남긴다.
지금 photos/ 에는 이렇게 받은 363장 가운데 손으로 고른 223장만 남겨 두었다
(사람 얼굴 · 상표 · 그림 · 제품 사진을 뺐다). 다시 돌리면 고르기 전 상태로 전부 받아 온다.
팀에서 만든 사진으로 바꾸려면 photos/<분류>/ 안의 파일을 교체하고 build.py 를 다시 돌리면 된다.

  python3 fetch_photos.py
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "photos")
UA = "gallery-mock-fetch/0.1 (python-urllib)"
API = "https://commons.wikimedia.org/w/api.php"
THUMB_W = 330          # Commons 표준 썸네일 폭
PER_QUERY = 30

# 분류 → 검색어. 개인 사진첩처럼 보이도록 일상 소재 위주로 고른다.
QUERIES = {
    "cafe":     ["cafe interior table window", "coffee shop interior", "latte art cup", "cappuccino on table"],
    "food":     ["restaurant meal plate", "korean food", "pasta dish", "cake slice dessert", "ramen bowl", "pizza on table"],
    "sea":      ["beach sea waves", "seaside coast view"],
    "mountain": ["mountain hiking trail", "mountain landscape view"],
    "city":     ["city street view", "seoul street"],
    "night":    ["city night lights", "night street lights"],
    "pet":      ["dog portrait pet", "cat sitting pet"],
    "flower":   ["flowers garden closeup", "cherry blossom"],
    "museum":   ["art museum gallery interior", "exhibition paintings wall"],
    "sky":      ["sunset sky clouds"],
    "park":     ["park autumn leaves", "park trees path"],
}


def api(params):
    params = dict(params, format="json")
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 — 요청이 몰리면 429가 온다. 쉬었다가 다시 한다
            err = e
            time.sleep(4 * (attempt + 1))
    raise err


def search(query):
    data = api({
        "action": "query",
        "generator": "search",
        "gsrnamespace": 6,
        "gsrlimit": PER_QUERY,
        "gsrsearch": f"{query} haslicense:unrestricted filetype:bitmap",
        "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": THUMB_W,
        "iiextmetadatafilter": "LicenseShortName|DateTimeOriginal|Artist",
    })
    pages = sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        if ii.get("mime") != "image/jpeg" or lic != "CC0":
            continue
        w, h = ii.get("width", 0), ii.get("height", 0)
        if min(w, h) < 900 or max(w, h) / max(1, min(w, h)) > 1.9:
            continue
        year = re.search(r"(19|20)\d\d", (meta.get("DateTimeOriginal") or {}).get("value", ""))
        if year and int(year.group(0)) < 2012:
            continue
        artist = re.sub(r"<[^>]+>", "", (meta.get("Artist") or {}).get("value", "")).strip()
        out.append({
            "title": p["title"], "thumb": ii.get("thumburl"), "page": ii.get("descriptionurl"),
            "w": w, "h": h, "license": lic, "artist": artist[:80],
        })
    return out


def download(job):
    url, path = job
    if os.path.exists(path):
        return True
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            with open(path, "wb") as f:
                f.write(data)
            time.sleep(0.25)
            return True
        except Exception as e:  # noqa: BLE001
            time.sleep(5 * (attempt + 1))
            err = e
    print("  실패:", url, err, file=sys.stderr)
    return False


def main():
    os.makedirs(OUT, exist_ok=True)
    seen, credits, jobs = set(), [], []
    for cat, queries in QUERIES.items():
        os.makedirs(os.path.join(OUT, cat), exist_ok=True)
        n = 0
        for q in queries:
            try:
                items = search(q)
            except Exception as e:  # noqa: BLE001
                print(f"검색 실패 {q}: {e}", file=sys.stderr)
                continue
            for it in items:
                if it["title"] in seen or not it["thumb"]:
                    continue
                seen.add(it["title"])
                n += 1
                name = f"{cat}_{n:03d}.jpg"
                jobs.append((it["thumb"], os.path.join(OUT, cat, name)))
                credits.append("\t".join([f"{cat}/{name}", it["license"], it["artist"], it["page"]]))
            time.sleep(1.0)
        print(f"{cat}: {n}장")
    with ThreadPoolExecutor(2) as ex:
        ok = sum(ex.map(download, jobs))
    with open(os.path.join(OUT, "CREDITS.tsv"), "w", encoding="utf-8") as f:
        f.write("file\tlicense\tartist\tsource\n" + "\n".join(credits) + "\n")
    print(f"받은 사진 {ok}/{len(jobs)}장 → {OUT}")


if __name__ == "__main__":
    main()

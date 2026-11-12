#!/usr/bin/env python3
"""갤러리 목업을 조립한다.

  python3 build.py [--deck ../AI_Dictionary_hackathon.html]

만드는 파일
  gallery_demo.html              폰 화면만 (확인 · 녹화용)
  <덱>_A_slide01.html            A안: 01 슬라이드 안에 폰을 넣은 판
  <덱>_B_new_slide.html          B안: 01 바로 뒤에 새 슬라이드로 넣은 판

원본 덱은 건드리지 않는다. 덱이 바뀌면 --deck 으로 새 파일을 주고 다시 돌리면 된다.
사진을 바꾸려면 photos/<분류>/ 안의 파일을 교체한다 (이름은 <분류>_<번호>.jpg).
"""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request

from PIL import Image, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
PHOTOS = os.path.join(HERE, "photos")
COLS, CELL = 16, 128                 # 썸네일 한 장에 모아 담는 격자
HERO = "cafe_004"                    # 크게 열어 보는 사진 (photos/_large/ 에 큰 판이 있어야 한다)
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def read(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


def data_uri(mime, raw):
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def jpeg(im, q):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
    return buf.getvalue()


def pack_photos():
    names, cats, files = [], {}, []
    for cat in sorted(d for d in os.listdir(PHOTOS) if os.path.isdir(os.path.join(PHOTOS, d)) and not d.startswith("_")):
        fs = sorted(f for f in os.listdir(os.path.join(PHOTOS, cat)) if f.lower().endswith((".jpg", ".jpeg", ".png")))
        cats[cat] = [len(names), len(fs)]
        names += [os.path.splitext(f)[0] for f in fs]
        files += [os.path.join(PHOTOS, cat, f) for f in fs]
    rows = -(-len(files) // COLS)
    sheet = Image.new("RGB", (COLS * CELL, rows * CELL), "#e9e9ee")
    for i, f in enumerate(files):
        thumb = ImageOps.fit(ImageOps.exif_transpose(Image.open(f)).convert("RGB"), (CELL, CELL), Image.LANCZOS)
        sheet.paste(thumb, ((i % COLS) * CELL, (i // COLS) * CELL))
    hero = ImageOps.exif_transpose(Image.open(os.path.join(PHOTOS, "_large", HERO + ".jpg"))).convert("RGB")
    hero.thumbnail((900, 900), Image.LANCZOS)
    manifest = {"cols": COLS, "rows": rows, "names": names, "cats": cats, "heroAspect": round(hero.width / hero.height, 4)}
    return manifest, jpeg(sheet, 72), jpeg(hero, 82)


def font_face(text):
    """폰 화면과 새 슬라이드에 쓰는 글자만 담은 Noto Sans KR. 받은 것은 src/.font_cache.json 에 둔다."""
    chars = "".join(sorted(set(text) | {chr(c) for c in range(0x20, 0x7F)}))
    cache_path = os.path.join(SRC, ".font_cache.json")
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}
    k = hashlib.sha1(chars.encode()).hexdigest()
    if k not in cache:
        try:
            url = "https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@100..900&text=" + urllib.parse.quote(chars)
            css = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": BROWSER_UA}), timeout=30).read().decode()
            src = re.search(r"url\((https://[^)]+)\)", css).group(1)
            raw = urllib.request.urlopen(urllib.request.Request(src, headers={"User-Agent": BROWSER_UA}), timeout=30).read()
            cache = {k: base64.b64encode(raw).decode()}
            json.dump(cache, open(cache_path, "w", encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"! 글꼴을 받지 못했다 ({e}). 기기에 깔린 글꼴로 대신 보인다.", file=sys.stderr)
            return ""
    return ('@font-face{font-family:"GP Sans";font-weight:100 900;font-style:normal;font-display:block;'
            f'src:url(data:font/woff2;base64,{cache[k]}) format("woff2")}}\n')


def deck_hangul(deck):
    body = re.sub(r"<script[\s\S]*?</script>", "", deck[deck.find("<body"):])
    return {c for c in re.sub(r"<[^>]+>", " ", body) if "가" <= c <= "힣"}


def slide01(deck):
    """01 슬라이드의 <section> 범위."""
    at = deck.find("<i>01</i>")
    if at < 0:
        sys.exit("덱에서 01 슬라이드를 찾지 못했다 (<i>01</i>).")
    return deck.rfind("<section", 0, at), deck.find("</section>", at) + len("</section>")


def inject(deck, css, js):
    return deck.replace("</style>", "\n" + css + "</style>", 1).replace("</body>", "<script>\n" + js + "</script>\n</body>", 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deck", default=os.path.join(HERE, "..", "AI_Dictionary_hackathon.html"))
    args = ap.parse_args()

    manifest, sprite, hero = pack_photos()
    gallery_html, slide_b_html = read("gallery.html"), read("slide_b.html")
    js = read("gallery.js").replace("/*GP_MANIFEST*/null", json.dumps(manifest, ensure_ascii=False, separators=(",", ":")))
    used = "".join(c for c in gallery_html + js + slide_b_html if ord(c) > 0x7F)
    css = (font_face(used)
           + f'.gp{{--gp-sprite:url("{data_uri("image/jpeg", sprite)}");--gp-hero:url("{data_uri("image/jpeg", hero)}");'
           + f'--gp-cols:{manifest["cols"]};--gp-rows:{manifest["rows"]}}}\n'
           + read("gallery.css"))

    demo = ('<!DOCTYPE html>\n<html lang="ko">\n<head>\n<meta charset="UTF-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>갤러리에서 직접 찾기</title>\n<style>\n'
            '*{margin:0;padding:0;box-sizing:border-box}html,body{height:100%}\n'
            'body{display:grid;place-items:center;background:#f5f5f7;overflow:hidden}\n'
            + css + '</style>\n</head>\n<body>\n' + gallery_html +
            # ?gs=1 로 크기를 고정하고 ?bg=fff 로 바탕색을 바꿀 수 있다 (녹화용)
            '<script>(()=>{const g=document.querySelector("[data-gp]"),q=new URLSearchParams(location.search);'
            'if(q.get("bg"))document.body.style.background="#"+q.get("bg");'
            'const fit=()=>g.style.setProperty("--gs",+q.get("gs")||Math.min((innerHeight-48)/798,(innerWidth-48)/378));'
            'fit();addEventListener("resize",fit)})();</script>\n<script>\n' + js + '</script>\n</body>\n</html>\n')
    outs = {os.path.join(HERE, "gallery_demo.html"): demo}

    deck_path = os.path.abspath(args.deck)
    if os.path.exists(deck_path):
        deck = open(deck_path, encoding="utf-8").read()
        stem = os.path.splitext(deck_path)[0]
        a, b = slide01(deck)

        # A안 — 01 슬라이드에 넣는다
        sec = deck[a:b].replace('class="slide"', 'class="slide gpA"', 1)
        sec = sec[:-len("</section>")] + '  <div class="gpA__slot rv" style="--i:5">\n' + gallery_html + "  </div>\n  </section>"
        outs[stem + "_A_slide01.html"] = inject(deck[:a] + sec + deck[b:], css + read("slide_a.css"), js)

        # B안 — 01 뒤에 새 슬라이드. 번호는 01을 이어 쓰므로 뒤 슬라이드 번호는 그대로다
        counted = "const counted = s => !isCover(s) && !isAppendix(s);"
        if counted not in deck:
            print("! 덱 스크립트의 번호 세는 줄이 바뀌었다. B안의 하단 번호가 하나씩 밀릴 수 있다.", file=sys.stderr)
        deck_b = deck.replace(counted, counted[:-1] + " && !s.hasAttribute('data-gp-cont');")
        a, b = slide01(deck_b)
        deck_b = deck_b[:b] + "\n" + slide_b_html.replace("{{GALLERY}}", "\n" + gallery_html + "    ").rstrip("\n") + deck_b[b:]
        outs[stem + "_B_new_slide.html"] = inject(deck_b, css + read("slide_b.css"), js)

        missing = sorted({c for c in re.sub(r"<[^>]+>", " ", slide_b_html) if "가" <= c <= "힣"} - deck_hangul(deck))
        if missing:
            print("  B안 문구 중 덱 글꼴에 없는 글자(다른 글꼴로 보인다):", "".join(missing))
    else:
        print(f"! 덱이 없다: {deck_path} — gallery_demo.html 만 만든다.", file=sys.stderr)

    for path, text in outs.items():
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"  {os.path.relpath(path, HERE)}  {len(text.encode()) / 1024:.0f} KB")
    print(f"사진 {len(manifest['names'])}장 · 썸네일 묶음 {len(sprite) / 1024:.0f} KB")


if __name__ == "__main__":
    main()

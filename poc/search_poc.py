#!/usr/bin/env python3
"""
AI ( ) Dictionary — 검색 품질 PoC

제안서 08장 검증 1번(최고 성능 모델로 품질 확인)을 작은 규모로 돌려본다.
저장소의 합성데이터 사진·문서를 실제로 해석해, 질의 8건이 몇 건이나
정답을 찾아내는지 센다.

  1) index   사진 1장당 1회만 해석해 설명을 저장한다      (돈이 드는 단계)
  2) ask     질의를 주면 저장된 설명과 대조해 찾는다       (거의 공짜)
  3) eval    질의 8건을 돌려 데모의 정답과 비교한다

사용법
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 poc/search_poc.py index
  python3 poc/search_poc.py ask "창밖으로 바다가 보이는 카페에서 찍은 사진"
  python3 poc/search_poc.py eval
"""

import base64, json, os, re, sys, time
from pathlib import Path

ROOT  = Path(__file__).resolve().parent.parent
INDEX = Path(__file__).resolve().parent / "index.json"
DEMO  = ROOT / "AI_Dictionary_demo.html"
MODEL = "claude-opus-5-5"

FOLDERS = ["01_cafe_photo", "02_shoe_photo", "03_quotation", "04_meeting_documents",
           "05_dior_photos", "06_wine_photos", "07_party_photos", "08_brand_documents"]
MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

# 사진 1장을 해석할 때 쓰는 지시. 저장될 설명의 품질이 검색 품질을 좌우한다.
DESCRIBE = """이 이미지를 나중에 검색할 수 있도록 설명해라.

다음을 빠짐없이 담되, 보이지 않는 것은 지어내지 마라.
- 무엇이 찍혔나: 사람, 사물, 장면의 구성, 배경
- 공간의 구조: 실내/실외, 창이 있다면 창밖에 무엇이 보이는지
- 글자: 간판, 라벨, 문서의 제목과 항목, 금액이 있으면 숫자 그대로
- 색과 재질: 옷차림, 물건의 색, 표면

한국어로 쓰고, 번호나 제목 없이 줄글 5~8문장으로만 답하라."""


# ── 공통 ────────────────────────────────────────────────────────────────
def client():
    try:
        import anthropic
    except ImportError:
        sys.exit("anthropic 패키지가 없다.  python3 -m pip install anthropic")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY 환경변수가 비어 있다.\n"
                 "  export ANTHROPIC_API_KEY=sk-ant-...")
    return anthropic.Anthropic()


def images():
    """저장소의 합성데이터 이미지를 전부 모은다."""
    out = []
    for folder in FOLDERS:
        d = ROOT / folder
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if p.suffix.lower() in MEDIA:
                out.append(p)
    return out


def ask_claude(c, blocks, max_tokens=1200):
    r = c.messages.create(model=MODEL, max_tokens=max_tokens,
                          messages=[{"role": "user", "content": blocks}])
    return "".join(b.text for b in r.content if b.type == "text"), r.usage


def img_block(path):
    return {"type": "image", "source": {
        "type": "base64", "media_type": MEDIA[path.suffix.lower()],
        "data": base64.standard_b64encode(path.read_bytes()).decode()}}


def load_index():
    if not INDEX.exists():
        sys.exit("색인이 없다. 먼저 'python3 poc/search_poc.py index' 를 돌려라.")
    return json.loads(INDEX.read_text(encoding="utf-8"))


# ── 1) 색인 ─────────────────────────────────────────────────────────────
def cmd_index():
    files = images()
    idx = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    todo = [p for p in files if str(p.relative_to(ROOT)) not in idx]

    print(f"이미지 {len(files)}장 중 {len(todo)}장이 아직 해석되지 않았다.")
    if not todo:
        print("할 일이 없다.")
        return
    print(f"예상 비용: 대략 ${len(todo) * 0.012:.2f} (1장당 1센트 안팎)")
    if input("진행할까? [y/N] ").strip().lower() != "y":
        return

    c = client()
    tin = tout = 0
    for i, p in enumerate(todo, 1):
        rel = str(p.relative_to(ROOT))
        try:
            text, usage = ask_claude(c, [img_block(p), {"type": "text", "text": DESCRIBE}])
        except Exception as e:
            print(f"  [{i}/{len(todo)}] {rel} — 실패: {e}")
            continue
        idx[rel] = {"folder": p.parent.name, "file": p.name, "desc": text.strip()}
        tin += usage.input_tokens; tout += usage.output_tokens
        print(f"  [{i}/{len(todo)}] {rel}\n      {text.strip()[:76]}…")
        INDEX.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        time.sleep(0.3)

    print(f"\n색인 {len(idx)}장 → {INDEX}")
    print(f"이번에 쓴 토큰: 입력 {tin:,} · 출력 {tout:,}  (약 ${tin/1e6*4 + tout/1e6*20:.2f})")


# ── 2) 검색 ─────────────────────────────────────────────────────────────
def search(c, query, idx, shortlist=8, verbose=True):
    """저장된 설명 전체와 질의를 대조해 후보를 고르고, 상위 후보만 이미지로 다시 확인한다."""
    keys = list(idx.keys())
    catalog = "\n".join(f"[{i}] {idx[k]['file']} — {idx[k]['desc']}" for i, k in enumerate(keys))

    prompt = (f"""아래는 어떤 사람의 사진과 문서 {len(keys)}건을 미리 읽어 적어둔 설명이다.

{catalog}

이 사람이 이렇게 찾는다: "{query}"

조건에 맞는 것만 골라라. 애매하면 빼지 말고 넣되 순위를 낮춰라.
번호만 쉼표로 구분해 많아야 {shortlist}개, 정확도 높은 순으로 한 줄로 답하라.
맞는 게 없으면 '없음'이라고만 답하라.""")

    text, _ = ask_claude(c, [{"type": "text", "text": prompt}], max_tokens=300)
    picked = [keys[int(n)] for n in re.findall(r"\d+", text) if int(n) < len(keys)][:shortlist]
    if verbose:
        print(f"  1차 후보 {len(picked)}건: {', '.join(idx[k]['file'] for k in picked)}")
    if not picked:
        return []

    # 상위 후보만 이미지를 다시 보고 확인한다 (제안서 07장 3단계)
    blocks = []
    for i, k in enumerate(picked):
        blocks.append({"type": "text", "text": f"[{i}] {idx[k]['file']}"})
        blocks.append(img_block(ROOT / k))
    blocks.append({"type": "text", "text":
        f'이 이미지들 중 "{query}" 에 실제로 맞는 것만 남겨라.\n'
        f"번호만 쉼표로 구분해 정확도 높은 순으로 한 줄로 답하라. 없으면 '없음'."})

    text2, _ = ask_claude(c, blocks, max_tokens=300)
    final = [picked[int(n)] for n in re.findall(r"\d+", text2) if int(n) < len(picked)]
    if verbose:
        dropped = [idx[k]['file'] for k in picked if k not in final]
        if dropped:
            print(f"  이미지 재확인에서 제외: {', '.join(dropped)}")
    return final


def cmd_ask(query):
    idx = load_index()
    c = client()
    print(f'질의: "{query}"  (색인 {len(idx)}건)')
    hits = search(c, query, idx)
    print(f"\n찾은 것 {len(hits)}건")
    for k in hits:
        print(f"  · {k}\n      {idx[k]['desc'][:76]}…")


# ── 3) 채점 ─────────────────────────────────────────────────────────────
def demo_answers():
    """데모 화면이 보여주는 결과를 정답으로 삼는다."""
    html = DEMO.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'\n  q:"([^"]+)"', html):
        seg = html[m.end(): html.find("\n},", m.end())]
        want = [s for s in re.findall(r'src:"([^"]+)"', seg) if "/" in s]
        if want:
            out.append((m.group(1), want))
    return out


def cmd_eval():
    idx = load_index()
    cases = [(q, w) for q, w in demo_answers() if all(k in idx for k in w)]
    if not cases:
        sys.exit("데모에서 정답을 뽑지 못했다. 먼저 index 를 돌렸는지 확인하라.")

    print(f"질의 {len(cases)}건을 채점한다. 색인 {len(idx)}건.")
    print(f"예상 비용: 대략 ${len(cases)*0.09:.2f}\n")
    if input("진행할까? [y/N] ").strip().lower() != "y":
        return

    c = client()
    rows = []
    for q, want in cases:
        print(f'── "{q}"')
        got = search(c, q, idx, shortlist=max(8, len(want)))
        hit = [k for k in want if k in got]
        extra = [k for k in got if k not in want]
        rows.append((q, len(want), len(hit), len(extra)))
        print(f"  정답 {len(want)}건 중 {len(hit)}건 적중 · 정답 밖 {len(extra)}건\n")

    print("=" * 66)
    print(f"{'질의':<30}{'정답':>5}{'적중':>5}{'놓침':>5}{'오검출':>7}")
    print("-" * 66)
    tw = th = 0
    for q, w, h, e in rows:
        print(f"{q[:28]:<30}{w:>5}{h:>5}{w-h:>5}{e:>7}")
        tw += w; th += h
    print("-" * 66)
    print(f"{'합계':<30}{tw:>5}{th:>5}{tw-th:>5}")
    print(f"\n재현율 {th}/{tw} = {th/tw*100:.0f}%")


# ── ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "index":  cmd_index()
    elif cmd == "ask":  cmd_ask(" ".join(sys.argv[2:]) or sys.exit('질의를 적어라.'))
    elif cmd == "eval": cmd_eval()
    else: print(__doc__)

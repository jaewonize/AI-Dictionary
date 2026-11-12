#!/usr/bin/env python3
"""gallery_demo.html 을 프레임 단위로 찍어 10초 영상으로 만든다.

  python3 record.py                      # gallery_search_fail.mp4 (1040x1920, 30fps)
  python3 record.py --bg f5f5f7 --fps 60
  python3 record.py --frames-only DIR    # ffmpeg 없이 PNG만 남긴다

헤드리스 크롬으로 ?gpt=<초> 화면을 한 장씩 찍고 ffmpeg으로 묶는다.
화면이 시간 t 하나로 그려지므로 몇 번을 찍어도 같은 영상이 나오고, 처음과 끝이 이어져 반복 재생해도 끊기지 않는다.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "gallery_demo.html")
SECONDS = 10
W, H = 520, 960            # 폰(378x798) 둘레에 그림자가 잘리지 않을 만큼 여백을 둔다


def find(names):
    return next((p for p in (shutil.which(n) for n in names) if p), None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--scale", type=int, default=2, help="픽셀 배율 (2면 1040x1920)")
    ap.add_argument("--bg", default="ffffff", help="바탕색 (# 없이)")
    ap.add_argument("--out", default=os.path.join(HERE, "gallery_search_fail.mp4"))
    ap.add_argument("--frames-only", metavar="DIR", help="영상으로 묶지 않고 PNG만 이 폴더에 남긴다")
    args = ap.parse_args()

    chrome = find(["google-chrome", "chromium", "chromium-browser"])
    ffmpeg = find(["ffmpeg"])
    if not chrome:
        sys.exit("크롬을 찾지 못했다.")
    if not ffmpeg and not args.frames_only:
        sys.exit("ffmpeg이 없다. 설치한 뒤 다시 돌리거나 --frames-only DIR 로 프레임만 받는다.")
    if not os.path.exists(DEMO):
        sys.exit("gallery_demo.html 이 없다. 먼저 python3 build.py 를 돌린다.")

    work = tempfile.mkdtemp(prefix="gp-rec-")
    frames = os.path.abspath(args.frames_only) if args.frames_only else os.path.join(work, "frames")
    os.makedirs(frames, exist_ok=True)
    total = SECONDS * args.fps

    def shoot(job):
        worker, ids = job
        profile = os.path.join(work, f"profile-{worker}")
        for i in ids:
            url = f"file://{DEMO}?gs=1&bg={args.bg}&gpt={i / args.fps:.4f}"
            subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                            f"--user-data-dir={profile}", f"--force-device-scale-factor={args.scale}",
                            f"--window-size={W},{H}", "--virtual-time-budget=1000",
                            f"--screenshot={os.path.join(frames, f'f_{i:04d}.png')}", url],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return len(ids)

    workers = 4
    with ThreadPoolExecutor(workers) as ex:
        done = sum(ex.map(shoot, [(w, range(w, total, workers)) for w in range(workers)]))
    missing = [i for i in range(total) if not os.path.exists(os.path.join(frames, f"f_{i:04d}.png"))]
    if missing:
        sys.exit(f"프레임 {len(missing)}장을 찍지 못했다 (예: {missing[:5]}).")
    print(f"프레임 {done}장 → {frames}")

    if not args.frames_only:
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(args.fps), "-i", os.path.join(frames, "f_%04d.png"),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart", args.out], check=True)
        print(f"영상 → {args.out}  ({os.path.getsize(args.out) / 1024:.0f} KB)")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import subprocess, sys, os
from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = HERE                              # images/src -- the .html sources live next to this script
OUT = os.path.dirname(HERE)             # images/     -- rendered .png output
CHROME = "google-chrome"
SCALE = 2            # device scale factor -> crisp 2x output
WIN_W = 1500
WIN_H = 2800

def render(name, win_h=WIN_H):
    html = f"file://{SRC}/{name}.html"
    raw = f"{SRC}/{name}.raw.png"
    final = f"{OUT}/{name}.png"
    cmd = [
        CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
        "--hide-scrollbars", "--default-background-color=ffffffff",
        f"--force-device-scale-factor={SCALE}",
        f"--window-size={WIN_W},{win_h}",
        f"--screenshot={raw}", html,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(raw):
        print(f"[FAIL] {name}\n{r.stderr[-800:]}"); return
    im = Image.open(raw).convert("RGB")
    # trim uniform white background
    bg = Image.new("RGB", im.size, (255, 255, 255))
    diff = ImageChops.difference(im, bg)
    bbox = diff.getbbox()
    if bbox:
        pad = 28  # 2x -> ~14px logical padding
        l, t, r2, b = bbox
        l = max(0, l - pad); t = max(0, t - pad)
        r2 = min(im.width, r2 + pad); b = min(im.height, b + pad)
        im = im.crop((l, t, r2, b))
    im.save(final)
    os.remove(raw)
    print(f"[ok] {final}  {im.width}x{im.height}")

if __name__ == "__main__":
    names = sys.argv[1:] or ["architecture", "lifecycle", "dm-flow"]
    for n in names:
        render(n)

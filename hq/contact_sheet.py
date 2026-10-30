"""複数の画像を格子状に並べた一覧画像（コンタクトシート）を作る。各画像の上に見出しを入れる。

実行方法:
  python hq/contact_sheet.py --out sheet.png --cols 4 --width 1600 a.png:正面 b.png:斜め ...
"""

import argparse
import sys

from PIL import Image, ImageDraw


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--cols", type=int, default=4)
    p.add_argument("--width", type=int, default=1600)
    p.add_argument("images", nargs="+", help="path[:見出し]")
    return p.parse_args(argv)


def make_sheet(items, out, cols=4, width=1600):
    cols = min(cols, len(items))
    cell_w = width // cols
    cells = []
    for item in items:
        path, _, label = item.partition(":")
        im = Image.open(path).convert("RGB")
        im = im.resize((cell_w, int(im.height * cell_w / im.width)))
        cells.append((im, label))
    cell_h = max(im.height for im, _ in cells) + 28
    rows = (len(cells) + cols - 1) // cols
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), (40, 41, 44))
    draw = ImageDraw.Draw(sheet)
    for k, (im, label) in enumerate(cells):
        x, y = (k % cols) * cell_w, (k // cols) * cell_h
        sheet.paste(im, (x, y + 28))
        draw.text((x + 8, y + 6), label, fill=(230, 230, 230))
    sheet.save(out)
    return out


if __name__ == "__main__":
    a = parse_args()
    make_sheet(a.images, a.out, a.cols, a.width)

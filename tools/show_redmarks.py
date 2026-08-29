# -*- coding: utf-8 -*-
"""把掃描件上的紅筆標記框出來，存成圖檔供肉眼確認。

承辦人用紅筆在樣本上標出了每一種表格要抓哪些欄位。這支工具把程式偵測到的
標記畫出來，用來檢查偵測參數對不對 —— 正式的樣板編輯器會拿同一套結果
當作欄位候選框，讓人確認並命名。

用法：

    python tools/show_redmarks.py 樣板資料夾 掃描件.pdf --page 1 --out 結果.png
"""

import argparse
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import layout, redmarks, render  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="框出掃描件上的紅筆標記",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("store", help="樣板資料夾")
    parser.add_argument("pdf", help="掃描件 PDF")
    parser.add_argument("--page", type=int, default=1, help="第幾頁（從 1 起算）")
    parser.add_argument("--out", default="redmarks.png", help="輸出圖檔")
    parser.add_argument("--code", help="表格代號；省略就自動判斷")
    args = parser.parse_args()

    templates = layout.TemplateSet.load(args.store)
    gray = render.render(args.pdf, args.page - 1, dpi=render.CLASSIFY_DPI)
    code, role, rotation, inliers, _ = templates.classify(gray)
    code = args.code or code
    if not code:
        raise SystemExit("認不出這是哪一種表格，請用 --code 指定")
    print("表格 %s（%s，內點 %d）" % (code, role, inliers))

    scan = render.rotate(render.render(args.pdf, args.page - 1, dpi=render.FULL_DPI, gray=False),
                         rotation)
    base_path = os.path.join(args.store, code, "base.png")
    base = cv2.imread(base_path, cv2.IMREAD_COLOR) if os.path.isfile(base_path) else None
    if base is None:
        print("找不到底圖 %s —— 粉紅色紙的表格沒有底圖會把整張紙判成紅筆" % base_path)

    marks = redmarks.find(scan, base)
    counts = {}
    for kind, *_ in marks:
        counts[kind] = counts.get(kind, 0) + 1
    print("找到 %d 個標記 %s" % (len(marks), counts))
    for kind, x, y, w, h in marks:
        print("  %-6s 位置(%4d,%4d) 大小 %3dx%-3d" % (kind, x, y, w, h))

    cv2.imwrite(args.out, redmarks.draw(scan, marks))
    print("已存檔: %s（綠框=勾號，藍框=框選區域）" % args.out)


if __name__ == "__main__":
    main()

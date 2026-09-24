# -*- coding: utf-8 -*-
"""替一種表格做出底圖，並檢查它對不對得上。

底圖是「只有印刷版面、沒有手寫內容」的參考影像，用來從掃描件上把版面減掉。
兩種來源：

    --blank 空白原稿.pdf
        自己印的表格（F、G）直接用空白表格。

    --compose 掃描件.pdf...
        其他單位送來的影印本（C、D）。原版空白表格對不上影印件，
        改用多份影印件對齊後取中位數 —— 手寫每份都不同會被濾掉，
        印刷每份都相同會留下來。至少要三份。

做完會拿底圖去跟每一份掃描件對一次，把覆蓋率印出來。
覆蓋率是「底圖的印刷筆畫有多少比例落在掃描件的墨跡上」，越高代表對得越準。

用法：

    python tools/build_base_image.py 樣板資料夾 --code F --blank 空白原稿.pdf
    python tools/build_base_image.py 樣板資料夾 --code D --compose D.pdf --check D.pdf
"""

import argparse
import glob
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import baseimage, layout, render, resources  # noqa: E402


def front_pages(paths, templates, code):
    """挑出這批 PDF 裡屬於指定表格、而且是正面的頁面。"""
    pages = layout.classify_pages(paths, templates)
    chosen = [p for p in pages if p.code == code and p.role == layout.FRONT]
    return chosen


def load(page, dpi, gray=False):
    """把某一頁算成影像。底圖一律用彩色 —— 紅筆偵測需要色彩資訊。"""
    return render.rotate(render.render(page.source, page.index, dpi=dpi, gray=gray),
                         page.rotation)


def main():
    parser = argparse.ArgumentParser(description="替一種表格做出底圖並檢查對位品質",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("store", help="樣板資料夾")
    parser.add_argument("--code", required=True, help="表格代號")
    parser.add_argument("--blank", help="空白原稿 PDF（自己印的表格用這個）")
    parser.add_argument("--blank-page", type=int, default=1)
    parser.add_argument("--compose", nargs="+", help="要合成的掃描件（影印本用這個）")
    parser.add_argument("--check", nargs="+", help="拿來檢查對位品質的掃描件")
    parser.add_argument("--dpi", type=int, default=render.FULL_DPI)
    args = parser.parse_args()

    if bool(args.blank) == bool(args.compose):
        raise SystemExit("--blank 與 --compose 請擇一")

    templates = layout.TemplateSet.load(args.store)
    folder = os.path.join(args.store, args.code)
    os.makedirs(folder, exist_ok=True)

    if args.blank:
        base = render.render(args.blank, args.blank_page - 1, dpi=args.dpi, gray=False)
        print("底圖來源: 空白原稿 %s 第 %d 頁" % (os.path.basename(args.blank), args.blank_page))
    else:
        pages = front_pages(args.compose, templates, args.code)
        if len(pages) < baseimage.MIN_SAMPLES:
            raise SystemExit("只找到 %d 份 %s 的正面，至少要 %d 份才能合成"
                             % (len(pages), args.code, baseimage.MIN_SAMPLES))
        print("底圖來源: %d 份掃描件合成" % len(pages))
        for page in pages:
            print("    %s 第 %d 頁" % (os.path.basename(page.source), page.index + 1))
        samples = [load(page, args.dpi) for page in pages]
        base, weak = baseimage.compose(samples)
        for index in weak:
            print("    ⚠ 第 %d 份對不齊，未納入合成" % (index + 1))

    target = os.path.join(folder, "base.png")
    resources.imwrite(target, base)
    print("底圖已存檔: %s  (%dx%d)" % (target, base.shape[1], base.shape[0]))

    checks = args.check or args.compose
    if not checks:
        return 0

    print()
    print("對位品質檢查")
    print("-" * 58)
    pages = front_pages(checks, templates, args.code)
    worst = 1.0
    for page in pages:
        scan = load(page, args.dpi)
        aligned, inliers = baseimage.align(base, scan)
        if aligned is None:
            worst = 0.0
            print("  %-24s p%-3d 對不上，找不到足夠的共同特徵點"
                  % (os.path.basename(page.source), page.index + 1))
            continue
        value = baseimage.coverage(aligned, scan)
        worst = min(worst, value)
        print("  %-24s p%-3d 覆蓋率 %.3f  內點 %-5d %s"
              % (os.path.basename(page.source), page.index + 1, value, inliers,
                 baseimage.verdict(value)))

    print("-" * 58)
    print("最差 %.3f — %s" % (worst, baseimage.verdict(worst)))
    return 0 if worst >= baseimage.USABLE_COVERAGE else 2


if __name__ == "__main__":
    sys.exit(main())

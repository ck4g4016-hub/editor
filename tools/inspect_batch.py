# -*- coding: utf-8 -*-
"""看一批掃描 PDF 會被切成幾件、每一頁被認成什麼。

實際作業時一個 PDF 裡會混著各種表格、沒有順序，所以程式得逐頁自己認。
切分錯會讓整件資料張冠李戴，而且從輸出的表格上完全看不出來，
所以正式的複核介面一定會把這個結果先攤給人確認。這支 CLI 是它的前身。

用法：

    python tools/inspect_batch.py 樣板資料夾 掃描檔資料夾或PDF...
"""

import argparse
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import layout  # noqa: E402


def collect(targets):
    paths = []
    for target in targets:
        if os.path.isdir(target):
            paths.extend(sorted(glob.glob(os.path.join(target, "*.pdf"))))
        else:
            paths.append(target)
    return paths


def main():
    parser = argparse.ArgumentParser(description="檢視一批掃描 PDF 的分類與切分結果",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("store", help="樣板資料夾")
    parser.add_argument("targets", nargs="+", help="掃描檔資料夾或個別 PDF")
    parser.add_argument("--verbose", action="store_true", help="逐頁列出比對分數")
    args = parser.parse_args()

    templates = layout.TemplateSet.load(args.store)
    if not len(templates):
        raise SystemExit("樣板資料夾裡沒有任何樣板: %s" % args.store)
    print("載入 %d 個樣板頁面" % len(templates))

    paths = collect(args.targets)
    if not paths:
        raise SystemExit("找不到任何 PDF")

    started = time.perf_counter()
    pages = layout.classify_pages(paths, templates)
    elapsed = time.perf_counter() - started

    if args.verbose:
        print()
        for page in pages:
            print("  %-28s p%-3d %-12s 內點=%-5d 差距=%.1fx 旋轉=%d"
                  % (os.path.basename(page.source), page.index + 1, page.label,
                     page.inliers, page.margin, page.rotation))

    documents = layout.split_documents(pages)
    print()
    print("=" * 66)
    print("共 %d 頁 → 切成 %d 件   (分類耗時 %.1f 秒，平均每頁 %.2f 秒)"
          % (len(pages), len(documents), elapsed, elapsed / max(len(pages), 1)))
    print("=" * 66)

    for number, document in enumerate(documents, 1):
        flag = "" if document.complete else "   ⚠ 沒有正面，請人工確認"
        pages_desc = "、".join("p%d(%s)" % (p.index + 1, p.label) for p in document.pages)
        print("  第 %2d 件  %-6s %d 頁  %s%s"
              % (number, document.code or "?", len(document.pages), pages_desc, flag))

    unknown = [p for p in pages if p.role == layout.UNKNOWN]
    if unknown:
        print()
        print("有 %d 頁認不出來，需要人工指定：" % len(unknown))
        for page in unknown:
            print("  %s 第 %d 頁 (最高內點 %d，差距 %.1fx)"
                  % (os.path.basename(page.source), page.index + 1, page.inliers, page.margin))


if __name__ == "__main__":
    main()

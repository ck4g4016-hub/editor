# -*- coding: utf-8 -*-
"""從一份參考 PDF 建立表格樣板。

樣板是用來認「這一頁是哪一種表格、是正面還是背面」的參考影像。
參考來源可以是空白原稿，也可以是任何一份該表格的掃描件 —— 認種類靠的是版面結構，
有沒有填寫都不影響。

樣板存在本機資料夾，不進版控 —— 那些影像可能來自真實件。

用法：

    python tools/build_template.py 樣板資料夾 --code F --name "地價稅自用住宅申請書(舊)" \\
        --pdf 空白原稿.pdf --front 1 --back 2

--back 可以省略，單面的表格不需要。
"""

import argparse
import json
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import render, resources  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="從參考 PDF 建立表格樣板",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("store", help="樣板資料夾")
    parser.add_argument("--code", required=True, help="表格代號，例如 F")
    parser.add_argument("--name", required=True, help="表格全名")
    parser.add_argument("--pdf", required=True, help="參考 PDF")
    parser.add_argument("--front", type=int, required=True, help="正面在第幾頁（從 1 起算）")
    parser.add_argument("--back", type=int, help="背面在第幾頁，單面表格免填")
    parser.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270],
                        help="把參考頁順時針轉這麼多度再存，讓樣板是正立的。"
                             "橫式的系統報表掃進來是躺著的，要轉正")
    args = parser.parse_args()

    folder = os.path.join(args.store, args.code)
    os.makedirs(folder, exist_ok=True)

    pages = {}
    for role, number in (("front", args.front), ("back", args.back)):
        if number is None:
            continue
        image = render.rotate(render.render(args.pdf, number - 1, dpi=render.CLASSIFY_DPI),
                              args.rotate)
        filename = "%s.png" % role
        resources.imwrite(os.path.join(folder, filename), image)
        pages[role] = filename
        turned = "，轉正 %d°" % args.rotate if args.rotate else ""
        print("  %-6s ← %s 第 %d 頁  (%dx%d%s)" % (role, os.path.basename(args.pdf),
                                                  number, image.shape[1], image.shape[0], turned))

    with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as handle:
        json.dump({"code": args.code, "name": args.name, "pages": pages},
                  handle, ensure_ascii=False, indent=2)

    print("樣板已建立: %s  (%s)" % (folder, args.name))


if __name__ == "__main__":
    main()

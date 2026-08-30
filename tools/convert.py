# -*- coding: utf-8 -*-
"""把一批掃描 PDF 轉成內外網兩個輸出檔。

這是整條流程的命令列版本：分類、切件、減版面、裁欄位、辨識、驗證、輸出。
正式使用會有複核介面，這支是它底下跑的東西，也方便在沒有畫面的情況下驗證。

用法：

    python tools/convert.py 樣板資料夾 掃描檔資料夾 --out 輸出資料夾

沒有先用樣板編輯器定義欄位的表格會被跳過，畫面上會列出來。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import fields as fieldmod  # noqa: E402
from pipeline import output, process  # noqa: E402


def collect(targets):
    paths = []
    for target in targets:
        if os.path.isdir(target):
            paths.extend(sorted(os.path.join(target, name) for name in os.listdir(target)
                                if name.lower().endswith(".pdf")))
        else:
            paths.append(target)
    return paths


def main():
    parser = argparse.ArgumentParser(description="把掃描 PDF 轉成內外網輸出檔",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("store", help="樣板資料夾")
    parser.add_argument("targets", nargs="+", help="掃描檔資料夾或個別 PDF")
    parser.add_argument("--out", default="輸出", help="輸出資料夾")
    parser.add_argument("--districts", nargs="+", default=["三峽區", "鶯歌區"],
                        help="行政區清單，用來把辨識結果吸附到合法區名")
    args = parser.parse_args()

    converter = process.Converter(args.store, districts=args.districts)
    paths = collect(args.targets)
    if not paths:
        raise SystemExit("找不到任何 PDF")

    print("處理 %d 個檔案…" % len(paths))
    records, unresolved = converter.run(
        paths, progress=lambda r: print("  %s" % r.describe()))

    print()
    print(process.summarise(records, unresolved))

    known = {template.code for template in converter.templates.templates}
    missing = sorted(code for code in known if not converter.fields_of(code))
    if missing:
        print()
        print("這些表格還沒定義欄位，已跳過：%s" % "、".join(missing))
        print("請先用 tools/template_editor.py 設定。")

    if not records:
        return 1

    print()
    for path in output.write_all([r.to_row() for r in records], args.out):
        print("已產出: %s" % path)

    flagged = [r for r in records if r.status == process.REVIEW]
    if flagged:
        print()
        print("需要人工確認的 %d 件：" % len(flagged))
        for record in flagged:
            notes = "；".join("%s（%s）" % (fieldmod.COLUMNS.get(c, c), n)
                             for c, n in record.flagged().items())
            print("  %s → %s" % (record.describe(), notes))
    return 0


if __name__ == "__main__":
    sys.exit(main())

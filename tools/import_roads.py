# -*- coding: utf-8 -*-
"""匯入路街名字典。

程式內建的路名只是從樣本抽出來的種子，不足以涵蓋轄區。
請從門牌資料或地籍圖資系統匯出轄區的路街名，用這支匯入。

來源檔可以是純文字（一行一個）或 CSV。是 CSV 的話會掃過每一欄，
把看起來像路街名的字串（結尾是路、街、大道、巷）挑出來，
不必先整理成單欄。

用法：

    python tools/import_roads.py 樣板資料夾 路名來源.csv [更多來源...]
    python tools/import_roads.py 樣板資料夾 --list
"""

import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import lexicon  # noqa: E402

ROAD = re.compile(r"[一-鿿]{1,8}(?:路|街|大道)(?:[一二三四五六七八九十]+段)?$")


def harvest(path):
    names = set()
    with open(path, encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        if "," in sample or "\t" in sample:
            for row in csv.reader(handle):
                for cell in row:
                    value = cell.strip()
                    if ROAD.match(value):
                        names.add(value)
        else:
            for line in handle:
                value = line.strip()
                if ROAD.match(value):
                    names.add(value)
    return names


def main():
    parser = argparse.ArgumentParser(description="匯入路街名字典",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("store", help="樣板資料夾")
    parser.add_argument("sources", nargs="*", help="路名來源檔（純文字或 CSV）")
    parser.add_argument("--list", action="store_true", help="列出目前字典內容")
    parser.add_argument("--replace", action="store_true", help="取代而不是累加")
    args = parser.parse_args()

    existing = set() if args.replace else set(lexicon.load(args.store))

    if args.list:
        names = sorted(existing)
        print("字典共 %d 個路街名：" % len(names))
        for name in names:
            print("   " + name)
        return 0

    if not args.sources:
        raise SystemExit("請指定來源檔，或用 --list 查看目前內容")

    found = set()
    for source in args.sources:
        names = harvest(source)
        print("  %-40s 取出 %d 個" % (os.path.basename(source), len(names)))
        found |= names

    target, count = lexicon.save(args.store, existing | found)
    print("字典已更新：%s（共 %d 個）" % (target, count))
    return 0


if __name__ == "__main__":
    sys.exit(main())

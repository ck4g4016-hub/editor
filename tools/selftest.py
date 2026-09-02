# -*- coding: utf-8 -*-
"""出貨前的自我檢查。

打包流程會先跑這個，不過就不打包。

存在的理由：有一次改動一路替換到檔尾，把 summarise() 連帶刪掉了。
每個模組單獨 import 都沒問題，只有真的呼叫到那一行才會炸 ——
而那一行在使用者按下「轉換」之後才會執行。等於壞掉的版本照樣發布出去。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def check():
    problems = []

    # 每個模組該有的東西。刪掉或改名都會在這裡被抓到。
    expected = {
        "pipeline.process": ["Converter", "Record", "summarise", "CRITICAL"],
        "pipeline.output": ["write_outer", "write_inner", "write_all",
                            "inner_address", "roc_date"],
        "pipeline.validate": ["id_number", "doc_number", "address", "district",
                              "land_number", "check", "best_id", "fix_id_positions"],
        "pipeline.recognise": ["read", "read_cells", "cells", "crop_field"],
        "pipeline.fields": ["Field", "load", "save", "COLUMNS", "KINDS"],
        "pipeline.layout": ["TemplateSet", "classify_pages", "split_documents"],
        "pipeline.diagnose": ["Journal", "build", "save", "mask", "mask_problem"],
        "pipeline.resources": ["imread", "imwrite", "workspace", "version"],
        "pipeline.baseimage": ["subtract", "compose", "align", "coverage"],
        "pipeline.lexicon": ["load", "for_district", "resolve_head"],
        "tools.newform": ["create", "base_from_blank", "base_from_scans", "check"],
        "tools.template_editor": ["Workspace", "make_handler", "collect"],
        "tools.review": ["make_handler", "collect", "describe"],
        "app": ["main", "menu", "run_convert", "run_editor", "run_new_form", "serve"],
    }
    for name, attributes in expected.items():
        try:
            module = __import__(name, fromlist=["_"])
        except Exception as error:                                  # noqa: BLE001
            problems.append("%s 載入失敗：%s" % (name, error))
            continue
        for attribute in attributes:
            if not hasattr(module, attribute):
                problems.append("%s 少了 %s" % (name, attribute))

    # 網頁介面用到的檔案
    from pipeline import resources
    for parts in (("editor", "page.html"), ("editor", "review.html"),
                  ("data", "roads-三峽-鶯歌.txt")):
        if not os.path.isfile(resources.path(*parts)):
            problems.append("少了資源檔 %s" % "/".join(parts))

    # 幾條不該壞掉的行為
    from pipeline import validate
    cases = [
        ("身分證位置修正", lambda: validate.best_id("A1Z3456789")[0], "A123456789"),
        ("身分證檢查碼",   lambda: validate.best_id("A1Z3456789")[1], None),
        ("文號去掉日期",   lambda: validate.doc_number("收文1155699046115/08/28")[0],
         "1155699046"),
        ("地號補零",       lambda: validate.land_number("532")[0], "0532-0000"),
        ("樓層轉中文",     lambda: validate.address("中正路2段15號17樓")[0],
         "中正路二段15號十七樓"),
        ("行政區比對",     lambda: validate.district("三山峡", ["三峽區", "鶯歌區"])[0],
         "三峽區"),
    ]
    for label, run, want in cases:
        try:
            got = run()
        except Exception as error:                                  # noqa: BLE001
            problems.append("%s 出錯：%s" % (label, error))
            continue
        if got != want:
            problems.append("%s 得到 %r，應該是 %r" % (label, got, want))

    return problems


def main():
    problems = check()
    if problems:
        print("自我檢查沒過：")
        for problem in problems:
            print("  ✗ %s" % problem)
        return 1
    print("自我檢查通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())

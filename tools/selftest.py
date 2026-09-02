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

# GitHub 的 Windows runner 主控台是 cp1252，印中文會丟 UnicodeEncodeError ——
# 檢查明明通過了，卻死在印出「自我檢查通過」那一行，整個建置失敗。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


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

    # 路街名由字典決定，讀到的那個字不算數 —— 「中華街」不可以變成「中園街」，
    # 那是另一條路，而且驗證會放行，從輸出表上看不出來
    from pipeline import lexicon
    roads = lexicon.for_district(lexicon.load(resources.base_dir()), "三峽區")
    if roads:
        for got, want in (("中華街", "中華路"), ("中華", "中華路"),
                          ("民生路", "民生街"), ("民生", "民生街")):
            name, _score = lexicon.resolve_head(got, roads)
            if name != want:
                problems.append("路名比對 %s 得到 %r，應該是 %r" % (got, name, want))
    else:
        problems.append("路名字典是空的")

    # 門牌正規化的每一條規則（規格書 docs/output-spec.md 談出來的）
    if roads:
        for label, raw, want in (
            ("樓層一律中文", "中華路38巷14號17樓", "中華路38巷14號十七樓"),
            ("段也是中文", "中正路2段15號", "中正路二段15號"),
            ("之用國字", "中華路38巷14-6號", "中華路38巷14之6號"),
            ("巷弄號半形", "中華路３８巷１４號", "中華路38巷14號"),
            ("去掉縣市行政區", "新北市三峽區中華路14號", "中華路14號"),
            ("去掉里鄰", "中華里5鄰中華路14號", "中華路14號"),
            ("英文換數字", "中華路I4號", "中華路14號"),
        ):
            got, _problem = validate.address(raw, roads=roads)
            if got != want:
                problems.append("門牌「%s」得到 %r，應該是 %r" % (label, got, want))
        for label, raw in (("沒有號", "中華路38巷"), ("空的", ""),
                           ("路名不在字典", "不存在路14號")):
            _got, problem = validate.address(raw, roads=roads)
            if not problem:
                problems.append("門牌「%s」應該被標記卻放行了" % label)

    # 這些邊界情況以前每一個都會丟例外，而且都在最不能出事的地方 ——
    # 輸出檔產不出來等於整批複核白做，診斷報告產不出來等於出事時沒有線索。
    import numpy as np

    from pipeline import diagnose, output, process, recognise

    edge = [
        ("輸出檔的值是 None",
         lambda: output.write_all([{"district": None, "address": None,
                                    "id_number": None, "name": None}], _scratch())),
        ("輸出檔沒有資料", lambda: output.write_all([], _scratch())),
        ("診斷報告：空的一批", lambda: diagnose.build(diagnose.Journal())),
        ("診斷報告：意見的鍵不是數字",
         lambda: diagnose.build(diagnose.Journal(),
                                notes={"overall": "x", "records": {"abc": "y"}})),
        ("路名字典是 None", lambda: lexicon.resolve_head("中華", None)),
        ("裁切圖灰階與彩色混合",
         lambda: process._stack([np.zeros((20, 30), np.uint8),
                                 np.zeros((20, 30, 3), np.uint8)])),
        ("辨識空影像",
         lambda: (recognise.read(None), recognise.cells(None),
                  recognise.read_cells(np.zeros((0, 0), np.uint8)))),
    ]
    for label, run in edge:
        try:
            run()
        except Exception as error:                                  # noqa: BLE001
            problems.append("%s 出錯：%s: %s" % (label, type(error).__name__, error))

    # 說明檔講的按鈕，程式裡要真的有 —— 紅筆偵測拿掉了，說明卻還在教
    # 使用者去標紅筆，那種文件比沒有文件更糟。
    manual = resources.path("說明.txt")
    if os.path.isfile(manual):
        with open(manual, encoding="utf-8") as handle:
            text = handle.read()
        for button in ("新增表格", "設定樣板", "轉　換", "產生診斷報告", "產生輸出檔"):
            if button.replace("　", "") not in text.replace("　", ""):
                problems.append("說明.txt 沒有提到按鈕「%s」" % button)
        for gone in ("綠色虛線的候選框", "點綠色虛線"):
            if gone in text:
                problems.append("說明.txt 還在講已經移除的紅筆候選框：%s" % gone)
    else:
        problems.append("找不到 說明.txt")

    # 寬欄位不可以放大 —— 放大之後偵測框會重疊，同一個字讀兩次
    if recognise.scale_for(1600) * 1600 > recognise.MAX_WIDTH + 1:
        problems.append("寬欄位的縮放沒有壓到 MAX_WIDTH 以內")
    if recognise.scale_for(100) < 1.5:
        problems.append("窄欄位沒有放大")

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


def _scratch():
    import tempfile

    return tempfile.mkdtemp(prefix="paper2excel-selftest-")


if __name__ == "__main__":
    sys.exit(main())

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
                  ("data", "roads-三峽-鶯歌.txt"),
                  ("data", "sections-三峽-鶯歌.txt"),
                  ("data", "簡繁對照.txt")):
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

    # 字典裡沒有的路名**絕對不可以**被換成另一條真的存在的路。
    # 這是最危險的一類錯：驗證會放行，輸出表上看不出來，RPA 拿著別人家的
    # 門牌去查調。實測出過一次：鶯歌區的「大湖路」被換成「東湖路」——
    # 那次的根本原因是內建字典漏收，現在字典換成內政部門牌開放資料了。
    roads_all = lexicon.load(resources.base_dir())
    yingge = lexicon.for_district(roads_all, "鶯歌區")
    sanxia = lexicon.for_district(roads_all, "三峽區")
    if len(yingge) < 100 or len(sanxia) < 80:
        problems.append("路名字典筆數不對：三峽 %d、鶯歌 %d"
                        % (len(sanxia), len(yingge)))
    if yingge:
        # 字典裡沒有的路名，要嘛不換，要嘛換了一定要標起來讓人看。
        # 靜靜換掉是最危險的一類錯：驗證放行、輸出表上看不出來。
        for got in ("天龍路", "幸福路", "光華路", "忠義街"):
            name, score, note = lexicon.resolve_head_full(got, yingge)
            if name is not None and not note:
                problems.append("路名「%s」不在字典裡，卻被靜靜換成 %r（%.2f）"
                                % (got, name, score))
        # 連一字之差的鄰居都沒有的，就該老實說不認得（門牌那邊會標記）
        for got in ("天龍路", "忠義街"):
            name, _score, _note = lexicon.resolve_head_full(got, yingge)
            if name is not None:
                problems.append("「%s」字典裡毫無相近的路，卻回了 %r" % (got, name))
        # 尾字由字典決定：官方清單裡只有西湖街、只有大湖路
        for got, want in (("大湖路", "大湖路"), ("大湖", "大湖路"),
                          ("大湖街", "大湖路"), ("西湖路", "西湖街"),
                          ("中湖街", "中湖街"), ("國華路", "國華路"),
                          ("館前路", "館前路"), ("高職南街", "高職南街")):
            name, _score = lexicon.resolve_head(got, yingge)
            if name != want:
                problems.append("路名比對 %s 得到 %r，應該是 %r" % (got, name, want))

        # 唯一一組主體相同、路街並存的：東湖街與東湖路。
        # 只讀到「東湖」時分不出來，一定要標起來，不可以挑一個。
        name, _score, note = lexicon.resolve_head_full("東湖", yingge)
        if name is not None or not note:
            problems.append("「東湖」路街並存卻沒有標起來：%r / %r" % (name, note))
        # 讀到的尾字是唯一線索時可以用，但要講出來讓人確認
        for got, want in (("東湖路", "東湖路"), ("東湖街", "東湖街")):
            value, problem = validate.address(got + "12號", roads=yingge)
            if value != want + "12號":
                problems.append("「%s」得到 %r，應該是 %r" % (got, value, want + "12號"))
            if not problem:
                problems.append("「%s」靠讀到的尾字決定，卻沒有提醒要確認" % got)

        # 表格上印的是「路／街」二選一的標籤，那不是路名的一部分
        for raw, want in (("大湖路/街732巷16弄15號2樓", "大湖路732巷16弄15號二樓"),
                          ("中湖路/街5巷3號", "中湖街5巷3號"),
                          ("館前路/街9號", "館前路9號")):
            value, problem = validate.address(raw, roads=yingge)
            if value != want:
                problems.append("「%s」得到 %r，應該是 %r" % (raw, value, want))
            if problem:
                problems.append("「%s」不該被標記：%s" % (raw, problem))

        value, problem = validate.address("天龍路732巷16弄15號2樓", roads=yingge)
        if not problem:
            problems.append("不在字典裡的門牌被放行了：%r" % value)

    # 公文文號：收文戳上還印著別的數字，不可以跟條碼號各切一半黏起來
    for raw, want in (
        ("115FF005424機關收文115/08/251155698710", "1155698710"),
        ("CR酮收文1155698710115/08/25", "1155698710"),
        ("1155699046115/08/28", "1155699046"),
        ("機關收文115/08/251155699261", "1155699261"),
    ):
        got, problem = validate.doc_number(raw)
        if got != want or problem:
            problems.append("公文文號「%s」得到 %r（%s），應該是 %r"
                            % (raw, got, problem, want))

    # 內建的地段清單是從地政局易找查的下拉選單直接複製的，那是權威資料。
    # 這幾條是使用者實際遇到的：段名讀成「圍際」，而鶯歌真的有「國際段」。
    sections = lexicon.builtin_sections()
    if len(sections.get("三峽區") or ()) < 80 or len(sections.get("鶯歌區") or ()) < 40:
        problems.append("內建地段清單筆數不對：%s"
                        % {k: len(v) for k, v in sections.items()})
    for district, raw, want, flagged in (
        ("鶯歌區", "國際段", "國際段", False),
        ("鶯歌區", "國際", "國際段", False),
        ("鶯歌區", "圍際", "國際段", False),          # 差一個字，清單裡只有一個像的
        ("鶯歌區", "犬湖", "犬湖", True),             # 大中西東三湖都只差一個字 —— 不准猜
        ("鶯歌區", "大湖段", "大湖段", False),        # 底下七個小段，只寫到段
        ("鶯歌區", "阿南坑段茶山小段", "阿南坑段茶山小段", False),
        ("鶯歌區", "不存在段", "不存在段", True),
        ("三峽區", "白雞段", "白雞段", False),
        ("三峽區", "白雞段白雞小段", "白雞段白雞小段", False),
        ("三峽區", "大學段一小段", "大學段一小段", False),
        ("三峽區", "焦溪段", "焦溪段", True),          # 礁溪段與安溪段都只差一個字
    ):
        names = lexicon.for_district(sections, district)
        got, problem = validate.check("section", raw, known=names)
        if got != want:
            problems.append("地段（%s）「%s」得到 %r，應該是 %r"
                            % (district, raw, got, want))
        if bool(problem) != flagged:
            problems.append("地段（%s）「%s」標記狀態不對：%r"
                            % (district, raw, problem))

    # 地段清單是從地政局易找查的下拉選單複製過來的，貼進來長什麼樣都有可能。
    # 要求使用者先自己整理成乾淨清單，等於把工作推回去給他。
    for line, want in (
        ('<option value="0039">(0039) 白雞段白雞小段</option>', ("0039", "白雞段白雞小段")),
        ("(0040) 白雞段中坑小段", ("0040", "白雞段中坑小段")),
        ("0041 國際段", ("0041", "國際段")),
        ("　嘉添段　", (None, "嘉添段")),
        ("# 這是註解", (None, "")),
        ("", (None, "")),
    ):
        got = lexicon.parse_section(line)
        if got != want:
            problems.append("地段行 %r 解析成 %r，應該是 %r" % (line, got, want))

    # 表格上可能只寫到段。那個段底下不只一個小段的時候，輸出要停在段 ——
    # 替使用者挑一個小段就是猜，而猜錯從輸出表上看不出來。
    listing = ['<option value="0039">(0039) 白雞段白雞小段</option>',
               "(0040) 白雞段中坑小段", "0041 國際段"]
    for raw, want, flagged in (("白雞段白雞小段", "白雞段白雞小段", False),
                               ("白雞段", "白雞段", False),
                               ("白雞", "白雞段", False),
                               ("國際", "國際段", False),
                               ("國際段", "國際段", False),
                               ("田橋", "田橋", True)):
        got, problem = validate.check("section", raw, known=listing)
        if got != want:
            problems.append("地段「%s」得到 %r，應該是 %r" % (raw, got, want))
        if bool(problem) != flagged:
            problems.append("地段「%s」標記狀態不對：%r" % (raw, problem))

    # 段名沒有格式規則，只能靠清單。清單裡沒有的絕對不可以自己代換。
    for label, raw, known, want, flagged in (
        # 輸出的是清單上登記的寫法，不是讀到的寫法
        ("清單裡有", "國際", ["國際段", "二甲段"], "國際段", False),
        ("尾字的段可有可無", "國際段", ["國際", "二甲"], "國際", False),
        # 差一個字、而且清單裡只有一個像的，就修掉
        ("錯一個字", "圍際", ["國際段", "二甲段"], "國際段", False),
        # 完全不像的一定要標起來
        ("清單裡沒有要標起來", "田橋", ["國際段", "二甲段"], "田橋", True),
        ("沒有清單就照讀的寫", "圍際", [], "圍際", False),
    ):
        got, problem = validate.check("section", raw, known=known)
        if got != want:
            problems.append("段名「%s」得到 %r，應該是 %r" % (label, got, want))
        if flagged and problem is None and known:
            problems.append("段名「%s」應該被標記卻放行了" % label)
    for raw, known in (("田橋", ["國際段"]), ("", ["國際段"])):
        _got, problem = validate.check("section", raw, known=known)
        if not problem:
            problems.append("段名 %r 應該被標記卻放行了" % raw)

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

    # 提供一個什麼都不做的選項，比沒有這個選項還糟。
    # 「錨點相對」在 fields 存得好好的，process 卻從來沒實作，
    # 設了的欄位會被當成固定框而且畫面上看不出來 —— 要嘛實作、要嘛擋掉。
    from pipeline import fields as _f
    anchored = _f.Field(id="x", name="x", column="address", kind="address",
                        box=[1, 2, 3, 4], mode=_f.ANCHOR, anchor_text="地址")
    if not any("錨點" in issue for issue in anchored.problems()):
        problems.append("錨點相對還沒實作，卻沒有被擋下來")
    with open(resources.path("editor", "page.html"), encoding="utf-8") as handle:
        page_source = handle.read()
    if 'value="anchor"' in page_source:
        problems.append("樣板編輯器還在提供「錨點相對」，但那個模式沒有實作")

    # 段名有自己的型別。這份對應表以前抄在編輯器的 JS 裡一份，兩邊各改各的，
    # 結果段名一直用沒有驗證器的 chinese，整批段名從來沒被比對過。
    from pipeline import fields as fieldmod
    for column in fieldmod.COLUMNS:
        if column not in fieldmod.DEFAULT_KIND:
            problems.append("輸出欄「%s」沒有預設型別" % column)
    for column, kind in fieldmod.DEFAULT_KIND.items():
        if kind not in fieldmod.KINDS:
            problems.append("預設型別 %r 不在 KINDS 裡" % kind)
    if fieldmod.DEFAULT_KIND.get("section") != "section":
        problems.append("段名的預設型別不是 section，地段清單不會生效")
    with open(resources.path("editor", "page.html"), encoding="utf-8") as handle:
        page = handle.read()
    if "data.default_kinds" not in page:
        problems.append("樣板編輯器沒有跟 API 拿預設型別，又會各改各的")

    # 舊樣板要能就地升級，不必要求使用者把欄位重框一遍
    import json as _json
    import tempfile as _tempfile
    old_store = _tempfile.mkdtemp()
    os.makedirs(os.path.join(old_store, "F"))
    with open(os.path.join(old_store, "F", "fields.json"), "w", encoding="utf-8") as handle:
        _json.dump({"fields": [{"id": "a", "name": "段名", "column": "section",
                                "kind": "chinese", "box": [1, 2, 3, 4],
                                "page": "front", "mode": "fixed"}]},
                   handle, ensure_ascii=False)
    upgraded = fieldmod.load(old_store, "F")
    if not upgraded or upgraded[0].kind != "section":
        problems.append("舊樣板的段名型別沒有被升級")

    # 那個小視窗一關，網頁伺服器就跟著收掉，瀏覽器那一頁只會說
    # 「Failed to fetch」，而且程式已經不在，連錯誤紀錄都寫不出來。
    # 使用者遇到過一次，整批白跑還查不出原因。關之前一定要先問。
    with open(resources.path("app.py"), encoding="utf-8") as handle:
        source = handle.read()
    for needed, why in (
        ("WM_DELETE_WINDOW", "右上角的 X 沒有攔下來，關掉就把伺服器一起收掉"),
        ("askyesno", "關視窗之前沒有先問一句"),
        ('root.after(1500, lambda: root.attributes("-topmost", False))',
         "小視窗還是一直置頂，會擋住瀏覽器讓人想把它關掉"),
    ):
        if needed not in source:
            problems.append("serve() %s" % why)
    if "diagnose.save(" not in source.split("def run_convert", 1)[-1].split("def menu", 1)[0]:
        problems.append("轉換完沒有自動寫診斷報告 —— 複核畫面掛掉就什麼線索都沒有")

    # 說明檔講的按鈕，程式裡要真的有 —— 紅筆偵測拿掉了，說明卻還在教
    # 使用者去標紅筆，那種文件比沒有文件更糟。
    manual = resources.path("說明.txt")
    if os.path.isfile(manual):
        with open(manual, encoding="utf-8") as handle:
            text = handle.read()
        for button in ("新增表格", "設定樣板", "轉　換", "字典",
                       "產生診斷報告", "產生輸出檔"):
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

    # 照原稿印好的格線切格子。承辦人說得對：「原稿就有格子了」，
    # 一字一格的欄位不該要人去框十個小方塊。
    import cv2

    printed = np.full((150, 1101, 3), 255, np.uint8)
    for x in range(0, 1101, 100):
        cv2.line(printed, (x, 0), (x, 149), (0, 0, 0), 2)
    blank = np.full((150, 1101), 255, np.uint8)
    cut = recognise.grid_cells(printed, blank)
    if len(cut) != 11:
        problems.append("印刷格線應該切出 11 格，實際切出 %d 格" % len(cut))
    widths = {c.shape[1] for c in cut}
    if len(widths) > 2:
        problems.append("切出來的格子寬度不一致：%s" % sorted(widths))
    if recognise.grid_cells(np.full((150, 1101, 3), 255, np.uint8), blank):
        problems.append("沒有格線的欄位不該被切成格子")
    # 說明文字的直筆畫寬度不一致，不可以被當成格線 —— 那樣會把字剖成兩半
    noisy = np.full((150, 1101, 3), 255, np.uint8)
    for x in (10, 40, 300, 900, 1000):
        cv2.line(noisy, (x, 0), (x, 149), (0, 0, 0), 2)
    if recognise.grid_cells(noisy, blank):
        problems.append("寬度不一致的直線被誤當成格線")

    # 使用者框選時本來就該框寬鬆一點（字才不會被切掉），所以格線常常只佔
    # 框高的一小部分。原本的門檻是「佔框高 55%」，在真實件上一次都沒觸發過，
    # 而診斷報告看不出來這件事，害我照著錯誤的假設又猜了一輪。
    for frame_height in (200, 300, 500, 800):
        tall = np.full((frame_height, 1400, 3), 255, np.uint8)
        top = (frame_height - 180) // 2
        for x in range(150, 1251, 110):
            cv2.line(tall, (x, top), (x, top + 180), (0, 0, 0), 3)
        cut = recognise.grid_cells(tall, np.full((frame_height, 1400), 255, np.uint8))
        if len(cut) != 10:
            problems.append("框高 %d 時應該切出 10 格，實際 %d 格（格線只佔框高 %.0f%%）"
                            % (frame_height, len(cut), 180.0 / frame_height * 100))
    # 欄位框通常畫得比那排格子高（說明裡就是這樣教的）。讀的時候要收回到
    # 格子本身，不然上下相鄰那一行的字會一起被讀進來 —— 實測框高 240px
    # 連下一行一起框進去時，同一張影像十格全部讀不出來。
    # 這個門檻踩過兩次坑，兩次都是「框畫大一點就抓不到」，而且時好時壞。
    # 所以這裡把框高從剛好到五倍都測一遍，還故意在框裡加一條比格線更長的
    # 直線（表格外框那種），確認它不會把真正的格線擠掉。
    for frame_height in (130, 200, 300, 420, 560):
        top = (frame_height - 110) // 2
        tall = np.full((frame_height, 1400, 3), 255, np.uint8)
        for x in range(150, 1251, 110):
            cv2.line(tall, (x, top), (x, top + 110), (0, 0, 0), 3)
        if frame_height >= 300:
            # 一條貫穿整個框的直線，比格線長得多
            cv2.line(tall, (40, 0), (40, frame_height - 1), (0, 0, 0), 3)
        band = recognise.grid_band(tall)
        cut = recognise.grid_spans(tall, np.full((frame_height, 1400), 255, np.uint8))
        if len(cut) != 10:
            problems.append("框高 %d 應該切出 10 格，實際 %d 格" % (frame_height, len(cut)))
        if band is None:
            problems.append("框高 %d 找不到格子的上下界" % frame_height)
        elif abs(band[0] - top) > 8 or abs(band[1] - (top + 110)) > 8:
            problems.append("框高 %d 的上下界 %s，應該接近 (%d, %d)"
                            % (frame_height, band, top, top + 110))
    if recognise.grid_band(np.full((200, 800, 3), 255, np.uint8)) is not None:
        problems.append("空白欄位不該算出格子的上下界")

    # 滑動視窗：一次讀好幾格，每一格會被好幾個視窗讀到
    spans = [(i * 100, i * 100 + 90) for i in range(10)]
    picks = recognise.read_grid(np.full((120, 1000), 255, np.uint8), spans)
    if len(picks) != 10:
        problems.append("滑動視窗應該回傳 10 格的候選，實際 %d" % len(picks))
    if any(picks):
        problems.append("全白的欄位不該讀出任何候選字")

    # 只有印刷文字、沒有格線的欄位不可以被切
    words = np.full((300, 1400, 3), 255, np.uint8)
    cv2.putText(words, "ADDRESS", (30, 180), cv2.FONT_HERSHEY_SIMPLEX, 3.0, (0, 0, 0), 6)
    if recognise.grid_cells(words, np.full((300, 1400), 255, np.uint8)):
        problems.append("只有印刷文字的欄位被誤切成格子")

    # 欄位框多框到相鄰的一行時，段落要由上而下、每行由左而右接起來。
    # 以前只照 x 排序，好幾行的字會橫著交錯（公文文號讀出「CR酮收文…115/08/25」
    # 就是這樣來的）；而且去重複只看水平重疊，正上方那一行會被整段丟掉。
    def _box(x0, y0, x1, y1):
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]

    row_cases = (
        ("兩行對齊", [(_box(40, 20, 400, 90), "上", 0.9),
                      (_box(40, 120, 400, 190), "下", 0.9)], "上下"),
        ("下面那行靠左", [(_box(500, 20, 900, 90), "上", 0.9),
                          (_box(40, 120, 400, 190), "下", 0.9)], "上下"),
        ("同一行兩段", [(_box(500, 20, 900, 90), "右", 0.9),
                        (_box(40, 20, 400, 90), "左", 0.9)], "左右"),
        ("同一行重複偵測", [(_box(40, 20, 400, 90), "左", 0.9),
                            (_box(60, 20, 420, 90), "左", 0.9)], "左"),
    )
    for label, items, want in row_cases:
        got = "".join(item[1] for line in recognise.rows(items) for item in line)
        if got != want:
            problems.append("分行「%s」得到 %r，應該是 %r" % (label, got, want))

    # 一欄有好幾格、只有其中一格有印刷格子的時候（門牌就是這樣：路名是
    # 空白欄，「號」才有格子），沒格線的那幾格要沿用整行讀的結果。
    # 只收有格線的那幾格會讓整欄變成單一個「15號」，路名整段消失。
    import tempfile

    from pipeline import fields as fieldmod

    conv = process.Converter(tempfile.mkdtemp())
    mixed_base = np.full((150, 1500, 3), 255, np.uint8)
    for x in range(900, 1301, 100):
        cv2.line(mixed_base, (x, 0), (x, 149), (0, 0, 0), 2)
    mixed = np.full((150, 1500), 255, np.uint8)
    cv2.putText(mixed, "AAA", (30, 108), cv2.FONT_HERSHEY_SIMPLEX, 2.2, 0, 6)
    for index, ch in enumerate("158"):
        cv2.putText(mixed, ch, (920 + index * 100, 108),
                    cv2.FONT_HERSHEY_SIMPLEX, 2.2, 0, 6)
    mixed_field = fieldmod.Field(id="a", name="門牌", column="address",
                                 kind="address", box=(0, 0, 880, 150), suffix="路",
                                 parts=[{"box": (900, 0, 500, 150), "suffix": "號"}])
    mixed_record = process.Record("F", "x.pdf", 0)
    conv._read_field(mixed_record, mixed, mixed_field, False, mixed_base)
    if mixed_record.raw.get("address") != "AAA路158號":
        problems.append("有格線與沒格線的格子混在一欄時讀成 %r，應該是 'AAA路158號'"
                        % mixed_record.raw.get("address"))

    # 逐格求解：用檢查碼從「每一格的候選字」解出身分證。
    # 最重要的一條是**失敗要講出來**，不可以生出一個通過檢查碼但錯的號碼。
    good_id = "G220390817"
    if validate.id_number(good_id)[1] is None:
        def cells_of(code):
            return [[c] for c in code]

        value, problem = validate.solve_id(cells_of(good_id))
        if value != good_id or problem:
            problems.append("逐格求解：全讀對卻得到 %r（%s）" % (value, problem))

        # 檢查碼那一格沒讀到 → 推得回來，但一定要標起來
        gap = cells_of(good_id); gap[9] = []
        value, problem = validate.solve_id(gap)
        if value != good_id:
            problems.append("逐格求解：缺檢查碼那格應該推得回來，得到 %r" % value)
        if not problem:
            problems.append("逐格求解：用推的補回來卻沒有標記")

        # 空太多格 → 補回來的比讀到的多，不可以硬解
        many = cells_of(good_id)
        for i in (2, 3, 4):
            many[i] = []
        value, problem = validate.solve_id(many)
        if value is not None or not problem:
            problems.append("逐格求解：缺三格竟然給了答案 %r" % value)

        # 一格有兩種讀法，檢查碼要能挑出對的那個
        two = cells_of(good_id); two[5] = ["3", "9"]
        value, problem = validate.solve_id(two)
        if value != good_id or problem:
            problems.append("逐格求解：一格兩解沒挑對，得到 %r（%s）" % (value, problem))

        # 格數不對就不要硬解
        value, problem = validate.solve_id(cells_of(good_id)[:9])
        if value is not None or not problem:
            problems.append("逐格求解：只有 9 格竟然給了答案 %r" % value)

    # 輸出一律繁體。辨識模型的字典同時收了簡繁兩種字形，會吐出簡體字 ——
    # 實測「樓」讀成「楼」、「鄰」讀成「邻」。
    for raw, want in (("尖山路27號六楼", "尖山路27號六樓"),
                      ("中华路38巷", "中華路38巷"),
                      ("莺歌区", "鶯歌區"),
                      ("陈大华", "陳大華")):
        got = validate.to_traditional(raw)
        if got != want:
            problems.append("簡轉繁「%s」得到 %r，應該是 %r" % (raw, got, want))
    table = validate._traditional_table()
    if len(table) < 200:
        problems.append("簡繁對照表只有 %d 組，太少了" % len(table))
    for simple, trad in table.items():
        if simple == trad:
            problems.append("簡繁對照表裡「%s」簡繁同形，留著只是雜訊" % simple)
    # 一個簡體對到好幾個繁體的絕對不能收 —— 換錯字比不換更糟
    for risky in "发干后里松面表制系历只":
        if risky in table:
            problems.append("簡繁對照表收了有歧義的「%s」" % risky)
    # 中文欄位都要轉
    for kind, raw, want in (("chinese", "陈大华", "陳大華"),
                            ("district", "莺歌区", "鶯歌區")):
        extra = {"known": ["三峽區", "鶯歌區"]} if kind == "district" else {}
        got, _p = validate.check(kind, raw, **extra)
        if got != want:
            problems.append("%s 欄位沒轉成繁體：%r" % (kind, got))

    # 路名只錯一個字時可以修，但**一定要標記**。
    # 字典裡根本沒有那條路的時候，它照樣會找到一字之差的鄰居
    # （實測「幸福路」→「鳳福路」、「秀山街」→「秀川街」），
    # 那種替換沒有任何東西擋得住，只能靠標記讓人看。
    if yingge:
        for raw in ("鳯一路25號", "幸福路9號", "光華路9號"):
            _v, problem = validate.address(raw, roads=yingge)
            if not problem:
                problems.append("路名一字之差被靜靜換掉了：%s → %r" % (raw, _v))
        value, problem = validate.address("鳯一路25號", roads=yingge)
        if value != "鳳一路25號":
            problems.append("「鳯一路」應該修成「鳳一路」，得到 %r" % value)
        # 讀對的不該被打擾
        for raw in ("鳳一路25號", "中湖街5號"):
            _v, problem = validate.address(raw, roads=yingge)
            if problem:
                problems.append("讀對的門牌被標記了：%s → %s" % (raw, problem))

    # 「鄰」那一格印在門牌左邊，框大一點就會把鄰別的數字吃進來。
    # 那個數字常被讀成字母（實測讀成 A）。門牌一定從路街名開始。
    for raw, want in (("A鳳鳴路123號5楼", "鳳鳴路123號五樓"),
                      ("4鳳鳴路123號五樓", "鳳鳴路123號五樓")):
        value, problem = validate.address(raw, roads=yingge)
        if value != want:
            problems.append("門牌「%s」得到 %r，應該是 %r" % (raw, value, want))
        if not problem or "鄰" not in problem:
            problems.append("門牌「%s」把開頭拿掉了卻沒說：%s" % (raw, problem))

    # 「弄」一定掛在「巷」底下。有弄沒巷多半是雜訊湊出來的，但不刪、只標
    # （實測「秀川街4之1號」被讀成「秀川街17弄4之1號」）
    sanxia_roads = lexicon.for_district(roads_all, "三峽區")
    if sanxia_roads:
        value, problem = validate.address("秀川路/街17弄4-1號", roads=sanxia_roads)
        if value != "秀川街17弄4之1號":
            problems.append("「秀川路/街17弄4-1號」得到 %r" % value)
        if not problem or "弄" not in problem:
            problems.append("有弄沒巷卻沒有標起來：%s" % problem)
        value, problem = validate.address("秀川街4-1號", roads=sanxia_roads)
        if value != "秀川街4之1號" or problem:
            problems.append("正常門牌被打擾了：%r（%s）" % (value, problem))

    # 橫式表格要能認出來需要轉正。躺著的樣板分類照樣對得上，但欄位裁下來
    # 是一條直的細長條，辨識時字的順序會整個錯亂（實測 E 表地址欄讀成
    # 「05粼尖山路27號六楼中一新北市歌區尖山里00」），所以建樣板時就要轉正。
    import tempfile as _tmp

    import pymupdf as _pdf

    from pipeline import render as _render
    from tools import newform as _newform

    _rot_dir = _tmp.mkdtemp()
    _upright = np.full((1100, 780, 3), 255, np.uint8)
    for row, text in enumerate(("NEW TAIPEI CITY", "LAND OFFICE 2026",
                                "ADDRESS 27 SEC 3", "TOTAL 1234567890")):
        cv2.putText(_upright, text, (40, 150 + row * 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 3)
    for degrees, want in ((0, 0), (90, 270), (270, 90)):
        turned = _render.rotate(_upright, degrees)
        path = os.path.join(_rot_dir, "r%d.pdf" % degrees)
        doc = _pdf.open()
        ok, buf = cv2.imencode(".png", turned)
        pg = doc.new_page(width=turned.shape[1] / 4.0, height=turned.shape[0] / 4.0)
        pg.insert_image(_pdf.Rect(0, 0, turned.shape[1] / 4.0, turned.shape[0] / 4.0),
                        stream=buf.tobytes())
        doc.save(path)
        doc.close()
        got = _newform.upright_rotation(path, 1)
        if got != want:
            problems.append("方向判斷：轉了 %d 度的頁面應該要轉回 %d 度，卻算出 %d 度"
                            % (degrees, want, got))

    # 一件固定兩頁，照頁數配對。這是承辦人的作業規則，也是最可靠的切分依據。
    #
    # 舊規則是「認出表格正面就開新的一件」，破口在於：正面認不出來的時候
    # 那一頁會被併進**前一件**，兩件變一件，件數上完全看不出少了誰。
    # 而空白背面認不出來是正常的 —— 兩種情形從分類結果上長得一模一樣。
    from pipeline import layout as _layout

    def fake_pages(source, roles):
        return [_layout.Page(source, i, code, role, 0, 0 if code is None else 500, 0.0)
                for i, (code, role) in enumerate(roles)]

    F, B, U, K = _layout.FRONT, _layout.BACK, _layout.UNKNOWN, _layout.BLANK
    pair_cases = (
        ("正常三件", "a.pdf", [("F", F), (None, K), ("F", F), (None, K),
                               ("F", F), (None, K)], 3, 3),
        ("背面有印東西", "a.pdf", [("F", F), ("F", B), ("F", F), ("F", B)], 2, 2),
        # 舊規則在這裡會把兩件併成一件
        ("第二件正面認不出來", "a.pdf", [("F", F), (None, K), (None, U), ("F", B)], 2, 2),
        ("兩面都認不出來", "a.pdf", [("F", F), (None, K), (None, U), (None, K)], 2, 1),
        ("奇數頁：漏掃一面", "a.pdf", [("F", F), (None, K), ("F", F)], 2, 1),
    )
    for label, source, roles, want_docs, want_ok in pair_cases:
        docs = _layout.split_documents(fake_pages(source, roles))
        good = sum(1 for d in docs if d.complete)
        if len(docs) != want_docs or good != want_ok:
            problems.append("配對「%s」切出 %d 件（完整 %d），應該是 %d 件（完整 %d）"
                            % (label, len(docs), good, want_docs, want_ok))
    # 不跨檔案配對
    mixed = fake_pages("a.pdf", [("F", F)]) + fake_pages("b.pdf", [("F", F), (None, K)])
    docs = _layout.split_documents(mixed)
    if len(docs) != 2 or [len(d.pages) for d in docs] != [1, 2]:
        problems.append("配對跨到別的檔案去了：%s" % [len(d.pages) for d in docs])
    # 正面認不出來但背面認得出來 → 表格種類要靠背面補回來
    rescued = _layout.split_documents(fake_pages("a.pdf", [(None, U), ("G", B)]))[0]
    if rescued.code != "G" or not rescued.complete:
        problems.append("正面認不出來時沒有靠背面判斷表格種類：%r" % rescued.code)

    # 沒有印刷格子的身分證欄一定要走得通。
    #
    # A、B、E 那種電腦產製的表格，身分證是一行印刷字、沒有方格。逐格那條路
    # 走不進去，底下卻讀得到只在那條路裡指派的變數 —— 整份文件在這裡丟
    # UnboundLocalError 被跳過。使用者看到的是「新建的三個樣板通通沒反應，
    # 只有舊的 F 讀得到」，八件裡有七件無聲消失，完全看不出是這一行。
    import tempfile as _tf

    plain = process.Converter(_tf.mkdtemp())
    sheet = np.full((120, 700), 255, np.uint8)
    cv2.putText(sheet, "A123456789", (20, 85), cv2.FONT_HERSHEY_SIMPLEX, 1.6, 0, 4)
    plain_field = fieldmod.Field(id="a", name="身分證", column="id_number",
                                 kind="id_number", box=(0, 0, 700, 120))
    plain_record = process.Record("A", "x.pdf", 0)
    try:
        plain.__class__._read_field(plain, plain_record, sheet, plain_field, False, None)
    except Exception as error:                                      # noqa: BLE001
        problems.append("沒有印刷格子的身分證欄出錯：%s: %s"
                        % (type(error).__name__, error))
    else:
        if plain_record.values.get("id_number") != "A123456789":
            problems.append("沒有印刷格子的身分證欄讀成 %r"
                            % plain_record.values.get("id_number"))

    # 三種讀法都要能挑出通過檢查碼的那一個
    good = "A123456789"
    if validate.id_number(good)[1] is None:
        picked, problem = validate.best_id("A12345678", good, "")
        if problem is not None or picked != good:
            problems.append("身分證三種讀法沒有挑出通過檢查碼的那個：%r %s"
                            % (picked, problem))

    # 合成底圖：指定基準座標系的話，做出來的底圖一定落在那個座標系。
    # 樣板上的欄位框是照底圖量的，座標系換了就要整個重框 —— 日後樣本
    # 變多想把底圖重做得更乾淨，不可以連帶把框全部弄歪。
    from pipeline import baseimage

    form = np.full((900, 700, 3), 255, np.uint8)
    for row in range(12):
        cv2.putText(form, "LAND OFFICE FORM %02d" % row, (40, 70 + row * 65),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    cv2.rectangle(form, (40, 780), (660, 850), (0, 0, 0), 2)

    def _filled(shift, text):
        moved = cv2.warpAffine(
            form, np.float32([[1, 0, shift], [0, 1, -shift]]), (700, 900),
            borderValue=(255, 255, 255))
        cv2.putText(moved, text, (60 + shift, 835 - shift),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (120, 60, 40), 3)
        return moved

    scans = [_filled(shift, text) for shift, text in
             ((7, "AAA"), (13, "BBB"), (19, "CCC"), (25, "DDD"))]
    made, _weak = baseimage.compose(scans, reference=form)
    if made.shape != form.shape:
        problems.append("指定基準之後底圖尺寸變了：%s" % (made.shape,))
    else:
        landed = baseimage.coverage(form, made)
        if landed < 0.90:
            problems.append("合成底圖沒有落在指定的座標系上（覆蓋率 %.3f）" % landed)
        # 手寫要被抹掉：那一格裡不該還留著別人的字
        strip = baseimage.ink_mask(made)[780:850, 40:660]
        edges = baseimage.ink_mask(form)[780:850, 40:660]
        if int((strip > 0).sum()) > int((edges > 0).sum()) * 1.5:
            problems.append("合成底圖上還留著手寫（%d 對 %d 像素）"
                            % (int((strip > 0).sum()), int((edges > 0).sum())))

    for label, run, want in cases:
        try:
            got = run()
        except Exception as error:                                  # noqa: BLE001
            problems.append("%s 出錯：%s" % (label, error))
            continue
        if got != want:
            problems.append("%s 得到 %r，應該是 %r" % (label, got, want))

    return problems


def end_to_end():
    r"""從 PDF 一路跑到資料列，整條走一遍。

    單元檢查抓不到「函式簽章改了但呼叫端沒改」這種事 —— sheet_of 多回傳
    一個值的時候，每一個單元檢查都照樣通過，真正跑起來才會炸。
    所以這裡自己造一份 A4 表格（含身分證的十個印刷格子與手寫內容）、
    建樣板與底圖、存成 PDF，然後跑完整的 Converter.run()。

    這是**整條路走得通**的檢查，不是辨識品質的檢查 —— 合成出來的字乾淨得
    不像手寫，光靠整行讀就會對。它要擋下來的是分類、切件、減版面、裁欄位、
    驗證、產報告這幾段之間接不起來。辨識品質的檢查在上面各自的單元裡。

    回傳問題清單。
    """
    import json
    import tempfile

    import cv2
    import fitz
    import numpy as np

    from pipeline import diagnose, fields as fieldmod, process, resources

    problems = []
    work = tempfile.mkdtemp()
    store = os.path.join(work, "樣板")
    os.makedirs(os.path.join(store, "F"))
    width, height = 2480, 3508              # A4 @300dpi
    wanted = "G220390817"                   # 通得過檢查碼

    def draw(handwriting):
        img = np.full((height, width, 3), 255, np.uint8)
        cv2.rectangle(img, (200, 200), (2280, 3300), (0, 0, 0), 4)
        cv2.putText(img, "APPLICATION FORM 2026", (300, 300),
                    cv2.FONT_HERSHEY_SIMPLEX, 3.0, (0, 0, 0), 6)
        for index in range(11):             # 一字一格的印刷方格
            x = 300 + index * 130
            cv2.line(img, (x, 950), (x, 1130), (0, 0, 0), 4)
        cv2.line(img, (300, 950), (300 + 10 * 130, 950), (0, 0, 0), 4)
        cv2.line(img, (300, 1130), (300 + 10 * 130, 1130), (0, 0, 0), 4)
        rng = np.random.RandomState(7)      # 給對位用的固定特徵點
        for _ in range(400):
            x, y = rng.randint(250, 2200), rng.randint(1400, 3200)
            cv2.rectangle(img, (x, y), (x + 18, y + 18), (0, 0, 0), -1)
        if handwriting:
            for index, char in enumerate(wanted):
                cv2.putText(img, char, (300 + index * 130 + 30, 1090),
                            cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0), 5)
        return img

    blank, filled = draw(False), draw(True)
    resources.imwrite(os.path.join(store, "F", "base.png"), blank)
    resources.imwrite(os.path.join(store, "F", "front.png"), blank)
    with open(os.path.join(store, "F", "index.json"), "w", encoding="utf-8") as handle:
        json.dump({"code": "F", "name": "測試表", "pages": {"front": "front.png"}},
                  handle, ensure_ascii=False)
    fieldmod.save(store, "F", [fieldmod.Field(
        id="a", name="身分證", column="id_number", kind="id_number",
        box=(300, 950, 1300, 180))])

    # 一件固定兩頁：正面加一張空白背面。承辦人的作業規則就是這樣，
    # 而且這裡順便驗到「空白背面不會讓整件被判成不完整」。
    path = os.path.join(work, "scan.pdf")
    document = fitz.open()
    ok, buffer = cv2.imencode(".png", filled)
    page = document.new_page(width=595, height=842)
    page.insert_image(fitz.Rect(0, 0, 595, 842), stream=buffer.tobytes())
    document.new_page(width=595, height=842)        # 空白背面
    document.save(path)
    document.close()

    converter = process.Converter(store)
    records, unresolved = converter.run([path], keep_crops=True)
    if len(records) != 1:
        problems.append("端對端：應該辨識出 1 件，實際 %d 件（切不完整 %d）"
                        % (len(records), len(unresolved)))
        return problems
    got = records[0].values.get("id_number")
    if got != wanted:
        problems.append("端對端：身分證讀成 %r，應該是 %r" % (got, wanted))
    if records[0].problems.get("id_number"):
        problems.append("端對端：身分證被標記了 —— %s"
                        % records[0].problems["id_number"])

    # 報告要能產生，而且不可以夾帶個資
    text = diagnose.build(converter.journal, notes={"overall": "自我檢查"},
                          version=resources.version())
    if wanted in text:
        problems.append("端對端：診斷報告裡出現了未遮罩的身分證")
    return problems


def main():
    problems = check()
    problems.extend(end_to_end())
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

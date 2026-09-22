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

    # 「鳳」是承辦人回報最常讀不出來的字。實測它最常見的失敗**不是被讀成
    # 別的字，而是整個字沒被讀出來** —— 診斷報告裡「鳳吉一街」讀成兩個字、
    # 「鳳X路」讀成一個字。所以一字之差要涵蓋「少讀一個字」。
    if yingge:
        for raw, want in (("鳴路9號", "鳳鳴路9號"),        # 少讀一個字
                          ("吉一街9號", "鳳吉一街9號"),    # 少讀一個字
                          ("風鳴路9號", "鳳鳴路9號"),      # 讀成字形接近的字
                          ("凰鳴路9號", "鳳鳴路9號"),
                          ("鳯一路9號", "鳳一路9號")):
            value, problem = validate.address(raw, roads=yingge)
            if value != want:
                problems.append("門牌「%s」得到 %r，應該是 %r" % (raw, value, want))
            if not problem:
                problems.append("路名的字被改掉了卻沒有提醒：%s → %r" % (raw, value))
        # 承辦人說「只有鳳會有一三五路」，但官方清單裡龍三路、龍五路也在。
        # 分不出來的時候就是要回報分不出來，不可以照那句話挑一個。
        for raw in ("三路9號", "五路9號"):
            value, problem = validate.address(raw, roads=yingge)
            if not problem:
                problems.append("「%s」鳳與龍都可能，卻沒有標記：%r" % (raw, value))

    # 易混字表只能幫忙對到字典，不可以把兩條真的路混成一條
    for district_name in ("三峽區", "鶯歌區"):
        seen = {}
        for name in lexicon.for_district(roads_all, district_name):
            seen.setdefault(lexicon.canonical(lexicon.stem(name)), []).append(name)
        for key, group in seen.items():
            # 東湖街與東湖路本來主體就一樣，靠尾字分，不是易混字表造成的
            if len(group) > 1 and len({lexicon.stem(n) for n in group}) > 1:
                problems.append("易混字表把 %s 的「%s」混成同一條"
                                % (district_name, "」「".join(sorted(group))))

    # 有些表格的地址欄是一整串「地址：鶯歌區鳳福里12鄰鳳福路34巷5號」。
    # 門牌欄要的是路街名以後的部分，前面的標籤、區、里、鄰都要拿掉。
    if yingge:
        for raw, want in (
                ("地址: 鶯歌區鳳福里12鄰鳳福路123巷45號", "鳳福路123巷45號"),
                ("鶯歌區鳳福里12鄰鳳鳴路9號", "鳳鳴路9號"),
                ("地址：鶯歌區中湖街5巷3號", "中湖街5巷3號"),
                ("新北市鶯歌區鳳一路25號3樓", "鳳一路25號三樓")):
            value, problem = validate.address(raw, roads=yingge)
            if value != want:
                problems.append("門牌「%s」得到 %r，應該是 %r" % (raw, value, want))
            if problem:
                problems.append("「%s」不該被標記：%s" % (raw, problem))
    # 路名裡沒有「里」「鄰」，這一刀才切得下去
    for name in lexicon.for_district(roads_all, "三峽區") + \
            lexicon.for_district(roads_all, "鶯歌區"):
        if "里" in name or "鄰" in name:
            problems.append("路名「%s」帶了里或鄰，切地址那一刀會切到它" % name)

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

    # 公文文號：整頁上自己找，不靠框選。
    # 組成是固定的（民國年＋機關代號＋流水號，共十碼），整頁上符合這個
    # 格式的數字串幾乎只有它一個 —— 實測 A 表四件全中、沒有一件誤判。
    from pipeline import stamp

    if stamp.agency_codes():
        for texts, want in (
                # 實測 A 表第 1 頁抓到的所有十碼以上數字串
                (["307500000D_1150817_092512_5391_OLFETWLL15",
                  "1150817_006595_OLFLL15", "2026-08-1714:37:54",
                  "2026-08-3123:59:59", "機關收文", "115/08/18",
                  "1155698196"], "1155698196"),
                # 日期跟文號黏在一起
                (["機關收文115/08/251155698710"], "1155698710"),
                (["CR酮收文1155698710115/08/25"], "1155698710"),
                (["115FF005424機關收文115/08/251155698710"], "1155698710")):
            got, problem = stamp.pick(texts)
            if got != want or problem:
                problems.append("整頁找公文文號得到 %r（%s），應該是 %r"
                                % (got, problem, want))
        # 電話號碼剛好也是十碼，中間三碼不對就不能收
        got, _p = stamp.pick(["0910118290", "1150806221830", "1150807142810"])
        if got is not None:
            problems.append("整頁找公文文號把 %r 當成文號了" % got)
        # 好幾個都符合就要說分不出來，不可以挑一個
        got, problem = stamp.pick(["1155698196", "1155697295"])
        if got is not None or not problem:
            problems.append("兩個都符合機關代號卻挑了一個：%r" % got)

    # 關鍵字模式：電腦產製的表格欄位會上下移動，靠印刷標籤去找才穩。
    # 這裡畫一張有格線的表，同一張表把某一格撐高，兩次都要讀到一樣的值 ——
    # 「撐高之後還讀得對」正是這個模式存在的理由。
    from pipeline import pagetext

    def _table(extra_rows):
        rows = [("OWNER NAME", ["CHEN"]),
                ("OWNER ID", ["A123456789"]),
                ("LAND SITE", ["SEC FENGFU"] + extra_rows),
                ("HOUSE SITE", ["NO 41 LANE 3", "00368000"]),
                ("USE TYPE", ["SELF USE"])]
        height = 60 + sum(60 * max(len(v), 1) + 20 for _k, v in rows)
        sheet = np.full((height, 1000, 3), 255, np.uint8)
        y = 40
        cv2.line(sheet, (40, y), (960, y), (0, 0, 0), 2)
        for key, values in rows:
            tall = 60 * len(values) + 20
            cv2.putText(sheet, key, (50, y + 45), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, (0, 0, 0), 2)
            for index, value in enumerate(values):
                cv2.putText(sheet, value, (420, y + 45 + index * 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
            y += tall
            cv2.line(sheet, (40, y), (960, y), (0, 0, 0), 2)
        cv2.line(sheet, (40, 40), (40, y), (0, 0, 0), 2)
        cv2.line(sheet, (400, 40), (400, y), (0, 0, 0), 2)
        cv2.line(sheet, (960, 40), (960, y), (0, 0, 0), 2)
        return sheet

    for extra in ([], ["OTHER SITE A", "OTHER SITE B", "OTHER SITE C"]):
        sheet = _table(extra)
        lines = pagetext.read_image(sheet)
        rules = pagetext.horizontal_rules(sheet)
        if len(rules) < 6:
            problems.append("關鍵字模式找不到表格橫線（找到 %d 條）" % len(rules))
        for label, want in (("OWNER ID", "A123456789"),
                            ("HOUSE SITE", "NO 41 LANE 3")):
            got, _score, note = pagetext.value_for(
                lines, label, drop_digits=(label == "HOUSE SITE"), rules=rules)
            # 這裡要驗的是「挑到哪一格」，不是空白怎麼接 ——
            # 中文沒有空白，拉丁字母的分段只是這張測試圖的產物
            if got.replace(" ", "") != want.replace(" ", ""):
                problems.append(
                    "關鍵字「%s」（中間插 %d 列）讀到 %r，應該是 %r（%s）"
                    % (label, len(extra), got, want, note))

    # 門牌那一格底下的房屋建號（一整串數字）要丟掉
    sheet = _table([])
    lines = pagetext.read_image(sheet)
    rules = pagetext.horizontal_rules(sheet)
    kept, _s, _n = pagetext.value_for(lines, "HOUSE SITE", drop_digits=False,
                                      rules=rules)
    if "00368000" not in kept:
        problems.append("不丟數字時應該連建號一起讀到，得到 %r" % kept)

    # 同一格裡靠右的內容不可以被丟掉。
    # 實測 B 表的「地址：鶯歌區　　　115巷15號五樓」中間有一大段空白（原本
    # 印路名的地方被塗掉了），右半截曾經被當成「別欄」濾掉，門牌只剩前半段。
    wide = np.full((260, 1000, 3), 255, np.uint8)
    for y in (40, 140, 240):
        cv2.line(wide, (40, y), (960, y), (0, 0, 0), 2)
    for x in (40, 400, 960):
        cv2.line(wide, (x, 40), (x, 240), (0, 0, 0), 2)
    cv2.putText(wide, "HOUSE SITE", (50, 100), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (0, 0, 0), 2)
    cv2.putText(wide, "ADDR", (420, 100), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (0, 0, 0), 2)
    cv2.putText(wide, "NO 15", (800, 100), cv2.FONT_HERSHEY_SIMPLEX,
                0.8, (0, 0, 0), 2)          # 隔著一大段空白的後半截
    wide_lines = pagetext.read_image(wide)
    wide_rules = pagetext.horizontal_rules(wide)
    got, _s, _n = pagetext.value_for(wide_lines, "HOUSE SITE", rules=wide_rules)
    if "NO" not in got or "15" not in got:
        problems.append("同一格裡隔著空白的後半截被丟掉了：%r" % got)

    # 只有標籤沒有值的行（「建號：」「配偶姓名：」）不該接進值裡
    if pagetext._EMPTY_LABEL.match("建號：") is None:
        problems.append("「建號：」應該被當成空標籤")
    if pagetext._EMPTY_LABEL.match("地址：鶯歌區9號") is not None:
        problems.append("有值的行被當成空標籤了")

    # 「户」是「戶」的異體字，辨識常吐這個 —— 不轉的話「戶籍地址」永遠找不到
    if validate.to_traditional("户籍地址") != "戶籍地址":
        problems.append("户 沒有轉成 戶")

    # 公文文號現在是內網腳本 3、4 用來對應資料的鍵（腳本 4 第 75 行把它寫進
    # 查調系統的案號，腳本 3 第 52 行用它把下載結果對回身分證字號）。
    # 撞號會有一筆在對應表裡被蓋掉，而且從輸出的 Excel 上看不出來。
    dup = output.duplicate_doc_numbers([
        {"doc_number": "1155698196"}, {"doc_number": "1155697295"},
        {"doc_number": "1155698196"}, {"doc_number": ""}, {"doc_number": ""}])
    if dict(dup) != {"1155698196": 2, "": 2}:
        problems.append("重複的公文文號沒抓對：%r" % dup)
    if output.duplicate_doc_numbers([{"doc_number": "1155698196"},
                                     {"doc_number": "1155697295"}]):
        problems.append("沒有重複卻被誤報")

    # 本機小網頁伺服器的三道防護，真的開一台起來打打看。
    # 複核畫面上有姓名、身分證、門牌，這幾道漏一道就是個資外洩。
    problems.extend(_server_guard())
    problems.extend(_pages_carry_token())
    problems.extend(_offline())
    problems.extend(_inner_sheet())
    problems.extend(_household_sheet())
    problems.extend(_stamp_year_gate())
    problems.extend(_stamp_scans_both_ways())
    problems.extend(_doc_number_not_forced())
    problems.extend(_export_warnings())
    problems.extend(_review_page_can_add_manual())
    problems.extend(_address_drops_building_number())
    problems.extend(_address_floor_always_chinese())
    problems.extend(_address_follows_the_rule())
    problems.extend(_road_shortlist_when_stuck())
    problems.extend(_road_gap_tells_which_one())
    problems.extend(_zhongxing_street_is_gone())
    problems.extend(_report_keeps_its_own_words())
    problems.extend(_grid_survives_thin_lines())
    problems.extend(_grid_image_falls_back())
    problems.extend(_grid_state_tells_the_truth())
    problems.extend(_partial_id_beats_a_wrong_one())
    problems.extend(_id_search_space_stays_wide())
    problems.extend(_hard_cells_are_safe_to_send())
    problems.extend(_blank_cells_get_a_second_look())
    problems.extend(_per_cell_display_matches_the_count())
    problems.extend(_sheet_quality_reported())

    for label, run, want in cases:
        try:
            got = run()
        except Exception as error:                                  # noqa: BLE001
            problems.append("%s 出錯：%s" % (label, error))
            continue
        if got != want:
            problems.append("%s 得到 %r，應該是 %r" % (label, got, want))

    return problems


def _server_guard():
    """實際開一台伺服器，用各種不合法的請求打它，確認都被擋下來。

    這幾條規則很容易在改別的東西時被弄壞，而壞掉之後**畫面照樣正常**——
    只有攻擊者知道。所以一定要有自動檢查，不能靠記得。
    """
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    from tools import localserver

    problems = []

    guard = localserver.Guard()

    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        def _run(self, write):
            refused = guard.check(self, write=write)
            if refused:
                localserver.deny(self, *refused)
                return
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_GET(self):       # noqa: N802
            self._run(False)

        def do_POST(self):      # noqa: N802
            self._run(True)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    guard.port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = "127.0.0.1:%d" % guard.port
        good = "http://127.0.0.1:%d" % guard.port

        def call(method, path, headers=None, body=None):
            conn = http.client.HTTPConnection("127.0.0.1", guard.port, timeout=5)
            try:
                conn.request(method, path, body=body, headers=headers or {})
                return conn.getresponse().status
            finally:
                conn.close()

        checks = [
            ("帶著權杖的一般請求", 200,
             call("GET", "/?k=" + guard.token, {"Host": host})),
            ("沒帶權杖", 403, call("GET", "/", {"Host": host})),
            ("權杖是錯的", 403, call("GET", "/?k=wrong", {"Host": host})),
            ("Host 是別的網域（DNS rebinding）", 403,
             call("GET", "/?k=" + guard.token, {"Host": "evil.example.com"})),
            ("跨來源的請求", 403,
             call("GET", "/?k=" + guard.token,
                  {"Host": host, "Origin": "http://evil.example.com"})),
            ("同來源的請求", 200,
             call("GET", "/?k=" + guard.token, {"Host": host, "Origin": good})),
            ("表單送出的 POST（CSRF）", 415,
             call("POST", "/?k=" + guard.token,
                  {"Host": host, "Content-Type": "text/plain",
                   "Content-Length": "2"}, b"{}")),
            ("正常的 POST", 200,
             call("POST", "/?k=" + guard.token,
                  {"Host": host, "Content-Type": "application/json",
                   "Content-Length": "2"}, b"{}")),
        ]
        for label, want, got in checks:
            if got != want:
                problems.append("伺服器防護：%s 回了 %d，應該是 %d" % (label, got, want))
    finally:
        server.shutdown()
        server.server_close()

    # 樣板代號會被拿去組資料夾路徑，跳出去的一律擋掉
    for bad in ("../x", "a/b", "..", "a\\b", "", "x" * 20, "A B"):
        if localserver.safe_code(bad) is not None:
            problems.append("樣板代號 %r 應該被擋下來" % bad)
    for good_code in ("A", "F", "form-1", "a_2"):
        if localserver.safe_code(good_code) != good_code:
            problems.append("樣板代號 %r 不該被擋" % good_code)
    return problems


# 打包時會進到執行檔裡的程式碼。probe/ 是另外的診斷工具，不打包。
SHIPPED = ("app.py", "pipeline", "editor", "tools/review.py",
           "tools/template_editor.py", "tools/localserver.py", "tools/newform.py")

# 會對外連線的模組。socket 這一項連 localserver 自己都不例外 ——
# 它只用 secrets，真正開伺服器的是 app.py 用 http.server（那是本機的）。
NETWORK = ("socket", "urllib.request", "urllib.error", "requests", "httpx",
           "ftplib", "smtplib", "telnetlib", "poplib", "imaplib", "xmlrpc",
           "http.client", "websocket", "aiohttp", "boto3", "paramiko")


def _offline():
    """離線是這支程式的硬性需求，所以要有自動檢查盯著。

    承辦人的顧慮很具體：這是民眾的個資，不能有任何一個位元流到機關外面。
    「我看過程式碼、沒有連線」不是保證 —— 下一次改動就可能加進來，
    而且加進來的人不會知道這條規則。所以把它寫成檢查。

    兩層：
      靜態  出貨的程式碼裡不可以出現對外連線的模組
      動態  把 socket 攔下來，載入模組、跑一次真正的辨識，看有沒有人想連線
    """
    import ast
    import socket

    import cv2
    import numpy as np

    from pipeline import resources

    problems = []
    root = resources.base_dir()

    # ---- 靜態：出貨的程式碼有沒有 import 對外連線的東西 ----
    targets = []
    for item in SHIPPED:
        full = os.path.join(root, item)
        if os.path.isdir(full):
            for folder, _dirs, names in os.walk(full):
                targets += [os.path.join(folder, n) for n in names if n.endswith(".py")]
        elif full.endswith(".py") and os.path.isfile(full):
            targets.append(full)

    for path in targets:
        with open(path, encoding="utf-8") as handle:
            try:
                tree = ast.parse(handle.read(), filename=path)
            except SyntaxError as error:
                problems.append("%s 語法有問題：%s" % (path, error))
                continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                for banned in NETWORK:
                    if name == banned or name.startswith(banned + "."):
                        problems.append(
                            "%s 第 %d 行 import 了 %s —— 這支程式必須完全離線"
                            % (os.path.relpath(path, root), node.lineno, name))

    # ---- 動態：真的攔一次 ----
    allowed = {"127.0.0.1", "::1", "localhost", None}
    tried = []
    real_connect = socket.socket.connect
    real_getaddrinfo = socket.getaddrinfo

    def watched_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in allowed:
            tried.append(str(host))
        return real_connect(self, address)

    def watched_getaddrinfo(host, *args, **kwargs):
        if host not in allowed:
            tried.append(str(host))
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = watched_connect
    socket.getaddrinfo = watched_getaddrinfo
    try:
        sheet = np.full((160, 700, 3), 255, np.uint8)
        cv2.putText(sheet, "OFFLINE CHECK 1155698196", (20, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
        from pipeline import pagetext as _pagetext

        _pagetext.read_image(sheet)
    finally:
        socket.socket.connect = real_connect
        socket.getaddrinfo = real_getaddrinfo

    if tried:
        problems.append("載入模型與辨識時試圖連線到：%s" % "、".join(sorted(set(tried))))
    return problems


def _inner_sheet():
    """內網中繼檔的 A 欄放的是公文文號，不是流水號。

    這一欄餵給內網腳本 2，而腳本 2 是**用欄位名稱取值**的，所以名稱一旦
    改掉就可能拿到空值而且沒有任何錯誤訊息 —— 名稱要釘住，內容才是我們的事。
    """
    import tempfile

    from openpyxl import load_workbook

    from pipeline import output

    problems = []
    records = [
        {"doc_number": "1155698196", "district": "鶯歌區",
         "id_number": "A123456789", "address": "鳳鳴路9號", "name": "王大明"},
        # 公文文號讀不到的那一件：留空，**不可以**退回流水號 ——
        # 同一欄混兩種東西，從 Excel 上分不出哪一格是文號、哪一格是第幾列
        {"doc_number": "", "district": "三峽區",
         "id_number": "A223456780", "address": "民生街1號", "name": "李小華"},
    ]
    folder = tempfile.mkdtemp()
    path = output.write_inner(records, os.path.join(folder, "HH1150907_01.xlsx"))
    sheet = load_workbook(path).active

    headers = [cell.value for cell in sheet[1]]
    if headers != output.INNER_HEADERS:
        problems.append("內網標題列變成 %r，腳本 2 靠名稱取值，不能動" % headers)
    if headers and headers[0] != "序號":
        problems.append("A 欄標題被改名了：%r" % headers[0])

    first = sheet.cell(row=2, column=1).value
    if first != "1155698196":
        problems.append("內網 A 欄應該是公文文號，得到 %r" % first)
    if not isinstance(first, str):
        problems.append("公文文號要存成文字，得到 %s" % type(first).__name__)

    second = sheet.cell(row=3, column=1).value
    if second not in ("", None):
        problems.append("讀不到公文文號時 A 欄應該留空，得到 %r" % second)

    # 其餘欄位沒有被推移
    if sheet.cell(row=2, column=2).value != "鶯歌區":
        problems.append("B 欄不是行政區：%r" % sheet.cell(row=2, column=2).value)
    if sheet.cell(row=2, column=4).value != "新北市鶯歌區鳳鳴路9號":
        problems.append("D 欄完整地址不對：%r" % sheet.cell(row=2, column=4).value)

    # 外網那份的「申請案號或事由」本來就放公文文號，兩份要一致
    outer = output.write_outer(records, os.path.join(folder, "outer.xlsx"))
    osheet = load_workbook(outer).active
    if osheet.cell(row=2, column=3).value != "1155698196":
        problems.append("外網 C 欄的公文文號不對：%r"
                        % osheet.cell(row=2, column=3).value)
    return problems


def _household_sheet():
    """戶政系統直接匯入用的地址清冊（.xls）。

    規格是從承辦人翻拍的 YHQ101_addr_Sample.xls 逆向的，**還沒實測匯入過**，
    所以這裡釘住的是「照片上看得到的事實」那幾條：沒有標題列、六個欄位的順序、
    門牌的巷弄號是全形、真的是 .xls 而不是改副檔名的 .xlsx。
    """
    import tempfile

    from pipeline import output

    problems = []
    records = [
        {"doc_number": "1155698196", "district": "鶯歌區",
         "address": "鳳鳴路9號六樓", "name": "王大明", "id_number": "A123456789"},
        {"doc_number": "1155691776", "district": "三峽區",
         "address": "大學路176之3號十八樓", "name": "李小華",
         "id_number": "A223456780"},
    ]
    folder = tempfile.mkdtemp()
    path = output.write_household(records, os.path.join(folder, "YHQ101_addr_x.xls"))

    # 真的是 .xls（OLE 複合檔），不是改了副檔名的 zip
    with open(path, "rb") as handle:
        magic = handle.read(8)
    if magic != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        problems.append("戶政清冊不是真正的 .xls（開頭是 %r）" % magic)

    # 全形只換數字，段與樓層的中文數字不能動
    if output.to_fullwidth("鳳鳴路9號六樓") != "鳳鳴路９號六樓":
        problems.append("全形轉換不對：%r" % output.to_fullwidth("鳳鳴路9號六樓"))
    if output.to_fullwidth("中正路二段15號") != "中正路二段１５號":
        problems.append("全形轉換動到中文數字：%r"
                        % output.to_fullwidth("中正路二段15號"))

    # 以前這裡是「裝不到 xlrd 就直接 return」，於是 Windows 建置機上沒裝
    # 的那段期間，下面每一條都靜靜地沒跑過，建置照樣綠燈。
    # 現在 xlrd 列進 requirements.txt，缺了就讓檢查不過 ——
    # 「跳過但不出聲」比沒有檢查更糟，它給的是假的安心。
    try:
        import xlrd
    except ImportError:
        problems.append("沒有裝 xlrd，戶政清冊的內容就沒有被驗過 —— "
                        "請 pip install -r requirements.txt")
        return problems

    sheet = xlrd.open_workbook(path).sheet_by_index(0)
    if sheet.ncols != 6:
        problems.append("戶政清冊有 %d 欄，範例是 6 欄" % sheet.ncols)
    if sheet.nrows != len(records):
        problems.append("戶政清冊有 %d 列 —— 範例**沒有標題列**，第 1 列就是資料"
                        % sheet.nrows)
    want = ["1155698196", "新北市", "鶯歌區", "", "", "鳳鳴路９號六樓"]
    got = [sheet.cell_value(0, c) for c in range(min(sheet.ncols, 6))]
    if got != want:
        problems.append("戶政清冊第 1 列是 %r，應該是 %r" % (got, want))
    if sheet.nrows > 1 and sheet.cell_value(1, 5) != "大學路１７６之３號十八樓":
        problems.append("門牌的全形沒轉：%r" % sheet.cell_value(1, 5))
    return problems


def _pages_carry_token():
    """畫面上每一個 api/ 請求都必須包在 api() 裡（權杖是那個函式加上去的）。

    **這條檢查是踩過坑才有的。** 加本機權杖那一次，我把所有 fetch() 都包好了，
    卻漏掉樣板編輯器裡用 img.src 載入掃描影像的那一行 ——

        img.src = 'api/image?code=' + ...        ← 沒有權杖

    結果被自己的防護擋成 403：右邊的欄位清單正常（那些走 fetch），左邊的圖
    整片空白。而且**畫面上不會有任何錯誤訊息**，看起來就像程式壞了。

    漏掉一個就會壞掉、壞掉又看不出原因，這種東西不能靠記得。
    """
    import re

    from pipeline import resources

    problems = []
    root = resources.base_dir()
    for name in ("page.html", "review.html"):
        path = os.path.join(root, "editor", name)
        if not os.path.isfile(path):
            problems.append("找不到 %s" % name)
            continue
        text = open(path, encoding="utf-8").read()
        # 抓出所有出現 'api/xxx' 或 `api/xxx` 的地方，看前面有沒有 api(
        for match in re.finditer(r"""['"`]api/[A-Za-z]+""", text):
            start = match.start()
            line = text.count("\n", 0, start) + 1
            before = text[max(0, start - 6):start]
            if "api(" not in before:
                problems.append(
                    "editor/%s 第 %d 行的 api/ 請求沒有包在 api() 裡，"
                    "會因為沒帶權杖被擋成 403：%s"
                    % (name, line, text[start:start + 40].replace("\n", " ")))
    return problems


def _grid_survives_thin_lines():
    """影印件那種又細又淡的格線，合成底稿之後還要找得到。

    **這條檢查是踩過坑才有的。** 承辦人的 C 表是房屋稅來文的影印件，格線
    又細又淡。底稿的合成規則是「逐像素取最亮的」—— 只要有任何一份樣本在
    那個點沒有墨，那個點就變白。每份掃描對位差一兩個像素，三份一疊就把
    格線吃到只剩殘骸，程式再也認不出「一排格子」。

    後果不是「差一點」，是**整欄退回整行讀**：身分證十個字連在一起讀成
    六碼、七碼，而診斷報告上只看得到「格數 0」這三個字。

    拿承辦人給的真實 C 表實測：單張原稿找得到 5~11 條格線，三份／四份／
    五份合成的底稿**全部找不到**；加粗 4 像素之後找到 11 條，grid_spans
    切出 10 格、每格寬 94~96 像素。

    負向驗證兩條，都在最後：
      * 加粗 0 的結果必須跟預設完全一樣（相減用的底稿一個像素都不能動）
      * 加粗過的底稿必須真的比較黑（不然這條檢查等於什麼都沒測）
    """
    import cv2
    import numpy as np

    from pipeline import baseimage, recognise

    problems = []

    # 固定的特徵點，讓 align 有東西可以對；位置固定所以每次跑結果一樣
    texture = np.random.RandomState(1)
    dots = [(texture.randint(20, 1180), texture.randint(20, 240)) for _ in range(500)]

    def photocopy(seed, dx, dy):
        """一份「影印件」：細又淡的格線 + 每份不同的手寫 + 對位誤差。"""
        rng = np.random.RandomState(seed)
        img = np.full((260, 1200), 255, np.uint8)
        for x, y in dots:
            cv2.rectangle(img, (x, y), (x + 3, y + 3), 40, -1)
        for index in range(11):                       # 十個格子要十一條線
            cv2.line(img, (60 + index * 100, 40), (60 + index * 100, 210), 150, 1)
        for index in range(10):                       # 每份寫不一樣的字
            cv2.putText(img, str(rng.randint(0, 10)),
                        (60 + index * 100 + 25, 170),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.8, 0, 4)
        return cv2.warpAffine(img, np.float32([[1, 0, dx], [0, 1, dy]]),
                              (1200, 260), borderValue=255)

    samples = [photocopy(0, 0, 0), photocopy(1, 2, 1), photocopy(2, -2, -1),
               photocopy(3, 1, -2), photocopy(4, -1, 2)]

    plain, weak = baseimage.compose(samples)
    if weak:
        problems.append("測試樣本有 %d 份對不齊，這條檢查測不到東西" % len(weak))
    thick, _weak = baseimage.compose(samples, thicken=baseimage.GRID_THICKEN)

    # 正向：加粗過的那張找得到一排格線
    lines, _top, _bottom = recognise.grid_lines(thick)
    if not lines or len(lines) < recognise.MIN_CELLS + 1:
        problems.append("加粗 %d 之後還是找不到格線（找到 %s 條）"
                        % (baseimage.GRID_THICKEN,
                           len(lines) if lines else 0))

    # 這條檢查要有意義，前提是「不加粗真的會失敗」。不會失敗就代表
    # 樣本做得不像影印件，這條檢查是假的安心。
    before, _t, _b = recognise.grid_lines(plain)
    if before and len(before) >= recognise.MIN_CELLS + 1:
        problems.append("不加粗竟然也找得到格線 —— 測試樣本沒有重現影印件"
                        "被吃掉格線的情形，這條檢查等於沒測")

    # 負向驗證一：相減用的底稿一個像素都不能動
    again, _weak = baseimage.compose(samples, thicken=0)
    if not np.array_equal(plain, again):
        problems.append("thicken=0 的結果跟預設不一樣 —— 相減用的底稿被動到了")

    # 負向驗證二：加粗過的那張必須真的比較黑
    if (thick < 160).sum() <= (plain < 160).sum():
        problems.append("加粗過的底稿沒有比較黑，thicken 根本沒有作用")
    return problems


def _grid_image_falls_back():
    """沒有 grid.png 的舊樣板要照舊能跑，有的時候要用它。

    退回 base.png 是刻意的：舊樣板還沒重做底稿，退回去就是改之前的行為，
    不會壞掉，重做一次就自動變好。**「改版之後舊樣板整批壞掉」比慢一點
    才變好糟得多。**
    """
    import json
    import tempfile

    import cv2
    import numpy as np

    from pipeline import process, resources

    problems = []
    store = tempfile.mkdtemp()
    os.makedirs(os.path.join(store, "Z"))
    with open(os.path.join(store, "Z", "index.json"), "w", encoding="utf-8") as handle:
        json.dump({"code": "Z", "name": "測試", "pages": {}}, handle, ensure_ascii=False)

    base = np.full((80, 80, 3), 200, np.uint8)
    resources.imwrite(os.path.join(store, "Z", "base.png"), base)

    converter = process.Converter(store)
    got = converter.grid_of("Z")
    if got is None or int(got.mean()) != 200:
        problems.append("沒有 grid.png 的時候沒有退回 base.png")

    grid = np.full((80, 80, 3), 100, np.uint8)
    resources.imwrite(os.path.join(store, "Z", "grid.png"), grid)
    converter = process.Converter(store)          # 重新載入，不吃快取
    got = converter.grid_of("Z")
    if got is None or int(got.mean()) != 100:
        problems.append("有 grid.png 的時候沒有拿它來用")
    return problems


def _per_cell_display_matches_the_count():
    """診斷報告上「逐格」印出來的東西，要跟旁邊那句「有幾格沒讀到」數得起來。

    2026-09-21 F 表第 4 件：逐格印「A|9|9|9|9|?|9|?|A|A」，說明卻寫
    「有 4 格完全沒讀到」—— 數得出來的「?」只有兩個。**我自己先被騙了一次**，
    以為是報告算錯。真相是那兩格讀到的是對不回數字的字母，對解碼完全沒用，
    所以算「沒讀到」，但印出來看起來好好的。

    看不懂的報告比沒有報告糟，它會把人帶去錯的方向 —— 跟 2026-09-09
    那次「（字字字）其實是（沒讀到）」是同一顆雷。

    負向驗證在最後兩段：讀對的那幾格**不可以**被標上問號（每一格都標等於
    沒標），而且標記不可以混進輸出的值裡。
    """
    from pipeline import validate

    problems = []

    def shown(cells):
        return "|".join(validate.cell_shown(c, i) for i, c in enumerate(cells))

    # 讀到的字對不回這一位該有的東西：印出來要標，數量要跟說明對得起來
    cells = [["A"], ["1"], ["2"], ["3"], ["4"], [], ["5"], [], ["N"], ["王"]]
    text = shown(cells)
    _got, why = validate.solve_id(cells)
    marks = text.count("?")
    if marks != 4:
        problems.append("逐格印「%s」，標出來的只有 %d 格" % (text, marks))
    if not why or "4 格" not in why:
        problems.append("說明沒有寫出幾格讀不出來：%s" % why)

    # 負向驗證一：十格都讀對的時候，一個問號都不可以有
    good = [["A"], ["1"], ["2"], ["3"], ["4"], ["5"], ["6"], ["7"], ["8"], ["9"]]
    text = shown(good)
    if "?" in text:
        problems.append("十格都讀對了卻還是標了問號：%s —— "
                        "每一格都標等於沒標" % text)

    # 負向驗證二：對得回數字的字母（A→4）算讀到了，不可以標
    text = shown([["A"], ["1"], ["2"], ["3"], ["4"], ["5"], ["6"], ["7"], ["A"], ["9"]])
    if "A?" in text:
        problems.append("「A」在數字位對得回 4，不該被標成讀不出來：%s" % text)

    # 負向驗證三：標記不可以混進輸出的值。逐格那一欄是給人看的，
    # 寫進 Excel 的身分證裡多一個「?」，RPA 就查不到人了。
    source = open(os.path.join(resources_base(), "pipeline", "process.py"),
                  encoding="utf-8").read()
    if 'raw = how["逐格"]' in source:
        problems.append("辨識原文是直接拿「逐格」那串顯示字串來用的，"
                        "標記會跟著混進值裡")
    if "plain_cells" not in source:
        problems.append("process.py 沒有留一份乾淨的逐格結果給輸出用")
    return problems


def resources_base():
    from pipeline import resources
    return resources.base_dir()


def _partial_id_beats_a_wrong_one():
    """檢查碼推不出唯一解的時候，交給人的要是「逐格結果 + ?」，不是一串錯號碼。

    2026-09-21 F 表第 4 件：格子切出十格、逐格讀到六格，但有四格讀不出來，
    檢查碼湊不出唯一解。程式退回整行讀的結果，那是一串**九碼**的東西 ——
    長度都不對。承辦人拿到它只能整串重打，還得自己一格一格對位置。

    逐格的位置是對的，讀出來的那幾格多半也是對的。把它原樣交出去，
    人只要補「?」那幾格。

    負向驗證有三段，每一段擋的都是「靜靜寫錯」：
      一、「?」一定要過不了驗證，不可以被當成讀好的值送進 RPA
      二、匯出前那一關一定要攔下來
      三、十格全讀到、檢查碼也過的時候**不可以**變成問號串
    """
    from pipeline import validate
    from tools import review

    problems = []

    # 六格讀到、四格讀不出來（第 4 件的形狀）
    cells = [["A"], ["1"], ["2"], ["3"], ["4"], [], ["5"], [], ["N"], ["王"]]
    partial = validate.partial_id(cells)
    if partial != "A1234?5???":
        problems.append("逐格結果應該是「A1234?5???」，實際是「%s」" % partial)
    if len(partial) != 10:
        problems.append("交給人的長度不是 10，位置就對不起來：「%s」" % partial)

    # 負向驗證一：「?」一定要過不了驗證
    _fixed, why = validate.id_number(partial)
    if not why:
        problems.append("「%s」竟然通過驗證 —— 那會被當成讀好的值送進 RPA" % partial)

    # 負向驗證二：匯出前那一關要攔下來
    row = {"district": "三峽區", "address": "民生街27巷26號16樓",
           "id_number": partial, "doc_number": "1155699478", "name": "許三"}
    if not any("身分證" in line for line in review.export_warnings([row])):
        problems.append("匯出前的檢查沒有攔下帶「?」的身分證")

    # 負向驗證三：十格全讀到就不可以變成問號串
    good = [[c] for c in "A123456789"]
    if "?" in (validate.partial_id(good) or "?"):
        problems.append("十格都讀到了卻還是給問號串：%s" % validate.partial_id(good))

    # 接線檢查：process.py 真的有把它交出去。上面全測的是函式本身，
    # 接線斷掉的話一條都不會叫。
    source = open(os.path.join(resources_base(), "pipeline", "process.py"),
                  encoding="utf-8").read()
    if "validate.partial_id(per_cell)" not in source:
        problems.append("process.py 沒有把逐格的部分結果交給複核畫面")
    if 'partial.count("?") < 10' not in source:
        problems.append("一格都沒讀到的時候還是交出整串問號，那比整行讀的結果更沒用")

    # 複核畫面要看得懂「?」。對著問號說「有一碼打錯了」，
    # 人會去找一個不存在的錯字。
    page = open(os.path.join(resources_base(), "editor", "review.html"),
                encoding="utf-8").read()
    if "const holes" not in page or "讀不出來的（?）" not in page:
        problems.append("複核畫面把「?」當成打錯字，沒有講出那是程式讀不出來")
    return problems


def _blank_cells_get_a_second_look():
    """滑動視窗完全讀不到的格子，要用「沒有緊裁」的那一版再問一次。

    trim() 在乾淨的字上比較好（那是量過的），但在難字上會把字弄丟 ——
    承辦人 2026-09-22 從實際作業傳回來的八格難字，緊裁版一格都讀不出來，
    不裁直接讀反而讀對兩格。

    **只能補空的格子。** 兩種都讀進候選試過了：拿假號碼三十格量，真值在
    候選裡的比例一模一樣（28/30），卻害兩格從「唯一決定」變成「兩種讀法
    互相矛盾」，還多一倍運算。空的格子沒有東西可以矛盾，所以只在那裡補。

    負向驗證在最後一段：已經讀到東西的格子**不可以**被再讀一次，
    不然就是把那個退步原封不動加回來。
    """
    import numpy as np

    from pipeline import recognise

    problems = []
    crop = np.full((40, 90), 255, np.uint8)
    spans = [(0, 30), (30, 60), (60, 90)]

    def run(fake):
        real_read, real_trim = recognise.read_only, recognise.trim
        calls = []
        try:
            recognise.read_only = fake
            recognise.trim = lambda img, pad_ratio=0.25: ("TRIMMED", img)[0]
            return recognise.read_grid(crop, spans, None), calls
        finally:
            recognise.read_only = real_read
            recognise.trim = real_trim

    # 緊裁版永遠讀不到，沒緊裁的讀得到 —— 補讀要接手
    seen = []

    def only_raw(image):
        seen.append(image)
        return "" if isinstance(image, str) else "7"

    picks, _ = run(only_raw)
    if [p for p in picks] != [["7"], ["7"], ["7"]]:
        problems.append("緊裁版讀不到的時候，沒有用沒緊裁的那一版補回來：%s" % picks)

    # 負向驗證：已經讀到東西的格子不可以再讀一次（那會把矛盾的候選加進來）
    def always_reads(image):
        # 三格視窗回三個字、一格回一個字；沒緊裁的會回別的字
        if isinstance(image, str):
            return "555"[:1] if False else "5"
        return "9"

    real_read, real_trim = recognise.read_only, recognise.trim
    try:
        counted = []
        recognise.read_only = lambda img: (counted.append(img), "5")[1]
        recognise.trim = lambda img, pad_ratio=0.25: "TRIMMED"
        picks = recognise.read_grid(crop, spans, None)
        if any(len(p) > 1 for p in picks):
            problems.append("已經讀到東西的格子又被讀了一次，候選變成 %s —— "
                            "那正是量出來會退步的那個做法" % picks)
        if any(not isinstance(img, str) for img in counted):
            problems.append("已經讀到東西了，卻還是去讀沒緊裁的那一版")
    finally:
        recognise.read_only = real_read
        recognise.trim = real_trim

    # 接線檢查：補讀那一段要真的被「空的才補」擋住
    source = open(os.path.join(resources_base(), "pipeline", "recognise.py"),
                  encoding="utf-8").read()
    if "for index, got in enumerate(picks):" not in source or \
       "        if got:\n            continue" not in source:
        problems.append("補讀那一段沒有用「已經讀到就跳過」擋住")
    return problems


def _hard_cells_are_safe_to_send():
    """「難字回報」只可以收程式讀錯的那幾格，而且不可以留下是哪一件的線索。

    承辦人 2026-09-22：「診斷的部分你會加一份某字辨識不出來嗎？然後我再
    修正給你之類的。」手寫辨識要變好只有一條路 —— 拿「圖 + 正確答案」去量。

    **這份東西是刻意做成可以外傳的，所以每一條規矩都是資安規矩：**

      只收讀錯的    一件十格通常只錯一兩格，缺了其他八格就湊不回號碼。
                    十格全收等於把整個身分證號搬出去（就算打亂，一天就
                    那幾件，憑筆跡拼得回來）。
      檔名不帶件號  留了件號，同一件的那幾格就串得起來。
      檔名不帶格號  留了格號，配上件號就是完整的位置資訊。
      順序要打亂    檔名沒有關聯資訊的話，寫檔順序就是最後一個破口。

    負向驗證在最後四段，每一段對應上面一條規矩。
    """
    import glob
    import re
    import tempfile

    import numpy as np

    from pipeline import process

    _ = (glob, np, tempfile)

    problems = []

    class Fake:
        def __init__(self):
            self.cells = {"id_number": [np.full((40, 30), 200, np.uint8)
                                        for _ in range(10)]}
            #           A  1  2  3  4  5  6  7  8  9   ← 真值
            # 程式讀到： A  1  X  3  4  （空）6  7  8  9
            self.cell_text = {"id_number": [["A"], ["1"], ["X"], ["3"], ["4"],
                                            [], ["6"], ["7"], ["8"], ["9"]]}

    work = tempfile.mkdtemp()
    rows = [{"id_number": "A123456789"}]
    folder, count = process.dump_hard_cells([Fake()], rows, work)
    if count != 2:
        problems.append("十格裡錯兩格，應該收兩張，實際收了 %d 張" % count)
        return problems

    names = [os.path.basename(f) for f in glob.glob(os.path.join(folder, "*.png"))]

    # 正向：檔名要帶得出正確答案，開發者才知道該是什麼
    if not any("真值2" in n for n in names):
        problems.append("讀成別的字那一格沒有記下正確答案：%s" % names)
    if not any("真值5" in n and "沒讀到" in n for n in names):
        problems.append("完全沒讀到那一格沒有記下來：%s" % names)

    # 負向驗證一：讀對的格子一張都不可以收
    for right in ("真值1_", "真值3_", "真值4_", "真值6_", "真值7_",
                  "真值8_", "真值9_", "真值A_"):
        if any(right in n for n in names):
            problems.append("讀對的格子也被收進去了（%s）—— "
                            "十格全收就湊得回整個身分證號" % right)

    # 負向驗證二、三：檔名不可以留下是哪一件、哪一格
    for name in names:
        if re.search(r"第\s*\d+\s*(件|格)", name) or "件" in name or "格" in name:
            problems.append("檔名留下了件號或格號（%s）—— "
                            "同一件的那幾格就串得起來了" % name)

    # 負向驗證四：讀我.txt 一定要在，而且要講清楚為什麼可以外傳
    readme = os.path.join(folder, "讀我.txt")
    if not os.path.isfile(readme):
        problems.append("沒有讀我.txt —— 承辦人不會知道這份能不能傳")
    else:
        text = open(readme, encoding="utf-8").read()
        for need in ("只有讀錯", "自己打開看一遍"):
            if need not in text:
                problems.append("讀我.txt 沒有寫「%s」" % need)

    # 一個字都沒錯的時候不可以憑空生出資料夾
    folder2, count2 = process.dump_hard_cells(
        [Fake()], [{"id_number": "A1X34X6789".replace("X", "2")}], work)
    good = process.dump_hard_cells(
        [type("F2", (), {"cells": {"id_number": [np.full((40, 30), 200, np.uint8)] * 10},
                         "cell_text": {"id_number": [[c] for c in "A123456789"]}})()],
        [{"id_number": "A123456789"}], work)
    if good[1] != 0:
        problems.append("十格全讀對，卻還是收了 %d 張" % good[1])

    # ── 門牌：只收路名那一格 ────────────────────────────────────
    #
    # 承辦人 2026-09-22 問門牌能不能也收。能，但**只能收路名**：
    # 路名是公開的街道名稱，一條路上幾百戶，單獨一個指不向任何人；
    # 門牌號、樓層一旦配上路名就是完整住址，那是實實在在的個資。
    class WithRoad:
        def __init__(self, read):
            self.cells = {}
            self.cell_text = {}
            self.road_cell = (np.full((40, 60), 180, np.uint8), read)

    folder, count = process.dump_hard_cells(
        [WithRoad("4大觀"), WithRoad("大觀"), WithRoad("")],
        [{"address": "大觀路147號九樓"}, {"address": "大觀路147號九樓"},
         {"address": "鳳鳴路12巷5號十二樓"}],
        tempfile.mkdtemp())
    names = ([os.path.basename(f) for f in glob.glob(os.path.join(folder, "*.png"))]
             if folder else [])
    if count != 2:
        problems.append("三件裡兩件路名讀錯，應該收兩張，實際 %d 張（%s）"
                        % (count, names))
    if not any("真值大觀" in n and "4大觀" in n for n in names):
        problems.append("路名讀錯沒有記下正確答案與讀到的東西：%s" % names)
    if any("真值大觀" in n and "程式讀成大觀" in n for n in names):
        problems.append("路名讀對了也被收進去")

    # 負向驗證：門牌號、樓、巷、弄**一個都不可以**出現在檔名裡。
    # 那幾個配上路名就是完整住址，這份資料夾是要外傳的。
    # 最後那段隨機碼不算（它本來就會有數字），所以先切掉再看。
    for name in names:
        body = name.rsplit("_", 1)[0]
        for leak in ("147", "12", "號", "樓", "巷", "弄"):
            if leak in body:
                problems.append("檔名裡出現了「%s」（%s）—— "
                                "門牌號或樓層配上路名就是完整住址" % (leak, name))
                break

    # ── 「路名那一格」不可以其實是整串住址 ────────────────────────
    #
    # **這是踩到才有的檢查，而且是我把承辦人的住址收出機關了。**
    # 2026-09-22 他傳回第一批難字回報，裡面的「路名」那幾張是整串完整住址
    # ——「程式讀成光明路75巷16號六樓之1」直接寫在檔名上。當時的條件只有
    # 「第一段、後綴是空的」，而門牌只框一個大框的表格（E 表那種）就只有
    # 一段，那一段當然是整串地址。
    for text, segs, want, why in (
            ("光明路75巷16號六樓之1", 1, False, "整串住址、門牌只有一個框"),
            ("光明路75巷16號六樓之1", 4, False, "整串住址（框到了整排）"),
            ("大觀", 4, True, "真的只有路名"),
            ("中園街", 4, True, "路名帶街字"),
            ("4大觀", 4, True, "路名前面多一個數字，還是路名"),
            ("鳳吉一街", 4, True, "字典裡最長的路名"),
            ("某某某某某某某", 4, False, "七個字，路名不會這麼長"),
            ("中正路15號", 4, False, "帶了號"),
            ("中正15樓", 4, False, "帶了樓"),
            ("", 4, False, "什麼都沒讀到")):
        got = process.only_the_road(text, segs)
        if got != want:
            problems.append("「%s」（%d 段，%s）應該%s，實際%s"
                            % (text, segs, why,
                               "收" if want else "不收", "收" if got else "不收"))

    # 負向驗證：只有一個框的門牌**永遠**不可以收 —— 那一定是整串住址
    for text in ("大觀", "中園街", "光明路75號"):
        if process.only_the_road(text, 1):
            problems.append("門牌只有一個框卻收了「%s」—— "
                            "只有一個框就是整串住址，路名切不出來" % text)

    # 接線檢查：_read_field 真的用這條規則擋，不是自己另外寫一套
    code = open(os.path.join(resources_base(), "pipeline", "process.py"),
                encoding="utf-8").read()
    if "only_the_road(text, len(definition.segments()))" not in code:
        problems.append("存路名那一格的時候沒有用 only_the_road 擋，"
                        "整串住址會被當成路名送出機關")

    # 接線檢查：匯出的時候真的會呼叫它
    source = open(os.path.join(resources_base(), "tools", "review.py"),
                  encoding="utf-8").read()
    where = source.find("process.dump_hard_cells(")
    if where < 0:
        problems.append("匯出時沒有產生難字回報，這份資料永遠不會累積起來")
    else:
        # 它是附加功能，壞掉不可以連累輸出檔 —— 檔案那時候已經產好了
        block = source[max(0, where - 200):where + 600]
        if "try:" not in block or "except Exception" not in block:
            problems.append("難字回報沒有包住例外 —— 它壞掉會連累輸出檔")
    return problems


def _id_search_space_stays_wide():
    """讀不出來的格子一定要攤開全部十個數字，**不可以**縮到模型覺得最像的那幾個。

    承辦人 2026-09-21 提的想法是對的方向：身分證只可能是英數，所以把不合法
    的字元遮掉、只在數字裡排名，確實救得回來 —— 拿他給的五格實測，
    真值全部進得了前三名，而目前讀不出來的那一格，真值還排第一名。

    **但那個排名只能拿來給人看，不能拿去縮小檢查碼的搜尋範圍。**
    實測造一排十格（真值 F128887458，中間兩格用承辦人給的難字），
    模型限定數字後的前三名是「127」與「729」——「8」不在第二格的候選裡：

        縮到前三名    只有一個組合通過檢查碼 → F128887958
                      **錯的，而且通過檢查碼，畫面上完全看不出來**
        攤開十個      十個組合都通過 → 分不出是哪一個，標記起來交給人

    攤開的時候真值一定在搜尋範圍內，所以「唯一解」才可信；一旦縮小而真值
    被排除，檢查碼反而會很有信心地挑出一個錯的號碼送進 RPA。這正是
    CLAUDE.md 第三條在講的事：分不出來就回報分不出來，不要猜。
    """
    import itertools

    from pipeline import validate

    problems = []

    # 讀不出來的格子要攤開十個
    choices, was_read = validate._cell_options([], 3)
    if was_read or len(choices) != 10:
        problems.append("讀不出來的格子沒有攤開十個數字，只有 %s" % choices)

    # 把上面那個實測案例寫死在這裡當守門員
    read = "F1?8887?58"
    truth = "F128887458"

    def passing(options):
        slots = [options.get(i, read[i]) for i in range(10)]
        return ["".join(c) for c in itertools.product(*slots)
                if validate.id_number("".join(c))[1] is None]

    narrow = passing({2: "127", 7: "729"})
    wide = passing({2: "0123456789", 7: "0123456789"})
    if len(narrow) != 1 or narrow[0] == truth:
        problems.append("這個案例不再示範得出縮小範圍的危險（narrow=%s），"
                        "請換一組還會出事的例子，不要把檢查刪掉" % narrow)
    if truth not in wide:
        problems.append("攤開十個數字之後真值竟然不在裡面 —— 那整套檢查碼推論都不成立")
    if len(wide) < 2:
        problems.append("攤開十個數字只剩一個解，這個案例示範不出差別")

    # 「限定字元後排名」整個不採用了，理由與實測寫在 recognise.py 的
    # 那一大段註解裡。**不要再把它加回來**：拿三十格有答案的實測，
    # 它在「現在讀不出來或讀錯」的那九格只對 44~56%，擺在承辦人眼前
    # 會把人帶往錯的方向。
    source = open(os.path.join(resources_base(), "pipeline", "process.py"),
                  encoding="utf-8").read()
    for forbidden in ("digit_shapes(", "solve_id(per_cell, ", "partial_id(per_cell, "):
        if forbidden in source:
            problems.append("形狀排名又被接回去了（%s）—— "
                            "它在真正需要的那幾格錯的比對的多，"
                            "而且一旦餵進解碼，檢查碼會挑出一個錯的號碼" % forbidden)
    note = open(os.path.join(resources_base(), "pipeline", "recognise.py"),
                encoding="utf-8").read()
    if "試過但**不採用**" not in note:
        problems.append("recognise.py 沒有留下「這條路試過、為什麼不走」的紀錄，"
                        "下一個人會再走一次")
    return problems


def _grid_state_tells_the_truth():
    """診斷報告上「格線圖」那一欄，不可以叫人去做做不到的事。

    **這條檢查是踩過坑才有的，而且代價是承辦人的一個禮拜。**
    grid.png 只有「多份掃描件合成」那條路才會產生。原本報告只分兩種狀態，
    於是用空白原稿建的 F、G 永遠顯示「沒有（底稿要重做一次）」——
    承辦人照著重做了好幾次，檔案當然還是不會出現，因為那條路根本不做它。

    給錯方向的診斷比沒有診斷還糟：沒有診斷只是不知道要修哪裡，
    給錯方向是**讓人確信自己在修對的地方**。

    負向驗證有三段：空白原稿建的不可以出現「要重做」這四個字；
    多份合成建的、grid.png 真的不見了，就一定要叫人重做；
    舊樣板沒記來源的時候不可以硬猜。
    """
    import json
    import tempfile

    import numpy as np

    from pipeline import process, resources
    from tools import newform

    problems = []
    store = tempfile.mkdtemp()

    def make(code, source=None, with_grid=False):
        folder = os.path.join(store, code)
        os.makedirs(folder, exist_ok=True)
        meta = {"code": code, "name": "測試", "pages": {}}
        if source:
            meta["base_source"] = source
        with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as handle:
            json.dump(meta, handle, ensure_ascii=False)
        resources.imwrite(os.path.join(folder, "base.png"),
                          np.full((80, 80, 3), 200, np.uint8))
        if with_grid:
            resources.imwrite(os.path.join(folder, "grid.png"),
                              np.full((80, 80, 3), 100, np.uint8))

    make("P", "blank")                      # 空白原稿，沒有也不該有 grid.png
    make("Q", "scans")                      # 多份合成，但 grid.png 不見了
    make("R", "scans", with_grid=True)      # 多份合成，做好了
    make("S", None)                         # 舊樣板，沒記來源

    converter = process.Converter(store)
    say = {code: converter._grid_state(code) for code in "PQRS"}

    if "不需要" not in say["P"]:
        problems.append("空白原稿建的樣板應該說「不需要」，實際說「%s」" % say["P"])
    # 負向驗證一：**絕對不可以**叫空白原稿那種去重做底稿
    if "重做" in say["P"]:
        problems.append("空白原稿建的樣板被叫去重做底稿（「%s」）—— "
                        "那條路永遠不會產生 grid.png，重做幾次都一樣" % say["P"])
    # 負向驗證二：多份合成而檔案真的不見了，就一定要叫人重做
    if "重做" not in say["Q"]:
        problems.append("多份合成建的樣板少了 grid.png，卻沒有叫人重做：「%s」" % say["Q"])
    if say["R"] != "有":
        problems.append("grid.png 明明在，卻說「%s」" % say["R"])
    # 負向驗證三：舊樣板沒記來源就說不確定，不要硬猜
    if "重做" in say["S"] or "不需要" in say["S"]:
        problems.append("舊樣板沒記底圖來源，卻硬猜成「%s」" % say["S"])

    # 接線檢查：建底圖的那兩條路真的會把來源記進 index.json。
    # 上面全部測的是讀的那一端，寫的那一端斷掉的話一條都不會叫。
    folder = os.path.join(store, "T")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as handle:
        json.dump({"code": "T", "name": "測試", "pages": {}}, handle, ensure_ascii=False)
    newform._note_base_source(store, "T", "blank")
    if newform.base_source(store, "T") != "blank":
        problems.append("_note_base_source 沒有把來源寫進 index.json")
    source = open(os.path.join(resources.base_dir(), "tools", "newform.py"),
                  encoding="utf-8").read()
    for call, why in (('_note_base_source(store, code, "blank")', "空白原稿"),
                      ('_note_base_source(store, code, "scans")', "多份合成")):
        if call not in source:
            problems.append("%s那條路沒有記下底圖來源，報告會分不出狀態" % why)
    return problems


def _report_keeps_its_own_words():
    """診斷報告裡「程式自己寫的話」不可以被遮罩掉。

    **這條檢查是踩過坑才有的，而且被騙的人是我。** 承辦人 2026-09-09 那份
    報告上，A 表段名那一欄的「怎麼讀出來的」寫著

        「地段字段」→ （字字字）

    看起來像「標籤找到了，也讀到三個中文字」，可是同一列的結果卻寫「整頁上
    找不到這個標籤」。我把它當成報告前後矛盾，回報給承辦人說要再查。

    真相是「（字字字）」根本不是讀到的東西，是程式自己的字串「（沒讀到）」
    被遮罩了。**看不懂的報告比沒有報告糟**，因為它會把人帶去錯的方向。

    負向驗證在最後三段：真的資料照樣要遮，而且說明裡夾到六碼以上的數字、
    或引號裡的內容，一樣要遮掉 —— 「字面留著」不能變成整串放行。
    """
    from pipeline import diagnose

    problems = []

    # 正向：程式自己的說明要看得懂
    got = diagnose.mask_reading({"值": "", "說明": "找不到：整頁上找不到十碼的公文文號"})
    if "整頁上找不到十碼的公文文號" not in got:
        problems.append("程式自己的說明被遮掉了：%s" % got)

    got = diagnose.mask_reading({"值": "「地段小段」→", "說明": "沒讀到"})
    if "沒讀到" not in got:
        problems.append("「沒讀到」被遮成看不懂的東西：%s" % got)

    # 負向驗證一：讀到的東西照樣要遮
    got = diagnose.mask_reading("鳳鳴路9號")
    if got != "字字路9號":
        problems.append("讀出來的門牌沒有被遮罩：%s" % got)
    got = diagnose.mask_reading({"值": "鳳鳴路9號", "說明": ""})
    if "鳳鳴" in got:
        problems.append("dict 形式的值沒有被遮罩：%s" % got)

    # 負向驗證二：說明裡夾到長數字（文號、身分證）一樣要遮
    got = diagnose.mask_reading({"值": "", "說明": "整頁上有 1155697586 這個號碼"})
    if "1155697586" in got:
        problems.append("說明裡的十碼數字漏出去了：%s" % got)

    # 負向驗證三：說明裡引號夾住的內容要遮
    got = diagnose.mask_reading({"值": "", "說明": "路名讀到的是「鳳鳴」"})
    if "鳳鳴" in got:
        problems.append("說明裡引號夾住的內容漏出去了：%s" % got)

    # 那兩個現場不可以再把程式的話拼進要遮罩的字串裡
    text = open(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "pipeline", "process.py"), encoding="utf-8").read()
    for bad in ('or "（沒讀到）"', 'or ("找不到（%s）" % note)'):
        if bad in text:
            problems.append("process.py 又把程式自己的話拼進要遮罩的字串裡：%s" % bad)
    return problems


def _address_drops_building_number():
    """門牌後面印的「建號：01234-000」要切掉，但不能切到路名。

    承辦人 2026-09-10：「地址的部分把建號抓出來了，不要建號這些東西。」

    **原本不但沒切，還通過驗證** —— 「…26號16樓建號：12345-000」正規化完
    看起來仍然像一個合法的門牌，畫面上一個提醒都沒有，就這樣進了 RPA。

    負向驗證在最後兩段：真的帶「建」字的路名一個都不能被切，而且拿兩區的
    官方路名清單整份掃一遍，確認沒有任何一條路名會踩到這條規則。
    不然這一刀哪天就會砍掉真的門牌，而且是安靜地砍。
    """
    from pipeline import lexicon, validate

    problems = []

    # 正向：建號那一段要不見，前面的門牌要原封不動。
    # 後面三個是**標籤印在值後面**的那種（2026-09-16 報告第 6 件）——
    # 原本只切掉「建號」兩個字，前面那串段名與地號整段留在門牌裡。
    for text, want in (
            ("地址：三峽區中山里民生街27巷26號五樓建號：01234-000", "民生街27巷26號五樓"),
            ("三峽區大埔里民生街27巷26號16樓建號：12345-000", "民生街27巷26號十六樓"),
            ("民生街27巷26號五樓 房屋建號：", "民生街27巷26號五樓"),
            ("三峽區民生街27巷26號16樓中正段1234之5678建號", "民生街27巷26號十六樓"),
            ("民生街26號 大埔段 0123 之 0045 房屋建號", "民生街26號"),
            ("民生街26號之3 中正段1234建號", "民生街26號之3")):
        got, _note = validate.address(text)
        if got != want:
            problems.append("「%s」應該變成「%s」，實際是「%s」" % (text, want, got))

    # 負向驗證一：帶「建」字的路名不可以被砍
    for text in ("建國路5號", "福建街12號", "建國南路一段3號", "建成路17巷2號"):
        got, _note = validate.address(text)
        if "建" not in got:
            problems.append("「%s」被建號那條規則砍掉了，變成「%s」" % (text, got))

    # 負向驗證二：整份官方路名清單掃一遍，沒有一條會踩到這條規則。
    # 這一條比上面四個例子重要 —— 例子是我想得到的，清單是真的會遇到的。
    names = set()
    for _district, roads in (lexicon.builtin() or {}).items():
        names.update(roads)
    hit = [name for name in names if validate._BUILDING_LABEL.search(name)]
    if hit:
        problems.append("路名清單裡有 %d 條會被建號那條規則砍到：%s"
                        % (len(hit), sorted(hit)[:5]))
    elif not names:
        problems.append("路名清單讀不到，建號那條規則等於沒有驗過")

    # 負向驗證三：沒有「建號」兩個字的門牌，切的那一段一個字都不能動。
    # 後置印法是從標籤往**前**切的，這一刀沒有守住就會砍到真的門牌。
    for text in ("民生街27巷26號16樓", "中正路二段15號之3", "大湖路100號",
                 "民權街5巷1弄2號3樓"):
        if validate._drop_building_number(text) != text:
            problems.append("「%s」裡面沒有建號，卻被切成「%s」"
                            % (text, validate._drop_building_number(text)))
    return problems


def _road_shortlist_when_stuck():
    """路名對不上字典的時候，要把「差一個字」的那幾條列出來給人挑。

    2026-09-16 的報告第 2 件：「鳳鳴路」的「鳴」整個沒被讀出來，只剩
    「鳳路」。鶯歌區有 12 條「鳳」開頭的路，程式沒有根據挑一條 ——
    **也不該挑**，猜錯的門牌會讓 RPA 去查別人的房子。但原本那句
    「路街名不在字典裡」是死路，人拿著它只能自己去翻清單。

    負向驗證有三段，都很重要：
      一、字典裡真的沒有一條像的，就不可以列清單（列了是硬湊）
      二、對得上的一件不可以被列清單（那會變成每一件都在叫）
      三、候選路名一定要在「」裡面 —— 診斷報告是照引號遮罩的，
          沒有引號就等於把民眾的門牌縮小到六條路，寫進要送出機關的檔案。
    """
    from pipeline import diagnose, lexicon, validate

    problems = []
    roads = lexicon.for_district(lexicon.builtin(), "鶯歌區")
    if not roads:
        return ["讀不到鶯歌區的路名清單，這條檢查等於沒有驗過"]

    _value, note = validate.address("新北市鶯歌區鳳路99號9樓", roads)
    if not note or "鳳鳴路" not in note or "鳳福路" not in note:
        problems.append("「鳳路」沒有列出差一個字的候選：%s" % note)

    # 負向一：字典裡沒有像的，不可以硬湊出清單
    _value, note = validate.address("新北市鶯歌區某某某某路5號", roads)
    if note and "挑一條" in note:
        problems.append("字典裡沒有像的路，卻列出了候選：%s" % note)

    # 負向二：對得上的不可以被列清單
    _value, note = validate.address("新北市鶯歌區鳳鳴路99號9樓", roads)
    if note:
        problems.append("路名讀對了卻還是被提醒：%s" % note)

    # 負向三：候選路名要被診斷報告遮掉，只留下條數
    _value, note = validate.address("新北市鶯歌區鳳路99號9樓", roads)
    masked = diagnose.mask_note(note or "")
    leaked = [name for name in roads if name in masked]
    if leaked:
        problems.append("診斷報告上留下了候選路名 %s —— "
                        "那等於把門牌縮小到這幾條路，而報告是要送出機關的"
                        % leaked[:3])
    if "6 條" not in masked:
        problems.append("診斷報告上看不出有幾條候選，開發者查不出問題：%s" % masked)
    return problems


def _road_gap_tells_which_one():
    """讀不出來的那個字**卡在哪一格**，本身就是線索。

    承辦人 2026-09-22 第 12 件：程式報「讀到中街」，他問「是 O中街 還是
    中O街？」—— 因為鶯歌區這兩種是完全不同的兩條路（國中街、中湖街）。
    程式其實一路都知道那一格有讀到東西、只是讀不成中文字，是比對前
    「只留中文字」那一步把位置當雜訊丟掉了。現在留著它。

    負向驗證有五段，每一段都是「不可以因此開始亂猜」：
      一、真的只讀到「中街」（那一格什麼都沒有）就**不可以**挑一條，
          但要在候選清單上畫出是哪一格空的，人才看得懂要去比什麼
      二、位置對得上不只一條的（鳳O路 有六條），照樣不挑
      三、開頭那段是**數字**的是隔壁的鄰別，不是讀壞的字，不可以拿來當位置
      四、本來就對得上的件不受影響，開頭的鄰別照樣要提醒
      五、那個讀壞的字絕對不可以跟到輸出的門牌上
    """
    from pipeline import lexicon, validate

    problems = []
    roads = lexicon.for_district(lexicon.builtin(), "鶯歌區")
    if not roads or "國中街" not in roads or "中湖街" not in roads:
        return ["鶯歌區字典裡沒有國中街／中湖街，這條檢查等於沒有驗過"]

    for raw, want in (("O中街12號", "國中街12號"), ("中O街12號", "中湖街12號"),
                      ("中Q街5巷3號", "中湖街5巷3號")):
        value, note = validate.address(raw, roads)
        if value != want:
            problems.append("「%s」得到 %r，應該照位置判成 %r" % (raw, value, want))
        if not note:
            problems.append("「%s」是照位置猜出來的，卻沒有標記" % raw)

    # 負向一：那一格真的什麼都沒讀到，就不准挑
    value, note = validate.address("中街12號", roads)
    if value != "中街12號":
        problems.append("只讀到「中街」卻挑了一條：%r" % value)
    if not note or "中□街" not in note or "□中街" not in note:
        problems.append("候選清單沒有畫出是哪一格空的，人分不出要比什麼：%s" % note)

    # 負向二：位置對得上的不只一條，照樣不挑
    _value, note = validate.address("鳳O路12號", roads)
    if not note or "挑一條" not in note:
        problems.append("「鳳O路」有六條都對得上位置，卻沒有交給人挑：%s" % note)

    # 負向三：開頭是數字的是鄰別，不是讀壞的字
    value, note = validate.address("12中街5號", roads)
    if value != "中街5號":
        problems.append("開頭的鄰別數字被當成讀壞的字，挑了一條：%r" % value)

    # 負向四：本來就對得上的件不受影響，鄰別照樣要提醒
    value, note = validate.address("A大湖路5號", roads)
    if value != "大湖路5號":
        problems.append("「A大湖路5號」得到 %r" % value)
    if not note or "鄰別" not in note:
        problems.append("開頭的「A」被吃掉了，沒有提醒是鄰別：%s" % note)

    # 負向五：讀壞的那個字不可以跟到輸出的門牌上
    for raw in ("O中街12號", "中O街12號", "A大湖路5號"):
        value, _note = validate.address(raw, roads)
        if any(ch.isascii() and ch.isalpha() for ch in value):
            problems.append("輸出的門牌裡還留著英文字母：%r" % value)
    return problems


def _zhongxing_street_is_gone():
    """三峽的「中興街」拿掉了（承辦人 2026-09-22）。

    它不在官方 115 年門牌清單裡，全三峽只有兩戶掛這個門牌，可是它會把
    「中X街」這種少讀一個字的門牌整個吸走 —— 2026-09-22 報告第 7 件
    就是這樣冒出來的。留著救兩戶，代價是別人的門牌被靜靜換成中興街。

    負向驗證：同一個查法要找得到字典裡真的有的路，否則這條檢查是
    「查法壞了所以永遠通過」，那比沒有檢查還糟。
    """
    from pipeline import lexicon

    problems = []
    roads = lexicon.for_district(lexicon.builtin(), "三峽區")
    if not roads:
        return ["讀不到三峽區的路名清單，這條檢查等於沒有驗過"]
    if "中興街" in roads:
        problems.append("三峽區字典裡還留著「中興街」")
    if "中園街" not in roads:
        problems.append("三峽區字典裡連「中園街」都找不到，這個查法有問題，"
                        "上面那條「中興街拿掉了」等於沒驗")
    return problems


def _address_follows_the_rule():
    """門牌只能是「路/街＋段＋巷＋弄＋號＋樓」，除此之外不會有別的東西。

    承辦人 2026-09-22 第二次講這條規則，因為那份報告上有兩件是**一個提醒
    都沒有就通過**的：

        4大觀123號9樓      路名前面多一個數字，而且「路」根本沒讀到
        中正路123號十檀    「樓」被讀成長得像的字（他的原話：「路名怎麼會
                           有檀，相似字型自動辨別成樓啦」）

    以前只檢查「有沒有號」，所以這兩種都算合格。現在整串照文法核一次。

    負向驗證有三段，每一段都是「不可以把對的改成錯的」：
      一、路名整個沒讀到、開頭就是巷號的門牌（12巷5號），開頭那兩個數字
          **不可以**被當成鄰別砍掉
      二、本來就寫對的門牌，一個都不可以被新規則擋下來
      三、「樓」本來就讀對的時候，不可以被那條修正動到
    """
    from pipeline import lexicon, validate

    problems = []
    roads = (lexicon.for_district(lexicon.builtin(), "三峽區")
             + lexicon.for_district(lexicon.builtin(), "鶯歌區"))
    if not roads:
        return ["讀不到路名清單，這條檢查等於沒有驗過"]

    # 正向一：開頭多一個數字、而且沒讀到「路」—— 砍掉之後字典要對得回來
    value, note = validate.address("4大觀123號9樓", roads)
    if value != "大觀路123號九樓":
        problems.append("「4大觀123號9樓」應該救回「大觀路123號九樓」，實際是「%s」"
                        % value)
    if not note:
        problems.append("開頭砍掉了一個字卻沒有標記 —— 萬一那是門牌的一部分，"
                        "人看不到")

    # 正向二：「號」後面讀到不是樓的字，要改成樓並且標記
    for text, want in (("中正路123號十檀", "中正路123號十樓"),
                       ("中正路123號十檀之3", "中正路123號十樓之3")):
        value, note = validate.address(text, roads)
        if value != want:
            problems.append("「%s」應該改成「%s」，實際是「%s」" % (text, want, value))
        elif not note or "樓" not in note:
            problems.append("把「%s」改成樓卻沒有講出來：%s" % (text, note))

    # 正向三：文法對不上就要擋下來
    value, note = validate.address("12巷5號", roads)
    if not note or "門牌的寫法" not in note:
        problems.append("「12巷5號」沒有路名，卻沒有被文法擋下來：%s" % note)

    # 負向驗證一：路名沒讀到時，開頭的巷號**不可以**被當成鄰別砍掉
    if not value.startswith("12巷"):
        problems.append("「12巷5號」開頭的巷號被當成鄰別砍掉了，變成「%s」—— "
                        "那是把讀對的資料砍掉" % value)

    # 負向驗證二：本來就寫對的門牌，一個都不可以被擋
    good = ["中正路123巷5弄7號三樓", "中正路12號十二樓", "中山路12巷34號三樓之5",
            "中正路二段15號", "大觀路15之3號", "中正路123號十六樓",
            "三峽區中正路15號建號：01234-000"]
    for text in good:
        _value, note = validate.address(text, roads)
        if note:
            problems.append("本來就對的「%s」被新規則擋下來了：%s" % (text, note))

    # 郵遞區號那一刀後面一定要接著縣市或行政區。2026-09-22 報告第 2 件
    # 路名整個沒讀到、只讀到「1234號」，那四個數字被當成郵遞區號吃掉，
    # 只剩一個「號」字，然後拿「號」去查路名字典 —— 錯得莫名其妙。
    value, note = validate.address("1234號", roads)
    if not value.startswith("1234"):
        problems.append("只讀到門牌號的時候，號碼被當成郵遞區號砍掉了：「%s」" % value)
    if not note:
        problems.append("「1234號」沒有路名，卻沒有被擋下來")
    # 負向驗證：真的有郵遞區號的時候還是要砍掉
    for text in ("237三峽區中正路15號", "23741三峽區中正路15號"):
        value, _note = validate.address(text, roads)
        if value != "中正路15號":
            problems.append("「%s」的郵遞區號沒有砍乾淨，變成「%s」" % (text, value))

    # 負向驗證三：「樓」讀對的時候不可以被那條修正動到
    before = "中正路123號十樓"
    value, _note = validate.address(before, roads)
    if value != before:
        problems.append("「樓」本來就對，卻被改成「%s」" % value)
    return problems


def _address_floor_always_chinese():
    """樓層與段一律中文數字 —— **讀不準的那幾件也要**。

    承辦人 2026-09-16：「樓層的數字沒有改成國字，所有的樓層數字都要是國字。」

    原本這兩行寫在 address() 的最後面，而前面有兩個提早 return
    （路名不在字典裡、換完還有英文字母）。走那兩條路的門牌就停在阿拉伯
    數字，同一批輸出裡有的寫「五樓」有的寫「5樓」。讀不準是一回事，
    寫法統一是另一回事。

    負向驗證在最後一段：**不是樓層的數字不可以被改成國字**。巷、弄、號
    都要留半形數字 —— 那是外部系統認的格式（見 CLAUDE.md 第四條）。
    """
    from pipeline import validate

    problems = []
    # 字典裡故意不放「長壽路」，這樣第二個案例一定會走「路名對不上」那條路
    roads = ["中正路", "民生街"]

    # 正向：三條路徑（通過、路名對不上、有英文字母）都要轉
    cases = [
        ("中正路2段15號7樓", "中正路二段15號七樓", "路名對得上"),
        ("鶯歌區長壽路99號9樓", "長壽路99號九樓", "路名對不上字典"),
        ("中正路15號Q樓", None, "還有英文字母"),
    ]
    for text, want, why in cases:
        got, _note = validate.address(text, roads)
        if want is not None and got != want:
            problems.append("（%s）「%s」應該變成「%s」，實際是「%s」"
                            % (why, text, want, got))
    got, note = validate.address("鶯歌區長壽路99號9樓", roads)
    if "9樓" in got or not note:
        problems.append("路名對不上字典的時候樓層沒有轉成國字，而且要有提醒："
                        "得到「%s」／%s" % (got, note))

    # 負向驗證：巷、弄、號的數字是半形的，不可以一起被換成國字
    got, _note = validate.address("中正路27巷5弄26號16樓", roads)
    if got != "中正路27巷5弄26號十六樓":
        problems.append("巷弄號的數字被動到了：「中正路27巷5弄26號16樓」"
                        "變成「%s」" % got)
    return problems


def _sheet_quality_reported():
    """診斷報告要看得出「底圖減得掉嗎」。

    **這條檢查是踩過坑才有的。** 承辦人回報手寫的申請書「門牌會誤抓到旁邊
    的字」，讀出來的東西裡混著印刷的「段」「弄」「號」「樓」。那有兩種完全
    不同的原因：

        底圖減不掉 → 欄位是從沒減過的原圖上裁的，印刷的字當然跟著進去
        底圖減得掉 → 那就是框的位置或 OCR 的問題

    修的地方不一樣（一個重建底圖、一個調樣板），而報告上**看不出是哪一種**，
    只能猜。所以把它變成報告的一段。

    負向驗證在最後一段：沒有底圖的時候一定要說「減不掉」，不能靜靜地退回
    原圖當作沒事 —— 那正是現在這個情況難查的原因。
    """
    import json
    import tempfile

    import numpy as np

    from pipeline import process, resources

    problems = []
    work = tempfile.mkdtemp()
    store = os.path.join(work, "樣板")
    os.makedirs(os.path.join(store, "F"))
    blank = np.full((3508, 2480, 3), 255, np.uint8)
    resources.imwrite(os.path.join(store, "F", "front.png"), blank)
    with open(os.path.join(store, "F", "index.json"), "w", encoding="utf-8") as handle:
        json.dump({"code": "F", "name": "測試表", "pages": {"front": "front.png"}},
                  handle, ensure_ascii=False)

    class Page:
        source = None
        index = 0
        rotation = 0

    # 造一頁真的可以算的影像
    import cv2
    import pymupdf

    path = os.path.join(work, "scan.pdf")
    document = pymupdf.open()
    _ok, buffer = cv2.imencode(".png", blank)
    page = document.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(0, 0, 595, 842), stream=buffer.tobytes())
    document.save(path)
    document.close()
    Page.source = path

    converter = process.Converter(store)
    result = converter.sheet_of("F", Page(), "front", 0)
    # 簽章本身也要守住：sheet_of 多回傳一個值而呼叫端沒改的話，
    # 每一個單元檢查都會照樣通過，真正跑起來才會炸。
    if len(result) != 4:
        problems.append("sheet_of 回了 %d 個值，應該是 4 個（多了對位品質那一份）"
                        % len(result))
        return problems

    # 負向驗證：這個樣板沒有 base.png，一定要說「減不掉」並講出原因
    quality = result[3]
    if quality.get("減版面"):
        problems.append("沒有底圖卻回報「減得掉」—— 報告會看不出欄位是從原圖裁的")
    if not quality.get("原因"):
        problems.append("減不掉卻沒有講原因，報告上等於什麼都沒說")

    text = open(resources.path("pipeline", "diagnose.py"), encoding="utf-8").read()
    if "底圖減得掉嗎" not in text:
        problems.append("診斷報告沒有「底圖減得掉嗎」這一段")
    return problems


def _doc_number_not_forced():
    """公文文號不可以被列成「一定要設定」。

    **這條檢查是踩過坑才有的。** 承辦人照著「公文文號不用設關鍵字」去做，
    樣板卻怎麼樣都存不起來：關鍵字模式擋「關鍵字沒填」，把那一欄清掉又擋
    「還缺必要欄位」—— 兩邊都堵死，沒有一個合法的狀態走得通。

    根源是「輸出上這一欄不能空」（CRITICAL）跟「建樣板的人一定要動手設定」
    （MUST_CONFIGURE）共用了同一份清單。公文文號兩者不一致：它由整頁掃描
    自動找，不必框也不必給關鍵字。

    畫面那一半也要一起顧 —— 存檔的擋門在 page.html 裡，它退回去用 critical
    的話，Python 這邊分得再清楚都沒有用。
    """
    from pipeline import process
    from pipeline import resources

    problems = []
    if "doc_number" in process.MUST_CONFIGURE:
        problems.append("公文文號被列進 MUST_CONFIGURE，樣板會存不起來")
    for column in process.MUST_CONFIGURE:
        if column not in process.CRITICAL:
            problems.append("MUST_CONFIGURE 裡的 %r 不在 CRITICAL 裡，兩份清單對不起來"
                            % column)

    path = os.path.join(resources.base_dir(), "editor", "page.html")
    text = open(path, encoding="utf-8").read()
    if "must_configure" not in text:
        problems.append("editor/page.html 沒有讀 must_configure")
    # 擋存檔與提示那兩處必須用 state.must，用 state.critical 就等於沒改
    for phrase in ("state.must.filter(k => !state.fields[k])",):
        if text.count(phrase) < 2:
            problems.append("editor/page.html 擋存檔的地方沒有全部改用 state.must，"
                            "公文文號還是會被當成缺少的必要欄位")
    if "state.critical.filter(k => !state.fields[k])" in text:
        problems.append("editor/page.html 還有地方拿 state.critical 擋存檔")
    return problems


def _stamp_year_gate():
    """整頁找到的十碼數字，年份跟這一頁的日期對不上就不准採用。

    **這條檢查是踩過坑才有的。** 拿 E 表（戶政系統的橫式報表）轉正之後掃
    整頁，真正的文號 1155697586 開頭那個 1 被切掉，只剩九碼落選；報表內文
    裡的 1125555274（民國 112 年）卻通過了「十碼＋合理民國年」，變成整頁上
    唯一的候選，然後拿去蓋掉框選讀對的值。那一頁上印的日期全部是 115 年。

    「只剩一個候選」不等於「它就是對的」—— 年份的比對因此從破平手用的
    條件改成必要條件。

    負向驗證在最後一段：把年份的關卡拿掉，錯的那個一定要被撿回來。
    不會叫的檢查比沒有檢查更糟。
    """
    from pipeline import stamp

    problems = []

    # 正向一：這一頁上寫著 115 年，卻只找得到一個 112 年的十碼數字 → 不採用
    decoy = ["製表日期：115/08/11", "1125555274", "155697587"]
    value, note = stamp.pick(decoy)
    if value is not None:
        problems.append(
            "整頁找的年份關卡沒擋住：這一頁的日期是 115 年，卻採用了 %s" % value)
    elif not note:
        problems.append("整頁找擋掉了年份對不上的候選，卻沒有給提醒")

    # 正向二：真的文號讀到了，就要挑出真的那個，不能因為多了雜訊而放棄
    value, _note = stamp.pick(decoy + ["1155697587"])
    if value != "1155697587":
        problems.append("整頁找沒有從雜訊裡挑出年份對得上的 1155697587，拿到的是 %r" % value)

    # 正向三：整頁上根本沒有日期可比的時候，不能反過來把唯一的候選也擋掉
    value, _note = stamp.pick(["1155697587"])
    if value != "1155697587":
        problems.append("整頁上沒有日期可比時，唯一的候選被誤擋了，拿到的是 %r" % value)

    # 負向驗證：把年份關卡拿掉，錯的那個一定要被撿回來 —— 撿不回來就代表
    # 上面那個正向案例根本沒測到年份這一關，這支檢查是假的安心。
    real_years = stamp.stamp_years
    try:
        stamp.stamp_years = lambda _texts: set()
        broken, _note = stamp.pick(decoy)
    finally:
        stamp.stamp_years = real_years
    if broken != "1125555274":
        problems.append(
            "負向驗證失敗：拿掉年份關卡之後，錯的 1125555274 應該要被撿回來，"
            "實際拿到 %r —— 表示這支檢查測到的不是年份那一關" % broken)
    return problems


def _stamp_scans_both_ways():
    """轉正過的頁面，整頁找要連原圖一起掃。

    **這條檢查是踩過坑才有的。** E 表六頁實測，轉正 90 度之後（程式實際在
    用的那個方向）三頁讀丟開頭那個 1、一頁撿到內文的數字；同樣六頁不轉直接
    掃，兩種解析度每一頁都讀對。兩個方向併起來才十二次全對。

    只掃一個方向就會漏，而漏掉的時候畫面上看起來一切正常 —— 框選剛好也讀
    得到的話，連提醒都不會有。

    負向驗證在最後一段：沒轉正的頁面不可以白白多掃一遍。
    """
    from pipeline import stamp

    problems = []
    calls = []
    real = stamp.read_page
    try:
        stamp.read_page = lambda page, rotation=0, dpi=None: calls.append(rotation) or []
        stamp.scan(object(), 90)
        if sorted(calls) != [0, 90]:
            problems.append(
                "轉正 90 度的頁面應該要掃「轉正後」與「原圖」兩個方向，"
                "實際掃的是 %r" % (calls,))
        # 負向驗證：rotation 是 0 的頁面只掃一遍，不能為了保險每頁都掃兩次
        calls[:] = []
        stamp.scan(object(), 0)
        if calls != [0]:
            problems.append("沒轉正的頁面只該掃一遍，實際掃的是 %r" % (calls,))
    finally:
        stamp.read_page = real
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

    problems.extend(_editor_serves_image(store, path))
    problems.extend(_review_serves_everything(records, unresolved, converter, work))
    return problems


def _review_serves_everything(records, unresolved, converter, work):
    """真的把複核畫面跑起來，把它會發的每一個請求打一遍，一路做到匯出。

    跟 _editor_serves_image 同一個理由：畫面載不出東西的時候**不會有錯誤
    訊息**，只會是一片空白，而單元檢查不會發現。這裡驗的是「正常使用還能
    不能用」，不是「防護擋不擋得住」。

    每一個回應都看內容，不是只看狀態碼 —— 200 配一個空的 PNG 照樣是壞的。
    """
    import http.client
    import json as jsonlib
    import threading
    from http.server import ThreadingHTTPServer

    from tools import localserver, review

    problems = []
    out = os.path.join(work, "輸出")
    state = {"records": records, "unresolved": unresolved, "out": out,
             "journal": converter.journal, "unknown": converter.unknown}
    guard = localserver.Guard()
    server = ThreadingHTTPServer(("127.0.0.1", 0), review.make_handler(state, guard))
    guard.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        host = "127.0.0.1:%d" % guard.port

        def call(method, path, body=None, kind=None):
            conn = http.client.HTTPConnection("127.0.0.1", guard.port, timeout=20)
            try:
                headers = {"Host": host}
                if kind:
                    headers["Content-Type"] = kind
                sep = "&" if "?" in path else "?"
                conn.request(method, path + sep + "k=" + guard.token,
                             body=body, headers=headers)
                response = conn.getresponse()
                return response.status, response.read()
            finally:
                conn.close()

        status, body = call("GET", "/")
        if status != 200 or b"api/records" not in body:
            problems.append("複核畫面首頁回了 %d（%d bytes）" % (status, len(body)))

        status, body = call("GET", "/api/records")
        if status != 200:
            problems.append("/api/records 回了 %d" % status)
        elif len(jsonlib.loads(body).get("records", [])) != len(records):
            problems.append("/api/records 的件數對不上")

        # 原圖裁切：跟樣板編輯器那張圖同一類的坑
        status, body = call("GET", "/api/crop?record=0&column=id_number")
        if status != 200:
            problems.append("/api/crop 回了 %d —— 複核畫面上會看不到原圖" % status)
        elif body[:8] != b"\x89PNG\r\n\x1a\n":
            problems.append("/api/crop 回的不是 PNG（開頭 %r）" % body[:8])

        status, body = call("POST", "/api/diagnose",
                            jsonlib.dumps({"notes": {"overall": "自我檢查"}}).encode(),
                            "application/json")
        if status != 200 or not jsonlib.loads(body).get("ok"):
            problems.append("/api/diagnose 回了 %d：%s" % (status, body[:120]))

        # 排除掉的件要寫進診斷報告。輸出檔上看不出來少了一列，
        # 報告是唯一留得下紀錄的地方。
        status, body = call("POST", "/api/diagnose",
                            jsonlib.dumps({"notes": {"overall": "自我檢查",
                                                     "excluded": [3, 5]}}).encode(),
                            "application/json")
        text = jsonlib.loads(body).get("text", "") if status == 200 else ""
        if "第 3、5 件" not in text:
            problems.append("診斷報告沒有記下被排除的件 —— 之後查不出少了什麼")
        # 負向驗證：沒有排除任何件的時候不可以無中生有
        status, body = call("POST", "/api/diagnose",
                            jsonlib.dumps({"notes": {"overall": "自我檢查"}}).encode(),
                            "application/json")
        if "排除不輸出" in (jsonlib.loads(body).get("text", "") if status == 200 else ""):
            problems.append("沒有排除任何件，報告上卻寫了「排除不輸出」")

        # 手動輸入的那一件跟辨識出來的一起送出去。複核畫面上按「手動加一件」
        # 之後送出的就長這樣：沒有原圖、沒有辨識原文，只有人打進去的值。
        手動 = {"district": "三峽區", "address": "民生街27巷26號16樓",
                "id_number": "F128887458", "doc_number": "1155699478",
                "name": "許三", "section": "民生段", "land_number": "0657-0000"}
        rows = [record.values for record in records] + [手動]
        status, body = call("POST", "/api/export",
                            jsonlib.dumps({"records": rows}).encode(),
                            "application/json")
        result = jsonlib.loads(body) if status == 200 else {}
        if not result.get("ok"):
            problems.append("/api/export 回了 %d：%s" % (status, body[:160]))
        else:
            # 三個檔都要真的產出來，而且不是空的
            want = ("RPA-查調謄本清冊", "HH", "YHQ101")
            names = result.get("files", [])
            for prefix in want:
                hit = [n for n in names if n.startswith(prefix)]
                if not hit:
                    problems.append("匯出少了 %s 開頭的檔案，只有 %s" % (prefix, names))
                    continue
                full = os.path.join(out, hit[0])
                if not os.path.isfile(full) or os.path.getsize(full) < 1000:
                    problems.append("%s 沒產出來或是空的" % hit[0])
            problems.extend(_manual_row_in_files(out, names, 手動))

        # 複核畫面把掃錯的件排除掉之後送出來的樣子：少一列，而且序號有缺口。
        # 提醒上的「第 N 件」必須照畫面的序號寫，不是照清單位置。
        status, body = call(
            "POST", "/api/export",
            jsonlib.dumps({"records": [dict(手動, address="")],
                           "numbers": [7]}).encode(), "application/json")
        result = jsonlib.loads(body) if status == 200 else {}
        if not result.get("ok"):
            problems.append("排除過件之後匯出失敗：%d %s" % (status, body[:160]))
        elif not any("第 7 件" in line for line in result.get("warnings", [])):
            problems.append("排除過件之後，匯出提醒沒有照畫面的序號寫：%s"
                            % result.get("warnings"))

        # 全部都被排除掉的話要講清楚，不能丟一個看不懂的錯
        status, body = call("POST", "/api/export",
                            jsonlib.dumps({"records": [], "numbers": []}).encode(),
                            "application/json")
        if status == 200 or "排除" not in body.decode("utf-8", "replace"):
            problems.append("一件都不剩的時候，匯出沒有講出原因：%d %s"
                            % (status, body[:160]))
    finally:
        server.shutdown()
        server.server_close()
    return problems


def _manual_row_in_files(out, names, manual):
    """手動輸入的那一件，三個輸出檔都要真的有它，而且是**讀回來**逐格比對。

    **不能只看「匯出沒有報錯」。** 手動加的件跟辨識出來的件走的是同一條
    輸出路徑，但它沒有 crops、沒有 raw、旗標是空的 —— 只要哪一段順手拿
    record.crops 或 record.raw 去做事，它就會在半路被吃掉，而畫面上一切
    正常：檔案照產、件數照樣寫著，只是少了一列。少一列這種事沒有人會發現。
    """
    import glob

    problems = []

    def find(prefix):
        hit = [n for n in names if n.startswith(prefix)]
        return os.path.join(out, hit[0]) if hit else None

    # 外網清冊：行政區、門牌、公文文號、身分證
    path = find("RPA-查調謄本清冊")
    if path:
        import openpyxl

        rows = list(openpyxl.load_workbook(path).active.iter_rows(values_only=True))
        want = (manual["district"], manual["address"],
                manual["doc_number"], manual["id_number"])
        if not any(tuple(r[:4]) == want for r in rows):
            problems.append("外網清冊裡找不到手動輸入的那一件（要 %s，實際 %s）"
                            % (want, [tuple(r[:4]) for r in rows[1:]]))

    # 內網中繼檔：序號欄放公文文號、完整地址要接成一整串
    path = find("HH")
    if path:
        import openpyxl

        rows = list(openpyxl.load_workbook(path).active.iter_rows(values_only=True))
        want = (manual["doc_number"], manual["district"], manual["id_number"],
                "新北市" + manual["district"] + manual["address"], manual["name"])
        if not any(tuple(r[:5]) == want for r in rows):
            problems.append("內網中繼檔裡找不到手動輸入的那一件（要 %s，實際 %s）"
                            % (want, [tuple(r[:5]) for r in rows[1:]]))

    # 戶政清冊：地址要轉成全形
    path = find("YHQ101")
    if path:
        import xlrd

        from pipeline import output

        sheet = xlrd.open_workbook(path).sheet_by_index(0)
        want = output.to_fullwidth(manual["address"])
        got = [[sheet.cell_value(r, c) for c in range(sheet.ncols)]
               for r in range(sheet.nrows)]
        if not any(want in row for row in got):
            problems.append("戶政清冊裡找不到手動輸入那一件的全形地址（要 %r）" % want)
    return problems


def _export_warnings():
    """匯出前的最後一道關：值是人打的，格式不對要講出來。

    複核畫面上人可以改任何一個值，也可以自己加一件手動輸入的 —— 辨識時
    跑過的驗證全部在那之前，攔不到人打錯的字。手動那件更是從頭到尾沒經過
    任何驗證。

    負向驗證在最後一段：一件完全正確的資料**必須一句話都不講**。
    每一件都叫的檢查等於沒有檢查，人看兩次就開始跳過了。
    """
    from pipeline import resources
    from tools import review

    problems = []
    good = {"district": "三峽區", "address": "民生街27巷26號16樓",
            "id_number": "F128887458", "doc_number": "1155699478", "name": "許三"}

    cases = [
        (dict(good, address=""), "門牌"),
        (dict(good, district=""), "行政區"),
        (dict(good, doc_number=""), "公文文號"),
        (dict(good, id_number="F128887459"), "檢查碼"),
        (dict(good, doc_number="11556994"), "10 碼"),
    ]
    for row, expect in cases:
        said = review.export_warnings([row])
        if not any(expect in line for line in said):
            problems.append("匯出前的檢查沒有講出「%s」的問題，只說了 %s" % (expect, said))

    # 排除掉的件不送過來，所以清單位置不等於畫面上的序號。
    # 提醒上寫的「第 N 件」一定要是畫面上看得到的那個號碼。
    said = review.export_warnings([good, dict(good, address="")], numbers=[2, 7])
    if not any("第 7 件" in line for line in said):
        problems.append("排除過件之後，提醒沒有照畫面的序號寫：%s" % said)
    if any("第 2 件" in line for line in said):
        problems.append("沒有問題的那一件也被提醒了：%s" % said)

    # 負向驗證一：完全正確的一件不可以有任何提醒
    said = review.export_warnings([good])
    if said:
        problems.append("一件完全正確的資料也被提醒了：%s —— "
                        "每一件都叫的檢查等於沒有檢查" % said)
    # 負向驗證二：沒給序號就照順序編，不能因為少一個參數就整個錯位
    said = review.export_warnings([good, dict(good, address="")])
    if not any("第 2 件" in line for line in said):
        problems.append("沒給序號的時候，提醒沒有照順序編：%s" % said)

    # 函式會用序號不代表**有人把序號傳進去**。這一段就是驗那條接線 ——
    # 上面全部測的是 export_warnings 本身，接線斷掉的話一條都不會叫。
    source = open(os.path.join(resources.base_dir(), "tools", "review.py"),
                  encoding="utf-8").read()
    if "export_warnings(rows, numbers)" not in source:
        problems.append("tools/review.py 匯出時沒有把畫面送來的序號傳進 "
                        "export_warnings，提醒會標到別件身上")
    if 'payload.get("numbers")' not in source:
        problems.append("tools/review.py 沒有收畫面送來的序號")
    return problems


def _review_page_can_add_manual():
    """複核畫面上要有「手動加一件」，掃錯的件要能排除，而且排除**不等於刪除**。

    承辦人 2026-09-16：「我掃錯了…應該要加一個刪除功能來應對這種狀況。」

    辨識出來的件只能標記成不輸出，不能真的從清單裡拿掉：原圖是用序號跟
    後端要的（api/crop?record=N），意見也是照序號存的 —— 序號一移，
    後面每一件的原圖和意見就全部貼到別人身上，而且畫面上看不出來。
    """
    from pipeline import resources

    problems = []
    text = open(os.path.join(resources.base_dir(), "editor", "review.html"),
                encoding="utf-8").read()
    for need, why in (
            ('id="add"', "沒有「手動加一件」的按鈕"),
            ("function addManual", "沒有 addManual"),
            ("function dropManual", "沒有 dropManual"),
            ("function toggleDrop", "沒有 toggleDrop，掃錯的件沒辦法排除"),
            ("data-out=", "卡片上沒有「這一件不要輸出」的按鈕"),
            ("r.dropped", "排除的狀態沒有畫出來，人看不出自己按了什麼"),
            ("excluded:", "排除掉的件沒有寫進診斷報告，之後查不出少了什麼"),
            ("manual: true", "手動加的件沒有標記成 manual，畫面分不出它沒有原圖")):
        if need not in text:
            problems.append("editor/review.html %s" % why)
    if "if (!data.records[index] || !data.records[index].manual) return;" not in text:
        problems.append("dropManual 沒有擋住「刪掉辨識出來的件」，"
                        "序號一移，每一件的意見就會貼到別人身上")
    if "record.splice" in text or "if (!record || record.manual) return;" not in text:
        problems.append("toggleDrop 沒有擋住「真的把辨識出來的件刪掉」")
    # 排除掉的件不能送去輸出，而且序號要一起送 —— 少了序號，提醒上的
    # 「第 N 件」就跟畫面對不起來，人照著去找會找到別件身上。
    for need, why in (
            ("item => !item.row.dropped", "排除掉的件還是被送去輸出了"),
            ("numbers: keep.map", "沒有把畫面上的序號送過去，提醒會標錯件")):
        if need not in text:
            problems.append("editor/review.html %s" % why)
    return problems


def _editor_serves_image(store, sample):
    """真的把樣板編輯器跑起來，把畫面會發的每一個請求打一遍。

    **這是踩過坑才有的。** 加本機權杖那一次漏掉 img.src 那一行，樣板編輯器
    左邊的掃描影像整片空白（被自己的防護擋成 403），而畫面上不會有任何錯誤
    訊息 —— 承辦人看到的是「以前能跑出來的東西全部都無法顯示」。

    靜態掃描（_pages_carry_token）擋的是「忘記包 api()」；這一支擋的是
    「包了但端點本身壞了」。兩層都要，因為壞掉的樣子都是一片空白。
    """
    import http.client
    import json
    import threading
    from http.server import ThreadingHTTPServer

    from tools import localserver, template_editor

    problems = []
    workspace = template_editor.Workspace(store, [sample])
    guard = localserver.Guard()
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 template_editor.make_handler(workspace, guard))
    guard.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        host = "127.0.0.1:%d" % guard.port

        def get(path):
            conn = http.client.HTTPConnection("127.0.0.1", guard.port, timeout=10)
            try:
                sep = "&" if "?" in path else "?"
                conn.request("GET", path + sep + "k=" + guard.token, headers={"Host": host})
                response = conn.getresponse()
                return response.status, response.read()
            finally:
                conn.close()

        status, body = get("/")
        if status != 200 or b"<canvas" not in body:
            problems.append("樣板編輯器首頁回了 %d（%d bytes）" % (status, len(body)))

        status, body = get("/api/codes")
        if status != 200 or b'"F"' not in body:
            problems.append("/api/codes 回了 %d：%s" % (status, body[:80]))

        status, body = get("/api/template?code=F")
        if status != 200 or b'"fields"' not in body:
            problems.append("/api/template 回了 %d：%s" % (status, body[:80]))

        # 公文文號不設定也要存得起來 —— 見 _template_saves_without_doc_number
        status, body = get("/api/template?code=F")
        if status == 200:
            import json as _json
            data = _json.loads(body.decode("utf-8"))
            if "must_configure" not in data:
                problems.append("/api/template 沒有回 must_configure，"
                                "畫面會退回用 critical 擋存檔，公文文號就存不起來")
            elif "doc_number" in data.get("must_configure", []):
                problems.append("/api/template 把公文文號列成「一定要設定」，"
                                "但它是整頁自動找的，設不出來也存不起來")

        def post(path, payload):
            conn = http.client.HTTPConnection("127.0.0.1", guard.port, timeout=10)
            try:
                sep = "&" if "?" in path else "?"
                conn.request("POST", path + sep + "k=" + guard.token,
                             body=json.dumps(payload).encode("utf-8"),
                             headers={"Host": host,
                                      "Origin": "http://" + host,
                                      "Content-Type": "application/json"})
                response = conn.getresponse()
                return response.status, response.read()
            finally:
                conn.close()

        # 存檔會蓋掉 F 的 fields.json，測完要放回去 —— 後面還有檢查在用它。
        # 「檢查本身把環境弄壞，害下一支檢查誤報」比沒測還難查。
        original = open(os.path.join(store, "F", "fields.json"),
                        encoding="utf-8").read()

        # 正向：沒有公文文號那一欄的樣板，要存得起來
        keep = [{"id": "f_id", "name": "身分證字號", "column": "id_number",
                 "kind": "id_number", "box": [100, 100, 400, 60], "page": "front"},
                {"id": "f_addr", "name": "門牌", "column": "address",
                 "kind": "address", "box": [100, 200, 800, 60], "page": "front"}]
        status, body = post("/api/template?code=F", {"fields": keep})
        if status != 200 or b'"ok": true' not in body.replace(b'"ok":true', b'"ok": true'):
            problems.append("沒有公文文號那一欄的樣板存不起來：%d %s" % (status, body[:200]))

        # 負向驗證：關鍵字模式卻沒填關鍵字，一定要被擋 —— 那個檢查還在
        bad = keep + [{"id": "f_doc", "name": "公文文號", "column": "doc_number",
                       "kind": "doc_number", "box": [0, 0, 0, 0], "page": "front",
                       "mode": "label", "label": ""}]
        status, body = post("/api/template?code=F", {"fields": bad})
        if status == 200 and b'"ok": true' in body.replace(b'"ok":true', b'"ok": true'):
            problems.append("關鍵字模式沒填關鍵字竟然存得起來 —— "
                            "那一欄辨識時會整個廢掉，這個檢查不能拿掉")

        with open(os.path.join(store, "F", "fields.json"), "w",
                  encoding="utf-8") as handle:
            handle.write(original)

        # 這就是漏掉權杖時壞掉的那一個
        status, body = get("/api/image?code=F&view=0")
        if status != 200:
            problems.append("/api/image 回了 %d：%s —— 樣板編輯器左邊會是一片空白"
                            % (status, body[:120]))
        elif body[:8] != b"\x89PNG\r\n\x1a\n":
            problems.append("/api/image 回的不是 PNG（開頭 %r）" % body[:8])
        elif len(body) < 2000:
            problems.append("/api/image 回的 PNG 只有 %d bytes，太小了" % len(body))
    finally:
        server.shutdown()
        server.server_close()
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

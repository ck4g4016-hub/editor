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

    # 字典裡沒有的路名**絕對不可以**被換成另一條真的存在的路。
    # 這是最危險的一類錯：驗證會放行，輸出表上看不出來，RPA 拿著別人家的
    # 門牌去查調。實測出過一次：鶯歌區的「大湖路」被換成「東湖路」。
    yingge = lexicon.for_district(lexicon.load(resources.base_dir()), "鶯歌區")
    if yingge:
        for got in ("大湖路", "大湖", "西湖路", "不存在路"):
            name, score = lexicon.resolve_head(got, yingge)
            if name is not None:
                problems.append("路名「%s」不在字典裡，卻被換成 %r（%.2f）"
                                % (got, name, score))
        for got, want in (("東湖路", "東湖路"), ("中湖街", "中湖街"),
                          ("東湖", "東湖路"), ("國華路", "國華路")):
            name, _score = lexicon.resolve_head(got, yingge)
            if name != want:
                problems.append("路名比對 %s 得到 %r，應該是 %r" % (got, name, want))
        value, problem = validate.address("大湖路732巷16弄15號2樓", roads=yingge)
        if not problem:
            problems.append("不在字典裡的門牌被放行了：%r" % value)

    # 段名沒有格式規則，只能靠清單。清單裡沒有的絕對不可以自己代換。
    for label, raw, known, want, flagged in (
        ("清單裡有", "國際", ["國際段", "二甲段"], "國際", False),
        ("尾字的段可有可無", "國際段", ["國際", "二甲"], "國際", False),
        ("清單裡沒有要標起來", "圍際", ["國際段", "二甲段"], "圍際", True),
        ("沒有清單就照讀的寫", "圍際", [], "圍際", False),
    ):
        got, problem = validate.check("section", raw, known=known)
        if got != want:
            problems.append("段名「%s」得到 %r，應該是 %r" % (label, got, want))
        if flagged and problem is None and known:
            problems.append("段名「%s」應該被標記卻放行了" % label)
    for raw, known in (("圍際", ["國際段"]), ("", ["國際段"])):
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

    # 三種讀法都要能挑出通過檢查碼的那一個
    good = "A123456789"
    if validate.id_number(good)[1] is None:
        picked, problem = validate.best_id("A12345678", good, "")
        if problem is not None or picked != good:
            problems.append("身分證三種讀法沒有挑出通過檢查碼的那個：%r %s"
                            % (picked, problem))

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

    path = os.path.join(work, "scan.pdf")
    document = fitz.open()
    ok, buffer = cv2.imencode(".png", filled)
    page = document.new_page(width=595, height=842)
    page.insert_image(fitz.Rect(0, 0, 595, 842), stream=buffer.tobytes())
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

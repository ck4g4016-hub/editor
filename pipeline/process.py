# -*- coding: utf-8 -*-
"""從掃描 PDF 一路做到可以輸出的資料列。

    分類每一頁 → 切成一件一件 → 減掉印刷版面 → 依樣板裁出欄位
    → 辨識 → 正規化與驗證 → 產生資料列

每一列都帶著它的來源（哪個檔、第幾頁）與每個欄位的信心值和驗證結果，
複核介面靠這些資訊決定要把哪些格子標紅給人看。
"""

import json
import re
import os
import time

import cv2
import numpy as np

from . import (baseimage, diagnose, fields as fieldmod, layout, lexicon,
                pagetext, recognise, render, resources, stamp, validate)

# 這三欄錯了，RPA 會拿著錯的資料去查別人的房子，而且從輸出的表格上看不出來。
# 驗證不通過就一定要人工確認，不管信心值多高。
CRITICAL = ("id_number", "address", "doc_number")

# 整行讀的手寫文字欄位。這些才會先切掉壓到的印刷字（見 _read_field）。
TEXT_KINDS = ("address", "district", "chinese", "section")

# 樣板編輯器裡「一定要設定才存得起來」的欄位。**公文文號不在裡面。**
#
# 它跟上面那三欄是兩件事：CRITICAL 講的是「輸出上這一欄不能空、不能錯」，
# 這一份講的是「建樣板的人一定要動手設定」。公文文號兩者不一致 ——
# 它由整頁掃描自動找（見 stamp.py），不必框、也不必給關鍵字。
#
# 以前這兩件事共用同一份清單，結果是：承辦人照著「公文文號不用設關鍵字」
# 去做，樣板卻存不起來 —— 關鍵字模式擋「關鍵字沒填」，清除那一欄又擋
# 「還缺必要欄位」，兩邊都堵死，沒有一個合法的狀態可以走。
MUST_CONFIGURE = ("id_number", "address")

# 信心低於這個值就算沒把握，即使驗證通過也要人工看一眼
LOW_CONFIDENCE = 0.80

# 姓名沒有字典也沒有格式規則，錯了驗不出來。但它不會進 RPA，
# 只是承辦人用來對照「這件是不是我要的那件」，所以照樣輸出、
# 在複核介面標成僅供參考，不因為它把整件擋下來。
ADVISORY = ("name",)

OK = "ok"
REVIEW = "review"


# 門牌欄位裡，哪些字長得像「路名那一格」而不是整串住址。
_NOT_A_ROAD = "段巷弄號樓之"

# 路名最長幾個字。三峽、鶯歌最長的是「鳳吉一街」「中正一路」四個字，
# 六個字已經寬鬆很多了。
_ROAD_MAX = 6


# 整串住址那一格，最左邊那一個字可以單獨切出來嗎？
#
# 這幾個數字是資安門檻，不是調參數。切出來的東西要送出機關，所以寧可
# 切不出來（什麼都不收），也不要切出一塊「其實還連著門牌號」的圖。
_FIRST_MIN_GROUPS = 4      # 整串住址至少這麼多團墨（路名＋路＋號碼＋號）
_FIRST_MAX_RATIO = 1.6     # 一個中文字再寬也不會超過高度的 1.6 倍
_FIRST_MIN_RATIO = 0.45    # 太扁的是碎片或標點，不是字
_FIRST_MAX_REACH = 0.35    # 切出來的右邊界不可以超過整條的這個比例


def first_character(crop):
    """從「整串住址」那一格裡，只切出**最左邊那一個字**。

    為什麼要這樣做：鶯歌的「鳳X路」與「龍X路」三、五、七三組正面相撞，
    字典幫不上忙，只剩字形。要讓程式學會分辨就得有標好答案的圖，而那種
    表格（E 表）的門牌只框一個大框 —— 整格就是整串住址，不能送出機關。

    但**單獨一個「鳳」或「龍」不是個資**：那是公開的街道名稱用字，
    跟一個孤立的手寫數字同一個等級，指不向任何人。所以只切第一個字。

    切不出來就回 None —— 條件一條不過就什麼都不收。切錯的代價是把民眾的
    門牌號送出機關，而那從檔名上看不出來（檔名只寫真值那一個字）。

    為什麼值得做：拿承辦人 2026-09-24 傳回來的 13 張圖量過，「鳳」在這個
    掃描解析度下筆畫整個糊成一團、「龍」還看得出左右兩塊，兩個形狀特徵
    分得開（中間直條的空白比例 鳳 0.07–0.25、龍 0.42–0.57；封閉白區個數
    鳳 3–7、龍 12–16，13 張裡 12 張分得開）。但 13 張裡「龍」只有 4 張，
    **這個數量不足以拿來自動改門牌**，所以現在只收資料，不做判斷。

    回傳裁好的影像，或 None。
    """
    if crop is None or not getattr(crop, "size", 0):
        return None
    grey = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    ink = (grey < 128).astype(np.uint8)
    height, width = ink.shape
    if height < 8 or width < height * 2:
        return None                      # 本來就不是一整條住址

    column = ink.sum(axis=0)
    groups, start = [], None
    for x in range(width):
        if column[x] > 0 and start is None:
            start = x
        elif column[x] == 0 and start is not None:
            groups.append((start, x))
            start = None
    if start is not None:
        groups.append((start, width))
    if len(groups) < _FIRST_MIN_GROUPS:
        return None

    # 最左邊常常是裁切邊緣切到隔壁欄的碎片，太窄的跳過
    for left, right in groups:
        span = right - left
        if span < height * _FIRST_MIN_RATIO:
            continue
        if span > height * _FIRST_MAX_RATIO:
            return None                  # 第一團就黏成一片，分不出是幾個字
        if right > width * _FIRST_MAX_REACH:
            return None                  # 位置太靠右，不敢當成第一個字
        return crop[:, left:right]
    return None


def split_glyphs(crop, count):
    """把一格裡的手寫數字拆成一個字一張。拆出來剛好 count 個才算數，否則回 None。

    承辦人 2026-11-02：「我只要修改門牌的部分，就要把錯誤的字型變成難認字
    輸出給你。」門牌號、巷、弄、樓以前一律不收，因為整格（例如「38」配上
    路名）就是住址。**拆成單一個數字就不是了** —— 一個孤立的手寫「4」，
    跟身分證的一格一樣，指不向任何人。

    拆不準就不收：數字黏在一起、多了一點雜訊，拆出來的個數就會跟正確答案
    的位數不一樣，那時候硬配會把「4」標成「1」，標錯的資料比沒有資料還糟。
    """
    if crop is None or not getattr(crop, "size", 0) or count < 1:
        return None
    grey = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    ink = grey < 128
    height = ink.shape[0]
    column = ink.sum(axis=0)
    groups, start = [], None
    for x, value in enumerate(column):
        if value and start is None:
            start = x
        elif not value and start is not None:
            groups.append((start, x))
            start = None
    if start is not None:
        groups.append((start, len(column)))
    # 很小的一點是雜訊（框線殘渣、墨點），不算一個字
    glyphs = []
    for left, right in groups:
        piece = ink[:, left:right]
        rows = np.where(piece.any(axis=1))[0]
        tall = rows[-1] - rows[0] + 1 if len(rows) else 0
        if tall < height * 0.25 and piece.sum() < height:
            continue
        glyphs.append((left, right, rows[0], rows[-1] + 1, tall))
    if len(glyphs) != count:
        return None
    out = []
    for left, right, top, bottom, tall in glyphs:
        # 一個字不會比自己的高度寬太多；太寬就是兩個字黏在一起
        if right - left > max(tall, 1) * 1.6:
            return None
        out.append(crop[top:bottom, left:right])
    return out


def only_the_road(text, segment_count):
    """這一格看起來是「只有路名」，還是「整串住址」？

    **這是資安判斷，不是方便判斷。** 難字回報會把這一格的影像送出機關，
    而路名（公開的街道名稱）跟完整住址是完全不同等級的東西。

    2026-09-22 承辦人傳回第一批難字回報，裡面的「路名」那幾張其實是
    **整串完整住址** —— 檔名上就寫著「程式讀成光明路75巷16號六樓之1」。
    當時的條件只有「第一段、後綴是空的」，而門牌只框一個大框的表格
    （E 表那種）就只有一段，那一段當然是整串地址。

    三條都要成立才算數，任何一條不成立就不收 ——
    寧可少收資料，也不要把住址送出機關。
    """
    return (segment_count > 1                       # 真的一格一框
            and 0 < len(text) <= _ROAD_MAX          # 路名不會這麼長
            and not any(ch in text for ch in _NOT_A_ROAD))


class Record:
    """一件申請案轉出來的一列資料。"""

    def __init__(self, code, source, page):
        self.code = code
        self.source = source
        self.page = page
        self.values = {}
        self.raw = {}
        self.confidence = {}
        self.problems = {}
        # 每個欄位的原圖裁切（PNG bytes）。複核時要讓人對照著看，
        # 光給文字沒辦法判斷對錯。只放在記憶體，程式關掉就沒了。
        self.crops = {}
        # 每個欄位「是怎麼讀出來的」：三種讀法各自的結果、切出幾格。
        # 診斷報告靠它才看得出逐格辨識到底有沒有觸發。
        self.how = {}
        # 切出來的每一格影像。**含個資**，只給承辦人自己在本機看，
        # 不會進診斷報告。看圖才知道是「格子切歪了」還是「字真的認不出來」。
        self.cells = {}
        # 每一格**讀到了什麼**，跟 cells 一一對應。給「難字回報」用：
        # 人在複核畫面上改完之後，才知道哪一格程式讀錯了。
        self.cell_text = {}
        # 門牌「路名」那一格的影像與讀到的字。只收路名 ——
        # 路名是公開的街道名稱，單獨一個不指向任何人；
        # 門牌號、樓層配上路名就是完整的住址了（見 dump_hard_cells）。
        self.road_cell = None
        # 門牌只框一個大框（E 表）時的整格影像與讀到的字。**整格含完整住址，
        # 絕對不會被送出去**；難字回報只從裡面切出最左邊那一個字。
        self.address_cell = None
        # 門牌一格一框的表格（C、F、G）：每一格的 (影像, 讀到的字, 後綴)。
        # **整格絕對不會被送出去** —— 難字回報只把巷、弄、號、樓那幾格
        # 拆成「一個數字一張圖」才收（見 split_glyphs 與 dump_hard_cells）。
        self.address_parts = []
        # 行政區那一格的影像與讀到的字（框選、只有一格的才有）。「三峽區」
        # 「鶯歌區」是公開的地名，給難字回報當樣本用（見 dump_hard_cells）。
        self.district_cell = None
        # 每一面的底圖對位品質：{"front": {"減版面": True, "對位": 0.87}}。
        # 減不掉的時候欄位是從**沒減過的原圖**上裁的，印刷的「段巷弄號樓」
        # 會跟手寫混在一起被讀進來 —— 那種讀出來的東西看起來就像 OCR 很爛，
        # 但根本不是 OCR 的問題。以前診斷報告看不出這件事，只能猜。
        self.sheets = {}

    @property
    def status(self):
        return REVIEW if self.problems else OK

    def flagged(self):
        """需要人工看的欄位，以及原因。"""
        notes = dict(self.problems)
        for column in ADVISORY:
            if column in self.values:
                notes.setdefault(column, "僅供人眼核對，不影響輸出")
        for column, value in self.confidence.items():
            if value < LOW_CONFIDENCE:
                notes.setdefault(column, "辨識信心偏低（%.2f）" % value)
        return notes

    def to_row(self):
        return dict(self.values)

    def describe(self):
        return "%s 第 %d 頁（%s）" % (os.path.basename(self.source), self.page + 1, self.code)


class Converter:
    """把樣板、底圖、欄位定義備齊，然後一批一批處理。"""

    def __init__(self, store, districts=None):
        self.store = store
        self.templates = layout.TemplateSet.load(store)
        self.districts = list(districts or ["三峽區", "鶯歌區"])
        self.roads = lexicon.load(store)
        # 地段名字典。沒建就是空的 —— 空的代表不驗證，照讀出來的寫。
        self.sections = lexicon.load_sections(store)
        self._bases = {}
        self._fields = {}
        # 每跑一次 run() 就換一本新的。診斷報告完全靠它。
        self.journal = diagnose.Journal()
        # 認不出來的頁。它們會被併進前一件，如果不講出來就等於靜靜吃掉資料。
        self.unknown = []

    def base_of(self, code, role="front"):
        """底圖。背面用 base_back.png，沒有就回 None（不減版面）。"""
        key = (code, role)
        if key not in self._bases:
            name = "base.png" if role == "front" else "base_back.png"
            self._bases[key] = resources.imread(os.path.join(self.store, code, name),
                                                cv2.IMREAD_COLOR)
        return self._bases[key]

    def grid_of(self, code, role="front"):
        """**找格線專用**的底圖。沒有就退回一般底圖。

        跟 base.png 同一批樣本、同一個座標系，差別只在合成前把墨跡加粗過
        —— 影印來文（承辦人的 C 表）格線又細又淡，「逐像素取最亮」會把它
        吃掉，格線一找不到，身分證那一欄就退回整行讀，十個字讀成六碼。

        退回 base.png 是刻意的：舊樣板沒有 grid.png，退回去就是改之前的
        行為，不會壞掉，重做一次底稿就會自動變好。
        """
        key = (code, role, "grid")
        if key not in self._bases:
            name = "grid.png" if role == "front" else "grid_back.png"
            image = resources.imread(os.path.join(self.store, code, name),
                                     cv2.IMREAD_COLOR)
            self._bases[key] = image if image is not None else self.base_of(code, role)
        return self._bases[key]

    def fields_of(self, code):
        if code not in self._fields:
            self._fields[code] = fieldmod.load(self.store, code)
        return self._fields[code]

    def run(self, paths, progress=None, keep_crops=False):
        """處理一批 PDF，回傳 (資料列, 需要人工分頁的頁面)。

        沿路把每一步的中間結果記進 self.journal，出問題時才有東西可以看。
        """
        journal = self.journal = diagnose.Journal()
        journal.store = self.store
        self._survey(journal, paths)

        clock = time.time()
        pages = layout.classify_pages(paths, self.templates)
        journal.timing["分類"] = time.time() - clock
        for page in pages:
            journal.pages.append({
                "file": journal.file_id(page.source),
                "page": page.index + 1,
                "code": page.code,
                "role": page.role,
                "rotation": page.rotation,
                "inliers": page.inliers,
                "margin": page.margin,
            })

        documents = layout.split_documents(pages)
        journal.documents = len(documents)

        # 切分改成照頁數配對之後，「認不出來」不再會吃掉一整件 ——
        # 它只影響「這是哪一種表格」。所以這裡只列真正會出事的：
        # 正面認不出來、但背面認得出來的那些件。那種件的欄位是照背面認出的
        # 表格去裁的，正面可能掃歪了或蓋章蓋掉特徵，值得人看一眼。
        self.unknown = []
        for document in documents:
            front, code = document.front, document.code
            if front is None or code is None or front.code == code:
                continue
            self.unknown.append({"file": journal.file_id(front.source),
                                 "page": front.index + 1,
                                 "inliers": front.inliers,
                                 "margin": front.margin})

        clock = time.time()
        records, unresolved = [], []
        for document in documents:
            if not document.complete:
                unresolved.append(document)
                journal.unresolved.append({
                    "file": journal.file_id(document.pages[0].source),
                    "pages": "、".join(str(p.index + 1) for p in document.pages),
                    "why": document.problem or "",
                })
                continue
            try:
                record = self.read_document(document, keep_crops=keep_crops)
            except Exception as error:                              # noqa: BLE001
                # 一件壞掉不該讓整批停下來 —— 十幾件裡有一件認不出來，
                # 其餘的照樣要能輸出，那一件記進診斷報告讓人去看。
                journal.note_error("讀取 %s 第 %d 頁" % (
                    journal.file_id(document.pages[0].source),
                    document.pages[0].index + 1), error)
                continue
            if record is not None:
                records.append(record)
                journal.records.append(self._describe(record, len(records) - 1))
                if progress:
                    progress(record)
        journal.timing["辨識"] = time.time() - clock
        return records, unresolved

    def _grid_state(self, code):
        """診斷報告上「格線圖」那一欄要寫什麼。

        有三種狀態，不是兩種。原本只分「有／沒有（底稿要重做一次）」，
        於是用空白原稿建的樣板永遠被判成「沒有」，而那種樣板**重做幾次
        都不會產生 grid.png** —— grid.png 只有「多份掃描件合成」那條路
        才會做。承辦人 2026-09-21：「F、G 型表格在製作底稿時，不知道為
        甚麼沒辦法跑出 grid.png 這個檔案。」花了一週在修一個不存在的問題。

        **給錯方向的診斷比沒有診斷還糟。** 分不出來的時候就說分不出來。
        """
        if os.path.isfile(os.path.join(self.store, code, "grid.png")):
            return "有"
        source = self._base_source(code)
        if source == "blank":
            # 空白原稿的格線是印刷廠印的，乾淨得很，本來就不需要救
            return "不需要（底稿是空白原稿）"
        if source == "scans":
            return "沒有（底稿要重做一次）"
        # 舊樣板沒記來源。不要硬猜成「要重做」—— 猜錯就是叫人去做白工
        return "沒有（不確定底稿怎麼做的，見下方說明）"

    def _base_source(self, code):
        """底圖是怎麼做的。blank／scans／None（舊樣板沒記）。"""
        try:
            with open(os.path.join(self.store, code, "index.json"),
                      encoding="utf-8") as handle:
                return json.load(handle).get("base_source")
        except (OSError, ValueError):
            return None

    def _survey(self, journal, paths):
        """把樣板與輸入檔的概況記下來。樣板沒建好是最常見的「它壞了」。"""
        for template in self.templates.templates:
            entry = next((t for t in journal.templates if t["code"] == template.code), None)
            if entry is None:
                base = self.base_of(template.code)
                definitions = self.fields_of(template.code)
                entry = {
                    "code": template.code,
                    "roles": 0,
                    "base": "有" if base is not None else "沒有",
                    "base_size": "%dx%d" % (base.shape[1], base.shape[0]) if base is not None else "-",
                    "grid": self._grid_state(template.code),
                    "field_count": len(definitions),
                    "fields": "、".join(
                        "%s/%s/%s" % (fieldmod.COLUMNS.get(d.column, d.column), d.kind, d.mode)
                        for d in definitions),
                }
                journal.templates.append(entry)
            entry["roles"] += 1

        for path in paths:
            try:
                count = render.page_count(path)
            except Exception as error:                              # noqa: BLE001
                journal.note_error("開啟 %s" % journal.file_id(path), error)
                count = "讀不到"
            journal.files.append({
                "id": journal.file_id(path),
                "ext": os.path.splitext(path)[1].lower(),
                "pages": count,
            })

    def _describe(self, record, index):
        """把一件的辨識結果整理成診斷用的資料。內容全部遮罩。"""
        definitions = {d.column: d for d in self.fields_of(record.code)}
        entries = []
        # **完全失敗的欄位也要進報告。** 以前這裡只走 record.values，
        # 而一個欄位如果什麼都沒讀到，values 裡根本不會有它 ——
        # 於是報告上那一列整個消失，連「找不到」都看不到。
        # 承辦人 2026-09-24：「抓不到文號是怎麼回事? 有兩件這樣了」——
        # 那兩件的公文文號就是這樣從報告上蒸發的，而它是必要欄位。
        # 失敗的欄位比成功的欄位更需要出現在診斷報告上。
        columns = list(record.values)
        for column in list(record.problems) + list(record.how):
            if column not in columns:
                columns.append(column)
        for column in columns:
            value = record.values.get(column, "")
            definition = definitions.get(column)
            how = record.how.get(column) or {}
            entries.append({
                "column": column,
                # 沒有欄位定義不代表沒有型別 —— 公文文號現在是整頁自動找的，
                # 樣板上根本不會有它的欄位，顯示「?」會讓人以為是壞掉了。
                "kind": (definition.kind if definition
                         else fieldmod.DEFAULT_KIND.get(column, "整頁找")),
                "confidence": record.confidence.get(column, 0.0),
                "raw": diagnose.mask(record.raw.get(column, "")),
                "value": diagnose.mask(value),
                "problem": diagnose.mask_problem(record.problems.get(column, "")),
                "cells": how.get("格數", 0),
                # 只有數字（每格幾個像素寬），不含內容，可以外傳
                "cell_widths": [int(w) for w in how.get("格寬", []) or []],
                "box_width": int(how.get("框寬", 0) or 0),
                # **白名單，不是黑名單。** 只有列在這裡的讀法才會進報告 ——
                # 新增一種讀法時如果忘了加，報告會少一欄（看得出來），
                # 而不是把沒遮罩過的東西漏出去（看不出來）。
                "readings": {name: diagnose.mask_reading(how.get(name, ""))
                             for name in ("整行", "照格子", "逐格", "逐空白",
                                          "關鍵字", "整頁找", "採用")
                             if name in how},
            })
        return {
            "index": index,
            "code": record.code,
            "file": self.journal.file_id(record.source),
            "page": record.page + 1,
            "fields": entries,
            "sheets": record.sheets,
        }

    def sheet_of(self, code, page, role, rotation=None):
        """把一頁算成「減掉印刷版面之後」的影像。

        回傳 (原圖, 減掉版面的影像, 底圖, 對位品質)。

        對位品質是 {"減版面": bool, "對位": 0~1 或 None}，只給診斷報告用。

        底圖對得上就相減，只留手寫的內容；對不上就退回灰階原圖 ——
        減不掉頂多辨識差一點，硬減會把整頁弄糊。

        第三個回傳值是**對得上時**的底圖。欄位裡印好的方格線就在它上面，
        辨識時要靠它來切格子。減不掉的時候回 None —— 那時候的影像還在
        掃描件自己的座標系，底圖的格線位置對不上，拿來切只會切錯。
        """
        # 空白或認不出來的那一面拿不到轉向（沒有特徵點可以對），
        # 跟著正面走 —— 同一張紙的兩面，掃進來的方向一定一樣。
        turn = page.rotation or (rotation or 0)
        image = render.rotate(
            render.render(page.source, page.index, dpi=render.FULL_DPI, gray=False),
            turn)
        base = self.base_of(code, role)
        if base is None:
            return image, baseimage.as_gray(image), None, {
                "減版面": False, "對位": None, "原因": "這一面沒有底圖"}
        try:
            sheet = baseimage.subtract(base, image)
        except ValueError:
            return image, baseimage.as_gray(image), None, {
                "減版面": False, "對位": None, "原因": "底圖對不上這張掃描件"}

        # 再靠顏色抽一次筆跡，兩條路取聯集（任一條認出的墨都留下）。
        #
        # 粉紅紙、紅色印刷、黑色格線的表格上，藍色原子筆用 B-R 一刀就切得乾淨，
        # 而且不受對位誤差影響；減版面則會在手寫壓到格線的地方把筆畫削斷。
        # 實測 F 表身分證欄：減版面「2」只剩一個小點，靠顏色十個字都完整。
        # 黑筆寫的抽不出來（跟格線同色），那時候顏色這條路是空的，
        # 聯集就等於只有減版面 —— 不會比現在差。
        moved, _inliers = baseimage.to_base(base, image)
        quality = {"減版面": True, "對位": None, "原因": ""}
        if moved is not None:
            colour = baseimage.ink_by_colour(moved)
            if colour is not None and colour.shape == sheet.shape:
                sheet = np.minimum(sheet, colour)
            # 對位有多準。減得掉不代表減得乾淨 —— 差幾個像素就會在印刷筆畫
            # 邊緣留下殘影，欄位裡看起來像多了幾撇。這個數字讓報告看得出來。
            quality["對位"] = baseimage.coverage(base, moved)
        return image, sheet, base, quality

    def read_document(self, document, keep_crops=False):
        """讀一件申請案，把欄位辨識出來。

        正面一定要讀。背面只有在樣板真的有定義背面欄位時才去算 ——
        多算一頁 300dpi 影像要一秒多，沒欄位的話白算。
        """
        front = document.front
        code = document.code
        definitions = self.fields_of(code)
        if not definitions:
            return None

        record = Record(code, front.source, front.index)

        # 每一面各自處理：算影像、裁欄位、辨識。
        # 行政區要先讀 —— 地址的路名字典是分區的，三峽有「仁愛街」、
        # 鶯歌有「仁愛路」，不知道哪一區就選不出來，所以正面先做。
        # 正面就是第一頁、背面就是第二頁，照頁數認，不看分類結果。
        # 空白背面本來就認不出來（墨跡太少，內點是 0），拿分類去找背面
        # 等於把「背面是空白」跟「沒掃到背面」混成同一件事。
        pages = {"front": front}
        if any(d.page == "back" for d in definitions):
            pages["back"] = document.back

        for role in ("front", "back"):
            wanted = [d for d in definitions if d.page == role]
            if not wanted:
                continue
            page = pages.get(role)
            if page is None:
                for definition in wanted:
                    record.problems.setdefault(
                        definition.column, "這一件沒有掃到背面，讀不到這一欄")
                continue

            # 整頁的文字和減掉版面的影像都很花時間，用到才做、只做一次。
            # 以前是「關鍵字欄位全部讀完，再讀框選欄位」，於是 B 表那種門牌
            # 用關鍵字、行政區用框選的，讀門牌的時候還不知道是哪一區，只好
            # 拿兩區合起來的字典去對 —— 三峽的「國際一街」和鶯歌的「國際一路」
            # 就撞在一起，跳出「字典裡兩條都有」的提醒（承辦人 2026-11-03：
            # 「三峽才有國際一街，國際一路在鶯歌。」）。行政區不管用哪一種方式
            # 讀，一律最先讀，見 reading_order。
            lines = rules = None
            sheet = grid = None
            for definition in reading_order(wanted):
                if definition.mode == fieldmod.LABEL:
                    if lines is None:
                        # 關鍵字欄位讀的是原始頁面，不是減掉版面之後的影像 ——
                        # 要找的就是印刷的標籤，減掉版面等於把要找的東西擦掉。
                        lines, rules = pagetext.read_page(
                            page.source, page.index, front.rotation)
                    self._read_label_field(record, definition, lines, rules)
                else:
                    if sheet is None:
                        _, sheet, base, quality = self.sheet_of(
                            code, page, role, front.rotation)
                        record.sheets[role] = quality
                        # 找格線用加粗過的那張（沒有就自動退回 base）。它只拿來找
                        # 格線，切字讀字用的還是 sheet —— 相減那一步完全沒動到。
                        grid = self.grid_of(code, role) if base is not None else None
                    self._read_field(record, sheet, definition, keep_crops, grid)

        # 公文文號**一律**整頁找，不管樣板有沒有定義這一欄。
        #
        # 原本的條件是「樣板有 doc_number 欄才找」，那等於逼承辦人為了一個
        # 根本不用框的東西去建一個空欄位 —— 而且忘了建就整批沒有文號，
        # 三個輸出檔的案號欄全空，還不會有任何提示。
        # 它是必要欄位，每一種表格都有收文戳，就不要讓它取決於設定。
        self._read_stamp(record, document)
        self._reconcile_district(record)

        for column in CRITICAL:
            if not record.values.get(column):
                record.problems.setdefault(column, "沒有讀到內容")
        return record

    def _reconcile_district(self, record):
        """行政區跟路名互相印證：行政區讀不出來，就從路名推回來。

        承辦人 2026-10-21：「行政區的部分不能從路名反推嗎?」—— 可以，而且
        三峽、鶯歌的路名大多只在其中一區有。做法是拿讀到的門牌，在每一區的
        字典裡各對一次，看哪幾區是「一個字都不用改就對上」：

          行政區沒讀出來、路名只在一區有   → 行政區補成那一區，標記
          行政區讀得很勉強（只對到一個字）、→ 改成路名那一區，門牌也照那一區
          但路名只在另一區一字不差           重新對，兩欄都標記
          行政區讀得很清楚、路名卻在別區     → 不改，但在門牌的提醒裡講出來
          路名兩區都有（例如中山路）         → 推不回來，什麼都不動

        **只認「一字不差」的對上。** 改過字才對上的路名本身就是猜的，
        不能再拿它去改另一欄 —— 那是拿一個猜測去證明另一個猜測。
        """
        raw = record.raw.get("address")
        if not raw or "district" not in record.values or not self.roads:
            return
        districts = [d for d in self.districts if d in self.roads]
        if len(districts) < 2:
            return
        exact = {}
        for name in districts:
            info = {}
            value, note = validate.address(raw, roads=lexicon.for_district(self.roads, name),
                                           info=info)
            if info.get("road") and info.get("road_note") is None:
                exact[name] = (value, note, info["road"])
        if len(exact) != 1:
            return
        (place, (value, note, road)), = exact.items()
        current = record.values.get("district") or ""
        if current == place:
            return
        heard = validate.to_traditional(record.raw.get("district") or "")
        if current in districts:
            strength = len(validate.distinct_chars(current, districts) & set(heard))
            if strength >= 2:
                # 行政區讀得很清楚：兩欄裡至少有一欄錯了，分不出是哪一欄，不改。
                # 但一定要講出來 —— 不講的話，門牌已經照這一區「修正」過了。
                record.problems["address"] = validate._join_notes(
                    record.problems.get("address"),
                    "路名「%s」一字不差的話是%s的路，%s沒有這條；"
                    "請確認行政區跟門牌哪一個讀錯了" % (road, place, current))
                return
        record.values["district"] = place
        record.problems["district"] = (
            "行政區%s，路名「%s」只有%s有，已經照路名改成%s，請確認"
            % ("沒讀出來" if current not in districts else "讀到的是「%s」" % current,
               road, place, place))
        record.values["address"] = value
        if note:
            record.problems["address"] = note
        else:
            record.problems.pop("address", None)

    def _read_stamp(self, record, document):
        """在整頁上找收文戳的公文文號，不靠框選。

        承辦人的原話：「有時候貼標籤的人不會貼在固定的位置」。框選的欄位裡
        什麼都沒有的時候，那一欄就整個廢掉 —— 實測就有一件是這樣。

        整頁掃描找得到是因為文號的組成固定（民國年＋機關代號＋流水號，共十碼），
        整頁上符合這個格式的數字串幾乎只有它一個。詳見 pipeline/stamp.py。

        框選的結果不丟掉：兩邊都有而且不一樣的時候，兩個都講出來讓人選。
        """
        boxed = record.values.get("doc_number")
        found, note = stamp.find([document.front, document.back], document.front.rotation)
        how = record.how.setdefault("doc_number", {})
        # 同上：找不到的原因是程式寫的字，遮掉就變成一串「字」看不懂了
        how["整頁找"] = ({"值": found, "說明": ""} if found
                        else {"值": "", "說明": "找不到：%s" % note})

        if not found:
            # 找不到就維持框選的結果，什麼都不動
            if not boxed:
                record.problems.setdefault("doc_number", note)
            return

        record.values["doc_number"] = found
        record.confidence.setdefault("doc_number", 1.0)
        how["採用"] = found
        if boxed and boxed != found:
            # 兩種讀法不一樣本身就是警訊。以整頁找到的為準（它通過了
            # 「民國年＋機關代號＋十碼」的格式檢查，框選的沒有），但要講出來。
            record.problems["doc_number"] = (
                "框選讀到「%s」，整頁上找到的是「%s」，兩個不一樣，請確認"
                % (boxed, found))
        elif note:
            record.problems["doc_number"] = note
        else:
            record.problems.pop("doc_number", None)

    def _dictionaries(self, definition, record):
        """這個欄位驗證時要用到的字典。行政區已經先讀好了，所以分得出區。"""
        extra = {}
        if definition.kind == "district":
            extra["known"] = self.districts
        elif definition.kind == "section":
            extra["known"] = lexicon.for_district(
                self.sections, record.values.get("district"))
        elif definition.kind == "address":
            extra["roads"] = lexicon.for_district(
                self.roads, record.values.get("district"))
        return extra

    def _read_label_field(self, record, definition, lines, rules):
        """靠印刷標籤讀一個欄位（電腦產製的表格用）。

        門牌那一格底下常常還印著房屋建號 —— 一整串數字。承辦人說那個不要，
        而門牌本來就不可能整行都是數字，所以整行是數字的就丟掉。
        這條規則綁在「型別是門牌」上，不是另外開一個選項：多一個選項就多一個
        設錯的機會，而這件事沒有第二種合理的答案。
        """
        drop_digits = definition.kind == "address"
        raw, confidence, note = pagetext.value_for(
            lines, definition.label, drop_digits=drop_digits, rules=rules)

        how = record.how.setdefault(definition.column, {})
        # 「沒讀到」是程式自己的話，不能拼進要遮罩的那一串 ——
        # 拼進去的話報告上會變成「（字字字）」，看起來像讀到了三個中文字。
        how["關鍵字"] = {"值": "「%s」→ %s" % (definition.label, raw) if raw
                                else "「%s」→" % definition.label,
                        "說明": "" if raw else (note or "沒讀到")}
        how["採用"] = raw

        value, problem = validate.check(
            definition.kind, raw, **self._dictionaries(definition, record))
        record.raw[definition.column] = raw
        record.values[definition.column] = value
        record.confidence[definition.column] = confidence
        if note:
            record.problems[definition.column] = note
        elif problem:
            record.problems[definition.column] = problem

    def _read_field(self, record, sheet, definition, keep_crops, grid=None):
        """讀一個欄位。

        同一個欄位最多讀三遍，因為沒有一種讀法對所有欄位都最好：

        整行讀      一般欄位（門牌、姓名）唯一合理的讀法。
        照格線讀    原稿上印好一字一格的欄位（身分證、地號）。格線就在底圖上，
                    位置精確而且每一份都一樣。承辦人說得對：「原稿就有格子了」，
                    不該要求人去框十個小方塊。
        照空白讀    沒有格線、但字跟字之間有明顯空白時的退路。

        整行讀在一字一格的欄位上很容易出事：偵測階段會把相鄰的字併成一塊，
        十個字讀出九個（實測讀到 9 碼、5 碼都有），而那九個看起來像模像樣。
        """
        pieces, crops, scores = [], [], []
        grid_pieces, grid_scores, any_grid = [], [], False
        cell_count = 0
        # 每一格切出來多寬、整個框多寬 —— 只有數字，不含任何內容。
        # C 表身分證一直切成 8 格（該 10 格），報告上只有「格數 8」看不出是
        # 兩格黏在一起，還是整排格子沒框到。有寬度就分得出來。
        span_widths, box_width = [], 0
        all_cells = []
        all_spans = []
        overlap_notes = []
        grid_source = None
        grid_band = None
        for box, suffix in definition.segments():
            crop = recognise.crop_field(sheet, box)
            # 一般文字欄位：框的上緣壓到上一行的印刷字時，先切掉那一條再讀
            # （見 recognise.strip_printed_top）。身分證、地號那種一字一格的
            # 不動。切過的那一份**另外放**、只拿來整行讀：底下照格線讀的那一段
            # 是拿 crop 跟「照整個框裁的格線圖」對齊的，crop 變矮就對不上了。
            seen = (recognise.strip_printed_top(crop)
                    if definition.kind in TEXT_KINDS else crop)
            text, confidence, merged = recognise.read_noted(seen)
            # 接段落時動到內容（重疊處的字只算一次）要標出來，見 recognise._merge_overlaps
            overlap_notes.extend(merged)
            if crop is not None and crop.size:
                crops.append(crop)
            text = (text or "").strip()
            # 一格一框的門牌，第一格照慣例就是路名（後綴留空，路還是街
            # 由字典決定，見 說明.txt）。留下來給難字回報用。
            #
            # **這裡的條件是資安條件，不是方便條件。** 2026-09-22 承辦人傳回來
            # 的第一批難字回報裡，「路名」那幾張其實是**整串完整住址**——
            # 檔名上就寫著「程式讀成光明路75巷16號六樓之1」。原因是當時只檢查
            # 「第一段而且後綴是空的」，而門牌只框一個大框的表格（E 表那種，
            # 還有框法比較隨性的 F、G）就只有一段，那一段就是整串地址。
            #
            # 所以條件加成三條，全部都要成立才收：
            #   一、門牌真的是一格一框（不只一段）—— 只有那種的第一段才是路名
            #   二、讀到的東西裡不能有 段巷弄號樓之 —— 有就代表框到整串了
            #   三、長度不超過 6 個字 —— 路名再長也不會超過
            # 任何一條不成立就不收。寧可少收資料，也不要把住址送出機關。
            if (definition.column == "address" and definition.mode == fieldmod.FIXED
                    and not suffix and record.road_cell is None
                    and crop is not None and crop.size
                    and only_the_road(text, len(definition.segments()))):
                record.road_cell = (seen, text)
            # 門牌只框一個大框的表格（E 表）走另一條路：整格是整串住址，
            # **整格絕對不能送出去**，但最左邊那一個字可以（見 first_character）。
            # 那一個字正是鶯歌「鳳X路／龍X路」分不出來的關鍵，而且單獨一個
            # 街道名稱用字指不向任何人。
            if (definition.column == "address" and definition.mode == fieldmod.FIXED
                    and not suffix and record.address_cell is None
                    and crop is not None and crop.size
                    and len(definition.segments()) == 1):
                record.address_cell = (seen, text)
            if (definition.column == "address" and definition.mode == fieldmod.FIXED
                    and len(definition.segments()) > 1
                    and seen is not None and getattr(seen, "size", 0)):
                record.address_parts.append((seen, text, suffix))
            if (definition.kind == "district" and len(definition.segments()) == 1
                    and record.district_cell is None
                    and seen is not None and getattr(seen, "size", 0)):
                record.district_cell = (seen, text)
            if text:
                pieces.append(text + suffix)
                scores.append(confidence)
            elif not suffix:
                # 沒有後綴的空格子代表真的沒讀到，信心要算進去；
                # 有後綴的空格子（例如沒有「段」）是正常的，整段跳過。
                scores.append(confidence)

            # 照原稿印好的格線再讀一次。格線在底圖上，減掉版面之後的影像
            # 只剩手寫，所以要拿底圖去找線、拿減完的影像去切字。
            #
            # 這裡拿的是**加粗過的**那張（grid.png），不是相減用的那張 ——
            # 影印件的細格線經不起「逐像素取最亮」。切字讀字用的還是 crop，
            # 所以加粗不會碰到手寫筆畫。詳見 baseimage.GRID_THICKEN。
            printed = recognise.crop_field(grid, box) if grid is not None else crop
            spans = recognise.grid_spans(printed, crop)
            cells = [crop[:, a:b] for a, b in spans]
            span_widths.extend(int(b - a) for a, b in spans)
            if crop is not None and getattr(crop, "size", 0):
                box_width += int(crop.shape[1])
            if spans and grid_source is None:
                # 滑動視窗要在同一張影像上跨格取，所以記住是哪一張，
                # 順便記住那排格子在垂直方向的位置 —— 欄位框通常畫得比
                # 格子高，讀的時候要收回去，不然會把隔壁行的字讀進來。
                grid_source, all_spans = crop, list(spans)
                grid_band = recognise.grid_band(printed)
            cell_text, cell_score = ("", confidence)
            cell_count += len(cells)
            all_cells.extend(cells)
            if cells:
                cell_text, cell_score = recognise.read_pieces(cells)
                any_grid = any_grid or bool(cell_text)
            # 沒有格線的那幾格要沿用整行讀的結果。
            # 門牌是分成路／巷／弄／號好幾格的，只有「號」那格有印格子；
            # 只收有格線的那幾格，grid_raw 就會變成單一個「15號」，
            # 拿它當整欄的值等於把路名整段丟掉。
            chosen = cell_text.strip() or text
            if chosen:
                grid_pieces.append(chosen + suffix)
                grid_scores.append(cell_score if cell_text.strip() else confidence)
            elif not suffix:
                grid_scores.append(confidence)

        if keep_crops and crops:
            stacked = crops[0] if len(crops) == 1 else _stack(crops)
            if grid_band is not None and len(crops) == 1:
                # 有印刷格子的欄位，複核畫面上只顯示那排格子。
                # 欄位框通常畫得比格子高，整框顯示的話字只佔一小條，
                # 而這一欄正是最需要看清楚、要照著打的那一欄。
                top, bottom = grid_band
                slack = max(6, (bottom - top) // 4)
                cut = stacked[max(0, top - slack):min(stacked.shape[0], bottom + slack), :]
                if cut.size:
                    stacked = cut
            ok, buffer = cv2.imencode(".png", stacked)
            if ok:
                record.crops[definition.column] = buffer.tobytes()

        raw = "".join(pieces)
        confidence = min(scores) if scores else 0.0
        grid_raw = "".join(grid_pieces)
        grid_confidence = min(grid_scores) if grid_scores else 0.0

        # 每一欄「是怎麼讀出來的」要留下來。上一版把照格線逐格辨識做進去之後，
        # 診斷報告上完全看不出它有沒有觸發過 —— 結果一次都沒觸發，
        # 而我看著報告看不出來，又照著錯誤的假設猜了一輪。
        how = {"格數": cell_count, "整行": raw, "照格子": grid_raw if any_grid else "",
               "格寬": span_widths, "框寬": box_width}
        record.how[definition.column] = how

        extra = self._dictionaries(definition, record)

        if definition.kind == "id_number":
            # 身分證有檢查碼 —— 這讓我們可以**驗證**而不是猜，別的欄位沒這優勢。
            #
            # 原稿上剛好切出十格的時候，走最強的那條路：每一格給兩種讀法
            # （偵測+辨識、只做辨識，它們失手的地方不一樣），再把十格的可能
            # 逐一組合，看哪一種通過檢查碼。讀錯一格時正確答案不在候選裡，
            # 沒有組合會通過，於是標起來 —— 不會生出一個通過檢查碼但錯的號碼。
            # per_cell 一定要先給值。沒有印刷格子的表格（A、B、E 那種電腦
            # 產製的）走不進下面那個 if，底下卻讀得到它 —— 於是整份文件
            # 在這裡丟 UnboundLocalError 被跳過，使用者看到的是「只有 F
            # 讀得到，新建的樣板通通沒反應」，完全看不出是這一行。
            solved = solved_problem = None
            per_cell = []
            if len(all_spans) == 10:
                # 一次讀三格，每一格被三個視窗各讀到一次。
                # 一格一個字等於把模型需要的上下文拿掉，實測差很多。
                per_cell = recognise.read_grid(grid_source, all_spans, grid_band)
                record.cells[definition.column] = list(all_cells)
                record.cell_text[definition.column] = [list(c) for c in per_cell]
                # 顯示用的加了「讀到但用不上」的標記（見 validate.cell_shown），
                # 底下要當辨識原文的那一份維持乾淨，不要把標記寫進輸出值
                how["逐格"] = "|".join(validate.cell_shown(c, i)
                                       for i, c in enumerate(per_cell))
                plain_cells = "".join(c[0] if c else "?" for c in per_cell)
                solved, solved_problem = validate.solve_id(per_cell)

            direct = ("".join(c[0] for c in per_cell)
                      if per_cell and all(per_cell) else "")

            if solved:
                value, problem = solved, solved_problem
                raw = plain_cells
                confidence = grid_confidence or confidence
            elif direct:
                # 十格都讀到字了，只是湊不出通過檢查碼的組合。
                #
                # 這時候**不可以**退回整行讀 —— 逐格的結果遠比整行可信。
                # 真實掃描件實測：逐格讀出 A123454321（十格全對），整行讀是
                # L423454312。以前這裡會退回去用整行那個，等於把讀對的答案
                # 丟掉換成錯的。
                #
                # 湊不出來通常代表兩件事之一：真的讀錯了一碼，或者這個號碼
                # 本身就不合法（測試用的假號碼多半是這樣）。兩種都要標起來
                # 讓人看，但值要給讀到的那個。
                raw = direct
                confidence = grid_confidence or confidence
                value, problem = validate.id_number(validate.fix_id_positions(direct))
                if problem and solved_problem:
                    problem = "%s（逐格：%s）" % (problem, solved_problem)
            else:
                # 切不出十格，或十格湊不出唯一解。退回原本的三種讀法比一比。
                spaced_pieces, spaced_scores = [], []
                for box, _suffix in definition.segments():
                    text, score = recognise.read_cells(recognise.crop_field(sheet, box))
                    if text:
                        spaced_pieces.append(text)
                        spaced_scores.append(score)
                spaced = "".join(spaced_pieces)
                spaced_confidence = min(spaced_scores) if spaced_scores else 0.0
                how["逐空白"] = spaced
                value, problem = validate.best_id(raw, grid_raw, spaced)
                if solved_problem and problem:
                    problem = "%s（逐格：%s）" % (problem, solved_problem)
                for candidate, score in ((grid_raw, grid_confidence),
                                         (spaced, spaced_confidence)):
                    if candidate and value == validate.fix_id_positions(candidate):
                        raw, confidence = candidate, score
                        break

                # 三種讀法都沒讀出一個合法的號碼，但格子有切出十格 ——
                # 那就把逐格的結果原樣交出去，讀不出來的那幾格寫「?」。
                #
                # **為什麼比整行讀的結果有用**：整行讀連長度都常常不對
                # （2026-09-21 F 表第 4 件就是十個字讀成九個），人拿到一串
                # 九碼的錯號碼只能整串重打，還得自己一格一格對位置。逐格的
                # 位置是對的，讀出來的那幾格多半也是對的，人補「?」就好。
                #
                # 「?」不是猜，是明講不知道：它過不了檢查碼，匯出前那一關
                # 也會擋，所以不可能被當成讀好的值送進 RPA。
                partial = validate.partial_id(per_cell) if problem else None
                if partial and partial.count("?") < 10:
                    missing = partial.count("?")
                    value, raw = partial, partial
                    confidence = grid_confidence or confidence
                    problem = ("逐格讀出來的擺在這裡了，有 %d 格讀不出來（?），"
                               "請對著上面的原圖把那幾格補起來"
                               % missing)
                    if solved_problem:
                        problem = "%s（%s）" % (problem, solved_problem)
        elif any_grid and grid_raw and grid_raw != raw:
            # 這一欄有印好的格子。格線切出來的結果比整行讀可靠 ——
            # 一格一個字，不會把兩個字併成一個，也不會漏掉最後那一豎
            # （實測「701」整行讀成「70」）。所以以格線的結果為準。
            #
            # 但兩種讀法不一樣這件事本身就是警訊，一定要講出來：
            # 換掉的是三個必要欄位之一的時候，人得自己看一眼原圖。
            value, problem = validate.check(definition.kind, grid_raw, **extra)
            line_value, _line_problem = validate.check(definition.kind, raw, **extra)
            raw, confidence = grid_raw, grid_confidence
            if line_value != value:
                problem = problem or ("整行讀是「%s」，照格子讀是「%s」，兩種不一樣"
                                      % (line_value, value))
        else:
            value, problem = validate.check(definition.kind, raw, **extra)

        if overlap_notes:
            how["段落重疊"] = len(overlap_notes)
            problem = validate._join_notes(problem, "；".join(overlap_notes))
        how["採用"] = raw
        record.raw[definition.column] = raw
        record.values[definition.column] = value
        record.confidence[definition.column] = confidence
        if problem:
            record.problems[definition.column] = problem


SENSITIVE = "裁切圖（含個資，勿外傳）"

_SENSITIVE_README = """\
這個資料夾裡是辨識時實際看到的影像，**含個資**（身分證號、門牌都在上面）。

它跟「診斷」資料夾不一樣：診斷報告是遮罩過、可以外傳的；這裡不是。
**不要把這個資料夾裡的東西傳給任何人，包括開發者。**

用途是你自己看：辨識結果不對的時候，打開對應的圖，看是哪一種情形 ——

  格子切歪了、一個字被切成兩半      → 樣板的框要重畫
  格子是對的，但那一格根本是空白    → 掃描太淡，或那一格真的沒寫
  格子是對的、字也清楚，卻讀錯      → 是辨識模型的問題，跟開發者說

看完可以直接刪掉整個資料夾，下次轉換會重建。
"""


def dump_cells(records, folder):
    """把切出來的每一格存成圖，讓承辦人自己看是切歪了還是認不出來。

    **含個資**，所以不放進診斷資料夾 —— 那個資料夾的規矩是可以外傳。
    """
    import cv2 as _cv2

    from . import fields as _fields, resources as _resources

    target = os.path.join(folder, SENSITIVE)
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "讀我.txt"), "w", encoding="utf-8") as handle:
        handle.write(_SENSITIVE_README)
    written = 0
    for index, record in enumerate(records, start=1):
        for column, cells in (record.cells or {}).items():
            label = _fields.COLUMNS.get(column, column)
            for number, cell in enumerate(cells, start=1):
                if cell is None or not getattr(cell, "size", 0):
                    continue
                name = "第%02d件-%s-第%02d格.png" % (index, label, number)
                if _resources.imwrite(os.path.join(target, name), cell):
                    written += 1
    return target, written


HARD_CELLS = "難字回報（可以傳給開發者）"

# 門牌開頭的路街名。難字回報只拿它跟程式讀到的比對 —— 只收這一格。
_ROAD_HEAD = re.compile(r"^([\u4e00-\u9fff]{1,8}(?:大道|[路街道]))")

# 人複核完的門牌照文法拆成幾段，好跟一格一框的那幾格對起來。
# 輸出的門牌一定長這樣（validate._ADDRESS_SHAPE），拆不開就不收。
_ADDRESS_PARTS = re.compile(
    r"^(?P<road>[\u4e00-\u9fff]{1,8}(?:大道|[路街道]))"
    r"(?:(?P<sec>[一二三四五六七八九十]+)段)?"
    r"(?:(?P<lane>\d+)巷)?"
    r"(?:(?P<alley>\d+)弄)?"
    r"(?P<no>\d+(?:之\d+)?)號"
    r"(?:(?P<floor>[一二三四五六七八九十百]+)樓)?"
    r"(?P<floorsub>之\d+)?$")

# 一格一框的後綴 → 上面拆出來的哪一段。段名不收：段多半寫國字，
# 一個國字跟一個阿拉伯數字的個數對不起來，標錯答案的機會太大。
_SUFFIX_PART = {"巷": "lane", "弄": "alley", "號": "no", "樓": "floor"}

_CHINESE_DIGIT = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9}


def _floor_digits(text):
    """十七 → "17"、二十 → "20"、五 → "5"。一百以上或看不懂就回空字串。"""
    if not text or "百" in text:
        return ""
    if "十" not in text:
        return str(_CHINESE_DIGIT[text]) if text in _CHINESE_DIGIT else ""
    tens, _, ones = text.partition("十")
    if (tens and tens not in _CHINESE_DIGIT) or (ones and ones not in _CHINESE_DIGIT):
        return ""
    return str((_CHINESE_DIGIT[tens] if tens else 1) * 10 + (_CHINESE_DIGIT[ones] if ones else 0))


# 民眾在門牌那個大框裡先寫了行政區的話，最左邊那個字就不是路名的第一個字。
_LEAD_IN_BOX = re.compile(r"^.{1,4}?[縣市區鄉鎮村里]")

# 難字回報檔名裡**絕對不可以**出現的字。檔名寫的是「真值」，而真值是人
# 複核完的門牌 —— 只要裡面混進門牌號、樓、巷、弄，那就是完整住址被寫進
# 一個標著「可以傳給開發者」的檔名裡。2026-09-22 真的發生過一次。
# 簡體的「号」「楼」也要擋 —— 辨識模型吐出來的常常是簡體字，
# 2026-09-22 那批外洩的檔名裡就是寫「七楼」。
_NEVER_IN_NAME = "號樓巷弄之段号楼"

# 檔名裡最多幾個中文字。路名首字只會有一個，整段路名最多四個（鳳吉一街）。
_NAME_MAX_CHINESE = 6


def safe_hard_name(name):
    """這個難字回報的檔名安全嗎？不安全就不要寫出去。

    **這是最後一道關卡，不是備援。** 前面每一條判斷都可能有沒想到的表格
    長相（2026-09-22 就是這樣外洩的：條件寫得好好的，但 E 表只框一個大框，
    整串住址照樣滿足條件）。所以出口再擋一次，用的是跟前面完全不同的依據：
    **看檔名本身**。檔名裡有「號」「樓」「巷」「弄」就一定不對，
    因為真值那一段應該只有路名，路名不會有這些字。
    """
    if any(char in name for char in _NEVER_IN_NAME):
        return False
    # 「真值」「程式讀成」「程式沒讀到」「程式分不出是」「路名」「首字」
    # 這些是固定的字樣，扣掉之後才是資料
    for word in ("路名首字", "路名", "門牌數字", "行政區", "字母", "數字", "真值",
                 "程式讀成", "程式沒讀到", "程式分不出是", "整格沒讀對", "程式讀對"):
        name = name.replace(word, "")
    chinese = sum(1 for char in name if "\u4e00" <= char <= "\u9fff")
    return chinese <= _NAME_MAX_CHINESE

_HARD_README = """\
這個資料夾裡是**辨識樣本**：程式讀錯、讀不出來的字，加上少數讀對的
當對照組，一個字一張圖。裁切圖去識別化之後就是這個樣子，可以直接傳。

檔名就是答案：

    數字_真值8_程式讀成7_a3f9c2.png    程式把 8 讀成 7
    數字_真值5_程式沒讀到_7b21de.png   程式那一格什麼都沒讀到
    字母_真值F_程式讀成T_c4e810.png    第 1 碼（區域碼）讀錯
    路名_真值大觀_程式讀成4大觀_9f1a55.png  門牌的路名那一格讀錯
    路名首字_真值鳳_程式沒讀到_ab12cd.png   路名第一個字沒讀出來
    門牌數字_真值4_程式讀成7_5e61f0.png    門牌的巷、弄、號、樓裡讀錯的那個數字
    行政區_真值三峽區_程式讀成三_88aa01.png 行政區那一格
    數字_真值3_程式讀對_d0e1f2.png     讀對的（對照組，見下面）

「真值」是**你在複核畫面上改完、按下產生輸出檔的那個值**，所以是對的。
程式就是靠這個才知道自己哪一格錯了。

── 這個資料夾可以傳給開發者 ──

跟「裁切圖（含個資，勿外傳）」不一樣，這裡是刻意做成可以外傳的：

  * **身分證一件最多 5 格**，讀錯的優先；讀錯的不到 3 格才補讀對的，
    補到 3 張為止。十格裡最多五格、又不知道是第幾格，湊不回任何人的號碼。
    （從裁切圖自己挑格子刪是不夠的：刪到剩八格，配上最後一碼的檢查碼，
      只剩十來種可能。上限一定要壓在程式裡，不能靠人刪。）
  * 讀對的那幾張是**對照組**：只收讀錯的，開發者量不出「改了之後有沒有
    把原本讀對的改壞」。
  * 門牌的**路名**那一格整格收。路名（「大觀」「鳳鳴」）是公開的街道
    名稱，一條路上有幾百戶，單獨一個指不向任何人。行政區那一格也一樣。
  * 門牌只框一個大框的表格（E 表那種），整格就是整串住址，所以**整格
    不收**，只從最左邊切出一個字（檔名開頭是「路名首字」）。
    鶯歌的「鳳七路」跟「龍七路」分不出來，差的就是那一個字。
  * 門牌的巷、弄、號、樓（一格一框的表格）**拆成一個數字一張**才收，
    整格不收，**一件最多 2 個數字**，也不記是巷、弄、號還是樓。整格的
    「38」配上路名就是住址；兩個沒有順序的數字指不到哪一戶。數字黏在
    一起拆不準、或寫了「之」的，整格不收 —— 寧可少收，也不要標錯答案。
  * 姓名、公文文號、地號**一律不收**。
  * 檔名寫出去之前再擋一次：只要檔名裡出現「號」「樓」「巷」「弄」，
    那一張就不寫。2026-09-22 真的外洩過一次整串住址，這一關是那次加的。
  * **不記第幾件、不記第幾格**，而且檔案順序是打亂的。哪幾個字屬於
    同一個人，這個資料夾裡沒有這個資訊。
  * 一個孤立的手寫數字不是個資 —— 它認不出是誰。

話雖如此，**送出去之前請自己打開看一遍**。這是刻意的設計：
「相信程式有把個資拿掉」不是資安，「自己看過、自己確認過」才是。
看到不該在裡面的東西（例如整排連號、姓名的字，或是**看得到門牌號碼的
整條住址**），就不要送，跟開發者說。

── 為什麼要這份東西 ──

手寫辨識要變好，唯一的辦法是拿「圖 + 正確答案」去量。開發者手上沒有
這種資料（真實件不能外傳），所以只能憑猜測改，改完也驗不出有沒有變好。
有了這份，就能量出「改之前錯幾格、改之後錯幾格」。

累積個幾百個字再一起傳比較有用，不必每天傳。
**傳完之後整個資料夾就可以刪掉**，下次跑會重新建。
"""


def reading_order(definitions):
    """同一面的欄位要照什麼順序讀。

    行政區一定最先，**不管它是框選還是關鍵字**：門牌的路名字典、地段的
    字典都是分區的，不知道哪一區就只能拿兩區合起來的去對，三峽的
    「國際一街」跟鶯歌的「國際一路」、三峽的「仁愛街」跟鶯歌的「仁愛路」
    就分不出來。其餘的照樣板裡的順序（sorted 是穩定排序）。
    """
    return sorted(definitions, key=lambda d: 0 if d.kind == "district" else 1)


def _row_source(records, position, numbers=None, origins=None):
    """匯出的第 position 列是 records 裡的哪一件；找不到就回 None。

    有 origins 就只信它：手動輸入的件可以插在中間，插了之後畫面序號減一
    就不再是清單位置。拿序號去找，會把人改好的身分證拿去跟**別件**的原圖
    比，存出一堆「真值」是錯的圖 —— 那比沒存更糟。所以 origins 裡是 None
    （手動輸入）或不是整數的，一律跳過，不猜。

    沒給 origins 是舊版畫面送來的，才退回用序號推。
    """
    if origins is not None:
        origin = origins[position] if position < len(origins) else None
        if isinstance(origin, bool) or not isinstance(origin, int):
            return None
        index = origin
    else:
        index = None
        if numbers and position < len(numbers):
            try:
                index = int(numbers[position]) - 1
            except (TypeError, ValueError):
                index = None
        if index is None:
            index = position
    return index if 0 <= index < len(records) else None


# 每一件最多收幾個字。這是**去識別化的上限**，不是取樣比例：
#
#   身分證  5 格（共 10 格）。不記第幾格、整批打亂，5 個沒有順序的數字
#           湊不回號碼 —— 剩下 5 碼扣掉檢查碼還有上萬種組合，再乘上
#           「已知的 5 個各在第幾格」的排法。
#           承辦人 2026-11-04 自己從裁切圖挑格子刪，刪完每件還剩 8 格，
#           有一件 10 格整組都在；8 格配上檢查碼只剩十來種可能。
#           原本訂 3 格；承辦人 2026-11-05 想要全部收錄、打亂順序，討論過
#           「同一個人的十格看筆跡就歸得在一起、檢查碼再篩一次就還原」之後，
#           折衷成 5 格：「就折衷改成五格吧。」**不要再往上調。**
#   門牌數字 2 個。不記是巷、弄、號還是樓，兩個沒有順序的數字配上路名
#           也指不到哪一戶。
#
# 讀錯的一律優先。讀對的只是對照組，一件湊到 ID_CONTROLS_UPTO 張為止，
# 不會把上限填滿 —— 上限放寬是為了多收難字，不是多收讀對的。
ID_CELLS_PER_CASE = 5
ID_CONTROLS_UPTO = 3
HOUSE_DIGITS_PER_CASE = 2


def _take(wrong, right, cap, controls, upto=None):
    """一件裡要收哪幾個：讀錯的先（打亂後取，最多 cap 個），
    controls 開著的話再放讀對的，讓總數湊到 upto（沒給就是 cap）為止。"""
    import random

    wrong = list(wrong)
    random.shuffle(wrong)
    picked = wrong[:cap]
    fill = min(cap, cap if upto is None else upto)
    if controls and len(picked) < fill and right:
        picked += random.sample(list(right), min(fill - len(picked), len(right)))
    return picked


def dump_hard_cells(records, rows, folder, numbers=None, origins=None, controls=False):
    """把「程式讀錯的那幾格」存成一個字一張圖，檔名帶正確答案。

    承辦人 2026-09-22：「診斷的部分你會加一份某字辨識不出來嗎？然後我再
    修正給你之類的，現行狀況不知道你是哪個字看不懂。」

    rows 是**人複核完、真的要輸出的那份值**，所以裡面的身分證是對的。
    拿它跟程式逐格讀到的東西比，不一樣的就是程式讀錯的那一格。

    **只收讀錯的。** 讀對的收進來有兩個壞處：一是開發者要的就是錯的那些，
    二是十格全收等於把整個身分證號搬出去（雖然打亂了，但一天就那幾件，
    憑筆跡拼得回來）。只收錯的那一兩格，缺了其他八格，湊不回任何人的號碼。

    origins 是每一列對應 records 的哪一件（手動輸入的是 None），見 _row_source。

    controls=True 時名額有剩會放幾張**讀對的**（檔名寫「程式讀對」）。
    承辦人 2026-11-04：「現在需要的是把裁切圖產生出來後去識別化，這樣就
    可以直接給你來辨識。」只有讀錯的，量不出改了之後有沒有把原本讀對的
    改壞 —— 要有對照組。讀對的一件最多湊到 ID_CONTROLS_UPTO 張，總數照樣受
    ID_CELLS_PER_CASE 限制。

    回傳 (資料夾, 寫出幾張)。
    """
    import random

    from . import (lexicon as _lexicon, resources as _resources,
                   validate as _validate)

    pairs = []
    for position, row in enumerate(rows or ()):
        index = _row_source(records, position, numbers, origins)
        if index is None:
            continue
        record = records[index]
        truth = (row.get("id_number") or "").strip().upper()
        cells = (record.cells or {}).get("id_number") or []
        texts = (record.cell_text or {}).get("id_number") or []
        if len(truth) != 10 or len(cells) != 10:
            continue
        wrong, right = [], []
        for slot in range(10):
            want = truth[slot]
            if want == "?" or not want.isalnum():
                continue
            cell = cells[slot]
            if cell is None or not getattr(cell, "size", 0):
                continue
            choices, was_read = _validate._cell_options(
                texts[slot] if slot < len(texts) else [], slot)
            if was_read and len(choices) == 1:
                if choices[0] == want:
                    right.append((want, "程式讀對", cell))
                    continue
                saw = "程式讀成%s" % choices[0]
            elif was_read:
                # 幾種讀法各說各話，程式沒辦法決定。這種一樣算失敗，
                # 而且對開發者最有用 —— 看得出模型在哪兩個字之間猶豫。
                saw = "程式分不出是%s" % "".join(choices)
            else:
                saw = "程式沒讀到"
            wrong.append((want, saw, cell))
        pairs.extend(_take(wrong, right, ID_CELLS_PER_CASE, controls, ID_CONTROLS_UPTO))

    # ── 門牌：只收「路名」那一格 ──────────────────────────────────
    #
    # 承辦人 2026-09-22：「門牌要不要也弄個難字回報？我發現門牌讀取
    # 錯誤率還是很高。」要，但**只能收路名那一格**。
    #
    # 路名（「大觀」「鳳鳴」）是公開的街道名稱，單獨一個指不向任何人 ——
    # 一條路上有幾百戶。但門牌號、樓層一旦配上路名就是完整住址了，
    # 那是實實在在的個資。所以號、樓、巷、弄一律不收，一件也只收這一格。
    #
    # 不收號其實也沒損失：門牌號跟身分證是同一個人用同一支筆寫的數字，
    # 上面身分證那一段已經在收了。
    road_pairs = []
    for position, row in enumerate(rows or ()):
        index = _row_source(records, position, numbers, origins)
        if index is None:
            continue
        record = records[index]
        # getattr：這一段是附加功能，遇到形狀不一樣的紀錄就跳過，
        # 不要讓整個匯出掛掉（輸出檔那時候已經產好了）
        road = getattr(record, "road_cell", None)
        if not road:
            continue
        cell, saw = road
        found = _ROAD_HEAD.match((row.get("address") or "").strip())
        if not found:
            continue
        want = _lexicon.stem(found.group(1))
        # 讀到的字裡本來就可能帶著印刷的「路／街」，比對前一起去掉
        # 先換成繁體再比：辨識模型偶爾吐簡體字（「爱」），但門牌那一欄本來就會
        # 照簡繁對照表換回來，輸出是對的 —— 那不算讀錯，收進來只會害開發者白查。
        got = _lexicon.stem(_validate.to_traditional((saw or "").strip()))
        if not want:
            continue
        if got == want:
            if controls:
                road_pairs.append((want, "程式讀對", cell))
            continue
        road_pairs.append((want, "程式讀成%s" % got if got else "程式沒讀到", cell))

    # ── 門牌只框一個大框的表格：只切最左邊那一個字 ─────────────────
    #
    # 承辦人 2026-09-24 那一批 19 件裡有 4 件是同一種錯：鶯歌的「鳳X路」
    # 與「龍X路」三、五、七三組正面相撞，字典幫不上忙，只剩字形。要讓
    # 程式學會分辨就得有標好答案的圖，而那種表格（E 表）的門牌只框一個
    # 大框 —— 整格就是整串住址。
    #
    # 所以整格不收，只切最左邊那一個字。單獨一個街道名稱用字（鳳、龍）
    # 是公開資訊，指不向任何人；門牌號、樓層一個都不會進到圖裡。
    head_pairs = []
    for position, row in enumerate(rows or ()):
        index = _row_source(records, position, numbers, origins)
        if index is None:
            continue
        record = records[index]
        cell = getattr(record, "address_cell", None)
        if not cell:
            continue
        crop, saw = cell
        # 民眾在框裡先寫了「新北市鶯歌區」的話，最左邊那個字就不是路名的
        # 第一個字了 —— 標錯答案的資料比沒有資料還糟，不如不收。
        if _LEAD_IN_BOX.match((saw or "").strip()):
            continue
        found = _ROAD_HEAD.match((row.get("address") or "").strip())
        want = _lexicon.stem(found.group(1)) if found else ""
        read = _ROAD_HEAD.match(_validate.to_traditional((saw or "").strip()))
        got = _lexicon.stem(read.group(1)) if read else ""
        if not want:
            continue
        if len(got) == len(want):
            if got[0] == want[0]:
                if not controls:
                    continue                   # 第一個字讀對了
                note = "程式讀對"
            else:
                note = "程式讀成%s" % got[0]
        elif len(got) == len(want) - 1 and want.endswith(got):
            note = "程式沒讀到"                # 第一個字整個沒讀出來
        else:
            continue                           # 對不上就別亂標
        piece = first_character(crop)
        if piece is None:
            continue
        head_pairs.append((want[0], note, piece))

    # ── 門牌一格一框：巷、弄、號、樓那幾格，拆成一個數字一張 ──────────
    #
    # 承辦人 2026-11-02：「我只要修改門牌的部分，就要把錯誤的字型變成難認字
    # 輸出給你，不然每次手寫的部分都會錯一大堆。」
    #
    # **整格不收，只收拆開的單一個數字。** 整格「38」配上同一件的路名就是
    # 住址；單獨一個「3」跟身分證的一格一樣，指不向任何人。而且跟身分證一樣
    # 只收讀錯的那幾個位數、檔名不帶件號、順序打亂。
    number_pairs = []
    for position, row in enumerate(rows or ()):
        index = _row_source(records, position, numbers, origins)
        if index is None:
            continue
        boxes = getattr(records[index], "address_parts", None) or []
        if not boxes:
            continue
        found = _ADDRESS_PARTS.match((row.get("address") or "").strip())
        if not found:
            continue                   # 人改完的門牌拆不開，就不知道哪一格該是什麼
        wrong_digits, right_digits = [], []
        for crop, saw, suffix in boxes:
            part = _SUFFIX_PART.get(suffix)
            if not part or not found.group(part):
                continue
            want = found.group(part)
            # 讀到的字照門牌那一欄的規則先整理過：全形換半形、像數字的英文字母
            # 換成數字。整理完就對的，輸出本來就是對的，不算讀錯。
            plain = "".join(_validate._LETTER_TO_DIGIT.get(ch, ch)
                            for ch in _validate.to_halfwidth(saw or ""))
            got = "".join(ch for ch in plain if ch.isdigit())
            if part == "floor":
                # 輸出的樓層是國字，手寫多半是阿拉伯數字。只有程式真的讀到
                # 阿拉伯數字時才照數字拆；寫國字的話個數對不起來，不收。
                if not got:
                    continue
                want = _floor_digits(want)
                if found.group("floorsub"):
                    continue           # 「十七樓之3」的之3 寫在哪一格不一定，不收
            if not want.isdigit():
                continue               # 「14之6」這種：之 寫成什麼樣子不一定，不收
            if got == want and not controls:
                continue               # 讀對了
            glyphs = split_glyphs(crop, len(want))
            if glyphs is None:
                continue               # 拆不準就不收
            if len(got) == len(want):
                for i in range(len(want)):
                    if got[i] != want[i]:
                        wrong_digits.append((want[i], "程式讀成%s" % got[i], glyphs[i]))
                    else:
                        right_digits.append((want[i], "程式讀對", glyphs[i]))
            else:
                for i in range(len(want)):
                    wrong_digits.append((want[i], "程式沒讀到" if not got else "整格沒讀對",
                                         glyphs[i]))
        number_pairs.extend(_take(wrong_digits, right_digits, HOUSE_DIGITS_PER_CASE, controls))

    # ── 行政區：整格收（「三峽區」「鶯歌區」是公開的地名）────────────
    #
    # 承辦人 2026-10-21：「第11件 行政區辨識度很差」。行政區那一格只寫區名，
    # 指不向任何人。但框畫得大、民眾又寫超出去的話，格子裡可能夾到別的東西，
    # 所以三條都要成立才收：格子是一行的形狀、人改完的值是「○○區」、
    # 程式讀到的字裡沒有數字也沒有路街巷弄號樓。
    district_pairs = []
    for position, row in enumerate(rows or ()):
        index = _row_source(records, position, numbers, origins)
        if index is None:
            continue
        cell = getattr(records[index], "district_cell", None)
        if not cell:
            continue
        crop, saw = cell
        want = (row.get("district") or "").strip()
        if not (2 <= len(want) <= 4 and want.endswith("區")):
            continue
        height, width = crop.shape[:2]
        if height <= 0 or width > height * 8:
            continue
        got = _validate.to_traditional((saw or "").strip())
        if any(ch.isdigit() or ch in "路街巷弄號樓段号楼" for ch in got):
            continue
        if got == want or (got.endswith(want) and len(got) - len(want) <= 3
                           and got[:-len(want)] in ("新北市", "北市", "市")):
            if controls:
                district_pairs.append((want, "程式讀對", crop))
            continue
        if not got:
            note = "程式沒讀到"
        elif len(got) <= 4:
            note = "程式讀成%s" % got
        else:
            note = "整格沒讀對"
        district_pairs.append((want, note, crop))

    if not (pairs or road_pairs or head_pairs or number_pairs or district_pairs):
        return None, 0

    # 打亂 —— 檔名裡沒有件號也沒有格號，順序是最後一個可能洩漏關聯的東西
    random.shuffle(pairs)
    random.shuffle(road_pairs)
    random.shuffle(head_pairs)
    random.shuffle(number_pairs)
    random.shuffle(district_pairs)

    target = os.path.join(folder, HARD_CELLS)
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "讀我.txt"), "w", encoding="utf-8") as handle:
        handle.write(_HARD_README)

    written = 0
    for want, saw, cell in pairs:
        kind = "字母" if want.isalpha() else "數字"
        name = "%s_真值%s_%s_%06x.png" % (kind, want, saw,
                                          random.getrandbits(24))
        if _resources.imwrite(os.path.join(target, name), cell):
            written += 1
    for want, saw, cell in road_pairs:
        name = "路名_真值%s_%s_%06x.png" % (want, saw, random.getrandbits(24))
        if safe_hard_name(name) and _resources.imwrite(
                os.path.join(target, name), cell):
            written += 1
    for want, saw, cell in head_pairs:
        name = "路名首字_真值%s_%s_%06x.png" % (want, saw, random.getrandbits(24))
        if safe_hard_name(name) and _resources.imwrite(
                os.path.join(target, name), cell):
            written += 1
    for want, saw, cell in number_pairs:
        # 最後再擋一次：真值只能是一個數字、圖只能是一個字的寬度。
        # 任何一條不對就不寫 —— 這裡寫出去的是門牌的一部分。
        height, width = cell.shape[:2]
        if len(want) != 1 or not want.isdigit() or width > height * 1.6:
            continue
        name = "門牌數字_真值%s_%s_%06x.png" % (want, saw, random.getrandbits(24))
        if safe_hard_name(name) and _resources.imwrite(
                os.path.join(target, name), cell):
            written += 1
    for want, saw, cell in district_pairs:
        name = "行政區_真值%s_%s_%06x.png" % (want, saw, random.getrandbits(24))
        if safe_hard_name(name) and _resources.imwrite(
                os.path.join(target, name), cell):
            written += 1
    return target, written


def _stack(crops):
    """把同一欄的幾個格子疊成一張圖，複核時才看得到完整的來源。"""
    import numpy as np

    # 灰階跟彩色混在一起 vstack 會炸。現在的流程不會混，但複核畫面
    # 少一張對照圖是小事，整批複核在這裡中斷是大事。
    if len({c.ndim for c in crops}) > 1:
        crops = [c if c.ndim == 3 else cv2.cvtColor(c, cv2.COLOR_GRAY2BGR) for c in crops]

    width = max(c.shape[1] for c in crops)
    padded = []
    for crop in crops:
        if crop.shape[1] < width:
            pad = np.full((crop.shape[0], width - crop.shape[1]) + crop.shape[2:],
                          255, crop.dtype)
            crop = np.hstack([crop, pad])
        padded.append(crop)
    return np.vstack(padded)


def summarise(records, unresolved, unknown=()):
    """給人看的統計。"""
    ok = sum(1 for r in records if r.status == OK)
    lines = [
        "共 %d 件，%d 件通過、%d 件需要人工確認" % (len(records), ok, len(records) - ok),
    ]
    if unresolved:
        lines.append("另有 %d 件不完整（一件固定兩頁，最常見是有一面漏掃）"
                     % len(unresolved))
        for document in unresolved:
            why = getattr(document, "problem", None)
            if why:
                lines.append("    %s" % why)
    if unknown:
        lines.append("有 %d 件的正面認不出是哪一種表格，是靠背面判斷的 ——"
                     " 件數沒少，但欄位有沒有對到請自己看一眼" % len(unknown))

    counts = {}
    for record in records:
        for column, note in record.flagged().items():
            counts[column] = counts.get(column, 0) + 1
    if counts:
        lines.append("各欄位被標記的次數：")
        for column, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            lines.append("    %-14s %d 次" % (fieldmod.COLUMNS.get(column, column), count))
    return "\n".join(lines)

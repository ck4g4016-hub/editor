# -*- coding: utf-8 -*-
"""從掃描 PDF 一路做到可以輸出的資料列。

    分類每一頁 → 切成一件一件 → 減掉印刷版面 → 依樣板裁出欄位
    → 辨識 → 正規化與驗證 → 產生資料列

每一列都帶著它的來源（哪個檔、第幾頁）與每個欄位的信心值和驗證結果，
複核介面靠這些資訊決定要把哪些格子標紅給人看。
"""

import os
import time

import cv2
import numpy as np

from . import baseimage, diagnose, fields as fieldmod, layout, lexicon, recognise, render, resources, validate

# 這三欄錯了，RPA 會拿著錯的資料去查別人的房子，而且從輸出的表格上看不出來。
# 驗證不通過就一定要人工確認，不管信心值多高。
CRITICAL = ("id_number", "address", "doc_number")

# 信心低於這個值就算沒把握，即使驗證通過也要人工看一眼
LOW_CONFIDENCE = 0.80

# 姓名沒有字典也沒有格式規則，錯了驗不出來。但它不會進 RPA，
# 只是承辦人用來對照「這件是不是我要的那件」，所以照樣輸出、
# 在複核介面標成僅供參考，不因為它把整件擋下來。
ADVISORY = ("name",)

OK = "ok"
REVIEW = "review"


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

        # 認不出來的頁會被 split_documents 併進前一件 —— 對「空白背面」來說
        # 這是對的，對「首頁沒認出來的下一件」來說就是把一整件吃掉：
        # 十五件掃進來變成十四筆，而且畫面上完全看不出來少了誰。
        # 所以單獨列出來給人看。
        self.unknown = [{"file": journal.file_id(p.source), "page": p.index + 1,
                         "inliers": p.inliers, "margin": p.margin}
                        for p in pages if p.role == layout.UNKNOWN]

        documents = layout.split_documents(pages)
        journal.documents = len(documents)

        clock = time.time()
        records, unresolved = [], []
        for document in documents:
            if not document.complete:
                unresolved.append(document)
                journal.unresolved.append({
                    "file": journal.file_id(document.pages[0].source),
                    "pages": "、".join(str(p.index + 1) for p in document.pages),
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
        for column, value in record.values.items():
            definition = definitions.get(column)
            how = record.how.get(column) or {}
            entries.append({
                "column": column,
                "kind": definition.kind if definition else "?",
                "confidence": record.confidence.get(column, 0.0),
                "raw": diagnose.mask(record.raw.get(column, "")),
                "value": diagnose.mask(value),
                "problem": diagnose.mask_problem(record.problems.get(column, "")),
                "cells": how.get("格數", 0),
                "readings": {name: diagnose.mask(how.get(name, ""))
                             for name in ("整行", "照格子", "逐格", "逐空白", "採用")
                             if name in how},
            })
        return {
            "index": index,
            "code": record.code,
            "file": self.journal.file_id(record.source),
            "page": record.page + 1,
            "fields": entries,
        }

    def sheet_of(self, code, page, role):
        """把一頁算成「減掉印刷版面之後」的影像。

        回傳 (原圖, 減掉版面的影像, 底圖)。

        底圖對得上就相減，只留手寫的內容；對不上就退回灰階原圖 ——
        減不掉頂多辨識差一點，硬減會把整頁弄糊。

        第三個回傳值是**對得上時**的底圖。欄位裡印好的方格線就在它上面，
        辨識時要靠它來切格子。減不掉的時候回 None —— 那時候的影像還在
        掃描件自己的座標系，底圖的格線位置對不上，拿來切只會切錯。
        """
        image = render.rotate(
            render.render(page.source, page.index, dpi=render.FULL_DPI, gray=False),
            page.rotation)
        base = self.base_of(code, role)
        if base is None:
            return image, baseimage.as_gray(image), None
        try:
            sheet = baseimage.subtract(base, image)
        except ValueError:
            return image, baseimage.as_gray(image), None

        # 再靠顏色抽一次筆跡，兩條路取聯集（任一條認出的墨都留下）。
        #
        # 粉紅紙、紅色印刷、黑色格線的表格上，藍色原子筆用 B-R 一刀就切得乾淨，
        # 而且不受對位誤差影響；減版面則會在手寫壓到格線的地方把筆畫削斷。
        # 實測 F 表身分證欄：減版面「2」只剩一個小點，靠顏色十個字都完整。
        # 黑筆寫的抽不出來（跟格線同色），那時候顏色這條路是空的，
        # 聯集就等於只有減版面 —— 不會比現在差。
        moved, _inliers = baseimage.to_base(base, image)
        if moved is not None:
            colour = baseimage.ink_by_colour(moved)
            if colour is not None and colour.shape == sheet.shape:
                sheet = np.minimum(sheet, colour)
        return image, sheet, base

    def read_document(self, document, keep_crops=False):
        """讀一件申請案，把欄位辨識出來。

        正面一定要讀。背面只有在樣板真的有定義背面欄位時才去算 ——
        多算一頁 300dpi 影像要一秒多，沒欄位的話白算。
        """
        front = document.pages[0]
        definitions = self.fields_of(front.code)
        if not definitions:
            return None

        record = Record(front.code, front.source, front.index)

        # 每一面各自處理：算影像、裁欄位、辨識。
        # 行政區要先讀 —— 地址的路名字典是分區的，三峽有「仁愛街」、
        # 鶯歌有「仁愛路」，不知道哪一區就選不出來，所以正面先做。
        pages = {"front": front}
        if any(d.page == "back" for d in definitions):
            pages["back"] = next(
                (p for p in document.pages[1:] if p.role == layout.BACK), None)

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

            _, sheet, base = self.sheet_of(front.code, page, role)
            ordered = sorted(wanted, key=lambda d: 0 if d.kind == "district" else 1)
            for definition in ordered:
                self._read_field(record, sheet, definition, keep_crops, base)

        for column in CRITICAL:
            if not record.values.get(column):
                record.problems.setdefault(column, "沒有讀到內容")
        return record

    def _read_field(self, record, sheet, definition, keep_crops, base=None):
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
        all_cells = []
        for box, suffix in definition.segments():
            crop = recognise.crop_field(sheet, box)
            text, confidence = recognise.read(crop)
            if crop is not None and crop.size:
                crops.append(crop)
            text = (text or "").strip()
            if text:
                pieces.append(text + suffix)
                scores.append(confidence)
            elif not suffix:
                # 沒有後綴的空格子代表真的沒讀到，信心要算進去；
                # 有後綴的空格子（例如沒有「段」）是正常的，整段跳過。
                scores.append(confidence)

            # 照原稿印好的格線再讀一次。格線在底圖上，減掉版面之後的影像
            # 只剩手寫，所以要拿底圖去找線、拿減完的影像去切字。
            printed = recognise.crop_field(base, box) if base is not None else crop
            cells = recognise.grid_cells(printed, crop)
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
        how = {"格數": cell_count, "整行": raw, "照格子": grid_raw if any_grid else ""}
        record.how[definition.column] = how

        extra = {}
        if definition.kind == "district":
            extra["known"] = self.districts
        elif definition.kind == "section":
            extra["known"] = lexicon.for_district(
                self.sections, record.values.get("district"))
        elif definition.kind == "address":
            extra["roads"] = lexicon.for_district(
                self.roads, record.values.get("district"))

        if definition.kind == "id_number":
            # 身分證有檢查碼 —— 這讓我們可以**驗證**而不是猜，別的欄位沒這優勢。
            #
            # 原稿上剛好切出十格的時候，走最強的那條路：每一格給兩種讀法
            # （偵測+辨識、只做辨識，它們失手的地方不一樣），再把十格的可能
            # 逐一組合，看哪一種通過檢查碼。讀錯一格時正確答案不在候選裡，
            # 沒有組合會通過，於是標起來 —— 不會生出一個通過檢查碼但錯的號碼。
            solved = solved_problem = None
            if len(all_cells) == 10:
                per_cell = [recognise.read_char(cell) for cell in all_cells]
                record.cells[definition.column] = list(all_cells)
                how["逐格"] = "|".join(c[0] if c else "?" for c in per_cell)
                solved, solved_problem = validate.solve_id(per_cell)

            if solved:
                value, problem = solved, solved_problem
                raw = how["逐格"].replace("|", "")
                confidence = grid_confidence or confidence
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
        lines.append("另有 %d 組頁面切不出完整的一件，需要人工分頁" % len(unresolved))
    if unknown:
        lines.append("有 %d 頁認不出是哪一種表格，已併進前一件 ——"
                     " 如果那是新的一件，這一批就少了一筆" % len(unknown))

    counts = {}
    for record in records:
        for column, note in record.flagged().items():
            counts[column] = counts.get(column, 0) + 1
    if counts:
        lines.append("各欄位被標記的次數：")
        for column, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            lines.append("    %-14s %d 次" % (fieldmod.COLUMNS.get(column, column), count))
    return "\n".join(lines)

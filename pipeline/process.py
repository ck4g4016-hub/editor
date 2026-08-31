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

from . import baseimage, diagnose, fields as fieldmod, layout, lexicon, recognise, render, validate

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
        self._bases = {}
        self._fields = {}
        # 每跑一次 run() 就換一本新的。診斷報告完全靠它。
        self.journal = diagnose.Journal()

    def base_of(self, code):
        if code not in self._bases:
            path = os.path.join(self.store, code, "base.png")
            self._bases[code] = cv2.imread(path, cv2.IMREAD_COLOR) if os.path.isfile(path) else None
        return self._bases[code]

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
            entries.append({
                "column": column,
                "kind": definition.kind if definition else "?",
                "confidence": record.confidence.get(column, 0.0),
                "raw": diagnose.mask(record.raw.get(column, "")),
                "value": diagnose.mask(value),
                "problem": diagnose.mask_problem(record.problems.get(column, "")),
            })
        return {
            "index": index,
            "code": record.code,
            "file": self.journal.file_id(record.source),
            "page": record.page + 1,
            "fields": entries,
        }

    def read_document(self, document, keep_crops=False):
        """讀一件申請案的正面，把欄位辨識出來。"""
        front = document.pages[0]
        definitions = self.fields_of(front.code)
        if not definitions:
            return None

        page = render.rotate(
            render.render(front.source, front.index, dpi=render.FULL_DPI, gray=False),
            front.rotation)

        base = self.base_of(front.code)
        if base is not None:
            try:
                sheet = baseimage.subtract(base, page)
            except ValueError:
                sheet = baseimage.as_gray(page)
        else:
            sheet = baseimage.as_gray(page)

        record = Record(front.code, front.source, front.index)

        # 行政區要先讀 —— 地址的路名字典是分區的，
        # 三峽有「仁愛街」、鶯歌有「仁愛路」，不知道哪一區就選不出來。
        ordered = sorted((d for d in definitions if d.page == "front"),
                         key=lambda d: 0 if d.kind == "district" else 1)

        for definition in ordered:
            crop = recognise.crop_field(sheet, definition.box)
            if keep_crops and crop is not None and crop.size:
                ok, buffer = cv2.imencode(".png", crop)
                if ok:
                    record.crops[definition.column] = buffer.tobytes()
            raw, confidence = recognise.read(crop)
            extra = {}
            if definition.kind == "district":
                extra["known"] = self.districts
            elif definition.kind == "address":
                extra["roads"] = lexicon.for_district(
                    self.roads, record.values.get("district"))
            value, problem = validate.check(definition.kind, raw, **extra)

            record.raw[definition.column] = raw
            record.values[definition.column] = value
            record.confidence[definition.column] = confidence
            if problem:
                record.problems[definition.column] = problem

        for column in CRITICAL:
            if not record.values.get(column):
                record.problems.setdefault(column, "沒有讀到內容")
        return record


def summarise(records, unresolved):
    """給人看的統計。"""
    ok = sum(1 for r in records if r.status == OK)
    lines = [
        "共 %d 件，%d 件通過、%d 件需要人工確認" % (len(records), ok, len(records) - ok),
    ]
    if unresolved:
        lines.append("另有 %d 組頁面切不出完整的一件，需要人工分頁" % len(unresolved))

    counts = {}
    for record in records:
        for column, note in record.flagged().items():
            counts[column] = counts.get(column, 0) + 1
    if counts:
        lines.append("各欄位被標記的次數：")
        for column, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            lines.append("    %-14s %d 次" % (fieldmod.COLUMNS.get(column, column), count))
    return "\n".join(lines)

# -*- coding: utf-8 -*-
"""從掃描 PDF 一路做到可以輸出的資料列。

    分類每一頁 → 切成一件一件 → 減掉印刷版面 → 依樣板裁出欄位
    → 辨識 → 正規化與驗證 → 產生資料列

每一列都帶著它的來源（哪個檔、第幾頁）與每個欄位的信心值和驗證結果，
複核介面靠這些資訊決定要把哪些格子標紅給人看。
"""

import os

import cv2

from . import baseimage, fields as fieldmod, layout, recognise, render, validate

# 這三欄錯了，RPA 會拿著錯的資料去查別人的房子，而且從輸出的表格上看不出來。
# 驗證不通過就一定要人工確認，不管信心值多高。
CRITICAL = ("id_number", "address", "doc_number")

# 信心低於這個值就算沒把握，即使驗證通過也要人工看一眼
LOW_CONFIDENCE = 0.80

# 姓名沒有字典也沒有格式規則，錯了驗不出來 —— 永遠人工確認
ALWAYS_REVIEW = ("name",)

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

    @property
    def status(self):
        if self.problems:
            return REVIEW
        if any(column in self.values for column in ALWAYS_REVIEW):
            return REVIEW
        return OK

    def flagged(self):
        """需要人工看的欄位，以及原因。"""
        notes = dict(self.problems)
        for column in ALWAYS_REVIEW:
            if column in self.values:
                notes.setdefault(column, "姓名一律人工確認")
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
        self._bases = {}
        self._fields = {}

    def base_of(self, code):
        if code not in self._bases:
            path = os.path.join(self.store, code, "base.png")
            self._bases[code] = cv2.imread(path, cv2.IMREAD_COLOR) if os.path.isfile(path) else None
        return self._bases[code]

    def fields_of(self, code):
        if code not in self._fields:
            self._fields[code] = fieldmod.load(self.store, code)
        return self._fields[code]

    def run(self, paths, progress=None):
        """處理一批 PDF，回傳 (資料列, 需要人工分頁的頁面)。"""
        pages = layout.classify_pages(paths, self.templates)
        documents = layout.split_documents(pages)

        records, unresolved = [], []
        for document in documents:
            if not document.complete:
                unresolved.append(document)
                continue
            record = self.read_document(document)
            if record is not None:
                records.append(record)
                if progress:
                    progress(record)
        return records, unresolved

    def read_document(self, document):
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
        for definition in definitions:
            if definition.page != "front":
                continue
            crop = recognise.crop_field(sheet, definition.box)
            raw, confidence = recognise.read(crop)
            extra = {"known": self.districts} if definition.kind == "district" else {}
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

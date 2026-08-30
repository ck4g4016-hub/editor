# -*- coding: utf-8 -*-
"""產生兩個輸出檔。規格依據見 docs/output-spec.md。

外網給 RPA 讀，用門牌到地政系統查地建號、下載謄本。
內網是戶役政全戶戶籍資料查調的匯入檔。

兩邊的格式限制不一樣，不能共用一個檔：
外網腳本的檔案選取寫死 `*.xlsx`，內網系統只收 xls/ods/csv 且不收 xlsx。
"""

import csv
import datetime
import os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

# 外網欄位。E~H 由 RPA 回寫，我們一定要留空。
# 尤其是 H（備註）—— 腳本每一列開頭會讀它，只要有內容整列就直接跳過，
# 不查、不回寫，畫面上也不會有任何提示。
OUTER_HEADERS = ["行政區", "門牌", "申請案號或事由", "身分證字號",
                 "段名(或代碼)", "地號", "建號", "備註", "姓名"]

# RPA 只讀到 H，排序也只到 D，所以新增的姓名放 I 欄不會影響它
OUTER_RPA_COLUMNS = 8

INNER_HEADERS = ["序號", "行政區", "所有權人IDN", "完整地址", "姓名"]

_HEADER_FILL = PatternFill("solid", fgColor="EFE6D0")
_RESERVED_FILL = PatternFill("solid", fgColor="F5F5F5")


def roc_date(when=None):
    """民國年月日，例如 1150827。"""
    when = when or datetime.date.today()
    return "%d%02d%02d" % (when.year - 1911, when.month, when.day)


def outer_path(folder, when=None):
    return os.path.join(folder, "RPA-查調謄本清冊_%s.xlsx" % roc_date(when))


def inner_path(folder, serial=1, when=None):
    """內網檔名只能用英數 —— 系統的限制。"""
    return os.path.join(folder, "HH%s_%02d.csv" % (roc_date(when), serial))


def write_outer(records, path):
    """外網 RPA 查調謄本清冊（xlsx）。"""
    book = Workbook()
    sheet = book.active
    sheet.title = "查調清冊"

    sheet.append(OUTER_HEADERS)
    for index, cell in enumerate(sheet[1], start=1):
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL if index <= OUTER_RPA_COLUMNS else _RESERVED_FILL
        cell.alignment = Alignment(horizontal="center")

    for record in records:
        sheet.append([
            record.get("district", ""),
            record.get("address", ""),
            record.get("doc_number", ""),
            record.get("id_number", ""),
            "", "", "", "",          # 段名、地號、建號、備註 —— 留給 RPA
            record.get("name", ""),
        ])

    for column, width in zip("ABCDEFGHI", (10, 34, 16, 14, 14, 12, 12, 24, 12)):
        sheet.column_dimensions[column].width = width

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    book.save(path)
    return path


def write_inner(records, path):
    """內網戶役政匯入檔（csv、UTF-8）。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # 用 utf-8-sig，Excel 直接開才不會把中文顯示成亂碼
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(INNER_HEADERS)
        for serial, record in enumerate(records, start=1):
            writer.writerow([
                serial,
                record.get("district", ""),
                record.get("id_number", ""),
                record.get("address", ""),
                record.get("name", ""),
            ])
    return path


# 內網系統一次匯入的上限
INNER_BATCH_LIMIT = 750


def write_all(records, folder, when=None):
    """兩個檔一起產。內網超過匯入上限就自動分批。"""
    written = [write_outer(records, outer_path(folder, when))]
    batches = [records[i:i + INNER_BATCH_LIMIT]
               for i in range(0, max(len(records), 1), INNER_BATCH_LIMIT)] or [[]]
    for serial, batch in enumerate(batches, start=1):
        written.append(write_inner(batch, inner_path(folder, serial, when)))
    return written

# -*- coding: utf-8 -*-
"""產生兩個輸出檔。規格依據見 docs/output-spec.md。

外網給 RPA 讀，用門牌到地政系統查地建號、下載謄本。
內網這一份**不是**戶政系統的上傳檔，是餵給內網腳本 2 的中繼檔 ——
腳本 2 讀它，自己轉出 upload1.xls／upload2.xls 再上傳。這點一開始弄錯過。

內網腳本 2（產製地址清冊並上傳至戶政系統）的兩行決定了格式：

    If LCase(fso.GetExtensionName(objFile.Name)) = "xlsx" Then   '第 234 行
    District = firstRow.RawData("行政區")                          '第 126 行

第一行：它在資料夾裡**只找 .xlsx**，csv、xls 一律看不到，找不到就跳
「資料夾內找不到 Excel 檔案」然後中止。所以這一份必須是 xlsx。

第二行：欄位是**用名稱取值**不是用位置，所以標題列一定要有、名稱要一字不差，
但多給一個「姓名」欄不會干擾它（腳本 2 不會去讀那一欄）。

還有一個坑：`GetTargetExcel` 撿到第一個 xlsx 就 `Exit Function`，
所以那個資料夾裡**只能有這一個 xlsx**。外網那份也是 xlsx，兩個混在一起
會撿到哪一個看檔案系統的順序，不是檔名順序 —— 所以兩份不要放同一個資料夾。
"""

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

# 內網中繼檔的欄位。前四個是腳本 0 原本產的（它的第 56~59 行），
# 名稱必須一字不差，腳本 2 靠名稱取值。姓名是我們多加的，只給人核對用。
#
# **標題維持「序號」，不要改成「公文文號」。** 腳本 2 是用名稱取值的
# （它的第 126 行 `District = firstRow.RawData("行政區")`），改掉名稱等於
# 賭它沒有讀這一欄 —— 賭輸的話腳本會拿到空值，而且不會有任何錯誤訊息。
# 裡面裝什麼是我們決定的，欄位叫什麼不是。
INNER_HEADERS = ["序號", "行政區", "所有權人IDN", "完整地址", "姓名"]

# 「完整地址」照腳本 0 的寫法要含縣市與行政區 —— 它的第 130 行
# `district = Mid(address, 4, 3)` 是從地址的第 4~6 個字取出行政區，
# 反推回去第 1~3 個字就是縣市。外網那份則相反，只留路街門牌。
INNER_CITY = "新北市"

# ── 戶政系統直接吃的地址清冊（YHQ101_addr）─────────────────────────
#
# 規格是從承辦人翻拍的 YHQ101_addr_Sample.xls 逆向出來的，**還沒實測匯入過**。
# 每一條都標了是「看得到的事實」還是「猜的」，因為猜錯的代價是整批匯入失敗。
#
#   事實  沒有標題列，第 1 列就是資料
#   事實  A=案號、B=縣市、C=鄉鎮市區、D=空、E=空、F=路以下的門牌
#   事實  F 欄的巷弄號用**全形**阿拉伯數字（１巷２弄３號），段與樓層用中文數字
#         （三段、六樓、地下二層）。A 欄的數字則是半形。
#   事實  舊制省轄縣寫成「臺灣省苗栗縣」，直轄市就寫「臺北市」
#   事實  .xls（相容模式），工作表叫 Sheet1，儲存格格式是文字
#   猜的  D、E 是村里與鄰 —— 範例四列都空著，所以我們也留空
#   猜的  檔名規則。範例叫 YHQ101_addr_Sample.xls，我們照 YHQ101_addr_ 開頭
#   猜的  一次匯入的筆數上限，先沿用中繼檔那邊的 750
HOUSEHOLD_CITY = "新北市"
HOUSEHOLD_SHEET = "Sheet1"

# 全形阿拉伯數字。範例檔的門牌欄是全形（１巷２弄３號），A 欄的案號是半形 ——
# 這不是排版習慣，是同一個檔裡兩種寫法並存，所以應該是規格。
_FULLWIDTH_DIGITS = {chr(0x30 + i): chr(0xFF10 + i) for i in range(10)}


def to_fullwidth(text):
    """把半形阿拉伯數字換成全形。只換數字，其餘原樣。"""
    return "".join(_FULLWIDTH_DIGITS.get(ch, ch) for ch in text or "")

_HEADER_FILL = PatternFill("solid", fgColor="EFE6D0")
_RESERVED_FILL = PatternFill("solid", fgColor="F5F5F5")


def roc_date(when=None):
    """民國年月日，例如 1150827。"""
    when = when or datetime.date.today()
    return "%d%02d%02d" % (when.year - 1911, when.month, when.day)


def outer_path(folder, when=None):
    return os.path.join(folder, "RPA-查調謄本清冊_%s.xlsx" % roc_date(when))


def inner_path(folder, serial=1, when=None):
    r"""內網檔名只能用英數 —— 系統的限制。

    腳本 2 的 `GetTargetExcel` 會用 `^(\d+)` 抓檔名開頭的數字當案號，
    要 9 位以上才算數。我們的檔名開頭是 HH，抓不到，案號會是空字串 ——
    腳本 0 自己產的「跨機關通報_….xlsx」開頭是中文，一樣抓不到，
    所以空的案號本來就是正常情況，不用為了這個去遷就檔名。
    """
    return os.path.join(folder, "HH%s_%02d.xlsx" % (roc_date(when), serial))


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
            _text(record, "district"),
            _text(record, "address"),
            _text(record, "doc_number"),
            _text(record, "id_number"),
            "", "", "", "",          # 段名、地號、建號、備註 —— 留給 RPA
            _text(record, "name"),
        ])

    for column, width in zip("ABCDEFGHI", (10, 34, 16, 14, 14, 12, 12, 24, 12)):
        sheet.column_dimensions[column].width = width

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    book.save(path)
    return path


def _text(record, key):
    """取值。缺鍵或值是 None 都當空字串 —— 輸出這一步炸掉，
    等於整批複核完的成果全沒了，不值得為了型別漂亮冒這個險。"""
    value = record.get(key)
    return "" if value is None else str(value)


def inner_address(record):
    """內網要的「完整地址」：縣市 + 行政區 + 門牌。

    我們平常把行政區跟門牌分開存，外網那份也要分開；
    但腳本 0 產的中繼檔是把三段黏成一串的，腳本 2 沿用那個寫法。
    """
    return INNER_CITY + _text(record, "district") + _text(record, "address")


def write_inner(records, path):
    """內網中繼檔（xlsx）—— 給腳本 2 讀，不是上傳檔。"""
    book = Workbook()
    sheet = book.active
    sheet.title = "工作表1"

    sheet.append(INNER_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(horizontal="center")

    for record in records:
        sheet.append([
            # A 欄放公文文號，不是流水號。
            #
            # 腳本 0 的這一欄裝的本來就是**案件編號**（它的第 123 行
            # `caseNum = "'" & Mid(...)`，前面還黏一個單引號逼 Excel 當文字），
            # 只是我們的來源是紙本申請書、沒有那個編號，才先填流水號頂著。
            # 現在公文文號整頁找得到了，就填它 —— 那是這一件在機關裡的身分。
            #
            # 讀不到的時候留空，**不要退回流水號**：同一欄混兩種東西，
            # 從 Excel 上完全分不出哪一格是文號、哪一格只是第幾列。
            # 公文文號是必要欄位，讀不到在複核畫面上就會被標記。
            _text(record, "doc_number"),
            _text(record, "district"),
            _text(record, "id_number"),
            inner_address(record),
            _text(record, "name"),
        ])

    for column, width in zip("ABCDE", (14, 10, 14, 40, 12)):
        sheet.column_dimensions[column].width = width

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    book.save(path)
    return path


def household_path(folder, serial=1, when=None):
    return os.path.join(folder, "YHQ101_addr_%s_%02d.xls" % (roc_date(when), serial))


def write_household(records, path):
    """戶政系統的地址清冊（.xls）—— 給人直接匯入，不經過腳本 2。

    **這一份還沒實測匯入過。** 它跟中繼檔並存，不是取代 —— 先拿它去試，
    確認戶政系統吃得下去，再決定要不要停掉中繼檔那條路。

    為什麼值得直接產：我們手上的縣市、行政區、門牌**本來就是分開的**，
    而這個格式要的也正好是分開的三欄。中繼檔卻是把三段黏成一串，
    腳本 2 再用 `Mid(address, 4, 3)` 從第 4~6 個字把行政區切回來 ——
    黏起來再切開，中間那一刀只要遇到「臺灣省苗栗縣」這種長度不同的寫法就會切錯。
    直接產等於把這一來一回省掉。

    副檔名是 .xls 這件事順便解決了一個衝突：腳本 2 的 `GetTargetExcel` 只找
    .xlsx（它的第 234 行 `GetExtensionName(...) = "xlsx"`），撿到第一個就
    `Exit Function`。這一份是 .xls，所以跟中繼檔放在同一個資料夾也不會被它撿走。
    """
    import xlwt

    book = xlwt.Workbook(encoding="utf-8")
    sheet = book.add_sheet(HOUSEHOLD_SHEET)
    # 儲存格格式設成文字。案號是身分不是數量，門牌裡的全形數字也不能被
    # Excel 當成數字處理 —— 一被當成數字，全形就會被吃掉。
    style = xlwt.easyxf(num_format_str="@")

    for row, record in enumerate(records):
        for column, value in enumerate((
                _text(record, "doc_number"),        # A 案號
                HOUSEHOLD_CITY,                     # B 縣市
                _text(record, "district"),          # C 鄉鎮市區
                "",                                 # D 村里（範例是空的）
                "",                                 # E 鄰（範例是空的）
                to_fullwidth(_text(record, "address")))):   # F 路以下的門牌
            sheet.write(row, column, value, style)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    book.save(path)
    return path


# 內網系統一次匯入的上限
INNER_BATCH_LIMIT = 750


def write_all(records, folder, when=None):
    """三個檔一起產。超過匯入上限就自動分批。

    外網    給 RPA 查調謄本
    中繼檔  給內網腳本 2（現行、已經在跑的那條路）
    戶政檔  直接給戶政系統匯入（**還沒實測過**，先並行，不取代中繼檔）

    並行而不取代是刻意的：中繼檔那條路已經在跑，能用就別斷。新的那份猜錯了
    也只是多一個沒人用的檔，損失是零；反過來換掉現行的那份，猜錯就是整批停擺。
    """
    written = [write_outer(records, outer_path(folder, when))]
    batches = [records[i:i + INNER_BATCH_LIMIT]
               for i in range(0, max(len(records), 1), INNER_BATCH_LIMIT)] or [[]]
    for serial, batch in enumerate(batches, start=1):
        written.append(write_inner(batch, inner_path(folder, serial, when)))
        written.append(write_household(batch, household_path(folder, serial, when)))
    return written

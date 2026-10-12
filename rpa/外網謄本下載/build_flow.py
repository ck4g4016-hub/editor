# -*- coding: utf-8 -*-
"""把承辦人 2026-10-12 傳來的外網流程，改成「照送件時間對回每一列」再命名。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, HERE)
from padjs import escape  # noqa: E402

SRC, DST = sys.argv[1], sys.argv[2]
raw = open(SRC, "rb").read().decode("utf-8")
assert "\r\n" in raw
L = raw.split("\r\n")


def line(n):
    return L[n - 1]


def expect(n, piece):
    assert piece in line(n), "第 %d 行應該有「%s」，實際是：%s" % (n, piece, line(n)[:120])


def js_block(path, result, indent):
    js = open(path, encoding="utf-8").read().rstrip("\n")
    assert "'''" not in js and "%%" not in js
    body = escape(js).split("\n")
    first = indent + "WebAutomation.ExecuteJavascript BrowserInstance: Browser Javascript: $'''" + body[0]
    last = body[-1] + "''' Result=> " + result
    return [first] + body[1:-1] + [last]


# ── 錨點：每一處要改的地方先確認內容對得上，對不上就停 ──
expect(41, "$'''備註''' Column: $'''H''' Row: 1")
expect(45, "SortRangeCoordinates")
expect(45, "EndColumn: $'''D'''")
expect(45, "[$'''A''', $'''asc''']")
expect(414, "送 出")
expect(415, "WAIT 2")
expect(498, "# ===計算要產製的筆數===")
expect(513, "DateTime.GetCurrentDateTime.Local")
expect(514, "CustomFormat: $'''yyyyMMdd''' Result=> FormattedDateTime")
expect(516, "LOOP LoopIndex_download FROM 1 TO CountForLOOP")
expect(549, "WAIT 2")
expect(550, "AttachToForegroundChrome")
expect(581, "WAIT 2")
expect(582, "CustomFormat: $'''yyyy''' Result=> FormattedYEAR")
expect(585, "Clipboard.SetText Text:")
expect(586, "Clipboard.GetText Text=> ClipboardText_fileName")
expect(595, "WAIT 2")
expect(596, "CloseByInstanceOrHandle WindowInstance: Browser_5")
expect(597, "ELSE")
expect(606, "        END")
expect(607, "**ENDREGION")
expect(623, "_土地建物查詢資料*.pdf")
expect(624, "FilesOrFoldersToZip: Files ")
expect(625, "FilesOrFoldersToZip: Orignal_ZIP")
expect(626, "File.Delete Files: Files")
expect(627, "File.Delete Files: Orignal_ZIP")
expect(628, "擷取完成")
expect(629, "資料夾''' Icon:")
expect(630, "**ENDREGION")

reuse_open = L[548:581]        # 549..581：點開之後 → 另存新檔對話框打開、按 Alt+N
reuse_save = L[585:595]        # 586..595：貼上檔名 → 存到桌面 → WAIT 2
date_lines = L[512:514]        # 513..514：今天日期（壓縮檔資料夾要用）

new_dl = []
A = new_dl.append
A("        # ===今天日期（壓縮檔的資料夾名稱要用）===")
new_dl += date_lines
A("        # === 2026-10-12 改：照「送件時間」把每一份謄本對回 Excel 的那一列，再用文號＋身分證號命名 ===")
A("        # 以前是照順序配：領件區第 N 個待下載的，就當成這一批第 N 個送出去的。")
A("        # 但領件區列的是這個帳號當天「所有」的申請，包括別的案子 —— 前面只要多一筆，")
A("        # 後面每一份的檔名就全部錯開一份，而且從檔名上完全看不出來（承辦人 2026-10-12）。")
A("        # 現在送件時把電腦時間記在 I 欄，下載時拿來跟領件區的「申請日期時間」對。")
A("        # 對不到或同時對到兩筆就不下載，寫在 J 欄、最後列出來，交給人處理 —— 不猜。")
A("        IF SelectedIndex = 2 THEN")
A("            Display.SelectFileDialog.SelectFile Title: $'''請選擇「送件時用的那一份」Excel 檔（要靠裡面記的送件時間，把謄本對回每一列）''' InitialDirectory: $'''%SpecialFolderPath%\\\\''' FileFilter: $'''*.xlsx''' IsTopMost: False CheckIfFileExists: False SelectedFile=> SelectedFile")
A("        END")
A("        Excel.LaunchExcel.LaunchAndOpenUnderExistingProcess Path: SelectedFile Visible: True ReadOnly: False Instance=> ExcelInstance")
A("        UIAutomation.SetWindowState.SetStateByInstanceOrHandle WindowInstance: ExcelInstance State: UIAutomation.WindowState.Minimized")
A("        Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''送件時間''' Column: $'''I''' Row: 1")
A("        Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''下載結果''' Column: $'''J''' Row: 1")
A("        Excel.GetFirstFreeRowOnColumn Instance: ExcelInstance Column: $'''A''' FirstFreeRowOnColumn=> LastRow")
A("        Variables.DecreaseVariable Value: LastRow DecrementValue: 1")
A("        # ===先收齊這一批全部的送件時間（對時間要整批一起看，才分得出誰是誰）===")
A("        SET TimesText TO $''''''")
A("        SET SentCount TO 0")
A("        LOOP LoopIndex_row FROM 2 TO LastRow STEP 1")
A("            Excel.ReadFromExcel.ReadCell Instance: ExcelInstance StartColumn: $'''I''' StartRow: LoopIndex_row ReadAsText: True CellValue=> ExcelData_SentTime")
A("            IF IsNotEmpty(ExcelData_SentTime) THEN")
A("                SET TimesText TO $'''%TimesText%%ExcelData_SentTime%|'''")
A("                Variables.IncreaseVariable Value: SentCount IncrementValue: 1")
A("            END")
A("        END")
A("        IF SentCount = 0 THEN")
A("            Excel.CloseExcel.CloseAndSave Instance: ExcelInstance")
A("            Display.ShowMessageDialog.ShowMessage Message: $'''這份 Excel 的 I 欄沒有任何送件時間，沒辦法把謄本對回每一列，所以這次不下載。")
A("（舊版流程送出去的件不會記送件時間 —— 那一批請到領件區手動下載，並自己確認是誰的謄本。）''' Icon: Display.Icon.None Buttons: Display.Buttons.OK DefaultButton: Display.DefaultButton.Button1 IsTopMost: False ButtonPressed=> ButtonPressed2")
A("            EXIT Code: 0")
A("        END")
A("        Variables.CreateNewList List=> PdfFiles")
A("        SET UsedNames TO $''''''")
A("        SET ResultNote TO $''''''")
A("        SET DownCount TO 0")
A("        SET MissCount TO 0")
A("        LOOP LoopIndex_row FROM 2 TO LastRow STEP 1")
A("            Excel.ReadFromExcel.ReadCell Instance: ExcelInstance StartColumn: $'''I''' StartRow: LoopIndex_row ReadAsText: True CellValue=> ExcelData_SentTime")
A("            IF IsEmpty(ExcelData_SentTime) THEN")
A("                NEXT LOOP")
A("            END")
A("            # 之前已經下載成功的列就跳過（同一份 Excel 可以重跑「僅下載謄本」補抓）")
A("            Excel.ReadFromExcel.ReadCell Instance: ExcelInstance StartColumn: $'''J''' StartRow: LoopIndex_row ReadAsText: True CellValue=> ExcelData_Result")
A("            IF Contains(ExcelData_Result, $'''已下載：''', False) THEN")
A("                NEXT LOOP")
A("            END")
A("            Excel.ReadFromExcel.ReadCell Instance: ExcelInstance StartColumn: $'''C''' StartRow: LoopIndex_row ReadAsText: False CellValue=> ExcelData_Application")
A("            Excel.ReadFromExcel.ReadCell Instance: ExcelInstance StartColumn: $'''D''' StartRow: LoopIndex_row ReadAsText: False CellValue=> ExcelData_ID")
A("            # ===在領件區找這一列送出去的那一筆===")
new_dl += js_block(os.path.join(HERE, "find.js"), "FindResult", "            ")
A("            IF Contains(FindResult, $'''對不到''', False) THEN")
A("                Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''未下載：%FindResult%''' Column: $'''J''' Row: LoopIndex_row")
A("                SET ResultNote TO $'''%ResultNote%第%LoopIndex_row%列（%ExcelData_Application%）%FindResult%")
A("'''")
A("                Variables.IncreaseVariable Value: MissCount IncrementValue: 1")
A("                NEXT LOOP")
A("            END")
A("            IF Contains(FindResult, $'''已下載''', False) THEN")
A("                Display.ShowMessageDialog.ShowMessageWithTimeout Message: $'''第%LoopIndex_row%列（%ExcelData_Application%）系統顯示已經下載過了，還要再下載一次嗎?''' Icon: Display.Icon.None Buttons: Display.Buttons.YesNo DefaultButton: Display.DefaultButton.Button2 IsTopMost: False Timeout: 5 ButtonPressed=> ButtonPressed4")
A("                IF ButtonPressed4 <> $'''Yes''' THEN")
A("                    Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''未下載：系統顯示已經下載過，這次沒有重下''' Column: $'''J''' Row: LoopIndex_row")
A("                    NEXT LOOP")
A("                END")
A("            END")
A("            # ===點開對到的那一筆（點之前會再確認一次收件號沒變）===")
new_dl += js_block(os.path.join(HERE, "click.js"), "ClickResult", "            ")
A("            IF Contains(ClickResult, $'''失敗''', False) THEN")
A("                Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''未下載：%ClickResult%''' Column: $'''J''' Row: LoopIndex_row")
A("                SET ResultNote TO $'''%ResultNote%第%LoopIndex_row%列（%ExcelData_Application%）%ClickResult%")
A("'''")
A("                Variables.IncreaseVariable Value: MissCount IncrementValue: 1")
A("                NEXT LOOP")
A("            END")
new_dl += reuse_open
A("                # ===檔名：公文文號＋身分證號，取自「對到的那一列」===")
A("                # 同一份 Excel 裡同一個人同一個文號出現兩次（例如兩間房子）時，第二份加上列號，")
A("                # 不然另存新檔會跳「要取代嗎」，流程沒處理那個對話框，那一份就靜靜不見了。")
A("                SET FileNameForPdf TO $'''%ExcelData_Application%_%ExcelData_ID%'''")
A("                IF Contains(UsedNames, $'''|%FileNameForPdf%|''', False) THEN")
A("                    SET FileNameForPdf TO $'''%ExcelData_Application%_%ExcelData_ID%_第%LoopIndex_row%列'''")
A("                END")
A("                SET UsedNames TO $'''%UsedNames%|%FileNameForPdf%|'''")
A("                Clipboard.SetText Text: FileNameForPdf")
new_dl += reuse_save
A("                Variables.AddItemToList Item: $'''%SpecialFolderPath%\\\\%FileNameForPdf%.pdf''' List: PdfFiles")
A("                Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''已下載：%FileNameForPdf%（%FindResult%）''' Column: $'''J''' Row: LoopIndex_row")
A("                SET ResultNote TO $'''%ResultNote%第%LoopIndex_row%列 → %FileNameForPdf%")
A("'''")
A("                Variables.IncreaseVariable Value: DownCount IncrementValue: 1")
A("                UIAutomation.CloseWindow.CloseByInstanceOrHandle WindowInstance: Browser_5")
A("        END")
A("        Excel.ResizeColumnsOrRows.AutofitSingleColumn Instance: ExcelInstance Column: $'''J'''")
A("        Excel.CloseExcel.CloseAndSave Instance: ExcelInstance")

new_zip = [
    "        # 只壓這一次真的存下來的檔案（PdfFiles）。以前用萬用字元撈桌面，",
    "        # 會把桌面上別的 PDF 一起壓進去、然後刪掉。",
    "        IF DownCount > 0 THEN",
    L[623].replace("FilesOrFoldersToZip: Files ", "FilesOrFoldersToZip: PdfFiles "),
    L[624],
    "            File.Delete Files: PdfFiles",
    "            File.Delete Files: Orignal_ZIP",
    "        END",
    "        Display.ShowMessageDialog.ShowMessage Message: $'''擷取完成",
    "這一批送件 %SentCount% 筆，這次下載 %DownCount% 筆，對不到 %MissCount% 筆。",
    "檔案位於桌面之「土地建物謄本查調」資料夾。",
    "",
    "%ResultNote%",
    "對不到的請到領件區手動下載，並自己確認是誰的謄本。每一列的結果也寫在 Excel 的 J 欄。" + line(629)[line(629).index("''' Icon:"):],
]

sent_time = [
    "            # ===記下送件時間（下載時靠它把謄本對回這一列，見「領件區」那一段）===",
    "            DateTime.GetCurrentDateTime.Local DateTimeFormat: DateTime.DateTimeFormat.DateAndTime CurrentDateTime=> SentDateTime",
    "            Text.ConvertDateTimeToText.FromCustomDateTime DateTime: SentDateTime CustomFormat: $'''yyyy-MM-dd HH:mm:ss''' Result=> SentTimeText",
    "            Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''送件 %SentTimeText%''' Column: $'''I''' Row: LoopIndex",
]

headers = [
    "        Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''送件時間''' Column: $'''I''' Row: 1",
    "        Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''下載結果''' Column: $'''J''' Row: 1",
]

sort_line = (line(45).replace("EndColumn: $'''D'''", "EndColumn: $'''J'''")
                     .replace("[$'''A''', $'''asc''']", "[$'''C''', $'''asc''']"))
sort_note = [
    "        # 照公文文號（C 欄）排，而且整列 A～J 一起排。以前只排 A～D，",
    "        # E～H（段名、地號、建號、備註）留在原地 —— 重跑一份做到一半的 Excel 時，",
    "        # 「這筆做過了」的備註會跑到別人那一列。",
]

out = (L[:41] + headers + L[41:44] + sort_note + [sort_line] + L[45:414] + sent_time
       + L[414:497] + new_dl + L[606:622] + new_zip + L[629:])
open(DST, "wb").write("\r\n".join(out).encode("utf-8"))
print("原本 %d 行 → 新的 %d 行" % (len(L), len(out)))

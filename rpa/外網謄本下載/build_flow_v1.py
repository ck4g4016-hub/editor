# -*- coding: utf-8 -*-
"""以「照文號排序」那一版（2026-09-24 給承辦人的）為底，換掉照順序配檔名的那一段。

那一版保留了之前所有的修正（畫面清除、重試、只壓真的存下來的檔），只有
「領件區第 N 個待下載 = 這一批第 N 個送出」這個假設是錯的。這裡只換掉它。

    python3 -I build_flow_v1.py 照文號排序那一版.txt 新的.txt
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from padjs import escape  # noqa: E402

SRC, DST = sys.argv[1], sys.argv[2]
raw = open(SRC, "rb").read().decode("utf-8")
NL = "\r\n" if "\r\n" in raw else "\n"
L = raw.split(NL)


def find(piece, start=0):
    """找唯一一行含有 piece 的，回傳 0 起算的索引。找不到或不只一行就停。"""
    hits = [i for i in range(start, len(L)) if piece in L[i]]
    assert hits, "找不到：%s" % piece
    return hits[0]


def only(piece):
    hits = [i for i, line in enumerate(L) if piece in line]
    assert len(hits) == 1, "「%s」應該只有一行，實際 %d 行" % (piece, len(hits))
    return hits[0]


def js_block(path, result, indent):
    js = open(path, encoding="utf-8").read().rstrip("\n")
    assert "'''" not in js
    body = escape(js).split("\n")
    return ([indent + "WebAutomation.ExecuteJavascript BrowserInstance: Browser Javascript: $'''" + body[0]]
            + body[1:-1] + [body[-1] + "''' Result=> " + result])


# ── 錨點 ──
h_header = only("Value: $'''備註''' Column: $'''H''' Row: 1")
s_note = only("# === 送件順序改成照公文文號（C 欄），不要照行政區（A 欄）===")
s_sort = find("Excel.SortCellsFromExcel.SortRangeCoordinates", s_note)
assert s_sort - s_note == 9 and "[$'''C''', $'''asc''']" in L[s_sort] and "EndColumn: $'''D'''" in L[s_sort]
q_start = only("# === 檔名要用公文文號，所以要記住「這一輪是照什麼順序送出去的」===")
q_end = only("SET SubmitCount TO 0")
assert q_end - q_start == 12
rec_start = only("# 只有真的送出去的才記，前面 NEXT LOOP 掉的")
rec_end = only("Variables.IncreaseVariable Value: SubmitCount IncrementValue: 1")
submit = rec_end + 1
assert "送 出" in L[submit] and L[submit + 1].strip() == "WAIT 2"
dl_start = only("# ===計算要產製的筆數===")
date1 = only("DateTime.GetCurrentDateTime.Local DateTimeFormat: DateTime.DateTimeFormat.DateOnly CurrentDateTime=> CurrentDateTime")
date2 = date1 + 1
assert "Result=> FormattedDateTime" in L[date2]
wait_after_click = only("''' Result=> RowText") + 1
assert L[wait_after_click].strip() == "WAIT 2"
alt_n = find("TextToSend: $'''{Alt}(n)'''", wait_after_click)
assert L[alt_n + 1].strip() == "WAIT 2"
get_clip = only("Clipboard.GetText Text=> ClipboardText_fileName")
after_save = only("Variables.AddItemToList Item: $'''%SpecialFolderPath%\\\\%ClipboardText_fileName%.pdf''' List: PdfFiles")
assert L[after_save - 1].strip() == "WAIT 2"
close_b5 = after_save + 1
assert "CloseByInstanceOrHandle WindowInstance: Browser_5" in L[close_b5]
dl_end = find("        END", close_b5 + 1)          # ELSE 那一段收尾之後，關掉 LOOP 的 END
while not (L[dl_end] == "        END" and L[dl_end + 1].strip() == "**ENDREGION"):
    dl_end = find("        END", dl_end + 1)
zip_note = only("# 檔名改成只有公文文號之後，原本那個萬用字元")
zip1 = zip_note + 4
assert "FilesOrFoldersToZip: PdfFiles" in L[zip1] and "FilesOrFoldersToZip: Orignal_ZIP" in L[zip1 + 1]
assert L[zip1 + 2].strip() == "File.Delete Files: PdfFiles" and L[zip1 + 3].strip() == "File.Delete Files: Orignal_ZIP"
msg = zip1 + 4
assert "擷取完成" in L[msg]
msg_end = find("%MappingNote%''' Icon:", msg)
assert L[msg_end + 1].strip() == "**ENDREGION"

# ── 新的片段 ──
headers = [
    "        Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''送件時間''' Column: $'''I''' Row: 1",
    "        Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''下載結果''' Column: $'''J''' Row: 1",
]
sort_block = [
    "        # === 照公文文號（C 欄）排，而且整列 A～J 一起排 ===",
    "        # 照文號排是承辦人 2026-09-24 要的（印出來的順序要跟手上那疊紙一樣）。",
    "        # 以前只排 A～D，E～H（段名、地號、建號、備註）留在原地 —— 重跑一份",
    "        # 做到一半的 Excel 時，「這筆做過了」的備註會跑到別人那一列。",
    L[s_sort].replace("EndColumn: $'''D'''", "EndColumn: $'''J'''"),
]
sent_time = [
    "            # ===記下送件時間（下載時靠它把謄本對回這一列，見「領件區」那一段）===",
    "            DateTime.GetCurrentDateTime.Local DateTimeFormat: DateTime.DateTimeFormat.DateAndTime CurrentDateTime=> SentDateTime",
    "            Text.ConvertDateTimeToText.FromCustomDateTime DateTime: SentDateTime CustomFormat: $'''yyyy-MM-dd HH:mm:ss''' Result=> SentTimeText",
    "            Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''送件 %SentTimeText%''' Column: $'''I''' Row: LoopIndex",
]

D = []
A = D.append
A("        # ===今天日期（壓縮檔的資料夾名稱要用）===")
D += [L[date1], L[date2]]
A("        # === 2026-10-12 改：照「送件時間」把每一份謄本對回 Excel 的那一列，再用那一列的文號＋身分證號命名 ===")
A("        # 以前是照順序配：領件區第 N 個待下載的，就當成這一批第 N 個送出去的。")
A("        # 但領件區列的是這個帳號當天「所有」的申請，包括別的案子 —— 前面只要多一筆，")
A("        # 後面每一份的檔名就全部錯開一份，冠到別人的謄本上，從檔名上完全看不出來")
A("        #（承辦人 2026-10-12）。現在送件時把電腦時間記在 I 欄，下載時拿來跟領件區的")
A("        # 「申請日期時間」對。對不到或同時對到兩筆就不下載，寫在 J 欄、最後列出來 —— 不猜。")
A("        IF SelectedIndex = 2 THEN")
A("            Display.SelectFileDialog.SelectFile Title: $'''請選擇「送件時用的那一份」Excel 檔（要靠裡面記的送件時間，把謄本對回每一列）''' InitialDirectory: $'''%SpecialFolderPath%\\\\''' FileFilter: $'''*.xlsx''' IsTopMost: False CheckIfFileExists: False SelectedFile=> SelectedFile")
A("        END")
A("        Excel.LaunchExcel.LaunchAndOpenUnderExistingProcess Path: SelectedFile Visible: True ReadOnly: False Instance=> ExcelInstance")
A("        UIAutomation.SetWindowState.SetStateByInstanceOrHandle WindowInstance: ExcelInstance State: UIAutomation.WindowState.Minimized")
D += headers
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
A("        # PdfFiles 是這一次真的存下來的檔案清單。壓縮和刪除都只動這份清單裡的檔 ——")
A("        # 用萬用字元去撈桌面很危險，會把使用者自己的 PDF 一起壓進加密檔然後刪掉。")
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
D += js_block(os.path.join(HERE, "find.js"), "FindResult", "            ")
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
D += js_block(os.path.join(HERE, "click.js"), "ClickResult", "            ")
A("            IF Contains(ClickResult, $'''失敗''', False) THEN")
A("                Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''未下載：%ClickResult%''' Column: $'''J''' Row: LoopIndex_row")
A("                SET ResultNote TO $'''%ResultNote%第%LoopIndex_row%列（%ExcelData_Application%）%ClickResult%")
A("'''")
A("                Variables.IncreaseVariable Value: MissCount IncrementValue: 1")
A("                NEXT LOOP")
A("            END")
D += L[wait_after_click:alt_n + 2]
A("                # ===檔名：公文文號＋身分證號，取自「對到的那一列」===")
A("                # 同一份 Excel 裡同一個人同一個文號出現兩次（例如兩間房子）時，第二份加上列號，")
A("                # 不然另存新檔會跳「要取代嗎」，流程沒處理那個對話框，那一份就靜靜不見了。")
A("                SET FileNameForPdf TO $'''%ExcelData_Application%_%ExcelData_ID%'''")
A("                IF Contains(UsedNames, $'''|%FileNameForPdf%|''', False) THEN")
A("                    SET FileNameForPdf TO $'''%ExcelData_Application%_%ExcelData_ID%_第%LoopIndex_row%列'''")
A("                END")
A("                SET UsedNames TO $'''%UsedNames%|%FileNameForPdf%|'''")
A("                Clipboard.SetText Text: FileNameForPdf")
D += L[get_clip:after_save]
A("                Variables.AddItemToList Item: $'''%SpecialFolderPath%\\\\%FileNameForPdf%.pdf''' List: PdfFiles")
A("                Excel.WriteToExcel.WriteCell Instance: ExcelInstance Value: $'''已下載：%FileNameForPdf%（%FindResult%）''' Column: $'''J''' Row: LoopIndex_row")
A("                SET ResultNote TO $'''%ResultNote%第%LoopIndex_row%列 → %FileNameForPdf%")
A("'''")
A("                Variables.IncreaseVariable Value: DownCount IncrementValue: 1")
D.append(L[close_b5])
A("        END")
A("        Excel.ResizeColumnsOrRows.AutofitSingleColumn Instance: ExcelInstance Column: $'''J'''")
A("        Excel.CloseExcel.CloseAndSave Instance: ExcelInstance")

Z = [
    "        # 只壓這一次真的存下來的檔案（PdfFiles）。**不要換成萬用字元** —— 下面是壓縮完",
    "        # 就把檔案刪掉，萬用字元一寬，使用者自己放在桌面的 PDF 就會被壓進加密檔然後刪除。",
    "        IF DownCount > 0 THEN",
    L[zip1], L[zip1 + 1], L[zip1 + 2], L[zip1 + 3],
    "        END",
    "        Display.ShowMessageDialog.ShowMessage Message: $'''擷取完成",
    "這一批送件 %SentCount% 筆，這次下載 %DownCount% 筆，對不到 %MissCount% 筆。",
    "檔案位於桌面之「土地建物謄本查調」資料夾。",
    "",
    "%ResultNote%",
    "對不到的請到領件區手動下載，並自己確認是誰的謄本。每一列的結果也寫在 Excel 的 J 欄。" + L[msg_end][L[msg_end].index("''' Icon:"):],
]

out = (L[:h_header + 1] + headers + L[h_header + 1:s_note] + sort_block
       + L[q_end + 1:rec_start] + L[submit:submit + 1] + sent_time + L[submit + 1:dl_start]
       + D + L[dl_end + 1:zip_note] + Z + L[msg_end + 1:])
open(DST, "wb").write(NL.join(out).encode("utf-8"))
print("原本 %d 行 → 新的 %d 行（換行：%s）" % (len(L), len(out), "CRLF" if NL == "\r\n" else "LF"))

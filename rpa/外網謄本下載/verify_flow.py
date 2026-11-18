# -*- coding: utf-8 -*-
import re
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, HERE)
from padjs import blocks, check, unescape  # noqa: E402

orig = open(sys.argv[1], "rb").read().decode("utf-8")
new = open(sys.argv[2], "rb").read().decode("utf-8")
problems = []

# 1. 行尾跟原本那份一樣（承辦人跑過的版本有 CRLF 也有 LF），不可以混著用
NL = "\r\n" if "\r\n" in orig else "\n"
if NL == "\r\n" and re.search(r"(?<!\r)\n", new):
    problems.append("原本是 CRLF，新的混進了單獨的 LF")
if NL == "\n" and "\r" in new:
    problems.append("原本是 LF，新的混進了 CR")

# 2. 控制項／影像庫那一段（含截圖）一個位元組都不能動
mark = "# [ControlRepository][PowerAutomateDesktop]"
if orig[orig.index(mark):] != new[new.index(mark):]:
    problems.append("ControlRepository 那一段被改到了")
code = new[:new.index(mark)]

# 3. 每一段 JavaScript 都要能解析；新加的兩段要跟測過的一模一樣
found = {}
for name, raw, line in blocks(code.replace(NL, "\n")):
    ok, err = check(unescape(raw))
    if not ok:
        problems.append("第 %d 行的 JavaScript（%s）語法錯：%s" % (line, name, err))
    found.setdefault(name, []).append(unescape(raw))
for name, path in (("FindResult", os.path.join(HERE, "find.js")), ("ClickResult", os.path.join(HERE, "click.js"))):
    tested = open(path, encoding="utf-8").read().rstrip("\n")
    got = [j.replace("\r\n", "\n") for j in found.get(name, [])]
    if got != [tested]:
        problems.append("%s 那一段跟測過的版本不一樣（%d 段）" % (name, len(got)))

# 4. 區塊有開有關：跳過字串裡面的行，只數 PAD 敘述
def statements(text):
    """逐字掃：$''' 進入字串，字串裡反斜線跳脫下一個字，''' 離開字串。
    只回傳「開頭不在字串裡」的那些行。"""
    inside = False
    for line in text.split(NL):
        if not inside:
            yield line.strip()
        i = 0
        while i < len(line):
            if not inside:
                if line.startswith("$'''", i):
                    inside, i = True, i + 4
                    continue
            else:
                if line[i] == "\\":
                    i += 2
                    continue
                if line.startswith("'''", i):
                    inside, i = False, i + 3
                    continue
            i += 1


def balance(text):
    depth, worst = 0, 0
    for s in statements(text):
        if re.match(r"^(IF .* THEN|LOOP |SWITCH |ON ERROR|FUNCTION )", s) or s == "ON ERROR":
            depth += 1
        elif s == "END" or s.startswith("END "):
            depth -= 1
        worst = min(worst, depth)
    return depth, worst

for label, text in (("原本", orig[:orig.index(mark)]), ("新的", code)):
    d, w = balance(text)
    print("%s：區塊收尾後深度 %d，過程中最低 %d" % (label, d, w))
    if label == "新的" and (d != balance(orig[:orig.index(mark)])[0] or w < 0):
        problems.append("新的流程區塊沒有收好")

# 5. 舊的、會造成錯開的東西都拿掉了
for gone in ("ASNAME", "CountForLOOP", "DataFromWebPage_領件區", "TextAsNumber_YEAR",
             "LoopIndex_download", "_土地建物查詢資料", "DocNumbers", "IdNumbers", "SeqIndex",
             "SubmitCount", "依然下載"):
    if gone in code:
        problems.append("還留著 %s" % gone)

# 6. 新變數都有先設定才用
for var, setter in (("TimesText", "SET TimesText TO"), ("SentCount", "SET SentCount TO"),
                    ("UsedNames", "SET UsedNames TO"), ("ResultNote", "SET ResultNote TO"),
                    ("DownCount", "SET DownCount TO"), ("MissCount", "SET MissCount TO"),
                    ("PdfFiles", "CreateNewList List=> PdfFiles"),
                    ("FormattedDateTime", "Result=> FormattedDateTime"),
                    ("LastRow", "FirstFreeRowOnColumn=> LastRow")):
    first_use = code.find(var)
    first_set = code.find(setter)
    if first_set < 0 or first_use < first_set - 400:
        problems.append("%s 可能在設定之前就被用到" % var)

print("JavaScript 段數：原本 %d、新的 %d" % (len(blocks(orig[:orig.index(mark)].replace(NL, "\n"))),
                                       len(blocks(code.replace(NL, "\n")))))
print("\n".join(problems) if problems else "全部通過")
sys.exit(1 if problems else 0)

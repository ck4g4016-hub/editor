# -*- coding: utf-8 -*-
"""決定這一次建置的版本號，例如 1150929、1150929-1。

承辦人 2026-09-29：「以後程式產生出來的版本號能不能也弄在壓縮檔的檔名上，
不然我都不知道我到底是下載哪一版的。」規則是他定的：

    當天第一版    1150929
    當天第二版    1150929-1
    當天第三版    1150929-2

**當天第幾版是看 Release 上現在掛的那個檔名算的**，不另外記在別的地方。
那個檔案就是承辦人會下載到的東西，拿它當依據，編號跟他手上的檔案永遠
對得起來；建置失敗或被取消的那一次沒有發布出去，也就不會佔掉一個號碼。

日期一律用台灣時間。建置機器是 UTC，直接取日期的話，台灣早上八點以前
建的版本會變成前一天的日期 —— 承辦人一早下載到的是「昨天那版」，
就是這個功能要消滅的那種混亂。台灣沒有日光節約時間，固定 +8 就對。

只做計算、不連網路：Release 上有哪些檔名由 workflow 用 gh 查好再傳進來。

用法（workflow 裡）：

    python tools/release_version.py paper2excel_1150929.zip
    → 1150929-1
"""

import datetime
import re
import sys

# 台灣時間。沒有日光節約，固定 +8。
TAIPEI = datetime.timezone(datetime.timedelta(hours=8))

# Release 附件的檔名。只認這個格式 —— 別的檔案（例如改版前的
# paper2excel.zip）一律不算，不能讓它影響編號。
ASSET = re.compile(r"^paper2excel_(\d{7})(?:-(\d+))?\.zip$")


def roc_today(now=None):
    """台灣時間的民國年月日，例如 1150929。"""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        # 沒有時區的時間不知道是哪裡的時間，猜錯就差一天。直接拒絕。
        raise ValueError("時間要帶時區")
    local = now.astimezone(TAIPEI)
    return "%d%02d%02d" % (local.year - 1911, local.month, local.day)


def next_version(existing, today):
    """給定 Release 上現有的附件檔名與今天的日期，決定這一版叫什麼。

    今天還沒發過就是「1150929」，發過了就在最大的那個號碼上加一。
    號碼要當數字比，不能當字串比 —— 字串比的話「-10」會排在「-9」前面。
    """
    used = []
    for name in existing or ():
        found = ASSET.match((name or "").strip())
        if found and found.group(1) == today:
            used.append(int(found.group(2) or 0))
    if not used:
        return today
    return "%s-%d" % (today, max(used) + 1)


def asset_name(version):
    """Release 附件的檔名。

    **只能用英數。** GitHub 會把附件檔名裡的非 ASCII 字元濾掉 ——
    以前「紙本轉excel.zip」上傳之後變成「excel.zip」。所以「版」這個字
    放不進檔名，只放在程式畫面上與診斷報告裡。
    """
    return "paper2excel_%s.zip" % version


def main(argv):
    print(next_version(argv, roc_today()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

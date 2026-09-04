# -*- coding: utf-8 -*-
"""在整頁上找收文戳的公文文號，不必框選。

**為什麼不框選**：貼標籤的人不會貼在固定的位置。承辦人實測有件的標籤貼在
別的地方，框選的欄位裡什麼都沒有，那一欄就整個廢掉。

**為什麼找得到**：公文文號的組成是固定的 ——

    115      民國年
    569      機關當年度被分到的值
    XXXX     流水編號

十碼，中間三碼是機關代號。整頁掃過去，符合這個格式的數字串幾乎只有它一個。
拿承辦人給的 A 表八頁（四件，正反面）實測，全頁辨識抓到的十碼以上數字串裡：

    第1頁  11508170925125391、1150817006595、20260817143754、
           20260831235959、**1155698196**
    第4頁  0910118290、1150806221830、1150807142810

案件編號太長、收件時間是 14 碼、電話 0910118290 剛好也是十碼但中間三碼不對。
四件的公文文號全部讀到，信心都是 1.00，沒有一件誤判。

機關代號每年會換，所以放在 data/公文文號.txt 讓承辦人自己改；
萬一忘了改，還有「開頭三碼是合理的民國年」這條退路，只是會標記起來讓人看。
"""

import re

from . import recognise, render, resources, validate

# 全頁掃描用的解析度。300dpi 跟 150dpi 對這個數字串的辨識結果一模一樣
# （信心都是 1.00），但 150dpi 一頁快一倍上下，所以用 150。
SCAN_DPI = 150

# 民國年的合理範圍。這支程式是 2026（民國 115）年寫的。
YEAR_LOW, YEAR_HIGH = 110, 199

_TEN = re.compile(r"(?<!\d)\d{10}(?!\d)")


def agency_codes():
    """機關當年度被分到的那三碼。讀 data/公文文號.txt，一行一個。"""
    codes = []
    try:
        with open(resources.path("data", "公文文號.txt"), encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#") and re.fullmatch(r"\d{3}", line):
                    codes.append(line)
    except OSError:
        pass
    return codes


def _plausible(value):
    """開頭三碼是不是合理的民國年。"""
    return len(value) == 10 and YEAR_LOW <= int(value[:3]) <= YEAR_HIGH


def candidates(texts):
    """從整頁的辨識結果裡挑出可能的公文文號。回傳 [(文號, 是否符合機關代號)]。

    先把英文字母換回形狀相近的數字 —— 手寫沒有這個問題，但條碼下面那行
    印得很小，8 讀成 B、0 讀成 O 都發生過。
    """
    codes = set(agency_codes())
    found, seen = [], set()
    for text in texts:
        cleaned = "".join(
            validate._LETTER_TO_DIGIT.get(ch, validate._LETTER_TO_DIGIT.get(ch.upper(), ch))
            if ch.isalpha() else ch
            for ch in validate.to_halfwidth(text or ""))
        # 日期會跟文號黏在一起（「機關收文115/08/25 1155698710」），先拆掉
        cleaned = validate._DOC_DATE.sub(" ", cleaned)
        for run in _TEN.findall(cleaned):
            if not _plausible(run) or run in seen:
                continue
            seen.add(run)
            found.append((run, run[3:6] in codes))
    return found


def pick(texts):
    """從整頁的辨識結果裡決定公文文號。回傳 (文號, 提醒)。

    找不到就回 (None, 原因)，讓呼叫端退回框選的結果。
    """
    found = candidates(texts)
    if not found:
        return None, "整頁上找不到十碼的公文文號"

    matched = [value for value, ok in found if ok]
    if len(matched) == 1:
        return matched[0], None
    if len(matched) > 1:
        return None, ("整頁上有 %d 個都符合機關代號，分不出是哪一個" % len(matched))

    # 沒有一個符合機關代號。可能是換年度了（代號每年會換），
    # 也可能根本抓錯。值照樣給，但一定要標起來 —— 這是猜的。
    others = [value for value, _ok in found]
    if len(others) == 1:
        return others[0], ("「%s」的中間三碼不在 data/公文文號.txt 裡，"
                           "如果機關代號換了請去改，否則請確認這個號碼"
                           % others[0])
    return None, "整頁上有 %d 個十碼數字，中間三碼都對不上機關代號" % len(others)


def read_page(page, rotation=0, dpi=SCAN_DPI):
    """把一整頁辨識成文字清單。"""
    image = render.rotate(
        render.render(page.source, page.index, dpi=dpi, gray=False), rotation or 0)
    try:
        result = recognise.engine()(image, use_det=True, use_cls=False, use_rec=True)
    except Exception:                                               # noqa: BLE001
        return []
    rows = result[0] if isinstance(result, tuple) else result
    return [text for _box, text, _score in (rows or [])]


def find(pages, rotation=0):
    """依序掃過幾頁，找到就停。回傳 (文號, 提醒)。

    正面先掃 —— 收文戳幾乎都蓋在正面，掃到就不必算背面那一頁。
    """
    last = "沒有頁面可以掃描"
    for page in pages:
        if page is None:
            continue
        value, note = pick(read_page(page, rotation))
        if value:
            return value, note
        last = note or last
    return None, last

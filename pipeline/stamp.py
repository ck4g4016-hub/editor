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

**機關代號不能當成必要條件。** 承辦人指出它會隨著流水號用完而往上跳：
今年從 5670001 開始，四碼用完變成 5680001，再用完變成 5690001，明年又是
另一組。寫死一個值的話，跳號那天全部的件就通不過了，而且沒有人會知道
是為什麼 —— 那種「設定過期造成的沉默失效」比讀錯還難查。

所以改成**逐層縮小**，機關代號只在最後當破平手用：

  1. 十碼、開頭三碼是合理的民國年
  2. 還不只一個 → 留下年份跟這一頁上收文日期同年的
  3. 還不只一個 → 留下中間三碼符合 data/公文文號.txt 的
  4. 剩下剛好一個就用它；還是不只一個就標記起來，不猜

實測那四頁在第 1 層就只剩一個了，根本用不到設定檔 —— 換句話說，
機關代號跳號、跨年度，程式都不必改，設定檔也不必動。
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
    """機關代號那三碼。讀 data/公文文號.txt。

    一行一個，可以寫單一個值（569）或一段範圍（567-570）——
    代號會隨流水號用完往上跳，寫範圍就不必每次跳號都來改。
    這份設定是**選用的**：整頁上只有一個候選時根本用不到它。
    """
    codes = set()
    try:
        with open(resources.path("data", "公文文號.txt"), encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                span = re.fullmatch(r"(\d{3})\s*[-~－]\s*(\d{3})", line)
                if span:
                    low, high = sorted((int(span.group(1)), int(span.group(2))))
                    codes.update("%03d" % n for n in range(low, high + 1))
                elif re.fullmatch(r"\d{3}", line):
                    codes.add(line)
    except OSError:
        pass
    return sorted(codes)


def _plausible(value):
    """開頭三碼是不是合理的民國年。"""
    return len(value) == 10 and YEAR_LOW <= int(value[:3]) <= YEAR_HIGH


# 收文戳上的日期，例如 115/08/18。它跟文號印在一起，年份一定同一年。
_ROC_DATE = re.compile(r"(?<!\d)(1\d{2})\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{1,2}(?!\d)")


def stamp_years(texts):
    """整頁上出現過的民國年（從收文日期上抓）。"""
    years = set()
    for text in texts:
        for year in _ROC_DATE.findall(validate.to_halfwidth(text or "")):
            if YEAR_LOW <= int(year) <= YEAR_HIGH:
                years.add(year)
    return years


def candidates(texts):
    """從整頁的辨識結果裡挑出可能的公文文號。回傳 [文號]。

    先把英文字母換回形狀相近的數字 —— 手寫沒有這個問題，但條碼下面那行
    印得很小，8 讀成 B、0 讀成 O 都發生過。
    """
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
            found.append(run)
    return found


def pick(texts):
    """從整頁的辨識結果裡決定公文文號。回傳 (文號, 提醒)。

    逐層縮小，每一層都先問「剩下剛好一個了嗎」。機關代號排在最後，
    因為它會跳號、會跨年度換 —— 把會過期的東西放在必要條件上，
    設定一過期就整批沉默失效。
    """
    found = candidates(texts)
    if not found:
        return None, "整頁上找不到十碼的公文文號"
    if len(found) == 1:
        return found[0], None

    years = stamp_years(texts)
    if years:
        same = [value for value in found if value[:3] in years]
        if len(same) == 1:
            return same[0], None
        if same:
            found = same

    codes = set(agency_codes())
    if codes:
        matched = [value for value in found if value[3:6] in codes]
        if len(matched) == 1:
            return matched[0], None
        if matched:
            found = matched

    return None, ("整頁上有 %d 個看起來都像公文文號的十碼數字，分不出是哪一個"
                  % len(found))


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

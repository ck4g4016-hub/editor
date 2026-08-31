# -*- coding: utf-8 -*-
"""欄位的正規化與驗證。

辨識出來的字串在進到輸出檔之前，都要先過這裡：
把寫法統一（半形、之、中文樓層），再檢查格式對不對。

身分證字號、公文文號、門牌是三個必要欄位，驗證不通過就強制標記，
即使日後開放自動放行也一樣擋下 —— 這三欄錯了，RPA 會拿著錯的資料去查別人的房子，
而且從輸出的表格上完全看不出來。
"""

import re

# 全形數字與英文字母轉半形
_FULLWIDTH = {chr(0xFF10 + i): str(i) for i in range(10)}
_FULLWIDTH.update({chr(0xFF21 + i): chr(ord("A") + i) for i in range(26)})
_FULLWIDTH.update({chr(0xFF41 + i): chr(ord("a") + i) for i in range(26)})
_FULLWIDTH["　"] = " "

_CHINESE_DIGITS = "〇一二三四五六七八九"

# 身分證字母對應的兩位數字（內政部的編碼規則）
_ID_LETTERS = "ABCDEFGHJKLMNPQRSTUVXYWZIO"


def to_halfwidth(text):
    return "".join(_FULLWIDTH.get(ch, ch) for ch in text or "")


def to_chinese_number(value):
    """把阿拉伯數字轉成中文數字。樓層用得到：17 → 十七。"""
    value = int(value)
    if value < 0:
        raise ValueError("樓層不會是負數")
    if value < 10:
        return _CHINESE_DIGITS[value]
    if value < 20:
        return "十" + (_CHINESE_DIGITS[value % 10] if value % 10 else "")
    if value < 100:
        tens = _CHINESE_DIGITS[value // 10] + "十"
        return tens + (_CHINESE_DIGITS[value % 10] if value % 10 else "")
    return str(value)


def id_number(text):
    """身分證字號：轉成標準寫法並驗檢查碼。

    回傳 (值, 問題)。問題是 None 代表通過。
    """
    value = re.sub(r"[^0-9A-Za-z]", "", to_halfwidth(text or "")).upper()
    if len(value) != 10:
        return value, "長度是 %d 碼，應該是 10 碼" % len(value)
    if value[0] not in _ID_LETTERS:
        return value, "開頭 %r 不是合法的縣市英文字母" % value[0]
    if value[1] not in "12":
        return value, "第 2 碼是 %r，應該是 1 或 2" % value[1]
    if not value[2:].isdigit():
        return value, "後 8 碼不全是數字"

    code = _ID_LETTERS.index(value[0]) + 10
    total = code // 10 + (code % 10) * 9
    for index, digit in enumerate(value[1:9]):
        total += int(digit) * (8 - index)
    total += int(value[9])
    if total % 10 != 0:
        return value, "檢查碼不符"
    return value, None


def doc_number(text):
    """公文文號（機關收文條碼號）：10 碼數字，開頭是民國年。"""
    value = re.sub(r"\D", "", to_halfwidth(text or ""))
    if len(value) != 10:
        return value, "長度是 %d 碼，應該是 10 碼" % len(value)
    year = int(value[:3])
    if not 100 <= year <= 199:
        return value, "開頭三碼 %s 不像民國年" % value[:3]
    return value, None


def land_number(text):
    """地號：母號 4 碼 + 子號 4 碼，補零成 0000-0000。"""
    value = to_halfwidth(text or "").strip()
    match = re.match(r"^(\d{1,4})\s*[-之]?\s*(\d{1,4})?$", value)
    if not match:
        return value, "看不出是地號"
    parent = match.group(1).zfill(4)
    child = (match.group(2) or "0").zfill(4)
    return "%s-%s" % (parent, child), None


# 地址要拿掉的前綴：縣市、行政區、里、鄰
_DROP = re.compile(
    r"^\s*(?:\d{3,5})?\s*"                       # 郵遞區號
    r"(?:[^\s]{1,3}[縣市])?\s*"                   # 新北市
    r"(?:[^\s]{1,4}[區鄉鎮市])?\s*"                # 三峽區
    r"(?:[^\s]{1,5}[里村])?\s*"                   # 中山里
    r"(?:\d{1,4}鄰)?\s*")

_FLOOR = re.compile(r"(\d+)\s*樓")

# 段用中文數字（戶役政的規定），例如「中正路2段」要寫成「中正路二段」。
# 三峽的中正路、介壽路都有一二三段。
_SECTION = re.compile(r"(\d+)\s*段")

# 地址只會有中文字與阿拉伯數字。出現英文字母一定是辨識錯的 ——
# 手寫的數字被讀成形狀相近的字母是最常見的一種錯，照這張表換回數字。
_LETTER_TO_DIGIT = {
    "O": "0", "o": "0", "D": "0", "Q": "0",
    "I": "1", "l": "1", "i": "1", "|": "1", "L": "1",
    "Z": "2", "z": "2",
    "E": "3",
    "A": "4",
    "S": "5", "s": "5",
    "G": "6", "b": "6",
    "T": "7", "t": "7",
    "B": "8",
    "g": "9", "q": "9",
}

# 「之」常被寫成或認成各種符號
_ZHI_SYMBOLS = "-–—~/\\_.,、"


def address(text, roads=None):
    """門牌正規化。

    只留 路(街) → 巷 → 弄 → 號 → 樓，其餘一律不寫：
    不含縣市與行政區（那是行政區欄的事），也不含里、鄰。
    巷弄號用半形數字，支號用「之」不用連字號，樓層一律中文數字。

    地址只會有中文字與阿拉伯數字，出現英文字母一定是辨識錯的，
    先照混淆表換成數字；換完還有字母就直接判為錯誤，不輸出。

    給了 roads（路街名字典）就把路名那一段吸附到最接近的合法名稱 ——
    這同時解決「路還是街」，民眾圈選糊掉也沒關係，字典裡是哪個就是哪個。
    """
    value = to_halfwidth(text or "").strip()
    value = _DROP.sub("", value, count=1)
    value = value.replace(" ", "")

    # 「之」的各種寫法統一
    value = re.sub(r"(\d)\s*[%s]\s*(\d)" % re.escape(_ZHI_SYMBOLS), r"\1之\2", value)

    # 先認路名，再處理英文字母 —— 順序不能反。
    # 紙本表格上的「路」「街」是印刷的，民眾圈起來，減掉版面後只剩一個圈，
    # 會被辨識成 Q、C、〇 之類。先換成數字的話，那個圈就變成 0 混進門牌號碼裡了。
    head, tail = re.match(r"^([^\d]*)(.*)$", value).groups()
    if roads:
        from . import lexicon
        # 路名結尾字有讀到的話就在那裡切開，後面的「段」之類要留著 ——
        # 不切的話「中正路二段」會整串拿去比對，match 到「二鬮路」。
        suffix = max((head.rfind(ch) for ch in "路街道"), default=-1)
        if suffix >= 0:
            stem, rest = head[:suffix + 1], head[suffix + 1:]
        else:
            stem, rest = head, ""

        name, score = lexicon.resolve_head(stem, roads)
        if name:
            # 地址一定以路街名開頭，前面黏著的行政區之類一律丟掉
            value = name + rest + tail
        elif head:
            return value, "路街名不在字典裡（讀到「%s」）" % head

    # 英文字母換成形狀相近的數字。地址只會有中文字與阿拉伯數字。
    value = "".join(_LETTER_TO_DIGIT.get(ch, ch) for ch in value)
    leftover = re.findall(r"[A-Za-z]", value)
    if leftover:
        return value, "出現英文字母 %s，地址不會有英文" % "".join(sorted(set(leftover)))

    # 段與樓層轉中文：2段 → 二段、17樓 → 十七樓
    value = _SECTION.sub(lambda m: to_chinese_number(m.group(1)) + "段", value)
    value = _FLOOR.sub(lambda m: to_chinese_number(m.group(1)) + "樓", value)

    if not value:
        return value, "地址是空的"
    if "號" not in value:
        return value, "沒有「號」，可能沒讀完整"
    return value, None


def district(text, known=None):
    """行政區：比對到已知的區名。

    手寫的「三峽」常被辨識成「三山峡」之類，但行政區的選項就那幾個，
    用最接近的合法區名取代即可。
    """
    value = to_halfwidth(text or "").strip().replace(" ", "")
    if not value:
        return value, "行政區是空的"
    options = list(known or ())
    if not options:
        return value, None
    for option in options:
        if option == value or option.rstrip("區") == value.rstrip("區"):
            return option, None
    best = max(options, key=lambda o: _overlap(value, o))
    if _overlap(value, best) >= 1:
        return best, None
    return value, "認不出是哪一區"


def _overlap(a, b):
    """兩個字串共有幾個字。用來做寬鬆的字典比對。"""
    return len(set(a) & set(b))


VALIDATORS = {
    "id_number": id_number,
    "doc_number": doc_number,
    "address": address,
}


def check(kind, text, **kwargs):
    """依型別做正規化與驗證，回傳 (值, 問題)。"""
    if kind == "address":
        return address(text, roads=kwargs.get("roads"))
    if kind == "district":
        return district(text, known=kwargs.get("known"))
    if kind == "land_number":
        return land_number(text)
    validator = VALIDATORS.get(kind)
    if validator:
        return validator(text)
    return (text or "").strip(), None

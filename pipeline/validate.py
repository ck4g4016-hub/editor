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


# 位置本身就是規則：第 1 碼一定是英文字母，後 9 碼一定是數字。
# 手寫的 1 常被讀成 L 或 I、2 讀成 Z、0 讀成 O —— 知道那一格該是數字，
# 就能直接換回來，不用等檢查碼失敗才說「錯了」。
_DIGIT_TO_LETTER = {"0": "O", "1": "I", "2": "Z", "3": "E", "4": "A",
                    "5": "S", "6": "G", "7": "T", "8": "B", "9": "G"}


def fix_id_positions(text):
    """照「1 碼英文 + 9 碼數字」把讀錯的字換回來。長度不對就原樣回傳。"""
    value = re.sub(r"[^0-9A-Za-z]", "", to_halfwidth(text or "")).upper()
    if len(value) != 10:
        return value
    head = value[0]
    if head.isdigit():
        head = _DIGIT_TO_LETTER.get(head, head)
    tail = "".join(_LETTER_TO_DIGIT.get(ch, ch).upper() if ch.isalpha() else ch
                   for ch in value[1:])
    return head + tail


def best_id(*texts):
    """好幾種讀法裡挑一個。

    身分證有檢查碼 —— 這讓我們可以**直接驗證**哪一種讀法是對的，不必猜。
    整行讀一次、逐格讀一次，位置規則各套一遍，誰通過檢查碼就用誰。
    都沒過的話回傳最像的那一個，讓人去看。
    """
    tried = []
    for text in texts:
        # fixed 排在後面但優先採用 —— 平手時要選套過位置規則的那個，
        # 「後 8 碼不全是數字」這種錯它已經處理掉了
        for rank, candidate in enumerate((fix_id_positions(text), text)):
            if not candidate or candidate in [t[0] for t in tried]:
                continue
            value, problem = id_number(candidate)
            if problem is None:
                return value, None
            tried.append((candidate, value, problem, rank))
    if not tried:
        return "", "沒有讀到內容"
    # 沒有一個通過檢查碼。長度對的優先，再來才是套過位置規則的
    tried.sort(key=lambda item: (abs(len(item[1]) - 10), item[3], -len(item[1])))
    return tried[0][1], tried[0][2]


# 一格一個字時，每一格的候選字。位置本身就是規則：
# 第 1 碼一定是英文字母、第 2 碼一定是 1 或 2、其餘一定是數字。
def _cell_options(texts, position):
    """把一格的幾種讀法，換算成那個位置**可能**的字。"""
    got = set()
    for text in texts or ():
        for char in to_halfwidth(text or ""):
            if position == 0:
                letter = _DIGIT_TO_LETTER.get(char, char).upper()
                if letter in _ID_LETTERS:
                    got.add(letter)
            else:
                digit = _LETTER_TO_DIGIT.get(char, _LETTER_TO_DIGIT.get(char.upper(), char))
                if digit.isdigit():
                    got.add(digit)
    read = bool(got)
    if position == 1:
        # 第 2 碼只可能是 1（男）或 2（女）
        got = (got & set("12")) or set("12")
    if not got:
        # 這一格什麼都沒讀到。攤開所有可能，交給檢查碼去挑。
        got = set(_ID_LETTERS) if position == 0 else set("0123456789")
    return sorted(got), read


# 候選組合數的上限。超過就不算 —— 那代表讀到的東西太少，
# 硬算出來的「唯一解」只是湊出一個通過檢查碼的號碼，不是讀出來的。
MAX_ID_COMBOS = 300000

# 最多容許幾格完全沒讀到。超過就不推 —— 補回來的成分比讀出來的還多，
# 那已經不叫辨識了。
MAX_ID_BLANKS = 2


def solve_id(cells):
    """從「每一格的幾種讀法」解出身分證字號。

    cells 是十個清單，每個是那一格讀到的字串（可能是空的）。

    做法是把每一格的可能字列出來，逐一組合，**看哪一種通過檢查碼**。
    身分證有檢查碼這件事，讓我們可以直接驗證而不是猜 —— 別的欄位沒這個優勢。

    只有**剛好一種**組合通過才採用。這一點是關鍵：

      - 讀對了：那一種就是答案。
      - 有一格讀不出來：檢查碼會把它唯一補回來。
      - 有一格讀錯了（讀成別的字）：正確答案不在候選裡，沒有任何組合會通過，
        於是回報失敗、標起來 —— 不會生出一個「通過檢查碼但錯的」號碼。
      - 讀不出來的太多：好幾種都通過，一樣標起來。

    也就是說它的失敗方式是「講出來」，不是「靜靜地寫錯」。

    回傳 (身分證, 問題)。
    """
    if len(cells) != 10:
        return None, "切出 %d 格，身分證應該是 10 格" % len(cells)

    options, blanks = [], []
    for index in range(10):
        choices, was_read = _cell_options(cells[index], index)
        options.append(choices)
        if not was_read:
            blanks.append(index + 1)
    if len(blanks) > MAX_ID_BLANKS:
        return None, "有 %d 格完全沒讀到，補回來的會比讀到的還多" % len(blanks)

    total = 1
    for choices in options:
        total *= len(choices)
    if total > MAX_ID_COMBOS:
        return None, "十格裡讀得出來的太少，湊不出唯一解"

    import itertools

    found = []
    for combo in itertools.product(*options):
        candidate = "".join(combo)
        if id_number(candidate)[1] is None:
            found.append(candidate)
            if len(found) > 1:
                return None, "有好幾種讀法都通過檢查碼，分不出是哪一個"
    if not found:
        return None, "十格湊不出通過檢查碼的號碼"

    if blanks:
        # 有格子是「推」出來的，不是「讀」出來的。值照樣給 —— 有個號碼可以
        # 對照比空白有用 —— 但一定要標起來，不可以當成讀到了。
        # 推回來的前提是其餘幾格都對，那個前提沒有任何東西保證。
        return found[0], ("第 %s 格沒讀到，是用檢查碼推回來的，請自己核對原圖"
                          % "、".join(str(n) for n in blanks))
    return found[0], None


def id_number(text):
    """身分證字號：轉成標準寫法並驗檢查碼。

    回傳 (值, 問題)。問題是 None 代表通過。
    """
    value = re.sub(r"[^0-9A-Za-z]", "", to_halfwidth(text or "")).upper()
    if len(value) != 10:
        return value, "長度是 %d 碼，應該是 10 碼" % len(value)
    if value[0] not in _ID_LETTERS:
        return value, "開頭「%s」不是合法的縣市英文字母" % value[0]
    if value[1] not in "12":
        return value, "第 2 碼是「%s」，應該是 1 或 2" % value[1]
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


# 收文戳上除了條碼號還印著收文日期（115/08/28）。框選時很難只框到條碼下面
# 那一行，日期通常會一起被讀進來，兩串數字黏成 17 碼。先把日期挑掉。
_DOC_DATE = re.compile(r"\d{2,3}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{1,2}")


def doc_number(text):
    """公文文號（機關收文條碼號）：10 碼數字，開頭是民國年。

    只要條碼下面那一行數字。「機關收文」這種標籤是中文，去掉非數字就沒了；
    收文日期是數字，會黏在文號後面變成 17 碼，所以要先照日期格式挑掉。
    """
    cleaned = _DOC_DATE.sub("", to_halfwidth(text or ""))

    # 先看「連續數字」怎麼斷開。收文戳上除了條碼號還印著別的數字（公文編號
    # 115FF005424 的前三碼就是），它們跟條碼號中間隔著文字，本來就是兩段。
    #
    # 以前直接把所有數字接成一整串再滑動視窗找，第一個以民國年開頭的十碼會
    # 橫跨兩段：115 與 1155698710 接成 1151155698710，取出 1151155698 ——
    # 十碼、115 開頭、驗證照樣通過，但那是兩個號碼各切一半黏起來的東西，
    # 而且從輸出的表格上看不出來。實測使用者第 1 件就是這樣錯的。
    runs = re.findall(r"\d+", cleaned)
    exact = [run for run in runs if len(run) == 10 and 100 <= int(run[:3]) <= 199]
    if len(exact) == 1:
        value = exact[0]
    else:
        value = "".join(runs)
        # 沒有剛好十碼的那一段，才退回原本的做法：
        # 從頭找第一段像民國年開頭的 10 碼
        if len(value) > 10:
            for start in range(len(value) - 9):
                window = value[start:start + 10]
                if 100 <= int(window[:3]) <= 199:
                    value = window
                    break

    if len(value) != 10:
        return value, "長度是 %d 碼，應該是 10 碼" % len(value)
    year = int(value[:3])
    if not 100 <= year <= 199:
        return value, "開頭三碼「%s」不像民國年" % value[:3]
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

# 表格上「路／街」那個二選一的標籤。民眾圈一個，但整串常常一起被讀進來，
# 承辦人也可能直接把欄位後綴填成「路/街」。它不是路名的一部分。
_ROAD_LABEL = re.compile(r"[路街道]\s*[/／\\|、,，]?\s*[路街道]")


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
    # 表格上印的是「路／街」二選一的標籤，民眾圈一個。整串被讀進來
    # （或承辦人把後綴填成「路/街」）的時候，那不是路名的一部分，是標籤。
    # 留著會讓比對整個歪掉，而且「/」還會被當成「之」的寫法。
    value = _ROAD_LABEL.sub("", value, count=1)

    # 「之」的各種寫法統一
    value = re.sub(r"(\d)\s*[%s]\s*(\d)" % re.escape(_ZHI_SYMBOLS), r"\1之\2", value)

    # 先認路名，再處理英文字母 —— 順序不能反。
    # 紙本表格上的「路」「街」是印刷的，民眾圈起來，減掉版面後只剩一個圈，
    # 會被辨識成 Q、C、〇 之類。先換成數字的話，那個圈就變成 0 混進門牌號碼裡了。
    warning = None
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

        name, _score, note = lexicon.resolve_head_full(stem, roads)
        if name:
            # 地址一定以路街名開頭，前面黏著的行政區之類一律丟掉
            value = name + rest + tail
            # 靠讀到的路／街決定的，一定要讓人看一眼 —— 那個字本來就不可信
            warning = note
        elif head:
            return value, note or ("路街名不在字典裡（讀到「%s」）" % head)

    # 英文字母換成形狀相近的數字。地址只會有中文字與阿拉伯數字。
    value = "".join(_LETTER_TO_DIGIT.get(ch, ch) for ch in value)
    leftover = re.findall(r"[A-Za-z]", value)
    if leftover:
        return value, "出現英文字母「%s」，地址不會有英文" % "".join(sorted(set(leftover)))

    # 段與樓層轉中文：2段 → 二段、17樓 → 十七樓
    value = _SECTION.sub(lambda m: to_chinese_number(m.group(1)) + "段", value)
    value = _FLOOR.sub(lambda m: to_chinese_number(m.group(1)) + "樓", value)

    if not value:
        return value, "地址是空的"
    if "號" not in value:
        return value, "沒有「號」，可能沒讀完整"
    return value, warning


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


def _one_off(value, options):
    """長度一樣、只差一個字的候選。剛好一個就回它，不只一個就不猜。

    段名多半只有兩三個字，錯一個字相似度就掉到 0.5，過不了一般的門檻 ——
    「圍際」對「國際」就是這樣。但如果整份清單裡只有這一個長得這麼像，
    它幾乎不會是別的東西。

    不只一個就一定要標起來：鶯歌有大湖、東湖、中湖、西湖、三湖五個段，
    讀到「犬湖」的時候五個都只差一個字，挑哪一個都是猜。
    """
    hits = [option for option in options
            if len(option) == len(value)
            and sum(1 for a, b in zip(value, option) if a != b) == 1]
    return hits[0] if len(hits) == 1 else None


def section(text, known=None):
    """地段名。

    段名沒有任何格式規則 —— 讀成「圍際」而不是「國際」，程式自己看不出來，
    以前就這樣原封不動寫進輸出檔，一個字都不標記。唯一的辦法是比對清單。

    清單是空的時候照讀出來的寫（不然一開始每一件都會被擋下來），
    但那時候這一欄本來就會因為信心偏低被標起來。
    """
    from . import lexicon

    value = to_halfwidth(text or "").strip().replace(" ", "")
    if not value:
        return value, "段名是空的"
    table = lexicon.section_aliases(known)
    if not table:
        # 沒有清單就沒有東西可以驗。照讀出來的寫，不要假裝驗過了。
        return value, None
    if value in table:
        return table[value], None
    trimmed = value.rstrip("段")
    if trimmed in table:
        return table[trimmed], None

    options = list(table)
    best, _score = lexicon.choose(value, options)
    if best:
        return table[best], None
    for candidate in (value, trimmed):
        best = _one_off(candidate, options)
        if best:
            return table[best], None
    return value, "段名不在清單裡（讀到「%s」）" % value


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
    if kind == "section":
        return section(text, known=kwargs.get("known"))
    if kind == "land_number":
        return land_number(text)
    validator = VALIDATORS.get(kind)
    if validator:
        return validator(text)
    return (text or "").strip(), None

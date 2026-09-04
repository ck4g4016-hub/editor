# -*- coding: utf-8 -*-
"""路街名字典。

地址是三個必要欄位裡最難辨識的一個，但它有個別的欄位沒有的優勢：
**路街名是有限清單**。轄區內就那些路，辨識出來的東西只要吸附到最接近的合法路名，
一大半的錯誤就自動修掉了。

字典同時解決「路還是街」—— 民眾圈選或劃掉常常糊成一團，但字典裡如果只有
「民生路」沒有「民生街」，答案就確定了，不必去猜那個圈。

字典存成純文字檔，一行一個路街名，可以隨時補。
真正完整的清單請從門牌資料或地籍圖資系統匯出後用 tools/import_roads.py 匯入 ——
程式內建的只是從樣本抽出來的種子，不足以涵蓋轄區。
"""

import os
import re

from . import resources

# 路街名的結尾字
SUFFIXES = ("路", "街", "大道", "巷")

# 內建字典的位置。內容見各該檔開頭的說明。
# 路名那份是從郵遞區號網站抄的，**不是**權威清單，一定有漏（實測就漏了
# 鶯歌區的大湖路）。地段那份不一樣，是從地政局易找查的下拉選單直接複製的，
# 那是地政局自己的資料。
BUILTIN = resources.path("data", "roads-三峽-鶯歌.txt")
BUILTIN_SECTIONS = resources.path("data", "sections-三峽-鶯歌.txt")


def _read(path):
    """讀字典檔，回傳 {行政區: [路街名]}。

    「## 區名」以下的路名屬於該區。分區是必要的 ——
    三峽有「仁愛街」、鶯歌有「仁愛路」，合在一起就分不出該用哪一個。
    沒有任何區段標題的檔案，全部收在 ALL 底下。
    """
    groups, current = {}, ALL
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith("##"):
                current = line.lstrip("#").strip()
                groups.setdefault(current, [])
            elif line and not line.startswith("#"):
                groups.setdefault(current, []).append(line)
    return groups


def builtin():
    """內建的三峽、鶯歌路街名，依區分組。"""
    try:
        return _read(BUILTIN)
    except OSError:
        return {}


def path_for(store):
    return os.path.join(store, "roads.txt")


def sections_path(store):
    return os.path.join(store, "sections.txt")


SECTIONS_HEADER = """\
# 地段名字典。段名沒有任何格式規則 —— 讀成「圍際」還是「國際」，
# 程式自己看不出來，只能靠這份清單。清單是空的時候段名一律照讀出來的寫，
# 錯了不會被標記，所以請把轄區的地段名貼進來。
#
# 最省事的來源是新北市地政局「地政資訊易找查」的「段小段」下拉選單：
#
#     https://www2.land.ntpc.gov.tw/MASP/MASP15_NEW/MASP1501index.jsp
#
# 行政區選三峽區（或鶯歌區），把段小段那個選單裡的項目整個複製貼過來就好。
# **不用先整理。** 下面這幾種寫法都收，混在一起也沒關係：
#
#     (0039) 白雞段白雞小段
#     0039 白雞段白雞小段
#     白雞段白雞小段
#     白雞段
#     <option value="0039">(0039) 白雞段白雞小段</option>
#
# 一行一個，「## 區名」以下的段名屬於該區。

## 三峽區

## 鶯歌區
"""


# 貼進來的可能是網頁原始碼，先把標籤拿掉
_TAGS = re.compile(r"<[^>]*>")
# 開頭的段別代碼：(0039)、（0039）、0039
_CODE = re.compile(r"^\s*[（(]?\s*(\d{3,5})\s*[)）]?[\s:：.、-]*")


def parse_section(line):
    """把一行地段名整理成 (代碼, 名稱)。名稱是空的就回 (None, "")。

    使用者最方便的來源是地政局易找查的下拉選單，而複製下來長什麼樣都有可能：
    選單文字是「(0039) 白雞段白雞小段」，網頁原始碼是
    「<option value="0039">(0039) 白雞段白雞小段</option>」。兩種都收 ——
    要求使用者先自己整理成乾淨清單，等於把工作推回去給他。
    """
    text = _TAGS.sub(" ", line or "").strip()
    if not text or text.startswith("#"):
        return None, ""
    found = _CODE.match(text)
    code = found.group(1) if found else None
    name = text[found.end():].strip() if found else text
    # 原始碼那一行會變成「0039 (0039) 白雞段白雞小段」，代碼要剝兩次
    second = _CODE.match(name)
    if second:
        code = code or second.group(1)
        name = name[second.end():].strip()
    name = name.replace(" ", "").replace("\u3000", "")
    return code, name


def section_aliases(names):
    """把地段清單展開成 {比對用的寫法: 要輸出的名稱}。

    表格上寫的可能是整串「白雞段白雞小段」，也可能只寫「白雞段」或「白雞」。
    三種都要對得到。只寫到段、而那個段底下有好幾個小段的時候，
    輸出就停在段 —— 挑一個小段等於替使用者猜，那正是這個專案不做的事。
    """
    table = {}
    stems = {}
    for raw in names or ():
        _code, name = parse_section(raw)
        if not name:
            continue
        head = name.split("段")[0] + "段" if "段" in name else name
        stems.setdefault(head, set()).add(name)
        table[name] = name
        if "小段" not in name:
            # 有小段的名稱不要去尾。「白雞段白雞小段」去掉尾字是
            # 「白雞段白雞小」，那不是任何人會寫的東西，只會讓模糊比對多一個
            # 亂七八糟的對象。段名本身（白雞段）另外由下面的 stems 補上。
            table[name.rstrip("段")] = name
    for head, members in stems.items():
        # 同一個段底下不只一個小段，就讓「白雞段」對到「白雞段」本身
        value = next(iter(members)) if len(members) == 1 else head
        table.setdefault(head, value)
        table.setdefault(head.rstrip("段"), value)
    return table


def builtin_sections():
    """內建的三峽、鶯歌地段名，依區分組。"""
    try:
        return _read(BUILTIN_SECTIONS)
    except OSError:
        return {}


def load_sections(store):
    """載入地段名字典。樣板資料夾裡有自己的就用那份，否則用內建的。"""
    target = sections_path(store)
    if os.path.isfile(target):
        groups = _read(target)
        if any(groups.values()):
            return groups
    return builtin_sections()


def ensure_roads(store):
    """路名字典不存在就把內建那份複製一份到樣板資料夾，讓人可以直接改。

    內建那份只是從樣本抽出來的種子，一定不夠 —— 使用者實測就遇到
    鶯歌區的「大湖路」不在裡面。字典缺一條路，那一件的門牌就會被標起來，
    所以一定要讓人補得進去，而且要補在資料夾裡、重新解壓縮不會被蓋掉的地方。
    """
    target = path_for(store)
    if not os.path.isfile(target):
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        try:
            with open(BUILTIN, encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            content = "# 路街名字典。一行一個，「## 區名」以下的路名屬於該區。\n"
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(content)
    return target


def ensure_sections(store):
    """地段字典不存在就把內建那份複製一份到樣板資料夾，讓人可以直接改。"""
    target = sections_path(store)
    if not os.path.isfile(target):
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        try:
            with open(BUILTIN_SECTIONS, encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            content = SECTIONS_HEADER
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(content)
    return target


ALL = "全部"


def load(store):
    """載入路街名字典。樣板資料夾裡有自己的就用那份，否則用內建的。"""
    target = path_for(store)
    if os.path.isfile(target):
        groups = _read(target)
        if any(groups.values()):
            return groups
    return builtin()


def for_district(groups, district=None):
    """取某一區的路名。沒指定或那一區沒資料，就把全部合起來。"""
    if not groups:
        return []
    if district:
        for name, names in groups.items():
            if name == district or name.rstrip("區") == district.rstrip("區"):
                return names
    merged = []
    for names in groups.values():
        merged.extend(names)
    return merged


def save(store, groups):
    """存檔。groups 是 {行政區: [路街名]}。"""
    target = path_for(store)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    total = 0
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("# 路街名字典。地址辨識會把結果吸附到這裡最接近的名稱。\n")
        handle.write("# 「## 區名」以下的路名屬於該區 —— 不同區可能有同名不同尾的路，\n")
        handle.write("# 例如三峽的「仁愛街」與鶯歌的「仁愛路」。\n")
        for district in sorted(groups):
            unique = sorted({n.strip() for n in groups[district] if n.strip()})
            if not unique:
                continue
            handle.write("\n## %s\n" % district)
            for name in unique:
                handle.write(name + "\n")
            total += len(unique)
    return target, total


def _similarity(a, b):
    """兩個字串有多像。用共同字元數除以較長者的長度，對辨識錯字很寬容。"""
    if not a or not b:
        return 0.0
    common = 0
    remaining = list(b)
    for char in a:
        if char in remaining:
            remaining.remove(char)
            common += 1
    return common / max(len(a), len(b))


def stem(name):
    """路街名的主體：去掉結尾的路／街／道／大道。"""
    return re.sub(r"(大道|[路街道])$", "", name or "")


def _score(candidate, name):
    """兩個路名有多像。比的是**主體**，結尾那個字完全不算分。

    尾字不可信也不該加分。紙本上的「路」「街」是印刷的，民眾只是圈起來，
    減掉版面之後剩下的是一個圈。讓尾字算分的話：

        「大湖路」對「東湖路」 —— 共同的「湖」「路」佔三分之二 = 0.67

    超過門檻，於是門牌被換成鶯歌區真的存在的另一條路「東湖路」，
    驗證還會放行，輸出表上完全看不出來。這種錯比讀不出來危險得多。
    只比主體的話是「大湖」對「東湖」= 0.5，擋得下來，交給人去看。
    """
    return _similarity(stem(candidate), stem(name))


def match(text, names, threshold=0.6):
    """把一段文字吸附到最接近的路街名。

    回傳 (路街名, 相似度)。找不到夠像的就回傳 (None, 最高相似度)。
    """
    if not text:
        return None, 0.0
    best, score = None, 0.0
    for name in names:
        value = _similarity(text, name)
        if value > score:
            best, score = name, value
    return (best, score) if score >= threshold else (None, score)


# 整串拿去比的門檻
THRESHOLD = 0.6

# 砍掉開頭幾個字之後才對上的，門檻高得多 —— 砍掉的字有可能是路名本身。
TRIM_THRESHOLD = 0.85

# 第一名要贏第二名這麼多才算數。兩條路一樣像的時候猜一個，
# 等於有一半的機率把門牌靜靜寫成別人家的。寧可標起來讓人看。
MARGIN = 0.08

# 黏在路名前面的行政區
_LEAD = re.compile(r"^.{1,4}?[縣市區鄉鎮村里]")


def _rank(candidate, names):
    """跟字典比一輪，回傳 (最像的, 分數, 第二像的分數, 同分的全部)。"""
    best, score, second = None, 0.0, 0.0
    for name in names:
        value = _score(candidate, name)
        if value > score:
            best, score, second = name, value, score
        elif value > second and name != best:
            second = value
    tied = [name for name in names if _score(candidate, name) >= score] if best else []
    return best, score, second, tied


def _leads(core):
    """候選：整串，以及把前面黏著的行政區一層層剝掉之後的樣子。"""
    out = [core]
    current = core
    for _ in range(3):
        found = _LEAD.match(current)
        if not found or len(current) - found.end() < 2:
            break
        current = current[found.end():]
        out.append(current)
    return out


def choose(text, names, threshold=THRESHOLD, margin=MARGIN):
    """從清單裡挑一個最像的。分不出來就回 None —— **不猜**。

    跟 resolve_head 同一套規矩：第一名要贏第二名 margin 以上才算數。
    兩個一樣像的時候猜一個，等於有一半的機率靜靜寫錯，而且從輸出表上看不出來。
    """
    if not text or not names:
        return None, 0.0
    best, score, second = None, 0.0, 0.0
    for name in names:
        value = _similarity(text, name)
        if value > score:
            best, score, second = name, value, score
        elif value > second and name != best:
            second = value
    if best is not None and score >= threshold and score - second >= margin:
        return best, score
    return None, score


def resolve_head(head, names, threshold=THRESHOLD):
    """從地址開頭那段文字裡認出路街名。回傳 (路街名, 相似度)。"""
    name, score, _note = resolve_head_full(head, names, threshold)
    return name, score


def resolve_head_full(head, names, threshold=THRESHOLD):
    """同 resolve_head，但多回傳一句要給人看的說明（沒有就是 None）。

    這裡不要求「路」「街」那個字有被讀出來 —— 紙本表格上那個字是印刷的，
    民眾只是圈起來或劃掉，減掉版面之後根本不會留下字，
    留下的是個圈（辨識成 Q、C、〇 之類）。所以拿去比對的是路名的**主體**，
    對到字典裡唯一的那條路，「路」還是「街」也就跟著確定了。

    對不上就回傳 None，讓上層把這一欄標起來 —— **絕對不猜**。
    這一欄猜錯的代價是 RPA 拿著別人家的門牌去查調，而且從輸出表上看不出來。

    回傳 (路街名, 相似度)。
    """
    if not head or not names:
        return None, 0.0, None
    # 只留中文字去比對，把圈和雜訊丟掉
    core = "".join(ch for ch in head if "\u4e00" <= ch <= "\u9fff")
    if len(core) < 2:
        return None, 0.0, None

    # 表格上讀到的結尾字。平常不採信（那是印刷的，民眾只是圈起來），
    # 但字典裡兩條路主體一模一樣時，它是唯一剩下的線索。
    read_suffix = core[-1] if core[-1] in "路街道" else None

    highest, blocked = 0.0, None
    for index, candidate in enumerate(_leads(core)):
        # 第一個候選是原文，後面的是砍掉行政區之後的，門檻要高很多
        bar = threshold if index == 0 else max(threshold, TRIM_THRESHOLD)
        best, score, second, tied = _rank(candidate, names)
        highest = max(highest, score)
        if best is None or score < bar:
            continue
        if score - second >= MARGIN:
            return best, score, None
        if len(tied) > 1:
            # 主體一樣、只差路或街。這兩區裡只有鶯歌的「東湖街」與「東湖路」
            # 是這種情形。讀到的那個字是唯一線索，用它 —— 但一定要講出來，
            # 因為那個字本來就不可信。
            picks = [name for name in tied if read_suffix and name.endswith(read_suffix)]
            if len(picks) == 1:
                return picks[0], score, (
                    "字典裡「%s」都有，這裡是照表格上讀到的「%s」決定的，請確認"
                    % ("」「".join(sorted(tied)), read_suffix))
            blocked = blocked or (
                "分不出是「%s」裡的哪一條" % "」「".join(sorted(tied)))

    if blocked is None:
        # 只錯一個字、而且整份字典裡只有一個長得這麼像的，就修掉。
        #
        # 兩個字的路名主體錯一個字，相似度只有 0.5，過不了門檻。
        # 承辦人指出「鳳」特別容易讀錯，而鶯歌的鳳一路、鳳三路、鳳五路、
        # 鳳七路都以它開頭 —— 錯的是第一個字時，剩下的字已經把答案鎖死了。
        # 實測「鳯一路」「凰七路」現在都對得回去。
        #
        # 不只一個候選就一定標起來，這一半才是重點：讀到的字剛好落在
        # 「一三五七」那一位時，四條路都只差一個字，挑一個就是猜。
        trimmed = stem(core)
        hits = []
        for name in names:
            key = stem(name)
            if len(key) != len(trimmed) or key == trimmed:
                continue
            if sum(1 for a, b in zip(trimmed, key) if a != b) == 1:
                hits.append(name)
        if len(hits) == 1:
            # **一定要標記。** 一字之差是「猜得有根據」，不是「讀出來的」——
            # 字典裡根本沒有那條路的時候，它照樣會找到一個一字之差的鄰居：
            # 實測「幸福路」→「鳳福路」、「光華路」→「國華路」、
            # 「秀山街」→「秀川街」，三個都是字典裡沒有的路被換成有的。
            # 值照樣給（多半是對的，省得人重打），但絕不能靜靜地過。
            return hits[0], highest, (
                "路名有一個字跟「%s」對不上，這是猜的，請對著原圖確認"
                % hits[0])
        if len(hits) > 1 and read_suffix:
            # 還是有好幾個，但讀到的結尾字只對得上其中一個。
            # 例如「鳯一路」一字之差的有「鳳一路」與「甲一街」——
            # 承辦人說得對：只有「鳳」會有一、三、五、七路，而甲一是「街」。
            # 尾字平常不採信，但這時候它是唯一剩下的線索，用它、然後講出來。
            picks = [name for name in hits if name.endswith(read_suffix)]
            if len(picks) == 1:
                return picks[0], highest, (
                    "路名有一個字沒讀準，「%s」都只差一個字，"
                    "這裡是照表格上讀到的「%s」決定的，請確認"
                    % ("」「".join(sorted(hits)), read_suffix))
    return None, highest, blocked

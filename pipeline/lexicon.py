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

# 內建字典的位置。內容見該檔開頭的說明 —— 它不是政府開放資料的權威清單。
BUILTIN = resources.path("data", "roads-三峽-鶯歌.txt")


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


def resolve_head(head, names, threshold=0.6):
    """從地址開頭那段文字裡認出路街名。

    這裡不要求「路」「街」那個字有被讀出來 —— 紙本表格上那個字是印刷的，
    民眾只是圈起來或劃掉，減掉版面之後根本不會留下字，
    留下的是個圈（辨識成 Q、C、〇 之類）。所以拿去比對的是路名的**前半**，
    對到字典裡唯一的那條路，「路」還是「街」也就跟著確定了。

    回傳 (路街名, 相似度)。
    """
    if not head or not names:
        return None, 0.0
    # 只留中文字去比對，把圈和雜訊丟掉
    core = "".join(ch for ch in head if "\u4e00" <= ch <= "\u9fff")
    if not core:
        return None, 0.0

    best, score = None, 0.0
    # 從尾端往前取候選 —— 地址前面可能還黏著行政區
    for start in range(len(core)):
        candidate = core[start:]
        if len(candidate) < 2:
            break
        # 候選也要去掉尾巴的路／街／道再比一次。
        # 「路」還是「街」是印刷的，民眾只是圈起來，讀出來的那個字不可信 ——
        # 拿「中華街」整串去比，會比到「中園街」（都以街結尾，尾字加分），
        # 而那是另一條路。去掉尾字之後比的是「中華」，才對得到中華路。
        trimmed = re.sub(r"[路街道]$", "", candidate)
        for name in names:
            stem = re.sub(r"[路街道]$", "", name)
            value = max(_similarity(candidate, name), _similarity(candidate, stem),
                        _similarity(trimmed, stem))
            if value > score:
                best, score = name, value
    return (best, score) if score >= threshold else (None, score)

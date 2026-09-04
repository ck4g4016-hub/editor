# -*- coding: utf-8 -*-
"""把一整頁辨識成「有位置的文字行」，給不靠框選的欄位用。

電腦產製的表格（A 表的稅務入口網列印）跟手寫申請書不一樣：欄位會隨資料
多寡上下移動。承辦人的原話是「這種表格會隨著資料的多寡來自行增減表格欄位」。
固定框對這種表格沒有用 —— 多一筆其他土地坐落，底下每一列就整個往下推。

但它有手寫表格沒有的優勢：**標籤是印刷的，而且每一份都一模一樣。**
「土地所有權人名稱」「地段」「房屋坐落」這些字每次都在，辨識印刷字的
信心實測 0.9 以上。所以改成先找標籤，再照版面關係去拿它的值。
"""

import re

import cv2
import numpy as np

from . import recognise, render, validate

# 全頁掃描的解析度。實測 150 與 300 對印刷標籤的辨識結果一樣，150 快一倍。
SCAN_DPI = 150


class Line:
    """一行辨識結果，連同它在頁面上的位置。

    文字一進來就轉繁體。辨識模型的字典同時收簡繁兩種字形，實測這一頁上
    「統一編號」讀成「统一编號」、「鄉鎮別」讀成「鄉鎮别」、「地號」讀成
    「地号」—— 不先轉的話，標籤永遠對不上，而那是整個機制的地基。
    """

    def __init__(self, text, box, score):
        self.text = validate.to_traditional(text or "")
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        self.left, self.right = min(xs), max(xs)
        self.top, self.bottom = min(ys), max(ys)
        self.score = float(score)

    @property
    def middle(self):
        return (self.top + self.bottom) / 2.0

    def __repr__(self):
        return "Line(%r, x=%d..%d, y=%d..%d)" % (
            self.text, self.left, self.right, self.top, self.bottom)


# 表格橫線至少要多長（佔頁寬的比例）才算是格線，不是底線或雜訊
RULE_WIDTH = 0.35

# 兩條線靠得比這個近就當成同一條（掃描件的線有厚度，也會斷斷續續）
RULE_MERGE = 12


def horizontal_rules(image):
    """找出表格的橫線，回傳由上而下的 y 座標。

    儲存格的上下界要用表格自己的線，不能靠標籤的位置去猜。
    A 表的「土地所有權人、配偶或直系親屬戶籍」那一格有七行，標籤是**垂直置中**
    的，所以「下一個標籤的上緣」比這一格的第一行值還低 —— 用標籤推邊界會把
    整格別人的內容吃進上一列（實測身分證欄讀成「F225136849郵遞區號：239縣市…」）。
    格線沒有這個問題，它就是儲存格的真正邊界。
    """
    if image is None or image.size == 0:
        return []
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, 41, 15)
    width = gray.shape[1]
    length = max(int(width * RULE_WIDTH), 40)
    opened = cv2.morphologyEx(dark, cv2.MORPH_OPEN,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1)))
    rows = np.flatnonzero(opened.sum(axis=1) > 0)
    if rows.size == 0:
        return []
    out = []
    for y in rows:
        if out and y - out[-1] <= RULE_MERGE:
            out[-1] = int(y)
        else:
            out.append(int(y))
    return out


def band_of(rules, y):
    """y 落在哪兩條格線之間。回傳 (上界, 下界)，沒有格線就 (None, None)。"""
    above = [rule for rule in rules if rule <= y]
    below = [rule for rule in rules if rule > y]
    if not above or not below:
        return None, None
    return max(above), min(below)


def read_page(source, index, rotation=0, dpi=SCAN_DPI):
    """辨識一整頁，回傳 (行清單, 表格橫線的 y 座標)。"""
    image = render.rotate(
        render.render(source, index, dpi=dpi, gray=False), rotation or 0)
    return read_image(image), horizontal_rules(image)


def read_image(image):
    try:
        result = recognise.engine()(image, use_det=True, use_cls=False, use_rec=True)
    except Exception:                                               # noqa: BLE001
        return []
    rows = result[0] if isinstance(result, tuple) else result
    lines = [Line(text, box, score) for box, text, score in (rows or [])]
    lines.sort(key=lambda line: (line.top, line.left))
    return lines


# 「地段：鳳福」這種同一行就帶著值的寫法
_SEPARATOR = "：:︰"


def _after_label(text, label):
    """同一行裡標籤後面的值。沒有值就回 None。"""
    at = text.find(label)
    if at < 0:
        return None
    rest = text[at + len(label):].lstrip()
    while rest and rest[0] in _SEPARATOR:
        rest = rest[1:].lstrip()
    return rest or None


# 標籤要讀對幾成才算數。印刷字的辨識信心實測 0.9 以上，但偶爾會掉字
# （「土地所有權人名稱」讀成「土地所有權人名」），所以不能要求一字不差。
LABEL_THRESHOLD = 0.8


def _overlap(text, label):
    """text 裡有多少比例的標籤字元出現了。"""
    if not label:
        return 0.0
    remaining = list(text)
    hit = 0
    for char in label:
        if char in remaining:
            remaining.remove(char)
            hit += 1
    return hit / len(label)


def _similar(text, label):
    """這一行有多像是這個標籤，回傳 (等級, 相似度)。

    等級 3  整行就是標籤（表格左欄的標籤格）
    等級 2  開頭是標籤（「地段：鳳福」這種同一行帶值的）
    等級 1  標籤出現在行中間 —— 最不可信，A 表上「其他土地坐落：…533地號
            面積…」也含「地號」，只看有沒有出現會挑到它
    """
    stripped = text.strip().rstrip(_SEPARATOR).strip()
    whole = _overlap(stripped, label)
    if whole >= LABEL_THRESHOLD and len(stripped) <= len(label) + 1:
        return 3, whole
    head = _overlap(text.strip()[:len(label) + 1], label)
    if head >= LABEL_THRESHOLD:
        return 2, head
    inside = _overlap(text, label)
    if inside >= LABEL_THRESHOLD:
        return 1, inside
    return 0, 0.0


def find_label(lines, label):
    """找出標籤那一行。回傳 (Line, 提醒)，找不到就 (None, 原因)。

    以「整行就是標籤」為最優先、「開頭是標籤」次之。這一點是必要的：
    A 表上「地號：532」跟「其他土地坐落：…533地號面積:61.28平方公尺」
    兩行都含「地號」，只看有沒有出現的話會挑錯。
    """
    ranked = []
    for line in lines:
        level, score = _similar(line.text, label)
        if level:
            ranked.append((level, score, line))
    if not ranked:
        return None, "整頁上找不到「%s」這個標籤" % label
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = ranked[0]
    same = [item for item in ranked
            if item[0] == best[0] and abs(item[1] - best[1]) < 1e-9]
    if len(same) > 1:
        return None, "整頁上有 %d 行都像「%s」，分不出是哪一行" % (len(same), label)
    return best[2], None


def value_lines(lines, label_line, rules=()):
    """標籤那一格右邊的內容，回傳 [Line]。

    一格的值可能有好幾行（A 表的「房屋坐落」是地址一行加建號一行）。
    一格到哪裡結束，用**表格自己的橫線**決定；沒有橫線可用時才退回
    「左欄的下一個標籤」這個比較粗的辦法。
    """
    margin = (label_line.right - label_line.left) * 0.5
    top, bottom = band_of(rules or (), label_line.middle)
    if top is not None:
        picked = [line for line in lines
                  if line is not label_line
                  and line.left > label_line.right - margin
                  and top < line.middle < bottom]
        return _same_column(picked)

    # 左欄＝開頭比標籤更靠左或差不多的那些行
    left_column = [line for line in lines
                   if line.left <= label_line.left + margin and line is not label_line]
    below = [line.top for line in left_column if line.top > label_line.top + 5]
    limit = min(below) if below else None

    out = []
    for line in lines:
        if line is label_line or line.left <= label_line.right - margin:
            continue
        if line.bottom <= label_line.top:
            continue
        # 用**中線**判斷屬於哪一列，不是用上緣。實測 A 表的「房屋坐落」那一格
        # 底下就差 6 個像素，用上緣會把下一列的「自住稅率」一起吃進來。
        if limit is not None and line.middle >= limit:
            continue
        out.append(line)
    return _same_column(out)


def _same_column(picked):
    """只留「值」那一欄。

    同一列的最右邊還可能有別的東西 —— A 表的收文戳就跟「申請房屋使用情形」
    同一個高度，不擋的話會讀成「…自住稅率115/08/18機關收文」。
    """
    if not picked:
        return []
    column = min(line.left for line in picked)
    width = max(line.right - line.left for line in picked)
    picked = [line for line in picked if line.left <= column + max(width, 200)]
    picked.sort(key=lambda line: (line.top, line.left))
    return picked


def value_for(lines, label, drop_digits=False, rules=()):
    """找出標籤對應的值。回傳 (文字, 信心, 提醒)。

    drop_digits  整行都是數字的那幾行丟掉。A 表的「房屋坐落」那一格裡，
                 地址底下還有一行房屋建號，承辦人說那個不要。
    """
    line, note = find_label(lines, label)
    if line is None:
        return "", 0.0, note

    inline = _after_label(line.text, label)
    if inline:
        return inline, line.score, None

    picked = value_lines(lines, line, rules)
    if drop_digits:
        picked = [item for item in picked
                  if not re.fullmatch(r"[\d\s,.\-]+", item.text.strip())]
    if not picked:
        return "", 0.0, "找到「%s」，但它右邊沒有東西" % label
    text = "".join(item.text.strip() for item in picked)
    return text, min(item.score for item in picked), None

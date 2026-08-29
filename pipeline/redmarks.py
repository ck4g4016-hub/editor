# -*- coding: utf-8 -*-
"""找出承辦人用紅筆做的標記。

樣本上承辦人用紅筆標出了每一種表格要抓哪些欄位，有兩種用法：

    勾號  標在欄位旁邊，指「這一欄要」。只給位置，沒有範圍
    紅框  把一塊區域圈起來並寫字（例如右下角框住收件章寫「公文」）。連範圍一起給了

這些標記只出現在建樣板用的樣本上，正式作業的掃描件不會有。
拿它們自動抓出候選欄位，樣板建立就從「一格一格拉框」變成「確認程式抓的對不對」。

**不能只靠顏色**：兩種地價稅申請書印在粉紅色紙上，框線與說明文字本身就是紅的，
整張紙都偏紅。做法是先跟底圖比對 —— 紅筆的紅出現在掃描件上、底圖上沒有，
把底圖的紅度減掉，剩下的才是紅筆。
"""

import cv2
import numpy as np

from . import baseimage

# 紅度要比底圖高出多少才算紅筆
REDNESS_DELTA = 40

# 太小的視為雜訊；太大的多半是整片色偏而不是筆跡
MIN_AREA = 120
MAX_AREA_RATIO = 0.02

# 表格本身印著紅色框線與說明文字。底圖對位差個幾像素，這些印刷紅的邊緣就會殘留，
# 看起來像細細的紅線。用侵蝕把細的濾掉，夠粗的筆畫留下。
#
# 實測各表格最粗紅筆筆畫的半徑：F 4.8px、G 5.5px、D 3.3px、C 3.3px（300dpi）。
# 侵蝕一圈需要半徑大於 2px 才活得下來，四種都過得了；侵蝕兩圈要大於 4px，
# 影印本的 C、D 會整個消失 —— 所以只能一圈。
STROKE_EROSION = 1

# 一筆一劃常被切成好幾塊（框的四邊、勾的兩劃）。先撐開讓它們連起來再切元件，
# 不然一個「文号」框會變成四個候選。
MERGE_RADIUS = 24

# 紅框與紅色印章都是大面積，靠填充率分開：
# 手寫的框是空心的（筆畫佔外接矩形的比例低），印章是實心的
MAX_FILL_RATIO = 0.45

CHECK = "check"   # 勾號：指位置
BOX = "box"       # 框：連範圍一起指定了


def redness(bgr):
    """每個像素有多紅。紅色通道減掉綠藍兩通道的平均。"""
    blue, green, red = cv2.split(bgr.astype(np.int16))
    return red - (green + blue) // 2


def find(scan_bgr, base_bgr=None):
    """找出紅筆標記，回傳 [(kind, x, y, w, h), ...]。

    base_bgr 是同一種表格的底圖（彩色）。粉紅色紙的表格一定要給，
    否則整張紙都會被當成紅筆。
    """
    scan_red = redness(scan_bgr)

    if base_bgr is not None:
        # 要逐像素比紅度，所以得把彩色底圖整張搬到掃描件的座標系
        if base_bgr.ndim != 3:
            raise ValueError("底圖必須是彩色的 —— 灰階底圖沒有紅度資訊，"
                             "粉紅色紙的表格會把整張紙都判成紅筆")
        base_warped, _ = baseimage.align(base_bgr, scan_bgr)
        if base_warped is None:
            raise ValueError("底圖對不上這張掃描件")
        delta = scan_red - redness(base_warped)
    else:
        delta = scan_red

    mask = (delta > REDNESS_DELTA).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    # 只留下夠粗的筆畫，濾掉印刷紅的對位殘影
    seeds = cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=STROKE_EROSION)
    if not seeds.any():
        return []
    survivors = cv2.dilate(seeds, np.ones((3, 3), np.uint8),
                           iterations=STROKE_EROSION + 1)
    mask = cv2.bitwise_and(mask, survivors)

    # 把同一個標記的碎片接起來，再切元件
    merged = cv2.dilate(mask, np.ones((MERGE_RADIUS, MERGE_RADIUS), np.uint8))

    page_area = scan_bgr.shape[0] * scan_bgr.shape[1]
    count, labels, _, _ = cv2.connectedComponentsWithStats(merged, 8)
    marks = []
    for index in range(1, count):
        # 範圍取自撐開前的實際筆畫，不是撐開後的
        strokes = cv2.bitwise_and(mask, (labels == index).astype(np.uint8) * 255)
        ys, xs = np.nonzero(strokes)
        if len(xs) < MIN_AREA:
            continue
        x, y = int(xs.min()), int(ys.min())
        w, h = int(xs.max() - x + 1), int(ys.max() - y + 1)
        area = len(xs)
        if area > page_area * MAX_AREA_RATIO:
            continue
        fill = area / float(w * h)
        # 框是空心的、而且有一定大小；其餘視為勾號
        kind = BOX if (fill < MAX_FILL_RATIO and w > 60 and h > 30) else CHECK
        marks.append((kind, x, y, w, h))

    marks = _merge_overlapping(marks)
    marks.sort(key=lambda m: (m[2], m[1]))
    return marks


def _merge_overlapping(marks, gap=12):
    """外接框互相重疊（或只差一點點）的就併成一個。

    手繪的框在影印本上筆畫常常斷開，碎片之間可能離得比合併半徑還遠，
    但它們的外接框一定是彼此重疊或包含的 —— 因為它們本來就圍著同一塊區域。
    單純加大合併半徑會把相鄰的勾號也黏在一起，用重疊判斷精準得多。
    """
    boxes = [[x, y, x + w, y + h] for _, x, y, w, h in marks]
    kinds = [kind for kind, *_ in marks]

    changed = True
    while changed:
        changed = False
        for i in range(len(boxes)):
            if boxes[i] is None:
                continue
            for j in range(i + 1, len(boxes)):
                if boxes[j] is None:
                    continue
                a, b = boxes[i], boxes[j]
                if (a[0] - gap < b[2] and b[0] - gap < a[2]
                        and a[1] - gap < b[3] and b[1] - gap < a[3]):
                    boxes[i] = [min(a[0], b[0]), min(a[1], b[1]),
                                max(a[2], b[2]), max(a[3], b[3])]
                    # 併起來之後多半是個框，不是勾
                    kinds[i] = BOX
                    boxes[j] = None
                    changed = True

    return [(kinds[i], b[0], b[1], b[2] - b[0], b[3] - b[1])
            for i, b in enumerate(boxes) if b is not None]


def draw(scan_bgr, marks):
    """把找到的標記畫出來，供肉眼確認。"""
    canvas = scan_bgr.copy()
    for kind, x, y, w, h in marks:
        colour = (255, 0, 0) if kind == BOX else (0, 160, 0)
        cv2.rectangle(canvas, (x, y), (x + w, y + h), colour, 4)
    return canvas

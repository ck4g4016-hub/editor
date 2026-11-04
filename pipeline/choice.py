# -*- coding: utf-8 -*-
"""二選一的欄位：民眾用圈選或劃掉來挑「路」還是「街」。

紙本表格上「路街」兩個字是印在表格上的，民眾把要的那個圈起來，
或是把不要的那個劃掉。這種欄位不要用 OCR ——
印刷字減掉之後只剩民眾畫的痕跡，那本來就不是字，
是「圈」還是「線」用形狀判斷比辨識可靠得多。

判斷方式是**凸包面積除以筆畫面積**：
圈是一條細環套住一大塊空白，比值高；線是實心的，填滿自己的外框，比值低。
再看長寬比輔助 —— 圈接近正方形，線是細長的。

實測（F 表，300dpi）：

    圈選      凸包/筆畫 4.0、4.9    長寬比 1.01、0.99
    手寫數字  凸包/筆畫 1.0 ~ 3.1
"""

import cv2
import numpy as np

# 凸包面積是筆畫面積的幾倍才算是圈
RING_RATIO = 3.5

# 圈要接近正方形。低於這個或高於它的倒數，就當作是線
RING_ASPECT = 0.6

# 筆跡太小就不猜，交給人 —— 實測有一份的標記只有 146 像素，判不出來
MIN_AREA = 250


def detect(sheet, region, options):
    """判斷選了哪一個。

    sheet    減掉印刷版面之後的影像（白底黑字），底圖座標系
    region   整個選項區塊 (x, y, w, h)，要把所有選項都包進來
    options  [(標籤, 中心x), ...]，中心 x 是印刷字在底圖上的位置

    回傳 (選中的標籤, 說明)。判不出來時標籤是 None。
    """
    x, y, w, h = region
    crop = (sheet[y:y + h, x:x + w] < 128).astype(np.uint8) * 255
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(crop, 8)
    if count <= 1:
        return None, "沒有圈選也沒有劃掉"

    index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    area = int(stats[index, cv2.CC_STAT_AREA])
    if area < MIN_AREA:
        return None, "標記太小（%d 像素），判不出是圈還是劃" % area

    blob = (labels == index).astype(np.uint8) * 255
    contours, _ = cv2.findContours(blob, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None, "找不到筆跡輪廓"

    hull = cv2.contourArea(cv2.convexHull(max(contours, key=cv2.contourArea)))
    ratio = hull / max(area, 1)
    width = int(stats[index, cv2.CC_STAT_WIDTH])
    height = int(stats[index, cv2.CC_STAT_HEIGHT])
    aspect = width / max(height, 1)

    centre = x + centroids[index][0]
    nearest = min(options, key=lambda option: abs(centre - option[1]))[0]
    others = [label for label, _ in options if label != nearest]

    if ratio >= RING_RATIO and RING_ASPECT <= aspect <= 1 / RING_ASPECT:
        return nearest, "圈選「%s」（凸包比 %.1f）" % (nearest, ratio)
    if len(options) == 2:
        return others[0], "劃掉「%s」，所以選「%s」（凸包比 %.1f）" % (nearest, others[0], ratio)
    return None, "有標記但判不出是圈還是劃（凸包比 %.1f、長寬比 %.2f）" % (ratio, aspect)

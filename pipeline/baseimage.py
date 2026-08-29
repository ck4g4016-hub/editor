# -*- coding: utf-8 -*-
"""做出「只有印刷版面、沒有手寫內容」的底圖，用來把版面從掃描件上減掉。

減掉版面之後剩下的就是手寫內容，欄位裁切與辨識都容易得多。
粉紅色紙的兩種表格尤其非這樣做不可 —— 它們的框線與說明文字本身就是紅色，
沒辦法靠色彩把紅筆與手寫分離出來。

底圖有兩種來源：

**空白原稿**（F、G 這種自己印的表格）
    直接拿空白表格的掃描件當底圖。

**多份掃描件合成**（C、D 這種由其他單位送來的影印本）
    影印會帶來 1~3% 的縮放漂移與非線性變形，而且逐次累積，
    所以原版空白表格永遠對不上影印件 —— 換一份原稿也沒用。
    改成拿同一種表格的多份掃描件對齊後逐像素取中位數：
    手寫每份都不同會被濾掉，印刷每份都相同會留下來，
    而且合成出來的底圖自帶影印件的變形特性。

以 D 表四份正面做留一驗證（合成時排除待測那份），四折結果一致：
    原版空白原稿當底圖    覆蓋率 0.548 ~ 0.562
    影印件中位數合成底圖  覆蓋率 0.991 ~ 0.993
"""

import cv2
import numpy as np

# 合成底圖至少要幾份。太少的話某一份的手寫可能在中位數裡存活下來。
MIN_SAMPLES = 3

# 對位品質的合格線：底圖的印刷筆畫，有多少比例落在掃描件的墨跡上
GOOD_COVERAGE = 0.90
USABLE_COVERAGE = 0.75

_ORB = cv2.ORB_create(20000)
_MATCHER = cv2.BFMatcher(cv2.NORM_HAMMING)


def ink_mask(gray):
    """抽出墨跡。用自適應二值化，才不會被紙張底色與掃描明暗影響。"""
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 41, 15)


def align(source, target):
    """把 source 對齊到 target 的座標系，回傳 (對齊後的影像, 內點數)。"""
    kp_source, desc_source = _ORB.detectAndCompute(source, None)
    kp_target, desc_target = _ORB.detectAndCompute(target, None)
    if desc_source is None or desc_target is None:
        return None, 0

    pairs = _MATCHER.knnMatch(desc_source, desc_target, k=2)
    good = [m for m, n in pairs if m.distance < 0.75 * n.distance]
    if len(good) < 20:
        return None, 0

    src = np.float32([kp_source[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_target[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    matrix, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if matrix is None or mask is None:
        return None, 0

    aligned = cv2.warpPerspective(source, matrix, (target.shape[1], target.shape[0]),
                                  borderValue=255)
    return aligned, int(mask.sum())


def coverage(base, scan):
    """底圖的印刷筆畫，有多少比例落在掃描件的墨跡上。

    這是對位品質最直接的指標：對得越準，比例越高。
    掃描件上的手寫只會讓分母以外的地方多出墨跡，不影響這個比值。
    """
    printed = ink_mask(base)
    scanned = cv2.dilate(ink_mask(scan), np.ones((3, 3), np.uint8))
    hit = cv2.bitwise_and(printed, scanned)
    return float((hit > 0).sum()) / max(int((printed > 0).sum()), 1)


def compose(samples, reference=None):
    """把多份同款掃描件合成一張只剩印刷版面的底圖。

    samples    同一種表格、同一個版本的灰階掃描件
    reference  以哪一份的座標系為準；省略就用第一份

    取中位數而不是平均：平均會把手寫淡淡地留在底圖上，中位數則是
    只要超過一半的樣本在該點沒有墨跡，該點就是白的。
    """
    if len(samples) < MIN_SAMPLES:
        raise ValueError("合成底圖至少要 %d 份，只收到 %d 份" % (MIN_SAMPLES, len(samples)))

    target = reference if reference is not None else samples[0]
    stack, weak = [], []
    for index, sample in enumerate(samples):
        if sample is target:
            stack.append(sample)
            continue
        aligned, inliers = align(sample, target)
        if aligned is None:
            weak.append(index)
            continue
        stack.append(aligned)

    if len(stack) < MIN_SAMPLES:
        raise ValueError("能對齊的樣本只有 %d 份，不足以合成底圖" % len(stack))

    base = np.median(np.stack(stack), axis=0).astype(np.uint8)
    return base, weak


def subtract(base, scan, delta=35, min_area=30):
    """把底圖從掃描件上減掉，只留下手寫內容。回傳白底黑字的影像。

    用灰階相減而不是「兩張各自二值化後相減」：後者為了吸收對位誤差得把印刷筆畫
    加粗才行，而手寫常常就寫在印刷底線與方格上，加粗會把筆畫一起削掉。
    """
    aligned, _ = align(base, scan)
    if aligned is None:
        raise ValueError("底圖對不上這張掃描件")

    difference = np.clip(aligned.astype(np.int16) - scan.astype(np.int16), 0, 255).astype(np.uint8)
    ink = (difference > delta).astype(np.uint8) * 255
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    # 去掉零星雜點，再把被切斷的筆畫接回來
    count, labels, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    kept = np.zeros_like(ink)
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] >= min_area:
            kept[labels == index] = 255
    kept = cv2.morphologyEx(kept, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    return 255 - kept


def verdict(value):
    """把覆蓋率翻成一句人看得懂的結論。"""
    if value >= GOOD_COVERAGE:
        return "可以直接用"
    if value >= USABLE_COVERAGE:
        return "堪用，欄位裁切範圍要放寬"
    return "不建議使用"

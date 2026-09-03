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


def as_gray(image):
    """彩色就轉灰階，已經是灰階就原樣回傳。

    底圖一律存彩色 —— 粉紅色紙的表格要靠色彩才分得出紅筆與紅色印刷，
    存成灰階的話紅度資訊就沒了。需要灰階的地方在這裡臨時轉。
    """
    return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image


def ink_mask(image):
    """抽出墨跡。用自適應二值化，才不會被紙張底色與掃描明暗影響。"""
    return cv2.adaptiveThreshold(
        as_gray(image), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 41, 15)


# 筆跡比印刷「藍」多少才算數。藍色原子筆 B-R 大約 +40 以上，
# 粉紅紙 -50、紅色印刷 -120、黑色格線 0 左右。
BLUE_MARGIN = 25


def ink_by_colour(image, margin=BLUE_MARGIN):
    """靠顏色把筆跡抽出來，回傳白底黑字。抽不到就回 None。

    這兩種表格是粉紅紙、紅色印刷、黑色格線，民眾多半用藍色原子筆填。
    藍色在 B-R 這個維度上跟紙、紅字、黑線都分得很開 —— 一刀切下去，
    紙、印刷說明、格線全部不見，只剩手寫，**而且不需要底圖、不怕對位誤差**。

    實測 F 表的身分證欄（真實掃描件）：

        減掉版面   筆畫被削得支離破碎，「2」只剩一個小點
        靠顏色     A123454321 十個字完整清楚

    減版面之所以會削掉筆畫，是因為底圖本身不乾淨（見 compose 的說明），
    而且手寫壓在印刷格線上的地方相減之後本來就會斷。顏色沒有這個問題。

    黑色原子筆寫的就分不出來（跟黑色格線同色），那時候回 None，
    由呼叫端退回減版面那條路。
    """
    if image is None or image.ndim != 3:
        return None
    blue = image[:, :, 0].astype(np.int16)
    red = image[:, :, 2].astype(np.int16)
    mask = (blue - red) > margin
    if not mask.any():
        return None
    out = np.full(mask.shape, 255, np.uint8)
    out[mask] = 0
    return out


def homography(source, target):
    """算出把 source 疊到 target 上的變換矩陣，回傳 (矩陣, 內點數)。"""
    kp_source, desc_source = _ORB.detectAndCompute(as_gray(source), None)
    kp_target, desc_target = _ORB.detectAndCompute(as_gray(target), None)
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
    return matrix, int(mask.sum())


def warp(image, matrix, shape):
    """按矩陣把影像搬到目標座標系。超出範圍的地方填白。"""
    border = (255, 255, 255) if image.ndim == 3 else 255
    return cv2.warpPerspective(image, matrix, (shape[1], shape[0]), borderValue=border)


def align(source, target):
    """把 source 對齊到 target 的座標系，回傳 (對齊後的影像, 內點數)。

    彩色進來就彩色出去 —— 紅筆偵測需要對齊後的彩色底圖。
    """
    matrix, inliers = homography(source, target)
    if matrix is None:
        return None, 0
    return warp(source, matrix, target.shape[:2]), inliers


def to_base(base, scan):
    """把掃描件搬進底圖的座標系，回傳 (搬好的影像, 內點數)。

    方向很重要。樣板上的欄位框是照底圖量的，所以每一張掃描件都要搬進
    同一個座標系，那些框才會對每一張都成立。反過來把底圖搬到掃描件上，
    框就只對「當初拿來量的那一張」有效 —— 換一張掃描件位移不同就整個歪掉。
    """
    matrix, inliers = homography(scan, base)
    if matrix is None:
        return None, 0
    return warp(scan, matrix, base.shape[:2]), inliers


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

    逐像素取最亮的：只要有任何一份樣本在該點沒有墨跡，該點就是白的。
    詳細理由見函式裡的註解 —— 簡單說，中位數對「大家都寫在同一格」
    的欄位無效，而那正是身分證欄。
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

    # 逐像素取**最亮**的，不是中位數。
    #
    # 中位數的想法是「手寫每份都不同會被濾掉」，但那要手寫很少重疊才成立。
    # 一字一格的欄位剛好相反：每個人都寫在同樣那十格裡，三份樣本有兩份
    # 在同一個像素有墨，中位數就是墨 —— 底圖上留下一團別人的字跡，
    # 相減時把真正的筆畫一起削掉。實測 F 表的合成底圖上，身分證那十格
    # 清清楚楚疊著三個人的號碼。
    #
    # 取最亮的則是「只要有任何一份在這裡是白的，就當成白的」：
    # 印刷版面每一份都在同一個位置是黑的，會留下來；
    # 手寫只要有一份沒寫到那個像素就會被抹掉。兩份樣本就有效果。
    base = np.stack(stack).max(axis=0)
    return base, weak


def subtract(base, scan, delta=35, min_area=30, thin=0):
    """把印刷版面從掃描件上減掉，只留下手寫內容。

    回傳白底黑字的影像，而且是**底圖座標系**的 —— 樣板上的欄位框直接可用。

    用灰階相減而不是「兩張各自二值化後相減」：後者為了吸收對位誤差得把印刷筆畫
    加粗才行，而手寫常常就寫在印刷底線與方格上，加粗會把筆畫一起削掉。
    """
    moved, _ = to_base(base, scan)
    if moved is None:
        raise ValueError("底圖對不上這張掃描件")

    difference = np.clip(as_gray(base).astype(np.int16) - as_gray(moved).astype(np.int16),
                         0, 255).astype(np.uint8)
    ink = (difference > delta).astype(np.uint8) * 255
    ink = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    # 對位再準也會在印刷筆畫邊緣留下細細的殘影，落在欄位框裡會干擾辨識。
    # thin 用侵蝕把細殘影濾掉，min_area 則是丟掉小塊雜點 —— 兩個都試著調過，
    # 三份樣本上有的欄位變好、有的變差（姓名救回來了，行政區反而讀壞；
    # min_area 調大會連真的筆畫一起丟）。這種規模的樣本調不出可信的參數，
    # 所以維持原設定，等實際跑一陣子累積統計數字再依數據決定。
    if thin:
        seeds = cv2.erode(ink, np.ones((3, 3), np.uint8), iterations=thin)
        if seeds.any():
            ink = cv2.bitwise_and(ink, cv2.dilate(seeds, np.ones((3, 3), np.uint8),
                                                  iterations=thin + 1))

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

# -*- coding: utf-8 -*-
"""檢查空白原稿能不能對上實際掃描件。

正式程式打算用「空白原稿相減」的方式，把印刷版面消掉、只留下手寫內容。
這對粉紅色紙的表格尤其重要 —— 那些表格的框線與說明文字本身就是紅色，
沒辦法靠色彩把紅筆或手寫分離出來。

但這個做法有個前提：手上的空白原稿，必須跟實際掃描件是同一個印刷版本。
版面看起來一樣不代表真的一樣 —— 影印過的、不同批印製的、
或掃描器縮放設定不同的，疊起來就會對不準，相減之後印刷殘留滿版。

這支程式就是在建立樣板之前先驗這一關。用法：

    python tools/check_blank_alignment.py 空白原稿.pdf 掃描件.pdf

判讀：
    印刷覆蓋率 >= 0.90   可以直接用
    0.75 ~ 0.90          堪用，但欄位裁切要放寬一點
    < 0.75               不要用，先換一份對得上的空白原稿

覆蓋率偏低時，會再逐區塊比對，指出是整體縮放不同、紙張變形，
還是根本就是不同印刷版本。
"""

import argparse
import sys

import cv2
import numpy as np
import pymupdf


def render(path, page=0, dpi=300):
    """把 PDF 的某一頁算成灰階影像。"""
    document = pymupdf.open(path)
    if page >= document.page_count:
        raise SystemExit("%s 只有 %d 頁，取不到第 %d 頁" % (path, document.page_count, page + 1))
    pixmap = document[page].get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72))
    image = np.frombuffer(pixmap.samples, np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
    document.close()
    if pixmap.n >= 3:
        return cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2GRAY)
    return image[:, :, 0].copy()


def ink_mask(gray):
    """抽出墨跡。用自適應二值化，才不會被紙張底色與掃描明暗影響。"""
    return cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 41, 15)


def align(blank, scan):
    """用特徵點把空白原稿對齊到掃描件，回傳對齊後的影像與內點數。"""
    orb = cv2.ORB_create(20000)
    kp_blank, desc_blank = orb.detectAndCompute(blank, None)
    kp_scan, desc_scan = orb.detectAndCompute(scan, None)
    if desc_blank is None or desc_scan is None:
        return None, 0

    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(desc_blank, desc_scan, k=2)
    good = [m for m, n in pairs if m.distance < 0.75 * n.distance]
    if len(good) < 20:
        return None, 0

    src = np.float32([kp_blank[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_scan[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    matrix, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if matrix is None:
        return None, 0

    aligned = cv2.warpPerspective(blank, matrix, (scan.shape[1], scan.shape[0]), borderValue=255)
    return aligned, int(mask.sum())


def printed_coverage(aligned_blank, scan):
    """對齊後，空白原稿的印刷筆畫有多少比例落在掃描件的墨跡上。

    這是對位品質最直接的指標：對得越準，比例越高。
    """
    printed = ink_mask(aligned_blank)
    scanned = cv2.dilate(ink_mask(scan), np.ones((3, 3), np.uint8))
    hit = cv2.bitwise_and(printed, scanned)
    return (hit > 0).sum() / max((printed > 0).sum(), 1)


def probe_tiles(blank, scan, rows=3, cols=3, search=180,
                scales=np.linspace(0.94, 1.14, 21)):
    """把版面切成幾塊，各自在掃描件裡找出實際位置、尺度與比對分數。

    分數高且各塊尺度/位移一致 → 同一版本、剛體變形，全域對位就夠。
    分數高但各塊不一致       → 紙張變形，要逐欄位局部對位。
    分數普遍偏低             → 印刷版本根本不同，換一份空白原稿。
    """
    height, width = blank.shape
    results = []
    for row in range(rows):
        for col in range(cols):
            x0, x1 = col * width // cols, (col + 1) * width // cols
            y0, y1 = row * height // rows, (row + 1) * height // rows
            template = blank[y0:y1, x0:x1]

            ry0, rx0 = max(0, y0 - search), max(0, x0 - search)
            ry1, rx1 = min(scan.shape[0], y1 + search), min(scan.shape[1], x1 + search)
            region = scan[ry0:ry1, rx0:rx1]

            best = (-1.0, 1.0, 0, 0)
            for scale in scales:
                resized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                if resized.shape[0] >= region.shape[0] or resized.shape[1] >= region.shape[1]:
                    continue
                response = cv2.matchTemplate(region, resized, cv2.TM_CCOEFF_NORMED)
                _, score, _, location = cv2.minMaxLoc(response)
                if score > best[0]:
                    best = (score, scale, rx0 + location[0] - x0, ry0 + location[1] - y0)
            results.append(best)
    return results


def diagnose(tiles):
    """從逐區塊的比對結果判斷問題出在哪。"""
    scores = np.array([t[0] for t in tiles])
    scales = np.array([t[1] for t in tiles])
    shifts = np.array([(t[2], t[3]) for t in tiles], dtype=float)

    print("  逐區塊比對:")
    for index, (score, scale, dx, dy) in enumerate(tiles):
        print("    區塊 %d  分數=%.3f  尺度=%.3f  位移=(%+5d,%+5d)"
              % (index + 1, score, scale, dx, dy))
    print("  分數中位數 %.3f   尺度全距 %.3f   位移標準差 X=%.1f Y=%.1f px"
          % (np.median(scores), scales.max() - scales.min(), shifts[:, 0].std(), shifts[:, 1].std()))

    if np.median(scores) < 0.70:
        return ("底圖與掃描件不同源",
                "各區塊的比對分數普遍偏低，代表印刷內容本身就有差異，不只是位置對不準。\n"
                "  最常見的原因是掃描件其實是影印本 —— 影印會帶來 1~3% 的縮放漂移與非線性變形，\n"
                "  影印再影印還會累積，所以原版空白表格永遠對不上影印件。\n"
                "  這種情況不要去找新的空白原稿，改用 build_base_image.py 從影印件本身合成底圖：\n"
                "  取同一種表格的多份掃描件對齊後逐像素取中位數，手寫每份都不同會被濾掉，\n"
                "  印刷部分每份都相同會留下來，而且合成出來的底圖自帶影印件的變形特性。")
    if shifts.std() > 8 or (scales.max() - scales.min()) > 0.03:
        return ("紙張變形",
                "各區塊分數不錯，但需要的位移/尺度彼此差很多，代表紙張有非線性變形\n"
                "  (影印件、送紙歪斜、或紙張皺摺)。全域對位不夠，要逐欄位局部對位。")
    return ("正常", "各區塊一致，屬於單純的平移/旋轉，全域對位就足夠。")


def main():
    parser = argparse.ArgumentParser(
        description="檢查空白原稿能不能對上實際掃描件",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("blank", help="空白原稿 PDF")
    parser.add_argument("scan", help="已填寫的掃描件 PDF")
    parser.add_argument("--blank-page", type=int, default=1, help="空白原稿要用第幾頁 (預設 1)")
    parser.add_argument("--scan-page", type=int, default=1, help="掃描件要用第幾頁 (預設 1)")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--save", metavar="PNG", help="把抽出的手寫內容存成圖檔，方便肉眼確認")
    args = parser.parse_args()

    blank = render(args.blank, args.blank_page - 1, args.dpi)
    scan = render(args.scan, args.scan_page - 1, args.dpi)
    print("空白原稿 %s  第 %d 頁  %dx%d" % (args.blank, args.blank_page, blank.shape[1], blank.shape[0]))
    print("掃描件   %s  第 %d 頁  %dx%d" % (args.scan, args.scan_page, scan.shape[1], scan.shape[0]))
    print()

    aligned, inliers = align(blank, scan)
    if aligned is None:
        print("對位失敗：找不到足夠的共同特徵點，這兩份文件八成不是同一種表格。")
        return 1

    coverage = printed_coverage(aligned, scan)
    print("特徵點內點數 : %d" % inliers)
    print("印刷覆蓋率   : %.3f" % coverage)

    if coverage >= 0.90:
        print("結果         : 可以直接用")
        verdict = 0
    elif coverage >= 0.75:
        print("結果         : 堪用，但欄位裁切範圍要放寬")
        verdict = 0
    else:
        print("結果         : 不建議使用，往下看原因")
        verdict = 2

    if coverage < 0.90:
        print()
        cause, advice = diagnose(probe_tiles(blank, scan))
        print()
        print("  研判原因: %s" % cause)
        print("  %s" % advice)

    if args.save:
        printed = cv2.dilate(ink_mask(aligned), np.ones((5, 5), np.uint8))
        hand = cv2.bitwise_and(ink_mask(scan), cv2.bitwise_not(printed))
        hand = cv2.morphologyEx(hand, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(hand, 8)
        keep = np.zeros_like(hand)
        for index in range(1, count):
            if stats[index, cv2.CC_STAT_AREA] >= 40:      # 小於這個面積的視為雜訊
                keep[labels == index] = 255
        keep = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        cv2.imwrite(args.save, 255 - keep)  # 開發用腳本，輸出路徑自己給，維持 cv2
        print()
        print("已存出抽取結果: %s (只剩手寫內容就代表成功)" % args.save)

    return verdict


if __name__ == "__main__":
    sys.exit(main())

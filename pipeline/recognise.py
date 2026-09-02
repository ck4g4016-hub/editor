# -*- coding: utf-8 -*-
"""欄位辨識。

用 RapidOCR（PP-OCRv4 模型，純 ONNX Runtime）。模型隨套件一起帶著，
執行時不連任何網路，這是離線要求的硬條件。

## 為什麼是逐欄位裁切，不是整頁辨識

整頁辨識會漏欄位。實測 F 表：整頁辨識漏掉門牌的「147」，
逐欄位裁切之後同一塊就讀得到。原因是偵測階段會把稀疏的手寫併成大區塊或直接忽略，
先把範圍限定住，辨識模型才有機會把那幾個字看清楚。

## 逐格辨識：曾經放棄，後來重測推翻

原本的結論是「逐字辨識是反效果，F 表身分證欄切成九格有五格回傳空白」，
所以一律整行辨識。後來拿真實件跑出 NL30477LZ（9 碼）、PUY3N（5 碼）
這種結果，重新量了一次單字辨識：A0-9 共 11 個字元，10 個讀得對。

所以逐格是可行的，之前那個量測不知道是被什麼影響（放大倍率、補白、
或是當時的裁切品質）。現在的做法是**兩種都讀**，讓檢查碼決定用哪一個 ——
身分證有檢查碼這件事，讓我們可以直接驗證哪一種讀法是對的，
不必猜。這是這個欄位獨有的優勢，別的欄位沒有。
"""

import threading

import cv2
import numpy as np

# 放大倍率。欄位裁下來通常只有幾十像素高，放大後辨識明顯較穩。
UPSCALE = 2

# 四周補白，避免筆畫貼著邊緣被截掉
MARGIN = 24

_engine = None
_lock = threading.Lock()


def engine():
    """第一次用到才載入模型 —— 載入要一兩秒，沒用到就不要付這個代價。"""
    global _engine
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
    return _engine


def prepare(crop):
    """把裁下來的欄位整理成辨識模型好處理的樣子。"""
    if crop is None or crop.size == 0:
        return None
    if crop.ndim == 2:
        crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    if UPSCALE != 1:
        crop = cv2.resize(crop, None, fx=UPSCALE, fy=UPSCALE, interpolation=cv2.INTER_CUBIC)
    return cv2.copyMakeBorder(crop, MARGIN, MARGIN, MARGIN, MARGIN,
                              cv2.BORDER_CONSTANT, value=(255, 255, 255))


def read(crop):
    """辨識一個欄位，回傳 (文字, 信心)。

    同一個欄位可能被切成好幾段（例如中間有空格），照左到右接起來。
    信心取最低的那一段 —— 一串裡只要有一個字沒把握，整串就不能算有把握。
    """
    image = prepare(crop)
    if image is None:
        return "", 0.0

    result, _ = engine()(image)
    if not result:
        return "", 0.0

    ordered = sorted(result, key=lambda item: np.array(item[0])[:, 0].min())
    text = "".join(item[1] for item in ordered).strip()
    confidence = min(float(item[2]) for item in ordered)
    return text, confidence


def crop_field(page, box):
    """依欄位定義把區域裁下來。box 是 (x, y, w, h)。"""
    x, y, w, h = box
    height, width = page.shape[:2]
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(width, int(x + w)), min(height, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return None
    return page[y0:y1, x0:x1]


# 兩塊墨跡之間的空白超過這個比例（相對於整格寬度）就算是分開的格子
GAP_RATIO = 0.06


def cells(crop):
    """依墨跡之間的空白，把一個欄位切成一格一格。

    一字一格的欄位（身分證、稅籍編號）字跟字之間有明顯空白，
    照著切開就能一格一格讀 —— 整行讀的時候漏掉的字，這樣有機會補回來。
    """
    if crop is None or crop.size == 0:
        return []
    gray = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    ink = (gray < 128).sum(axis=0) > 0
    if not ink.any():
        return []

    gap = max(2, int(len(ink) * GAP_RATIO))
    groups, start = [], None
    blanks = 0
    for index, has_ink in enumerate(ink):
        if has_ink:
            if start is None:
                start = index
            blanks = 0
        elif start is not None:
            blanks += 1
            if blanks >= gap:
                groups.append((start, index - blanks + 1))
                start = None
    if start is not None:
        groups.append((start, len(ink)))

    pad = 4
    out = []
    for x0, x1 in groups:
        if x1 - x0 < 3:
            continue
        out.append(crop[:, max(0, x0 - pad):min(crop.shape[1], x1 + pad)])
    return out


def read_cells(crop):
    """逐格辨識，回傳 (接起來的文字, 最低信心)。格子讀不到就跳過。"""
    pieces, scores = [], []
    for cell in cells(crop):
        text, confidence = read(cell)
        text = text.strip()
        if text:
            pieces.append(text)
            scores.append(confidence)
    return "".join(pieces), (min(scores) if scores else 0.0)

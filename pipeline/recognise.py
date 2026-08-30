# -*- coding: utf-8 -*-
"""欄位辨識。

用 RapidOCR（PP-OCRv4 模型，純 ONNX Runtime）。模型隨套件一起帶著，
執行時不連任何網路，這是離線要求的硬條件。

## 為什麼是逐欄位裁切，不是整頁辨識

整頁辨識會漏欄位。實測 F 表：整頁辨識漏掉門牌的「147」，
逐欄位裁切之後同一塊就讀得到。原因是偵測階段會把稀疏的手寫併成大區塊或直接忽略，
先把範圍限定住，辨識模型才有機會把那幾個字看清楚。

## 為什麼不逐字辨識

一字一格的欄位（身分證統一編號、稅籍編號）看起來很適合切成單格分開認，
實測是反效果 —— PP-OCR 是整行辨識模型，餵單一字元等於廢掉它的偵測階段。
F 表的身分證欄切成九格，其中五格回傳空白。所以一字一格照樣整行辨識，
正確性交給檢查碼把關。
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

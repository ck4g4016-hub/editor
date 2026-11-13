# -*- coding: utf-8 -*-
"""把掃描 PDF 算成影像。

掃描件一律是 A4 300dpi，但方向不一定 —— 系統報表那種橫式表格掃進來是轉 90 度的，
偶爾也會有整份上下顛倒。方向不在這裡處理，交給 layout 模組在分類時一併決定，
因為分類本來就要跟樣板比對，順手把四個方向都試過最省事。
"""

import cv2
import numpy as np
import pymupdf

# 分類用的解析度。300dpi 全開太慢，這個解析度足夠認出是哪一種表格。
CLASSIFY_DPI = 100

# 實際辨識用的解析度。
FULL_DPI = 300


def page_count(path):
    document = pymupdf.open(path)
    try:
        return document.page_count
    finally:
        document.close()


def render(path, page, dpi=FULL_DPI, gray=True):
    """算出某一頁的影像。gray=True 回傳灰階，否則回傳 BGR 彩色。"""
    document = pymupdf.open(path)
    try:
        if page >= document.page_count:
            raise IndexError("%s 只有 %d 頁" % (path, document.page_count))
        pixmap = document[page].get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72))
        image = np.frombuffer(pixmap.samples, np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n)
        if pixmap.n >= 3:
            image = image[:, :, :3]
            return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if gray else cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        flat = image[:, :, 0].copy()
        return flat if gray else cv2.cvtColor(flat, cv2.COLOR_GRAY2BGR)
    finally:
        document.close()


def rotate(image, degrees):
    """順時針旋轉 90 的倍數。"""
    if degrees % 360 == 0:
        return image
    if degrees % 360 == 90:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if degrees % 360 == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if degrees % 360 == 270:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError("只支援 90 的倍數，收到 %r" % degrees)


def ink_ratio(gray):
    """墨跡佔比。用來判斷一頁是不是空白背面。"""
    mask = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 41, 15)
    return float((mask > 0).mean())

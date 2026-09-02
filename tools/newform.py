# -*- coding: utf-8 -*-
"""建立一種新表格的樣板與底圖。

樣板編輯器是用來「在已知的表格上框欄位」的，它開場就要載入既有樣板，
所以第一個樣板不可能由它自己生出來。這個模組補的就是那一步。

一種表格在樣板資料夾裡長這樣：

    <樣板>/F/
        index.json      {"code": "F", "name": "…", "pages": {"front": "front.png"}}
        front.png       認種類用的參考影像（100dpi 灰階）
        back.png        背面，單面表格沒有
        base.png        底圖，用來把印刷版面減掉（300dpi 彩色）
        fields.json     欄位定義，由樣板編輯器產生

底圖有兩種來源：自己印的表格用空白原稿；別的單位送來的影印本要用
多份影印件合成 —— 空白原稿跟影印件對不上（影印會歪、會縮、顏色也不同）。
"""

import json
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import baseimage, layout, render  # noqa: E402

BLANK = "blank"
COMPOSE = "compose"
NONE = "none"


def valid_code(code):
    """代號會變成資料夾名稱，限英數 —— 中文在不同編碼下容易出事。"""
    code = (code or "").strip()
    if not code:
        return None, "代號不能空白"
    if not code.isascii() or not code.replace("_", "").replace("-", "").isalnum():
        return None, "代號只能用英文、數字、底線，不能有中文或空白"
    return code, None


def create(store, code, name, pdf, front, back=None, rotate=0):
    """寫出 index.json 與參考影像。front／back 是從 1 起算的頁碼。"""
    folder = os.path.join(store, code)
    os.makedirs(folder, exist_ok=True)

    pages, notes = {}, []
    for role, number in (("front", front), ("back", back)):
        if not number:
            continue
        image = render.rotate(
            render.render(pdf, number - 1, dpi=render.CLASSIFY_DPI), rotate)
        filename = "%s.png" % role
        cv2.imwrite(os.path.join(folder, filename), image)
        pages[role] = filename
        notes.append("%s ← 第 %d 頁 (%dx%d)" % (role, number, image.shape[1], image.shape[0]))

    with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as handle:
        json.dump({"code": code, "name": name, "pages": pages},
                  handle, ensure_ascii=False, indent=2)
    return folder, notes


def base_from_blank(store, code, pdf, page=1):
    """空白原稿直接當底圖。彩色 —— 紅筆偵測要用到色彩。"""
    image = render.render(pdf, page - 1, dpi=render.FULL_DPI, gray=False)
    target = os.path.join(store, code, "base.png")
    cv2.imwrite(target, image)
    return target, "底圖來源：空白原稿第 %d 頁" % page


def base_from_scans(store, code, paths):
    """多份影印件對齊後取中位數：手寫每份都不同會被濾掉，印刷每份相同會留下。"""
    templates = layout.TemplateSet.load(store)
    pages = [p for p in layout.classify_pages(paths, templates)
             if p.code == code and p.role == layout.FRONT]
    if len(pages) < baseimage.MIN_SAMPLES:
        raise ValueError("只找到 %d 份 %s 的正面，合成底圖至少要 %d 份"
                         % (len(pages), code, baseimage.MIN_SAMPLES))
    samples = [render.rotate(
        render.render(p.source, p.index, dpi=render.FULL_DPI, gray=False), p.rotation)
        for p in pages]
    image, weak = baseimage.compose(samples)
    target = os.path.join(store, code, "base.png")
    cv2.imwrite(target, image)
    note = "底圖來源：%d 份掃描件合成" % (len(samples) - len(weak))
    if weak:
        note += "（有 %d 份對不齊，沒有納入）" % len(weak)
    return target, note


def check(store, code, paths):
    """拿底圖去對每一份掃描件，回傳 (說明行, 最差覆蓋率)。

    覆蓋率是「底圖的印刷筆畫有多少比例落在掃描件的墨跡上」。
    對不準的話欄位框會整個偏掉，抓到隔壁格的內容 —— 那種錯最難發現，
    因為抓出來的東西看起來像模像樣，只是屬於別人。
    """
    path = os.path.join(store, code, "base.png")
    base = cv2.imread(path, cv2.IMREAD_COLOR)
    if base is None:
        return ["沒有底圖，跳過檢查"], 0.0

    templates = layout.TemplateSet.load(store)
    pages = [p for p in layout.classify_pages(paths, templates)
             if p.code == code and p.role == layout.FRONT]
    if not pages:
        return ["這批檔案裡找不到 %s 的正面，沒得檢查" % code], 0.0

    lines, worst = [], 1.0
    for page in pages:
        scan = render.rotate(
            render.render(page.source, page.index, dpi=render.FULL_DPI, gray=False),
            page.rotation)
        aligned, inliers = baseimage.align(base, scan)
        if aligned is None:
            worst = 0.0
            lines.append("第 %d 頁  對不上，找不到足夠的共同特徵點" % (page.index + 1))
            continue
        value = baseimage.coverage(aligned, scan)
        worst = min(worst, value)
        lines.append("第 %d 頁  覆蓋率 %.3f  %s"
                     % (page.index + 1, value, baseimage.verdict(value)))
    return lines, worst

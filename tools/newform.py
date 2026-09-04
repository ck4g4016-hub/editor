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

from pipeline import baseimage, layout, render, resources  # noqa: E402

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


def upright_rotation(pdf, page, dpi=150):
    """這一頁要順時針轉幾度，字才是正的。回傳 0、90、180 或 270。

    橫式的系統報表掃進來是躺著的。以前完全沒處理，樣板就照躺著的樣子建 ——
    分類是對的（躺著的樣板配躺著的掃描件，一樣對得上），問題出在**欄位裁切**：
    躺著的頁面上，一行地址是一條**直的**細長條。實測 E 表的地址欄（125x1210）：

        直接讀     「05粼尖山路27號六楼中一新北市歌區尖山里00」  順序整個錯亂
        轉正再讀   「新北市歌區尖山里005粼尖山路27號六楼」        正確

    順序錯亂是因為辨識時是照「由上而下、每行由左而右」接起來的，
    那對躺著的文字剛好變成由右而左。

    而且承辦人是在躺著的畫面上框欄位的，那本身就難用得要命。

    判斷方式是四個方向各辨識一次，挑「橫向的文字最多」的那個 ——
    字正的時候，一行文字的框一定是扁的。這件事一種表格只做一次。

    **一定要關掉 use_cls。** 那個模型會自己把倒過來的文字轉正再辨識，
    於是正立和倒立拿到的分數一樣高（實測 59.0 對 61.7），只分得出橫豎、
    分不出正倒。關掉之後只有真正正立的方向有分數（59.0 對 0.0）。
    """
    import numpy as np

    from pipeline import recognise

    image = render.render(pdf, page - 1, dpi=dpi, gray=False)
    best, best_score = 0, -1.0
    for degrees in (0, 90, 180, 270):
        turned = render.rotate(image, degrees)
        try:
            result, _ = recognise.engine()(turned, use_det=True, use_cls=False,
                                           use_rec=True)
        except Exception:                                           # noqa: BLE001
            result = None
        score = 0.0
        for item in result or ():
            box = np.array(item[0], dtype=float)
            width = box[:, 0].max() - box[:, 0].min()
            height = box[:, 1].max() - box[:, 1].min()
            if width <= height:          # 直的框：這個方向的字是躺著的
                continue
            score += len(item[1] or "") * float(item[2])
        if score > best_score:
            best, best_score = degrees, score
    return best


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
        target = os.path.join(folder, filename)
        if not resources.imwrite(target, image):
            raise ValueError("寫不出樣板影像：%s" % target)
        pages[role] = filename
        notes.append("%s ← 第 %d 頁 (%dx%d)" % (role, number, image.shape[1], image.shape[0]))

    with open(os.path.join(folder, "index.json"), "w", encoding="utf-8") as handle:
        json.dump({"code": code, "name": name, "pages": pages},
                  handle, ensure_ascii=False, indent=2)
    return folder, notes


def base_from_blank(store, code, pdf, page=1, role="front", rotate=0):
    """空白原稿直接當底圖。存成彩色 —— 存成灰階的話紅色會變成灰色，
    後續要靠色彩判斷的東西就全毀了。

    rotate 要跟 create() 用同一個值，不然底圖跟分類用的參考影像會不同向，
    欄位座標就整個對不起來。
    """
    image = render.rotate(
        render.render(pdf, page - 1, dpi=render.FULL_DPI, gray=False), rotate)
    name = "base.png" if role == "front" else "base_back.png"
    target = os.path.join(store, code, name)
    if not resources.imwrite(target, image):
        raise ValueError("寫不出底圖：%s" % target)
    return target, "%s底圖來源：空白原稿第 %d 頁" % (
        "" if role == "front" else "背面", page)


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
    if not resources.imwrite(target, image):
        raise ValueError("寫不出底圖：%s" % target)
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
    base = resources.imread(path, cv2.IMREAD_COLOR)
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

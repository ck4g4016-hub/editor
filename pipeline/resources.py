# -*- coding: utf-8 -*-
"""找出程式自己的資源檔（網頁、路名字典）放在哪。

打包成執行檔之後，資源不在原始碼旁邊，而是在打包工具解出來的位置，
所以路徑不能寫死成「相對於這個 .py」。
"""

import os
import sys


def base_dir():
    """程式資源的根目錄。"""
    packed = getattr(sys, "_MEIPASS", None)
    if packed:
        return packed
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def path(*parts):
    return os.path.join(base_dir(), *parts)


def workspace():
    """放樣板、輸出的地方。

    打包版放在執行檔旁邊 —— 綠色版整包搬走也不會掉東西，
    而且不必去猜使用者有沒有權限寫別的位置。
    直接跑原始碼時就用專案目錄。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def version():
    """程式版本。

    打包時 workflow 會寫一個 version.txt 進資源目錄，裡面是 commit 編號 —— 
    診斷報告要靠它才知道使用者手上跑的是哪一版程式碼。
    直接跑原始碼時就現場問 git。
    """
    marker = path("version.txt")
    if os.path.isfile(marker):
        with open(marker, encoding="utf-8") as handle:
            return handle.read().strip()
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "-C", base_dir(), "describe", "--always", "--dirty"],
            stderr=subprocess.DEVNULL, text=True).strip() + "（原始碼）"
    except Exception:                                               # noqa: BLE001
        return "不明"


def imread(path, flags=None):
    """讀影像。**不要直接用 cv2.imread。**

    OpenCV 的 imread／imwrite 走的是 ANSI API，路徑只要有非 ASCII 字元
    就整個失效 —— 而且不會丟例外：imread 回傳 None，imwrite 回傳 False。
    使用者的工作資料夾是「D:\\claude\\紙本轉excel\\樣板\\F」，
    每一層都有中文，所以樣板影像根本寫不出去，也讀不回來。

    改成自己讀 bytes 再交給 OpenCV 解碼，路徑就由 Python 處理，中文沒問題。
    """
    import cv2
    import numpy as np

    if not os.path.isfile(path):
        return None
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR if flags is None else flags)


def imwrite(path, image):
    """寫影像。回傳 True/False，理由同 imread。"""
    import cv2

    extension = os.path.splitext(path)[1] or ".png"
    ok, buffer = cv2.imencode(extension, image)
    if not ok:
        return False
    try:
        buffer.tofile(path)
    except OSError:
        return False
    return True

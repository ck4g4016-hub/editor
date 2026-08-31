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

# -*- coding: utf-8 -*-
"""
離線手寫轉 Excel 專案 — 環境探測程式

這支程式不處理任何業務資料，也不會連上任何外部網路。
它只檢查這台電腦能不能跑後續的正式程式，然後產生一份純文字報告。

檢查項目:
  1. 執行環境     Windows 版本、CPU、記憶體、打包方式、是否帶有網路下載標記
  2. 寫入權限     常用的幾個位置能不能建立檔案
  3. 套件載入     含原生 DLL 的影像/OCR 套件能不能載入 (最容易被防毒攔的部分)
  4. 本機介面     127.0.0.1 上能不能開本機伺服器 (人工複核介面的候選做法之一)
  5. 桌面視窗     tkinter 視窗能不能開 (人工複核介面的另一個候選做法)
  6. 效能量測     用合成的 A4 300dpi 影像量測影像處理速度

報告內容只有這台電腦的環境資訊，不含任何個人資料。
"""

import io
import os
import platform
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import traceback

REPORT = []


def say(line=""):
    """同時寫進報告與主控台。"""
    REPORT.append(line)
    try:
        print(line)
    except Exception:
        # 主控台編碼不支援中文時不要讓整支程式掛掉
        print(line.encode("ascii", "replace").decode("ascii"))


def section(title):
    say()
    say("=" * 64)
    say(title)
    say("=" * 64)


def timed(fn):
    """回傳 (結果, 毫秒)；失敗時結果為 Exception。"""
    start = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 - 探測程式要吞下所有錯誤並回報
        result = exc
    return result, (time.perf_counter() - start) * 1000


# --------------------------------------------------------------------------
# 1. 執行環境
# --------------------------------------------------------------------------

def probe_environment():
    section("1. 執行環境")

    say("作業系統       : %s %s (%s)" % (platform.system(), platform.release(), platform.version()))
    say("架構           : %s" % platform.machine())
    say("Python         : %s" % sys.version.replace("\n", " "))
    say("CPU            : %s" % (platform.processor() or "(未提供)"))
    say("邏輯核心數     : %s" % (os.cpu_count() or "未知"))
    say("記憶體         : %s" % describe_memory())

    frozen = getattr(sys, "frozen", False)
    say("打包執行       : %s" % ("是" if frozen else "否 (直接跑 .py)"))
    if frozen:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and os.path.dirname(meipass) == os.path.dirname(tempfile.gettempdir().rstrip("\\/")):
            say("打包方式       : onefile (執行時解壓到暫存資料夾)")
        elif meipass and meipass != os.path.dirname(sys.executable):
            say("打包方式       : onefile，解壓位置 %s" % meipass)
        else:
            say("打包方式       : onedir (免解壓，直接從資料夾執行)")
    say("執行檔位置     : %s" % sys.executable)
    say("工作目錄       : %s" % os.getcwd())
    say("網路下載標記   : %s" % describe_mark_of_the_web())


def describe_memory():
    if platform.system() == "Windows":
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(MemoryStatusEx)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            gb = 1024.0 ** 3
            return "總共 %.1f GB，可用 %.1f GB" % (status.ullTotalPhys / gb, status.ullAvailPhys / gb)
        except Exception as exc:  # noqa: BLE001
            return "查詢失敗 (%s)" % exc
    return "非 Windows，略過"


def describe_mark_of_the_web():
    """從網路下載的檔案會帶 Zone.Identifier；被 SmartScreen 擋通常就是因為它。"""
    if platform.system() != "Windows":
        return "非 Windows，略過"
    try:
        with open(sys.executable + ":Zone.Identifier", "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read()
        zone = "未知"
        for line in content.splitlines():
            if line.strip().startswith("ZoneId="):
                zone = line.strip().split("=", 1)[1]
        return "有 (ZoneId=%s)，如被 SmartScreen 擋，可右鍵→內容→勾選「解除封鎖」" % zone
    except FileNotFoundError:
        return "無 (檔案未被標記為網路下載)"
    except OSError:
        return "無 (檔案未被標記為網路下載)"
    except Exception as exc:  # noqa: BLE001
        return "查詢失敗 (%s)" % exc


# --------------------------------------------------------------------------
# 2. 寫入權限
# --------------------------------------------------------------------------

def probe_write_access():
    section("2. 寫入權限")
    say("正式程式需要能寫入設定檔、樣板檔、診斷資料庫與輸出的 Excel。")
    say()

    for label, path in candidate_write_dirs():
        if not path:
            say("  [略過] %-14s 找不到這個位置" % label)
            continue
        ok, detail = try_write(path)
        say("  [%s] %-14s %s" % ("OK  " if ok else "失敗", label, path))
        if not ok:
            say("           原因: %s" % detail)

    say()
    ok, detail = try_sqlite()
    say("  [%s] SQLite 資料庫  %s" % ("OK  " if ok else "失敗", detail))


def candidate_write_dirs():
    home = os.path.expanduser("~")
    exe_dir = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd()
    desktop = os.path.join(home, "Desktop")
    if not os.path.isdir(desktop):
        onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveCommercial")
        if onedrive and os.path.isdir(os.path.join(onedrive, "Desktop")):
            desktop = os.path.join(onedrive, "Desktop")
        elif os.path.isdir(os.path.join(home, "桌面")):
            desktop = os.path.join(home, "桌面")
    return [
        ("執行檔資料夾", exe_dir),
        ("LOCALAPPDATA", os.environ.get("LOCALAPPDATA")),
        ("APPDATA", os.environ.get("APPDATA")),
        ("暫存資料夾", tempfile.gettempdir()),
        ("使用者家目錄", home),
        ("桌面", desktop if os.path.isdir(desktop) else None),
        ("文件資料夾", os.path.join(home, "Documents") if os.path.isdir(os.path.join(home, "Documents")) else None),
    ]


def try_write(directory):
    probe_path = os.path.join(directory, "_probe_write_test.tmp")
    try:
        with open(probe_path, "w", encoding="utf-8") as handle:
            handle.write("probe")
        with open(probe_path, "r", encoding="utf-8") as handle:
            if handle.read() != "probe":
                return False, "寫入後讀回的內容不符"
        os.remove(probe_path)
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, exc)


def try_sqlite():
    """診斷庫要用 SQLite，先確認建得起來。"""
    target = os.path.join(tempfile.gettempdir(), "_probe_sqlite_test.db")
    try:
        conn = sqlite3.connect(target)
        conn.execute("CREATE TABLE IF NOT EXISTS t (k TEXT, v INTEGER)")
        conn.execute("INSERT INTO t VALUES (?, ?)", ("probe", 1))
        conn.commit()
        value = conn.execute("SELECT v FROM t WHERE k = 'probe'").fetchone()[0]
        conn.close()
        os.remove(target)
        if value != 1:
            return False, "寫入後讀回的內容不符"
        return True, "sqlite %s，建立/寫入/讀取皆正常" % sqlite3.sqlite_version
    except Exception as exc:  # noqa: BLE001
        return False, "%s: %s" % (type(exc).__name__, exc)


# --------------------------------------------------------------------------
# 3. 套件載入
# --------------------------------------------------------------------------

def _packaging_hint():
    """永遠不會被呼叫。

    打包工具靠靜態分析決定要收哪些套件，看不懂下面 PACKAGES 表裡的動態載入，
    結果就是完整版跟精簡版一樣什麼都沒包。函式裡的 import 一樣會被靜態分析看到，
    但執行時不會真的載入，所以下面量測到的載入耗時仍然是真實的第一次載入時間。
    """
    import cv2  # noqa: F401
    import numpy  # noqa: F401
    import onnxruntime  # noqa: F401
    import openpyxl  # noqa: F401
    import PIL  # noqa: F401
    import pymupdf  # noqa: F401


PACKAGES = [
    ("numpy", "numpy", "數值運算，所有影像處理的基礎"),
    ("cv2", "opencv-python-headless", "影像前處理：去歪斜、去雜訊、抽表格線、分離紅色印章"),
    ("pymupdf", "PyMuPDF", "把掃描 PDF 轉成 300dpi 影像"),
    ("PIL", "Pillow", "影像格式轉換"),
    ("onnxruntime", "onnxruntime", "離線跑 OCR 模型的推論引擎"),
    ("openpyxl", "openpyxl", "產生 Excel"),
]


def probe_packages():
    section("3. 套件載入 (含原生 DLL，最容易被防毒攔下)")
    loaded = {}
    for module_name, package_name, purpose in PACKAGES:
        module, elapsed = timed(lambda name=module_name: __import__(name))
        if isinstance(module, Exception):
            if isinstance(module, ImportError) and "No module named" in str(module):
                say("  [未包入] %-22s %s" % (package_name, purpose))
            else:
                say("  [失敗  ] %-22s %s: %s" % (package_name, type(module).__name__, module))
            continue
        version = getattr(module, "__version__", None) or getattr(module, "VERSION", "?")
        say("  [OK    ] %-22s %-8s 載入耗時 %6.0f ms   %s" % (package_name, version, elapsed, purpose))
        loaded[module_name] = module

    if "onnxruntime" in loaded:
        providers, _ = timed(loaded["onnxruntime"].get_available_providers)
        if not isinstance(providers, Exception):
            say("           可用推論裝置: %s" % ", ".join(providers))

    # 純 ASCII 的摘要行，給建置流程自動檢查用（避免主控台中文編碼干擾比對）
    say()
    say("  PACKAGES_OK=%d/%d" % (len(loaded), len(PACKAGES)))
    return loaded


# --------------------------------------------------------------------------
# 4. 本機介面 (localhost)
# --------------------------------------------------------------------------

def probe_localhost():
    section("4. 本機網頁介面 (人工複核介面的候選做法)")
    say("測試能不能在 127.0.0.1 上開一個只有本機連得到的伺服器。")
    say("這不會對外連線，也不會開放給區域網路。")
    say()

    try:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from urllib.request import urlopen

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 規定的名稱
                body = "PROBE_OK".encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        with urlopen("http://127.0.0.1:%d/" % port, timeout=5) as response:
            payload = response.read().decode("utf-8")

        if payload == "PROBE_OK":
            say("  [OK  ] 成功在 127.0.0.1:%d 開啟伺服器，並且自己連得到" % port)
        else:
            say("  [異常] 伺服器有開，但回應內容不符: %r" % payload)

        say()
        say("  請用瀏覽器打開這個網址，確認看得到 PROBE_OK：")
        say("      http://127.0.0.1:%d/" % port)
        say("  (伺服器會在這支程式結束時一起關掉)")
        return server
    except Exception as exc:  # noqa: BLE001
        say("  [失敗] %s: %s" % (type(exc).__name__, exc))
        say("         本機伺服器被擋，人工複核介面就得改用桌面視窗。")
        return None


# --------------------------------------------------------------------------
# 5. 桌面視窗
# --------------------------------------------------------------------------

def probe_window():
    section("5. 桌面視窗 (人工複核介面的另一個候選做法)")
    try:
        import tkinter

        root = tkinter.Tk()
        root.title("環境探測")
        root.geometry("360x120+120+120")
        label = tkinter.Label(root, text="視窗開得起來，這個視窗會自動關閉。", padx=16, pady=24)
        label.pack()
        root.update()
        time.sleep(1.2)
        root.destroy()
        say("  [OK  ] tkinter 視窗開啟成功 (版本 %s)" % tkinter.TkVersion)
    except Exception as exc:  # noqa: BLE001
        say("  [失敗] %s: %s" % (type(exc).__name__, exc))


# --------------------------------------------------------------------------
# 6. 效能量測
# --------------------------------------------------------------------------

# A4 紙在 300dpi 下的像素尺寸
A4_300DPI = (3508, 2480)


def probe_performance(loaded):
    section("6. 效能量測 (以 A4 300dpi 合成影像測試)")

    if "numpy" not in loaded:
        say("  numpy 未包入，略過效能量測。")
        return
    numpy = loaded["numpy"]

    matrix = numpy.random.rand(900, 900).astype(numpy.float32)
    _, elapsed = timed(lambda: matrix @ matrix)
    say("  矩陣運算 900x900          %7.0f ms" % elapsed)

    if "cv2" not in loaded:
        say("  OpenCV 未包入，略過影像處理量測。")
    else:
        cv2 = loaded["cv2"]
        page = numpy.random.randint(0, 255, A4_300DPI + (3,), dtype=numpy.uint8)

        gray, elapsed = timed(lambda: cv2.cvtColor(page, cv2.COLOR_BGR2GRAY))
        say("  彩色轉灰階 (整頁)         %7.0f ms" % elapsed)

        _, elapsed = timed(lambda: cv2.GaussianBlur(gray, (5, 5), 0))
        say("  高斯模糊 (去雜訊)         %7.0f ms" % elapsed)

        binary, elapsed = timed(
            lambda: cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10)
        )
        say("  自適應二值化              %7.0f ms" % elapsed)

        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (40, 1))
        _, elapsed = timed(lambda: cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel))
        say("  形態學抽表格線            %7.0f ms" % elapsed)

        _, elapsed = timed(lambda: cv2.warpAffine(gray, cv2.getRotationMatrix2D((1240, 1754), 1.5, 1.0), (2480, 3508)))
        say("  旋轉校正 (去歪斜)         %7.0f ms" % elapsed)

    if "pymupdf" not in loaded:
        say("  PyMuPDF 未包入，略過 PDF 轉檔量測。")
    else:
        fitz = loaded["pymupdf"]
        _, elapsed = timed(lambda: render_sample_pdf(fitz))
        say("  PDF 單頁轉 300dpi 影像    %7.0f ms" % elapsed)

    say()
    say("  參考值: 整條流程 (轉檔 + 前處理 + 辨識) 每頁約需上面總和的 3～6 倍。")
    say("  以每日 10～20 份、每份 1～2 頁估算，全部跑完應在數分鐘內。")


def render_sample_pdf(fitz):
    """臨時生一頁 PDF 再用 300dpi 算圖，量測轉檔速度。"""
    document = fitz.open()
    page = document.new_page(width=595, height=842)  # A4 in points
    page.insert_text((72, 120), "PROBE PAGE", fontsize=24)
    for index in range(30):
        y = 160 + index * 20
        page.draw_line(fitz.Point(60, y), fitz.Point(535, y))
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()

    reopened = fitz.open(stream=buffer.getvalue(), filetype="pdf")
    matrix = fitz.Matrix(300 / 72, 300 / 72)
    pixmap = reopened[0].get_pixmap(matrix=matrix)
    size = (pixmap.width, pixmap.height)
    reopened.close()
    return size


# --------------------------------------------------------------------------
# 報告輸出
# --------------------------------------------------------------------------

def write_report():
    """把報告寫成 txt。優先放執行檔旁邊，不行就往桌面、家目錄退。"""
    filename = "環境探測報告_%s.txt" % time.strftime("%Y%m%d_%H%M%S")
    for _, directory in candidate_write_dirs():
        if not directory:
            continue
        target = os.path.join(directory, filename)
        try:
            # 用 UTF-8 with BOM，記事本才不會把中文顯示成亂碼
            with open(target, "w", encoding="utf-8-sig") as handle:
                handle.write("\n".join(REPORT))
            return target
        except Exception:  # noqa: BLE001
            continue
    return None


def main():
    # Windows 主控台預設不是 UTF-8，先調整以免中文變亂碼
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    say("離線手寫轉 Excel 專案 — 環境探測報告")
    say("產生時間: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    say("這份報告只有環境資訊，不含任何個人資料，可以直接提供給開發者。")

    server = None
    try:
        probe_environment()
        probe_write_access()
        loaded = probe_packages()
        server = probe_localhost()
        probe_window()
        probe_performance(loaded)
    except Exception:  # noqa: BLE001
        section("探測過程發生未預期的錯誤")
        say(traceback.format_exc())

    section("結束")
    path = write_report()
    if path:
        say("報告已存檔: %s" % path)
        say("請把這個檔案的內容貼給開發者。")
    else:
        say("報告無法存檔 (所有位置都不能寫入)，請直接複製上面的畫面內容。")

    say()
    try:
        input("按 Enter 鍵結束…")
    except EOFError:
        pass
    if server is not None:
        server.shutdown()


if __name__ == "__main__":
    main()

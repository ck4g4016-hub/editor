# -*- coding: utf-8 -*-
"""紙本轉 Excel — 主程式。

把掃描的申請書 PDF 轉成內外網兩個檔案。全程在本機處理，不連任何網路。

執行後會出現一個小視窗，選要做什麼：

    設定樣板    第一次使用，或新增一種表格／新版本時用。
                在掃描影像上框出欄位，設定它對應到輸出的哪一欄。

    轉換        平常的工作。選掃描檔資料夾，程式辨識完開複核畫面，
                確認過再產生輸出檔。

樣板與輸出都放在執行檔旁邊的資料夾，整包搬走不會掉東西。

出問題的時候，複核畫面上的「產生診斷報告」會把整批的處理過程寫成一份
不含個資的純文字檔，放在「輸出\診斷」。程式整個當掉的話也會自動寫一份。
"""

import os
import platform
import sys
import threading
import time
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import resources  # noqa: E402

WORKSPACE = resources.workspace()
STORE = os.path.join(WORKSPACE, "樣板")
OUTPUT = os.path.join(WORKSPACE, "輸出")
REPORTS = os.path.join(OUTPUT, "診斷")


def ask_folder(title, initial=None):
    """跳出資料夾選取視窗。瀏覽器給不了本機路徑，這段一定要用桌面視窗。"""
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory(title=title, initialdir=initial or WORKSPACE)
    root.destroy()
    return folder or None


def message(title, text):
    import tkinter
    from tkinter import messagebox

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    messagebox.showinfo(title, text)
    root.destroy()


def serve(server, url, label):
    print()
    print("%s：%s" % (label, url))
    print("只綁 127.0.0.1，不對外開放。做完關掉這個視窗即可。")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def run_editor():
    from http.server import ThreadingHTTPServer

    from tools import template_editor

    folder = ask_folder("選擇樣本掃描檔所在的資料夾（用來建樣板）")
    if not folder:
        return
    os.makedirs(STORE, exist_ok=True)
    print("樣板資料夾：%s" % STORE)
    workspace = template_editor.Workspace(STORE, template_editor.collect([folder]))
    if not len(workspace.templates):
        message("還沒有樣板",
                "樣板資料夾是空的。\n\n"
                "第一次使用要先替每一種表格建立樣板，"
                "請參考 說明.txt 裡的步驟。")
        return
    server = ThreadingHTTPServer(("127.0.0.1", 0), template_editor.make_handler(workspace))
    serve(server, "http://127.0.0.1:%d/" % server.server_address[1], "樣板編輯器")


def run_convert():
    from http.server import ThreadingHTTPServer

    from pipeline import process
    from tools import review

    if not os.path.isdir(STORE) or not os.listdir(STORE):
        message("還沒有樣板", "請先執行「設定樣板」，替每一種表格框出欄位。")
        return

    folder = ask_folder("選擇要轉換的掃描檔資料夾")
    if not folder:
        return

    paths = review.collect([folder])
    if not paths:
        message("沒有檔案", "這個資料夾裡沒有 PDF。")
        return

    print("處理 %d 個檔案…" % len(paths))
    converter = process.Converter(STORE)
    records, unresolved = converter.run(
        paths, progress=lambda r: print("  %s" % r.describe()), keep_crops=True)
    print()
    print(process.summarise(records, unresolved))

    if not records:
        # 一件都認不出來，正是最需要診斷報告的時候 —— 沒有複核畫面可以按按鈕，
        # 所以直接寫一份出來，告訴使用者檔案在哪。
        from pipeline import diagnose
        path = diagnose.save(
            diagnose.build(converter.journal, version=resources.version()), REPORTS)
        message("沒有可複核的資料",
                "所有頁面都認不出來，或是對應的表格還沒定義欄位。\n\n"
                "請先執行「設定樣板」。\n\n"
                "已經寫了一份診斷報告：\n%s\n\n"
                "那份檔案不含個資，可以直接傳給開發者看是哪一步出問題。" % path)
        return

    state = {"records": records, "unresolved": unresolved, "out": OUTPUT,
             "journal": converter.journal}
    server = ThreadingHTTPServer(("127.0.0.1", 0), review.make_handler(state))
    serve(server, "http://127.0.0.1:%d/" % server.server_address[1], "複核介面")


def menu():
    import tkinter

    choice = {"value": None}
    root = tkinter.Tk()
    root.title("紙本轉 Excel")
    root.geometry("380x230")
    root.resizable(False, False)

    tkinter.Label(root, text="紙本轉 Excel", font=("", 15, "bold")).pack(pady=(22, 4))
    tkinter.Label(root, text="全程在本機處理，不連任何網路",
                  fg="#666").pack(pady=(0, 16))

    def pick(value):
        choice["value"] = value
        root.destroy()

    tkinter.Button(root, text="轉　換", width=26, height=2,
                   command=lambda: pick("convert")).pack(pady=4)
    tkinter.Button(root, text="設定樣板", width=26,
                   command=lambda: pick("editor")).pack(pady=4)

    root.mainloop()
    return choice["value"]


def crash_report(exception):
    """程式當掉時留下紀錄。

    打包成執行檔以後，主控台視窗會跟著程式一起關掉，錯誤訊息一閃而過 ——
    使用者只看得到「它壞了」，沒有東西可以回報。所以一定要落地成檔案。

    Traceback 裡只有程式的檔名與行號，不含辨識內容，可以直接外傳。
    """
    import traceback

    os.makedirs(REPORTS, exist_ok=True)
    path = os.path.join(REPORTS, "crash-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("紙本轉 Excel — 當機紀錄\n")
        handle.write("時間      %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        handle.write("程式版本  %s\n" % resources.version())
        handle.write("作業系統  %s\n" % platform.platform())
        handle.write("Python    %s\n\n" % sys.version.split()[0])
        handle.write("".join(traceback.format_exception(
            type(exception), exception, exception.__traceback__)))
        handle.write("\n這份紀錄只有程式的檔名與行號，不含個資，可以直接傳給開發者。\n")
    return path


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    print("紙本轉 Excel")
    print("工作資料夾：%s" % WORKSPACE)

    action = menu()
    try:
        if action == "convert":
            run_convert()
        elif action == "editor":
            run_editor()
    except Exception as error:                                      # noqa: BLE001
        path = crash_report(error)
        print("程式發生錯誤，已寫下紀錄：%s" % path)
        message("程式發生錯誤",
                "%s：%s\n\n"
                "已經寫了一份當機紀錄：\n%s\n\n"
                "那份檔案不含個資，可以直接傳給開發者。"
                % (type(error).__name__, error, path))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

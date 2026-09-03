# -*- coding: utf-8 -*-
"""紙本轉 Excel — 主程式。

把掃描的申請書 PDF 轉成內外網兩個檔案。全程在本機處理，不連任何網路。

執行後會出現一個小視窗，選要做什麼：

    新增表格    每一種表格做一次。告訴程式這種表格長什麼樣、正反面各是
                第幾頁，順便做底圖。做完才輪得到「設定樣板」——
                樣板編輯器開場要載入既有樣板，第一個樣板不可能由它自己生。

    設定樣板    在掃描影像上框出欄位，設定它對應到輸出的哪一欄。

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
    # 這個視窗只列資料夾不列檔案，使用者會以為資料夾是空的而不敢按。
    # 標題上直接講明白。
    folder = filedialog.askdirectory(
        title=title + "（視窗裡看不到 PDF 是正常的，選資料夾本身就好）",
        initialdir=initial or WORKSPACE, mustexist=True)
    root.destroy()
    return folder or None


def ask_file(title, initial=None):
    """跳出檔案選取視窗，只讓選 PDF。"""
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(title=title, initialdir=initial or WORKSPACE,
                                      filetypes=[("PDF", "*.pdf"), ("所有檔案", "*.*")])
    root.destroy()
    return path or None


def message(title, text):
    import tkinter
    from tkinter import messagebox

    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    messagebox.showinfo(title, text)
    root.destroy()


def serve(server, url, label, hint="", finished=None, pending=""):
    """把網頁介面跑起來，同時開一個小視窗讓使用者按完回主選單。

    原本是直接 serve_forever()，在主控台裡按 Ctrl+C 結束 ——
    但打包成執行檔之後根本沒有主控台可以按，程式就卡在那裡，
    使用者只能整個關掉，做完一件事就得重開。

    **這個視窗一關，瀏覽器那一頁就跟著失效。** 這件事害慘過一次：
    視窗設成永遠置頂，一直壓在瀏覽器上面，使用者很自然地把它關掉，
    伺服器跟著收掉 —— 但瀏覽器那一頁還好好地開著，看起來一切正常，
    一按「產生診斷報告」只得到「連不上程式：Failed to fetch」，
    而且程式早就不在了，連錯誤紀錄都寫不出來，輸出資料夾當然也是空的。
    整套東西看起來就像無聲無息地壞掉。

    所以現在三件事都改了：置頂只維持一下下（讓人看到它開了，但不擋路）、
    關視窗會先問過、真的做完了就不再囉嗦。

    finished  一個沒有參數的函式，回傳 True 代表瀏覽器那邊已經做完了。
    pending   還沒做完時，要提醒使用者去按的那個按鈕叫什麼。
    """
    import tkinter
    from tkinter import messagebox

    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    print()
    print("%s：%s" % (label, url))
    print("只綁 127.0.0.1，不對外開放。")

    root = tkinter.Tk()
    root.title(label)
    root.resizable(False, False)
    # 置頂只維持一下下。要讓使用者看到它開了，但不能一直擋在瀏覽器前面 ——
    # 擋著的話使用者只會想把它關掉，而關掉就等於把那一頁一起關了。
    root.attributes("-topmost", True)
    root.after(1500, lambda: root.attributes("-topmost", False))

    frame = tkinter.Frame(root, padx=22, pady=18)
    frame.pack()
    tkinter.Label(frame, text=label, font=("", 13, "bold")).pack()
    tkinter.Label(frame, text="已經在瀏覽器打開了。", fg="#666").pack(pady=(4, 0))
    if hint:
        tkinter.Label(frame, text=hint, fg="#666", justify="center").pack(pady=(6, 0))
    tkinter.Label(frame, text=url, fg="#888", font=("", 8)).pack(pady=(6, 0))
    tkinter.Label(frame,
                  text="這個視窗一關，瀏覽器那一頁就失效了。\n"
                       "瀏覽器那邊做完之後，回來按這個按鈕。",
                  fg="#8a3d3d", justify="center").pack(pady=(12, 6))

    def close():
        """關掉之前先確認。真的做完了就不囉嗦。"""
        try:
            done = bool(finished()) if finished is not None else False
        except Exception:                                           # noqa: BLE001
            done = False
        if not done:
            note = ("瀏覽器那一頁還沒按過「%s」。" % pending) if pending else ""
            if not messagebox.askyesno(
                    "確定要關掉嗎？",
                    "%s\n\n關掉之後那一頁就失效了，按什麼都只會顯示連不上，"
                    "這一批要從頭再跑一次。\n\n確定要關嗎？" % note,
                    parent=root, default="no"):
                return
        root.destroy()

    tkinter.Button(frame, text="做完了，回主選單", width=24, height=2,
                   command=close).pack()
    # 右上角的 X 走同一條路，不然使用者一樣會不小心把伺服器關掉
    root.protocol("WM_DELETE_WINDOW", close)

    root.mainloop()
    server.shutdown()
    server.server_close()
    print("已回到主選單")


def ask_new_form():
    """新增表格的填寫視窗。一次問完，回傳一個 dict 或 None。

    做成單一視窗而不是一連串的小對話 —— 這件事要做七次，
    每次跳五個對話框按到最後會想砸電腦。
    """
    import tkinter
    from tkinter import messagebox

    from pipeline import render
    from tools import newform

    pdf = ask_file("選擇這種表格的 PDF（空白原稿或任何一份掃描件都可以）")
    if not pdf:
        return None
    try:
        total = render.page_count(pdf)
    except Exception as error:  # noqa: BLE001
        message("讀不到這個 PDF", "%s：%s" % (type(error).__name__, error))
        return None

    result = {}
    root = tkinter.Tk()
    root.title("新增表格")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    frame = tkinter.Frame(root, padx=16, pady=14)
    frame.pack()

    tkinter.Label(frame, text=os.path.basename(pdf), font=("", 10, "bold")).grid(
        row=0, column=0, columnspan=2, sticky="w")
    tkinter.Label(frame, text="共 %d 頁" % total, fg="#666").grid(
        row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

    entries = {}
    rows = [("code", "代號", "英數，例如 F。會變成資料夾名稱"),
            ("name", "表格全名", "給人看的，例如 地價稅自用住宅申請書(舊)"),
            ("front", "正面在第幾頁", "從 1 起算"),
            ("back", "背面在第幾頁", "單面表格留空")]
    for index, (key, label, hint) in enumerate(rows, start=2):
        tkinter.Label(frame, text=label).grid(row=index * 2, column=0, sticky="e", padx=(0, 8))
        entry = tkinter.Entry(frame, width=34)
        entry.grid(row=index * 2, column=1, sticky="w")
        entries[key] = entry
        tkinter.Label(frame, text=hint, fg="#888", font=("", 8)).grid(
            row=index * 2 + 1, column=1, sticky="w", pady=(0, 6))
    entries["front"].insert(0, "1")

    tkinter.Label(frame, text="底圖來源").grid(row=12, column=0, sticky="ne", padx=(0, 8))
    source = tkinter.StringVar(value=newform.BLANK)
    box = tkinter.Frame(frame)
    box.grid(row=12, column=1, sticky="w")
    for value, text in ((newform.BLANK, "這份是空白原稿"),
                        (newform.COMPOSE, "這份是填過的掃描件，用多份合成"),
                        (newform.NONE, "先不做底圖")):
        tkinter.Radiobutton(box, text=text, variable=source, value=value,
                            anchor="w").pack(anchor="w")
    tkinter.Label(frame,
                  text="底圖是「只有印刷版面、沒有手寫」的參考影像，用來把版面減掉。\n"
                       "別的單位送來的影印本要選第二個 —— 影印會歪會縮，\n"
                       "原版空白表格對不上影印件。合成至少要三份。",
                  fg="#888", font=("", 8), justify="left").grid(
        row=13, column=1, sticky="w", pady=(4, 10))

    def submit():
        code, problem = newform.valid_code(entries["code"].get())
        if problem:
            messagebox.showwarning("代號不行", problem, parent=root)
            return
        name = entries["name"].get().strip()
        if not name:
            messagebox.showwarning("還沒填", "請填表格全名", parent=root)
            return
        numbers = {}
        for key, required in (("front", True), ("back", False)):
            text = entries[key].get().strip()
            if not text:
                if required:
                    messagebox.showwarning("還沒填", "正面頁碼一定要填", parent=root)
                    return
                numbers[key] = None
                continue
            if not text.isdigit() or not 1 <= int(text) <= total:
                messagebox.showwarning("頁碼不對",
                                       "「%s」不是 1 到 %d 之間的頁碼" % (text, total),
                                       parent=root)
                return
            numbers[key] = int(text)
        result.update(pdf=pdf, code=code, name=name, source=source.get(), **numbers)
        root.destroy()

    buttons = tkinter.Frame(frame)
    buttons.grid(row=14, column=0, columnspan=2, pady=(6, 0))
    tkinter.Button(buttons, text="建立", width=12, command=submit).pack(side="left", padx=4)
    tkinter.Button(buttons, text="取消", width=8, command=root.destroy).pack(side="left", padx=4)

    root.mainloop()
    return result or None


def run_new_form():
    """建立一種表格：樣板、底圖，然後檢查對位。"""
    from tools import newform

    answer = ask_new_form()
    if not answer:
        return

    os.makedirs(STORE, exist_ok=True)
    code, chosen = answer["code"], answer["pdf"]

    # 同一個資料夾裡的 PDF 都算這種表格的樣本。
    # 底圖檢查一定要拿「實際掃描件」去對 —— 拿空白原稿跟它自己比，
    # 覆蓋率永遠是 1.000，看起來很漂亮但什麼都沒驗到。
    folder = os.path.dirname(os.path.abspath(chosen))
    samples = sorted(os.path.join(folder, n) for n in os.listdir(folder)
                     if n.lower().endswith(".pdf"))
    others = [p for p in samples if os.path.abspath(p) != os.path.abspath(chosen)]

    target, notes = newform.create(STORE, code, answer["name"], chosen,
                                   answer["front"], answer["back"])
    lines = ["已建立樣板：%s（%s）" % (code, answer["name"])] + notes

    if answer["source"] == newform.BLANK:
        _, note = newform.base_from_blank(STORE, code, chosen, answer["front"])
        lines.append(note)
        if answer["back"]:
            # 背面也做一張，這樣背面的欄位一樣能把印刷版面減掉
            _, note = newform.base_from_blank(STORE, code, chosen, answer["back"], "back")
            lines.append(note)
    elif answer["source"] == newform.COMPOSE:
        try:
            _, note = newform.base_from_scans(STORE, code, samples)
            lines.append(note)
        except ValueError as error:
            lines.append("底圖沒做成：%s" % error)

    if answer["source"] != newform.NONE:
        # 有別的檔就拿別的檔驗，只有一個檔才退而求其次拿它自己驗
        checks, worst = newform.check(STORE, code, others or [chosen])
        lines.append("")
        if others:
            lines.append("拿同資料夾的 %d 個檔案檢查對位："
                         % len(others))
        else:
            lines.append("資料夾裡只有這一個 PDF，只能拿它自己檢查，")
            lines.append("這個數字不代表真實掃描件對得準不準。")
            lines.append("把實際掃描件放進同一個資料夾再做一次會比較準。")
        lines.extend("    " + line for line in checks[:10])
        if worst < 0.75 and others:
            lines.append("")
            lines.append("⚠ 覆蓋率偏低，欄位框可能整個偏掉抓到隔壁格。")
            lines.append("   影印本請改用「多份合成」，並且多給幾份。")

    for line in lines:
        print(line)
    message("新增表格完成", "\n".join(lines) + "\n\n接下來按「設定樣板」框欄位。")


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
                "樣板資料夾是空的，樣板編輯器沒有東西可以編。\n\n"
                "請先按「新增表格」，把每一種表格建立起來，再回來框欄位。")
        return
    server = ThreadingHTTPServer(("127.0.0.1", 0), template_editor.make_handler(workspace))
    serve(server, "http://127.0.0.1:%d/" % server.server_address[1], "樣板編輯器",
          "每一種表格框完欄位都要按「儲存」，\n沒存的話關掉就沒了。",
          finished=lambda: workspace.saved, pending="儲存")


def run_convert():
    from http.server import ThreadingHTTPServer

    from pipeline import process
    from tools import review

    if not os.path.isdir(STORE) or not os.listdir(STORE):
        message("還沒有樣板", "請先按「新增表格」建立表格，再按「設定樣板」框出欄位。")
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
    print(process.summarise(records, unresolved, converter.unknown))

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

    # 診斷報告先寫一份出來，不要等使用者去按按鈕。
    # 報告是出問題時唯一的線索，不能讓它取決於「複核畫面還活著沒有」——
    # 實測就發生過：小視窗被關掉，伺服器跟著收掉，按鈕按下去只得到
    # 「連不上程式」，資料夾裡一份報告也沒有，等於整批白跑還查不出原因。
    # 按鈕還是留著：那一份會多帶承辦人自己寫的意見，比這一份有用。
    from pipeline import diagnose

    try:
        early = diagnose.save(
            diagnose.build(converter.journal, version=resources.version()), REPORTS)
        print("已先寫一份診斷報告：%s" % early)
    except Exception as error:                                      # noqa: BLE001
        print("診斷報告寫不出來：%s: %s" % (type(error).__name__, error))
        early = None

    # 切出來的每一格也存一份圖。診斷報告是遮罩過的形狀，看得出「讀成幾個字」，
    # 看不出「那一格長什麼樣」—— 而那正是分辨「切歪了」與「認不出來」的關鍵。
    # 這些圖含個資，所以放在另一個明講的資料夾，不進診斷。
    try:
        where, count = process.dump_cells(records, OUTPUT)
        if count:
            print("已存 %d 張辨識用的裁切圖：%s" % (count, where))
    except Exception as error:                                      # noqa: BLE001
        print("裁切圖存不出來：%s: %s" % (type(error).__name__, error))

    state = {"records": records, "unresolved": unresolved, "out": OUTPUT,
             "journal": converter.journal, "unknown": converter.unknown}
    server = ThreadingHTTPServer(("127.0.0.1", 0), review.make_handler(state))
    serve(server, "http://127.0.0.1:%d/" % server.server_address[1], "複核介面",
          "確認完要按「產生輸出檔」，\n檔案才會出現在「輸出」資料夾。"
          + ("\n（診斷報告已經先寫了一份在「輸出\\診斷」）" if early else ""),
          finished=lambda: state.get("exported"), pending="產生輸出檔")


def run_dictionaries():
    """把兩份字典開起來給人改。

    路名字典缺一條路，那一件的門牌就會被標成「路街名不在字典裡」；
    地段字典是空的，段名就完全沒有東西可以驗。兩份都是純文字檔，
    改完存檔，下次轉換就生效 —— 不必重做樣板，也不必等我出新版。
    """
    import subprocess

    from pipeline import lexicon

    os.makedirs(STORE, exist_ok=True)
    roads = lexicon.ensure_roads(STORE)
    sections = lexicon.ensure_sections(STORE)
    opened = []
    for target in (roads, sections):
        try:
            os.startfile(target)                # noqa: S606  Windows 專用
            opened.append(target)
        except AttributeError:                  # 不是 Windows，開發機上會走到這裡
            subprocess.Popen(["xdg-open", target])
            opened.append(target)
        except OSError as error:
            print("開不起來 %s：%s" % (target, error))
    message("字典",
            "已經開了兩個檔案：\n\n"
            "roads.txt      路街名，一行一個\n"
            "sections.txt   地段名，一行一個\n\n"
            "「## 區名」以下的名稱屬於該區。改完存檔就生效，\n"
            "不必重做樣板，也不必等新版程式。\n\n"
            "門牌被標成「路街名不在字典裡」的時候，\n"
            "就是把那條路補進 roads.txt。\n\n"
            "檔案放在：\n%s" % STORE)


def menu():
    """主選單。回傳選了什麼，直接關掉視窗回傳 None（代表要結束程式）。"""
    import tkinter

    choice = {"value": None}
    root = tkinter.Tk()
    root.title("紙本轉 Excel")
    root.geometry("400x375")
    root.resizable(False, False)

    tkinter.Label(root, text="紙本轉 Excel", font=("", 15, "bold")).pack(pady=(20, 2))
    tkinter.Label(root, text="全程在本機處理，不連任何網路",
                  fg="#666").pack(pady=(0, 4))
    tkinter.Label(root, text="工作資料夾：%s" % WORKSPACE,
                  fg="#999", font=("", 8), wraplength=370).pack(pady=(0, 12))

    def pick(value):
        choice["value"] = value
        root.destroy()

    tkinter.Button(root, text="轉　換", width=28, height=2,
                   command=lambda: pick("convert")).pack(pady=4)
    tkinter.Button(root, text="設定樣板", width=28,
                   command=lambda: pick("editor")).pack(pady=4)
    tkinter.Button(root, text="新增表格", width=28,
                   command=lambda: pick("newform")).pack(pady=4)
    tkinter.Button(root, text="字　典", width=28,
                   command=lambda: pick("roads")).pack(pady=4)
    tkinter.Label(root, text="做完會回到這個畫面。要結束就關掉這個視窗。",
                  fg="#999", font=("", 8)).pack(pady=(12, 0))

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

    actions = {"convert": run_convert, "editor": run_editor, "newform": run_new_form,
               "roads": run_dictionaries}

    # 一直回到主選單，直到使用者把它關掉 —— 建樣板要做七次，
    # 做完一件就整個結束的話等於要重開七次。
    while True:
        action = menu()
        if action is None:
            print("結束")
            return 0
        try:
            actions[action]()
        except (Exception, SystemExit) as error:                    # noqa: BLE001
            # 一件事失敗不該讓整個程式關掉，記下來然後回主選單再試
            path = crash_report(error)
            print("發生錯誤，已寫下紀錄：%s" % path)
            message("發生錯誤",
                    "%s：%s\n\n"
                    "已經寫了一份紀錄：\n%s\n\n"
                    "那份檔案不含個資，可以直接傳給開發者。\n"
                    "按確定回到主選單，可以再試一次。"
                    % (type(error).__name__, error, path))


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""複核介面：確認辨識結果，改掉錯的，然後產生輸出檔。

介面是本機網頁，只綁 127.0.0.1，不對外開放也不連任何外部資源。

每個欄位都會把原圖裁切秀出來，讓人對照著看 —— 光給文字沒辦法判斷對錯。
驗證沒過、辨識信心偏低、或是僅供參考的欄位會標紅，其餘的通常掃過去就好。

影像只留在記憶體，程式關掉就什麼都不剩，不落地到暫存資料夾。

用法：

    python tools/review.py 樣板資料夾 掃描檔資料夾 --out 輸出資料夾
"""

import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import diagnose, fields as fieldmod, resources  # noqa: E402
from tools import localserver  # noqa: E402
from pipeline import output, process  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = resources.path("editor", "review.html")

# 複核畫面上欄位的排列順序。前三個是必須辨識正確的，放最前面。
ORDER = ["address", "id_number", "doc_number", "district", "name", "section", "land_number"]


def make_handler(state, guard):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, mime):
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, code=200):
            self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            refused = guard.check(self)
            if refused:
                localserver.deny(self, *refused)
                return
            route = urlparse(self.path)
            query = parse_qs(route.query)

            if route.path in ("/", "/index.html"):
                with open(PAGE, "rb") as handle:
                    self._send(200, handle.read(), "text/html; charset=utf-8")
            elif route.path == "/api/records":
                self._json(describe(state))
            elif route.path == "/api/crop":
                index = int(query.get("record", ["-1"])[0])
                column = query.get("column", [""])[0]
                records = state["records"]
                if 0 <= index < len(records) and column in records[index].crops:
                    self._send(200, records[index].crops[column], "image/png")
                else:
                    self._send(404, b"no crop", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            refused = guard.check(self, write=True)
            if refused:
                localserver.deny(self, *refused)
                return
            route = urlparse(self.path).path
            if route not in ("/api/export", "/api/diagnose"):
                self._send(404, b"not found", "text/plain")
                return
            # 這裡不接住例外的話，連線會直接斷掉，瀏覽器那邊只會永遠停在
            # 「整理中…」，使用者看不到任何原因，我也拿不到線索。
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if route == "/api/diagnose":
                    self._diagnose(payload)
                else:
                    self._export(payload)
            except Exception as error:                              # noqa: BLE001
                self._fail(error)

        def _fail(self, error):
            import traceback

            detail = "".join(traceback.format_exception(
                type(error), error, error.__traceback__))
            print(detail)
            path = None
            try:
                folder = os.path.join(state["out"], "診斷")
                os.makedirs(folder, exist_ok=True)
                path = os.path.join(folder, "error-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(detail)
            except Exception:                                       # noqa: BLE001
                pass
            self._json({"ok": False,
                        "error": "%s：%s" % (type(error).__name__, error),
                        "detail": detail,
                        "path": path}, 500)

        def _export(self, payload):
            rows = payload.get("records", [])
            if not rows:
                self._json({"ok": False, "error": "沒有資料可以輸出"}, 400)
                return
            written = output.write_all(rows, state["out"])
            for path in written:
                print("已產出: %s" % path)
            state["exported"] = True
            self._json({"ok": True, "files": [os.path.basename(p) for p in written]})

        def _diagnose(self, payload):
            journal = state.get("journal")
            if journal is None:
                self._json({"ok": False, "error": "這一批沒有留下診斷資料"}, 400)
                return
            text = diagnose.build(journal, notes=payload.get("notes"),
                                  version=resources.version())
            path = diagnose.save(text, os.path.join(state["out"], "診斷"))
            print("已產出診斷報告: %s" % path)
            # 順手在畫面上把全文顯示出來 —— 要使用者「送出前自己看過」，
            # 就得讓他不必先去翻資料夾才看得到內容。
            self._json({"ok": True, "path": path, "text": text})

        def log_message(self, *args):
            pass

    return Handler


def describe(state):
    columns = [{"key": key, "name": fieldmod.COLUMNS[key]}
               for key in ORDER if key in fieldmod.COLUMNS]
    records = []
    for record in state["records"]:
        records.append({
            "source": record.describe(),
            "values": record.values,
            "raw": record.raw,
            "flags": record.flagged(),
            "crops": sorted(record.crops),
        })
    unresolved = ["%s 第 %s 頁　%s"
                  % (os.path.basename(document.pages[0].source),
                     "、".join(str(p.index + 1) for p in document.pages),
                     document.problem or "")
                  for document in state["unresolved"]]
    unknown = ["%s 第 %d 頁（特徵點 %d、差距 %.1f）"
               % (item["file"], item["page"], item["inliers"], item["margin"])
               for item in state.get("unknown") or []]
    return {"columns": columns, "records": records,
            "unresolved": unresolved, "unknown": unknown}


def collect(targets):
    r"""把資料夾裡的 PDF 都找出來，**含子資料夾**。

    樣本是一種表格一個子資料夾（樣本\F、樣本\G…），
    只看第一層的話選最上層那個資料夾會一個檔都找不到。
    """
    paths = []
    for target in targets or []:
        if os.path.isdir(target):
            for folder, _dirs, names in os.walk(target):
                paths.extend(sorted(os.path.join(folder, n) for n in names
                                    if n.lower().endswith(".pdf")))
        else:
            paths.append(target)
    return sorted(paths)


def main():
    parser = argparse.ArgumentParser(description="複核辨識結果並產生輸出檔",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("store", help="樣板資料夾")
    parser.add_argument("targets", nargs="+", help="掃描檔資料夾或個別 PDF")
    parser.add_argument("--out", default="輸出", help="輸出資料夾")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    paths = collect(args.targets)
    if not paths:
        raise SystemExit("找不到任何 PDF")

    print("處理 %d 個檔案…" % len(paths))
    converter = process.Converter(args.store)
    records, unresolved = converter.run(
        paths, progress=lambda r: print("  %s" % r.describe()), keep_crops=True)

    print()
    print(process.summarise(records, unresolved, converter.unknown))
    if not records:
        raise SystemExit("沒有任何可以複核的資料 —— 請先用樣板編輯器定義欄位")

    # 先寫一份診斷報告，理由同 app.py：報告不能取決於複核畫面還活著沒有。
    try:
        print("已先寫一份診斷報告：%s" % diagnose.save(
            diagnose.build(converter.journal, version=resources.version()),
            os.path.join(args.out, "診斷")))
    except Exception as error:                                      # noqa: BLE001
        print("診斷報告寫不出來：%s: %s" % (type(error).__name__, error))

    state = {"records": records, "unresolved": unresolved, "out": args.out,
             "journal": converter.journal, "unknown": converter.unknown}
    guard = localserver.Guard()
    server = ThreadingHTTPServer(("127.0.0.1", args.port),
                                 make_handler(state, guard))
    guard.port = server.server_address[1]
    url = guard.url()
    print()
    print("複核介面已啟動：%s" % url)
    print("只綁 127.0.0.1，不對外開放。按 Ctrl+C 結束。")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已結束")
    return 0


if __name__ == "__main__":
    sys.exit(main())

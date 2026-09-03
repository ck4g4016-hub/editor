# -*- coding: utf-8 -*-
"""樣板編輯器：在掃描影像上框出欄位，設定它對應到輸出的哪一欄。

介面是本機網頁 —— 只綁 127.0.0.1，不對外開放，也不連任何外部資源。
選這個做法而不是桌面視窗，是因為環境探測確認過本機伺服器可用，
而且畫面調整起來比 GUI 工具箱快得多。

啟動時會做一次前置作業：把樣本掃描件分類，每一種表格挑一張正面出來。
所以開起來要等幾秒。

原本還會偵測樣本上的紅筆標記當作欄位候選框，實際用起來沒有幫助
（框的位置跟圈的位置本來就不一樣，還是得自己拖），已經拿掉。

用法：

    python tools/template_editor.py 樣板資料夾 --samples 掃描件資料夾

然後用瀏覽器打開畫面上印出的網址。
"""

import argparse
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import fields as fieldmod, resources  # noqa: E402
from pipeline import layout, render  # noqa: E402
from pipeline.process import CRITICAL  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = resources.path("editor", "page.html")

# 網頁上顯示的寬度。實際座標一律用 300dpi，顯示端再換算。
VIEW_WIDTH = 1000


class Workspace:
    """把每一種表格可以拿來框欄位的頁面準備好。

    影像是個資，只留在記憶體，不落地到暫存資料夾 —— 程式關掉就什麼都不剩。
    所以不預先把每一頁都算出來（一張 300dpi A4 彩色就 26MB），
    只記住「哪個檔第幾頁」，畫面要看哪一張才現算，並且留最後幾張快取。
    """

    MAX_VIEWS = 12          # 每種表格最多列幾張可選頁面
    CACHE = 3               # 現算過的影像留幾張

    def __init__(self, store, sample_paths):
        self.store = store
        # 有沒有存過。主程式的小視窗要靠它決定關掉之前要不要多問一句 ——
        # 框了半小時沒存就關掉，等於白做。
        self.saved = False
        self.templates = layout.TemplateSet.load(store)
        self.views = {}     # code -> [ {label, role, source, index, rotation} ]
        self._cache = []    # [(code, view index, image)]
        self._prepare(sample_paths)

    def _prepare(self, sample_paths):
        if not sample_paths:
            return
        print("正在分類樣本…")
        pages = layout.classify_pages(sample_paths, self.templates)

        # 切成一件一件，這樣才知道哪一張背面是配哪一張正面的 ——
        # 使用者要框背面欄位時，看到的必須是同一件的背面。
        counts = {}
        for document in layout.split_documents(pages):
            if not document.complete:
                continue
            code = document.code
            views = self.views.setdefault(code, [])
            if len(views) >= self.MAX_VIEWS:
                continue
            counts[code] = counts.get(code, 0) + 1
            number = counts[code]
            # 正反面照**位置**認，不看分類結果 —— 一件固定兩頁，第一頁是正面。
            # 空白背面本來就分類不出來，靠分類去挑的話那一面根本不會出現在
            # 清單上，使用者就框不到背面的欄位。
            for order, page in enumerate(document.pages):
                if len(views) >= self.MAX_VIEWS:
                    break
                role = layout.FRONT if order == 0 else layout.BACK
                views.append({
                    "label": "第 %d 份 %s" % (number,
                                              "正面" if role == layout.FRONT else "背面"),
                    "role": role,
                    "source": page.source,
                    "index": page.index,
                    # 空白或認不出來的那一面拿不到轉向，跟著正面走 ——
                    # 同一張紙的兩面，掃進來的方向一定一樣。
                    "rotation": page.rotation or document.front.rotation,
                })

        for code, views in self.views.items():
            print("  %s：%d 張可框選的頁面" % (code, len(views)))

    def image(self, code, which):
        """第 which 張頁面的影像。現算，並留最後幾張快取。"""
        views = self.views.get(code) or []
        if not 0 <= which < len(views):
            return None
        for key_code, key_which, image in self._cache:
            if key_code == code and key_which == which:
                return image
        view = views[which]
        image = render.rotate(
            render.render(view["source"], view["index"], dpi=render.FULL_DPI, gray=False),
            view["rotation"])
        self._cache.append((code, which, image))
        del self._cache[:-self.CACHE]
        return image

    def codes(self):
        seen = []
        for template in self.templates.templates:
            if template.code not in [c["code"] for c in seen]:
                seen.append({"code": template.code, "name": template.name})
        return seen

    def png(self, code, which):
        image = self.image(code, which)
        if image is None:
            return None, 1.0
        scale = VIEW_WIDTH / float(image.shape[1])
        small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ok, buffer = cv2.imencode(".png", small)
        return (buffer.tobytes() if ok else None), scale

    def describe(self, code):
        views = self.views.get(code) or []
        name = next((t.name for t in self.templates.templates if t.code == code), code)
        first = self.image(code, 0) if views else None
        width = first.shape[1] if first is not None else 0
        height = first.shape[0] if first is not None else 0
        return {
            "code": code,
            "name": name,
            "columns": fieldmod.COLUMNS,
            "kinds": fieldmod.KINDS,
            "default_kinds": fieldmod.DEFAULT_KIND,
            "critical": list(CRITICAL),
            "fields": [f.to_dict() for f in fieldmod.load(self.store, code)],
            "views": [{"label": v["label"], "role": v["role"]} for v in views],
            "has_base": os.path.isfile(os.path.join(self.store, code, "base.png")),
            "has_back_base": os.path.isfile(os.path.join(self.store, code, "base_back.png")),
            "width": width,
            "height": height,
            "scale": VIEW_WIDTH / float(width) if width else 1.0,
        }


def make_handler(workspace):
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
            route = urlparse(self.path)
            query = parse_qs(route.query)

            if route.path in ("/", "/index.html"):
                with open(PAGE, "rb") as handle:
                    self._send(200, handle.read(), "text/html; charset=utf-8")
            elif route.path == "/api/codes":
                self._json(workspace.codes())
            elif route.path == "/api/template":
                self._json(workspace.describe(query.get("code", [""])[0]))
            elif route.path == "/api/image":
                body, _ = workspace.png(query.get("code", [""])[0],
                                        int(query.get("view", ["0"])[0]))
                if body is None:
                    self._send(404, b"no sample for this form", "text/plain")
                else:
                    self._send(200, body, "image/png")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            route = urlparse(self.path)
            if route.path != "/api/template":
                self._send(404, b"not found", "text/plain")
                return
            # 任何一個沒接住的例外都會把連線扯斷，瀏覽器只會說
            # 「Failed to fetch」—— 那句話什麼線索都沒有。寧可回一份
            # traceback 讓人直接看到是哪一行。
            try:
                code = parse_qs(route.query).get("code", [""])[0]
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                items = [fieldmod.Field.from_dict(item)
                         for item in payload.get("fields", [])]
                target = fieldmod.save(workspace.store, code, items)
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
                return
            except Exception as error:                              # noqa: BLE001
                import traceback
                detail = "".join(traceback.format_exception(
                    type(error), error, error.__traceback__))
                print(detail)
                self._json({"ok": False,
                            "error": "%s：%s" % (type(error).__name__, error),
                            "detail": detail}, 500)
                return
            workspace.saved = True
            print("已儲存 %s：%d 個欄位 → %s" % (code, len(items), target))
            self._json({"ok": True, "count": len(items)})

        def log_message(self, *args):
            pass

    return Handler


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
    parser = argparse.ArgumentParser(description="樣板編輯器",
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("store", help="樣板資料夾")
    parser.add_argument("--samples", nargs="+", help="樣本掃描檔資料夾或個別 PDF")
    parser.add_argument("--port", type=int, default=0, help="指定埠號，預設隨機")
    parser.add_argument("--no-browser", action="store_true", help="不要自動開瀏覽器")
    args = parser.parse_args()

    workspace = Workspace(args.store, collect(args.samples))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(workspace))
    url = "http://127.0.0.1:%d/" % server.server_address[1]

    print()
    print("樣板編輯器已啟動：%s" % url)
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

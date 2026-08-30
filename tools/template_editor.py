# -*- coding: utf-8 -*-
"""樣板編輯器：在掃描影像上框出欄位，設定它對應到輸出的哪一欄。

介面是本機網頁 —— 只綁 127.0.0.1，不對外開放，也不連任何外部資源。
選這個做法而不是桌面視窗，是因為環境探測確認過本機伺服器可用，
而且畫面調整起來比 GUI 工具箱快得多。

啟動時會做一次前置作業：把樣本掃描件分類，每一種表格挑一張正面出來，
偵測上面的紅筆標記當作欄位候選框。所以開起來要等幾秒。

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

from pipeline import fields as fieldmod  # noqa: E402
from pipeline import layout, redmarks, render  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(os.path.dirname(HERE), "editor", "page.html")

# 網頁上顯示的寬度。實際座標一律用 300dpi，顯示端再換算。
VIEW_WIDTH = 1000


class Workspace:
    """把每一種表格要用的影像與候選框準備好，放在記憶體裡。

    影像是個資，不落地到暫存資料夾 —— 程式關掉就什麼都不剩。
    """

    def __init__(self, store, sample_paths):
        self.store = store
        self.templates = layout.TemplateSet.load(store)
        self.sheets = {}
        self._prepare(sample_paths)

    def _prepare(self, sample_paths):
        if not sample_paths:
            return
        print("正在分類樣本…")
        pages = layout.classify_pages(sample_paths, self.templates)
        for page in pages:
            if page.role != layout.FRONT or page.code in self.sheets:
                continue
            print("  %s ← %s 第 %d 頁" % (page.code, os.path.basename(page.source), page.index + 1))
            scan = render.rotate(
                render.render(page.source, page.index, dpi=render.FULL_DPI, gray=False),
                page.rotation)
            base_path = os.path.join(self.store, page.code, "base.png")
            base = cv2.imread(base_path, cv2.IMREAD_COLOR) if os.path.isfile(base_path) else None
            try:
                marks = redmarks.find(scan, base)
            except ValueError as exc:
                print("    紅筆偵測失敗：%s" % exc)
                marks = []
            self.sheets[page.code] = {
                "image": scan,
                "has_base": base is not None,
                "candidates": [{"x": x, "y": y, "w": w, "h": h} for _, x, y, w, h in marks],
            }

    def codes(self):
        seen = []
        for template in self.templates.templates:
            if template.code not in [c["code"] for c in seen]:
                seen.append({"code": template.code, "name": template.name})
        return seen

    def png(self, code):
        sheet = self.sheets.get(code)
        if sheet is None:
            return None
        image = sheet["image"]
        scale = VIEW_WIDTH / float(image.shape[1])
        small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ok, buffer = cv2.imencode(".png", small)
        return buffer.tobytes() if ok else None

    def describe(self, code):
        sheet = self.sheets.get(code)
        name = next((t.name for t in self.templates.templates if t.code == code), code)
        stored = fieldmod.load(self.store, code)
        info = {
            "code": code,
            "name": name,
            "columns": fieldmod.COLUMNS,
            "kinds": fieldmod.KINDS,
            "fields": [f.to_dict() for f in stored],
            "candidates": sheet["candidates"] if sheet else [],
            "has_base": bool(sheet and sheet["has_base"]),
            "width": sheet["image"].shape[1] if sheet else 0,
            "height": sheet["image"].shape[0] if sheet else 0,
        }
        info["scale"] = VIEW_WIDTH / float(info["width"]) if info["width"] else 1.0
        return info


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
                body = workspace.png(query.get("code", [""])[0])
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
            code = parse_qs(route.query).get("code", [""])[0]
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            items = [fieldmod.Field.from_dict(item) for item in payload.get("fields", [])]
            try:
                target = fieldmod.save(workspace.store, code, items)
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
                return
            print("已儲存 %s：%d 個欄位 → %s" % (code, len(items), target))
            self._json({"ok": True, "count": len(items)})

        def log_message(self, *args):
            pass

    return Handler


def collect(targets):
    paths = []
    for target in targets or []:
        if os.path.isdir(target):
            paths.extend(sorted(
                os.path.join(target, n) for n in os.listdir(target) if n.lower().endswith(".pdf")))
        else:
            paths.append(target)
    return paths


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

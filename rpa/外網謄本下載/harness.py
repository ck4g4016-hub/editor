# -*- coding: utf-8 -*-
"""在真的 Chromium 裡，拿仿造的領件區頁面跑 find.js / click.js。

伺服器時間（HEAD 回應的 Date）可以調快調慢、或故意不給，用來模擬
「電腦時間跟系統時間差多少」。
"""
import datetime
import email.utils
import glob
import http.server
import os
import threading

HERE = os.path.dirname(os.path.abspath(__file__))

from playwright.sync_api import sync_playwright

TW = datetime.timezone(datetime.timedelta(hours=8))
JS = {name: open(os.path.join(HERE, "%s.js" % name), encoding="utf-8").read() for name in ("find", "click")}
STATE = {"skew": 0, "date": True, "html": ""}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def date_time_string(self, timestamp=None):
        if not STATE["date"] and self.command == "HEAD":
            return "not-a-date"
        now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=STATE["skew"])
        return email.utils.format_datetime(now, usegmt=True)

    def do_HEAD(self):
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        body = STATE["html"].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def roc(dt):
    return "%d/%02d/%02d %02d:%02d:%02d" % (dt.year - 1911, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def page_html(rows, layout="normal"):
    head = "<tr><th></th><th>申請日期時間</th><th>資料來源</th><th>資料收件號</th><th>資料種類</th><th>張數</th><th>狀態</th><th>下載日期時間</th><th>錯誤描述</th></tr>"
    out = []
    for r in rows:
        cells = ['<td style="display:none">x</td>',
                 "<td>%s</td>" % roc(r["t"]), "<td>新北市政府三峽區</td>", "<td>%s</td>" % r["no"],
                 "<td>土地建物登記資料</td>", "<td>4</td>",
                 "<td><div><a href=\"#\" onclick=\"REG('%s');return false;\">查詢</a> 下載 查詢明細</div></td>" % r["no"],
                 "<td>%s</td>" % ("115/10/12 16:00:00" if r.get("done") else ""), "<td></td>"]
        if layout == "no-hidden":
            cells = cells[1:]
        out.append("<tr>%s</tr>" % "".join(cells))
    return ("<html><body><script>window.clicked=[];function REG(n){window.clicked.push(n);}</script>"
            "<table class='tab5'>%s%s</table></body></html>" % (head, "".join(out)))


def run(rows, sent_times, server_skew=0, date=True, layout="normal"):
    """rows: 領件區每一列；sent_times: Excel I 欄（照 Excel 順序）。回傳每一列 Excel 的結果。"""
    STATE.update(skew=server_skew, date=date, html=page_html(rows, layout))
    times_text = "".join("送件 %s|" % t.strftime("%Y-%m-%d %H:%M:%S") for t in sent_times)
    results = []
    with sync_playwright() as p:
        exe = glob.glob("/opt/pw-browsers/chromium-1194/chrome-linux/chrome")[0]
        browser = p.chromium.launch(executable_path=exe)
        page = browser.new_page()
        page.goto("http://127.0.0.1:%d/page" % PORT)
        for t in sent_times:
            js = (JS["find"].replace("%TimesText%", times_text)
                  .replace("%ExcelData_SentTime%", "送件 " + t.strftime("%Y-%m-%d %H:%M:%S")))
            found = page.evaluate("() => { %s\n return ExecuteScript(); }" % js)
            clicked = None
            if found.startswith("下載:") or found.startswith("已下載:"):
                c = page.evaluate("() => { %s\n return ExecuteScript(); }" % JS["click"].replace("%FindResult%", found))
                clicked = c
            results.append((found, clicked))
        browser.close()
    return results


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

# -*- coding: utf-8 -*-
"""本機小網頁伺服器的共用防護。

樣板編輯器與複核畫面都是「開一個只綁 127.0.0.1 的小伺服器，再用瀏覽器打開」。
這個做法本身沒問題，但它有三個已知的攻擊面，而這支程式處理的是民眾個資，
所以三個都要堵：

一、DNS rebinding
    使用者若在同一台機器上瀏覽惡意網站，那個網站可以讓自己的網域解析到
    127.0.0.1，於是它的 JavaScript 就跟我們的頁面「同源」，同源政策就擋不住了。
    **擋法：檢查 Host 標頭。** 瀏覽器送出的 Host 會是攻擊者的網域，不是
    127.0.0.1，對不上就拒絕。

二、跨站請求偽造（CSRF）
    惡意網頁可以用表單對我們的埠送 POST。瀏覽器不會讓它讀到回應，但「寫入」
    已經發生了 —— 對樣板編輯器來說就是把欄位定義覆蓋掉。
    **擋法：檢查 Origin 標頭**，而且 POST 一律要求 application/json
    （表單送不出這個 Content-Type，會先觸發預檢，而我們不回應預檢）。

三、同一台機器上的其他程式或帳號
    標頭擋得住瀏覽器，擋不住本機的其他行程 —— 它可以直接連上那個埠，
    把複核畫面上的姓名、身分證、門牌整批讀走。
    **擋法：每次啟動產生一組隨機權杖**，放在打開的網址裡，每個 API 都要帶。
    不知道權杖就什麼都拿不到，而權杖只存在記憶體裡，程式關掉就沒了。

三道都是幾行就做完的事，而漏掉任何一道的後果都是個資外洩。
"""

import secrets
from urllib.parse import parse_qs, urlparse


def new_token():
    return secrets.token_urlsafe(24)


class Guard:
    """一台伺服器的防護設定。port 要在伺服器綁好之後才填得進來。"""

    def __init__(self, token=None):
        self.token = token or new_token()
        self.port = None

    def url(self, path="/"):
        return "http://127.0.0.1:%d%s?k=%s" % (self.port, path, self.token)

    def _hosts(self):
        return {"127.0.0.1:%d" % self.port, "localhost:%d" % self.port}

    def check(self, handler, write=False):
        """回傳 None 代表放行，否則回傳 (狀態碼, 說明)。"""
        host = (handler.headers.get("Host") or "").strip()
        if host not in self._hosts():
            return 403, "Host 標頭是 %r，只接受本機" % host

        origin = handler.headers.get("Origin")
        if origin and origin not in {"http://127.0.0.1:%d" % self.port,
                                     "http://localhost:%d" % self.port}:
            return 403, "跨來源的請求一律拒絕"

        if write:
            kind = (handler.headers.get("Content-Type") or "").split(";")[0].strip()
            if kind != "application/json":
                return 415, "只接受 application/json"

        given = parse_qs(urlparse(handler.path).query).get("k", [""])[0]
        if not given:
            given = handler.headers.get("X-Token", "")
        if not secrets.compare_digest(given, self.token):
            return 403, "權杖不對。請從程式開啟的那個網址進來，不要自己改網址。"
        return None


def deny(handler, status, reason):
    body = ("拒絕：%s" % reason).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


# 樣板代號會被拿去組資料夾路徑，一定要擋掉 .. 與斜線 ——
# 不擋的話一個 code=../../.. 的請求就能把檔案寫到樣板資料夾外面。
def safe_code(code):
    code = (code or "").strip()
    if not code or len(code) > 16:
        return None
    if not all(ch.isalnum() or ch in "_-" for ch in code):
        return None
    return code

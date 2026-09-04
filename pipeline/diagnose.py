# -*- coding: utf-8 -*-
"""診斷報告：把一次轉換發生的事情整理成一份可以外傳的純文字檔。

程式出錯或抓錯的時候，光說「它抓錯了」沒辦法修 —— 要知道是哪一段出問題：
是頁面分類認錯表格、欄位框沒對到、OCR 讀不出來、還是正規化把對的改成錯的。
這個模組把每一步的中間結果收集起來，寫成一份報告。

**報告不含個資。** 這一點是硬性的：報告要送到機關外面給開發者看，
裡面不能有民眾的姓名、身分證號、門牌。所以所有辨識出來的文字都經過遮罩：

    中正路二段15號  →  字字路字段99號
    A123456789      →  A999999999
    王大明          →  字字字

中文字換成「字」、數字換成 9、英文字母保留大小寫換成 A/a，
只留下路街道段巷弄號樓這類「結構字」—— 那些字是表格的格式，不是誰的資料，
但少了它們就看不出「地址是不是少讀了『號』」這種問題。

檔名也換成代號，只留副檔名與頁數：機關的檔名可能帶案號。

報告是純文字，送出去之前請自己打開看過一遍。這是刻意的設計 ——
「相信程式有把個資拿掉」不是資安，「自己看得懂、自己確認過」才是。
"""

import os
import platform
import re
import sys
import time

from . import fields as fieldmod

# 遮罩後保留的字。這些是門牌與表格的結構，不是任何人的資料，
# 但沒有它們就沒辦法從報告上判斷地址讀得完不完整。
KEEP = set("路街道段巷弄號樓層之縣市區里鄰村鄉鎮地建")

# 問題訊息裡，被「」夾住的才是辨識出來的內容，要遮罩。
# 引號外面的是程式自己寫的說明（長度是 3 碼，應該是 10 碼），
# 那是最有用的部分，原封不動留著。validate 的訊息都照這個約定寫，
# 新增驗證訊息時要記得把讀到的內容放進「」，不然它會原封不動出現在報告裡。
_QUOTED = re.compile(r"[「『]([^「『」』]*)[」』]")


def mask(text):
    """把文字換成同樣形狀、但看不出內容的字串。"""
    out = []
    for ch in text or "":
        if ch in KEEP:
            out.append(ch)
        elif ch.isdigit():
            out.append("9")
        elif "a" <= ch <= "z":
            out.append("a")
        elif "A" <= ch <= "Z":
            out.append("A")
        elif "一" <= ch <= "鿿":
            out.append("字")
        else:
            out.append(ch)          # 標點、空白、之類的符號原樣保留
    return "".join(out)


def mask_problem(message):
    """遮罩問題訊息裡被引號夾住的內容，其餘保留。"""
    return _QUOTED.sub(lambda m: "「%s」" % mask(m.group(1)), message or "")


# 路徑會帶到使用者的 Windows 帳號名稱，那也是個資
_PATH = re.compile(r"[A-Za-z]:\\[^\s\"\']*|/[^\s\"\']{4,}")
_LONG_DIGITS = re.compile(r"\d{6,}")


def mask_error(message):
    """遮罩例外訊息。

    例外訊息多半是 OpenCV、numpy 丟出來的英文（「division by zero」之類），
    那正是要看的東西，整串遮掉就沒用了。所以只拿掉三種會夾帶個資的東西：
    檔案路徑（會有 Windows 帳號名，換成 <path>）、中文字（我們自己的訊息才會有中文，
    可能夾著讀到的內容）、以及六碼以上的連續數字（身分證、文號）。
    """
    text = _PATH.sub("<path>", message or "")
    text = _LONG_DIGITS.sub(lambda m: "9" * len(m.group()), text)
    return "".join("字" if "一" <= ch <= "鿿" else ch for ch in text)


class Journal:
    """一次轉換過程中收集到的東西。Converter 一邊跑一邊往裡面丟。"""

    def __init__(self):
        self.started = time.time()
        self.store = None
        self.templates = []         # {code, role 數, base, base_size, fields}
        self.files = []             # {id, ext, pages}
        self.pages = []             # {file, page, code, role, rotation, inliers, margin}
        self.records = []           # {index, code, file, page, fields:[...]}
        self.documents = 0
        self.unresolved = []        # {file, pages, why}
        self.errors = []            # {where, kind, message}
        self.timing = {}
        self._names = {}

    def file_id(self, path):
        """檔名換成代號。機關的檔名可能帶案號，不能直接寫進報告。"""
        if path not in self._names:
            self._names[path] = "檔%02d" % (len(self._names) + 1)
        return self._names[path]

    def note_error(self, where, exception):
        self.errors.append({
            "where": where,
            "kind": type(exception).__name__,
            "message": mask_error(str(exception)),
        })


def _packages():
    names = ["numpy", "cv2", "onnxruntime", "rapidocr_onnxruntime", "pymupdf", "openpyxl"]
    out = []
    for name in names:
        try:
            module = __import__(name)
            version = getattr(module, "__version__", None)
            if not isinstance(version, str):
                # rapidocr 之類沒有 __version__，只好去問套件metadata
                from importlib import metadata
                version = metadata.version(
                    {"cv2": "opencv-python-headless"}.get(name, name))
        except Exception as error:                                  # noqa: BLE001
            version = "問不到版本（%s）" % type(error).__name__
        out.append("%s %s" % (name, version))
    return out


def _models():
    """檢查三個 OCR 模型在不在。少一個就完全讀不出字。"""
    wanted = ["ch_PP-OCRv4_det_infer.onnx",
              "ch_PP-OCRv4_rec_infer.onnx",
              "ch_ppocr_mobile_v2.0_cls_infer.onnx"]
    try:
        import rapidocr_onnxruntime
        root = os.path.dirname(rapidocr_onnxruntime.__file__)
    except Exception:                                               # noqa: BLE001
        return ["rapidocr 載入不了，無法檢查模型"]
    found = set()
    for folder, _dirs, names in os.walk(root):
        found.update(names)
    return ["%s %s" % ("有" if name in found else "沒有", name) for name in wanted]


def _table(header, rows):
    """把表格排整齊。中文字寬度算兩格，不然欄位會歪掉。"""
    def width(text):
        return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)

    columns = list(zip(*([header] + rows))) if rows else [[h] for h in header]
    widths = [max(width(str(cell)) for cell in column) for column in columns]

    def line(cells):
        parts = []
        for cell, size in zip(cells, widths):
            cell = str(cell)
            parts.append(cell + " " * (size - width(cell)))
        return "  ".join(parts).rstrip()

    out = [line(header), line(["-" * w for w in widths])]
    out.extend(line(row) for row in rows)
    return out


def build(journal, notes=None, version=None):
    """產生報告全文。notes 是承辦人自己寫的意見。"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    out = []

    def section(title):
        out.append("")
        out.append("【%s】" % title)
        out.append("-" * 64)

    out.append("紙本轉 Excel — 診斷報告")
    out.append("產生時間  %s" % now)
    out.append("=" * 64)
    out.append(__doc__.split("\n\n", 1)[1].strip())

    # ---- 承辦人的意見放最前面。開發者要先看到「人覺得哪裡不對」，
    #      再去看機器的數字，順序反過來很容易被數字帶著走。
    section("承辦人的話")
    notes = notes or {}
    overall = (notes.get("overall") or "").strip()
    out.append(overall if overall else "（沒有填寫整批意見）")
    per_record = notes.get("records") or {}
    if per_record:
        out.append("")

        def order(key):
            # 鍵正常是件的序號，但這是外面傳進來的，不保證。
            # 診斷報告是出問題時唯一的線索，不能因為一個怪鍵就整份產不出來。
            try:
                return (0, int(key))
            except (TypeError, ValueError):
                return (1, 0)

        for key in sorted(per_record, key=order):
            text = (per_record.get(key) or "").strip()
            if not text:
                continue
            try:
                out.append("第 %d 件：%s" % (int(key) + 1, text))
            except (TypeError, ValueError):
                out.append("%s：%s" % (mask(str(key)), text))

    section("環境")
    out.append("程式版本    %s" % (version or "不明"))
    out.append("執行方式    %s" % ("打包執行檔" if getattr(sys, "frozen", False) else "原始碼"))
    out.append("作業系統    %s" % platform.platform())
    out.append("Python      %s" % sys.version.split()[0])
    out.append("套件        %s" % "、".join(_packages()))
    for line in _models():
        out.append("OCR 模型    %s" % line)

    section("樣板")
    if journal.templates:
        rows = [[t["code"], t["roles"], t["base"], t["base_size"], t["field_count"],
                 t["fields"] or "── 還沒定義欄位"] for t in journal.templates]
        out.extend(_table(["代號", "頁數", "底圖", "底圖尺寸", "欄位數", "欄位"], rows))
    else:
        out.append("樣板資料夾是空的 —— 這樣什麼都認不出來。")

    section("這一批")
    if journal.files:
        out.extend(_table(["檔案", "副檔名", "頁數"],
                          [[f["id"], f["ext"], f["pages"]] for f in journal.files]))
    out.append("")
    out.append("切出 %d 件，其中 %d 組切不完整" % (journal.documents, len(journal.unresolved)))
    out.append("實際辨識 %d 件" % len(journal.records))
    if journal.timing:
        out.append("耗時  " + "、".join("%s %.1f 秒" % (k, v) for k, v in journal.timing.items()))
    for item in journal.unresolved:
        out.append("  切不完整：%s 第 %s 頁　%s"
                   % (item["file"], item["pages"], item.get("why", "")))

    section("頁面分類")
    out.append("內點數是頁面跟樣板對上的特徵點數，差距是第一名比第二名的倍數。")
    out.append("內點少於 60 或差距小於 1.8 就判為認不出來。")
    out.append("")
    rows = []
    for page in journal.pages:
        rows.append([page["file"], page["page"], page["code"] or "？", page["role"],
                     page["rotation"], page["inliers"], "%.1f" % page["margin"],
                     "← 認不出來" if not page["code"] else ""])
    out.extend(_table(["檔案", "頁", "判定", "角色", "轉正", "內點", "差距", ""], rows))

    section("欄位辨識")
    out.append("原文與結果都是遮罩後的形狀，不是實際內容。")
    out.append("字＝中文字、9＝數字、A/a＝英文字母；路街段巷弄號樓等結構字原樣保留。")
    out.append("")
    rows = []
    for record in journal.records:
        for field in record["fields"]:
            rows.append([record["index"] + 1, record["code"],
                         fieldmod.COLUMNS.get(field["column"], field["column"]),
                         field["kind"],
                         "%.2f" % field["confidence"],
                         field["raw"] or "（空）",
                         field["value"] or "（空）",
                         field["problem"] or "通過"])
    if rows:
        out.extend(_table(["件", "表格", "欄位", "型別", "信心", "原文(遮罩)",
                           "正規化後(遮罩)", "結果"], rows))
    else:
        out.append("沒有任何欄位被辨識 —— 樣板可能還沒定義欄位。")

    section("怎麼讀出來的")
    out.append("同一欄最多讀三遍，因為沒有一種讀法對所有欄位都最好：")
    out.append("  整行    一般欄位（門牌、姓名）唯一合理的讀法")
    out.append("  照格子  原稿上印好一字一格的欄位。格線是從底圖上找出來的")
    out.append("  逐格    身分證專用：一格兩種讀法，再用檢查碼挑出唯一解。")
    out.append("          「?」代表那一格什麼都沒讀到，「|」是格子的分界。")
    out.append("  逐空白  沒有格線時，照墨跡之間的空白切（只有身分證會做）")
    out.append("  關鍵字  電腦產製的表格：在整頁上找印刷標籤，再讀它那一格")
    out.append("  整頁找  公文文號專用：整頁上找十碼、開頭是民國年的數字")
    out.append("")
    out.append("**格數是 0 就代表沒找到印刷格線**，那一欄只有整行讀的結果。")
    out.append("一字一格的欄位如果格數是 0，問題就出在找格線那一步，不是 OCR。")
    out.append("")
    rows = []
    for record in journal.records:
        for field in record["fields"]:
            readings = field.get("readings") or {}
            if not readings:
                continue
            rows.append([record["index"] + 1,
                         fieldmod.COLUMNS.get(field["column"], field["column"]),
                         field.get("cells", 0),
                         readings.get("整行") or "（空）",
                         readings.get("照格子") or "－",
                         readings.get("逐格") or "－",
                         readings.get("逐空白") or "－",
                         readings.get("關鍵字") or "－",
                         readings.get("整頁找") or "－",
                         readings.get("採用") or "（空）"])
    if rows:
        out.extend(_table(["件", "欄位", "格數", "整行", "照格子", "逐格", "逐空白",
                           "關鍵字", "整頁找", "採用"], rows))
    else:
        out.append("沒有資料。")

    section("欄位統計")
    stats = {}
    for record in journal.records:
        for field in record["fields"]:
            entry = stats.setdefault(field["column"], {"total": 0, "bad": 0,
                                                       "low": 0, "reasons": {}})
            entry["total"] += 1
            if field["problem"]:
                entry["bad"] += 1
                entry["reasons"][field["problem"]] = entry["reasons"].get(field["problem"], 0) + 1
            if field["confidence"] < 0.80:
                entry["low"] += 1
    if stats:
        rows = []
        for column, entry in sorted(stats.items(), key=lambda kv: -kv[1]["bad"]):
            top = sorted(entry["reasons"].items(), key=lambda kv: -kv[1])[:2]
            rows.append([fieldmod.COLUMNS.get(column, column), entry["total"],
                         entry["total"] - entry["bad"], entry["low"],
                         "、".join("%s ×%d" % (r, c) for r, c in top)])
        out.extend(_table(["欄位", "件數", "驗證通過", "信心偏低", "主要問題"], rows))
    else:
        out.append("沒有資料。")

    section("例外")
    if journal.errors:
        for error in journal.errors:
            out.append("%s：%s — %s" % (error["where"], error["kind"], error["message"]))
    else:
        out.append("沒有發生例外。")

    out.append("")
    out.append("=" * 64)
    out.append("報告結束。這份檔案不含個資，可以直接傳給開發者。")
    return "\n".join(out) + "\n"


def save(text, folder):
    """寫成檔案，回傳路徑。檔名用純英數，方便夾帶。"""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "diagnostic-%s.txt" % time.strftime("%Y%m%d-%H%M%S"))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path

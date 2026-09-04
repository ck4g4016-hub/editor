# -*- coding: utf-8 -*-
"""欄位定義：一種表格上，哪一塊區域對應到輸出的哪一欄。

存成 `<樣板資料夾>/<代號>/fields.json`，由樣板編輯器產生與修改。
正式辨識時照著它把每個欄位裁切出來。

定位有兩種方式：

**固定框**（`fixed`）
    紙本表格用這個。格線位置是印死的，第幾格就是第幾格，
    對位之後座標就準。

**錨點相對**（`anchor`）
    系統報表用這個。報表印幾列取決於資料有幾筆，
    同一個欄位在不同頁上的高度不一樣，寫死座標會抓到別人的資料。
    改成「先找到某個固定會出現的標記，再往下數第幾列」。

    戶政通報那種表格一頁上有三個地址，字面常常一模一樣
    （同一個人沒搬家），所以無法靠內容判斷取對了沒，只能靠位置 ——
    這正是非用錨點不可的原因。
"""

import json
import os

# 輸出欄位。鍵是程式內部用的名稱，值是給人看的說明。
# 對應到哪個 Excel/CSV 欄由 output 模組決定，這裡只認得業務意義。
COLUMNS = {
    "district": "行政區",
    "address": "門牌",
    "doc_number": "公文文號",
    "id_number": "身分證字號",
    "name": "姓名",
    "section": "段名",
    "land_number": "地號",
}

# 欄位的資料型別，決定要用哪種辨識與驗證
KINDS = {
    "text": "一般文字",
    "chinese": "中文（姓名）",
    "digits": "數字",
    "id_number": "身分證字號（驗檢查碼）",
    "doc_number": "公文文號（10 碼、開頭民國年）",
    "address": "地址（套用門牌正規化）",
    "district": "行政區（比對區名清單）",
    "section": "段名（比對地段清單）",
    "land_number": "地號（補零成 0000-0000）",
    "checkbox": "勾選框",
}

# 選了某個輸出欄，型別預設就該是對的那一個。
#
# 這份對應表以前有一份抄在樣板編輯器的 JavaScript 裡，兩邊各改各的：
# 我後來加了 section 型別（比對地段清單），卻忘了改 JS 那份，於是段名
# 一直用 chinese —— 那個型別根本沒有驗證器，「圍際」原封不動就寫出去了。
# 使用者實測整批段名都沒被比對過。現在只留這一份，編輯器跟 API 拿。
DEFAULT_KIND = {
    "district": "district",
    "address": "address",
    "doc_number": "doc_number",
    "id_number": "id_number",
    "name": "chinese",
    "section": "section",
    "land_number": "land_number",
}

FIXED = "fixed"
ANCHOR = "anchor"

# 關鍵字模式：不框位置，改成在整頁上找一個印刷標籤，再拿它那一格的內容。
#
# 電腦產製的表格（稅務入口網的列印件）欄位會隨資料多寡上下移動 —— 多一筆
# 「其他土地坐落」，底下每一列就整個往下推，固定框全部失準。但它的標籤是
# 印刷的、每一份都一樣，辨識信心實測 0.9 以上。所以改成認標籤。
LABEL = "label"


class Field:
    """一個欄位。

    box 一律是 300dpi、已轉正、已對齊到底圖座標系之後的 (x, y, w, h)。
    anchor 模式下 box 的 y 是相對於錨點列的位移，不是絕對座標。

    **一個欄位可以有好幾個框。** 紙本表格的門牌是一整排印好的格子：

        [三峽] 鄉鎮市區 [民生] 路街 [ ] 段 [38] 巷 [22] 弄 [14] 號 [ ] 樓

    整排拉一個大框，讀出來會是「民生382214」—— 數字全黏在一起，
    分不出哪個是巷哪個是號，而那正是這個欄位唯一有用的資訊。
    所以每一格各自框，各自標它後面印的字（段、巷、弄、號、樓），
    讀完再照順序接起來：民生路38巷22弄14號。

    parts 是額外的格子，每個是 {"box": [x,y,w,h], "suffix": "巷"}。
    主框自己的後綴放在 suffix。空的格子會整段跳過 —— 沒有段就不會冒出一個「段」。
    """

    def __init__(self, id, name, column, kind, box, page="front",
                 mode=FIXED, anchor_text=None, anchor_index=1, required=False,
                 suffix="", parts=None, label=""):
        self.id = id
        self.name = name
        self.column = column
        self.kind = kind
        self.box = list(box)
        self.page = page
        self.mode = mode
        self.anchor_text = anchor_text
        self.anchor_index = anchor_index
        self.required = required
        self.suffix = suffix or ""
        self.parts = [{"box": list(p["box"]), "suffix": p.get("suffix", "")}
                      for p in (parts or [])]
        self.label = (label or "").strip()

    def segments(self):
        """所有的框，照讀取順序。回傳 [(box, suffix), ...]。"""
        return [(self.box, self.suffix)] + [(p["box"], p["suffix"]) for p in self.parts]

    def to_dict(self):
        data = {
            "id": self.id,
            "name": self.name,
            "column": self.column,
            "kind": self.kind,
            "box": self.box,
            "page": self.page,
            "mode": self.mode,
            "required": self.required,
        }
        if self.suffix:
            data["suffix"] = self.suffix
        if self.parts:
            data["parts"] = self.parts
        if self.label:
            data["label"] = self.label
        if self.mode == ANCHOR:
            data["anchor_text"] = self.anchor_text
            data["anchor_index"] = self.anchor_index
        return data

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data["id"], name=data["name"], column=data["column"],
            kind=data["kind"], box=data["box"], page=data.get("page", "front"),
            mode=data.get("mode", FIXED), anchor_text=data.get("anchor_text"),
            anchor_index=data.get("anchor_index", 1),
            required=data.get("required", False),
            suffix=data.get("suffix", ""), parts=data.get("parts"),
            label=data.get("label", ""))

    def problems(self):
        """回傳這個欄位設定上的問題，沒有問題就回傳空清單。"""
        issues = []
        if not self.name.strip():
            issues.append("沒有名稱")
        if self.column not in COLUMNS:
            issues.append("輸出欄位 %r 不認得" % self.column)
        if self.kind not in KINDS:
            issues.append("型別 %r 不認得" % self.kind)
        if self.mode == LABEL:
            if not self.label:
                issues.append("關鍵字模式要填一個表格上印著的標籤，例如「房屋坐落」")
        else:
            for index, (box, _suffix) in enumerate(self.segments()):
                x, y, w, h = box
                if w <= 0 or h <= 0:
                    issues.append("第 %d 個框的大小不對" % (index + 1))
        if self.mode == ANCHOR:
            # 這個模式在 fields 這裡存得好好的，process 卻從來沒有實作 ——
            # 設成錨點相對的欄位會被當成固定框，而且畫面上看不出來。
            # 提供一個什麼都不做的選項比沒有這個選項還糟，所以直接擋下來。
            issues.append("「錨點相對」還沒實作，辨識時會被當成固定框，請改用固定框")
        return issues


def path_for(store, code):
    return os.path.join(store, code, "fields.json")


# 早期版本沒有這些型別，樣板編輯器只好給一個最接近的通用型別。
# 通用型別沒有驗證器，等於那一欄完全不檢查 —— 使用者不會知道，
# 而且要他把已經框好的欄位重做一遍才修得掉，那不合理。
# 所以載入時就地升級：只有在舊值是「當初的通用替代品」時才換。
_UPGRADE = {("section", "chinese"): "section"}


def load(store, code):
    """載入某一種表格的欄位定義。沒有就回傳空清單。

    順便把舊樣板的型別升級 —— 見 _UPGRADE。
    """
    target = path_for(store, code)
    if not os.path.isfile(target):
        return []
    with open(target, encoding="utf-8") as handle:
        data = json.load(handle)
    fields = [Field.from_dict(item) for item in data.get("fields", [])]
    for field in fields:
        field.kind = _UPGRADE.get((field.column, field.kind), field.kind)
    return fields


def save(store, code, fields):
    """存檔。會先檢查有沒有明顯的設定錯誤。"""
    problems = []
    seen, columns = set(), {}
    for field in fields:
        for issue in field.problems():
            problems.append("%s：%s" % (field.name or field.id, issue))
        if field.id in seen:
            problems.append("欄位代號 %s 重複" % field.id)
        seen.add(field.id)
        # 兩個框指到同一個輸出欄，寫檔的時候後面那個會蓋掉前面那個，
        # 而且輸出表上看不出來少了東西 —— 擋在這裡最保險。
        if field.column:
            if field.column in columns:
                problems.append("「%s」被指派了兩次（%s 與 %s）"
                                % (COLUMNS.get(field.column, field.column),
                                   columns[field.column], field.name or field.id))
            columns[field.column] = field.name or field.id
    if problems:
        raise ValueError("；".join(problems))

    target = path_for(store, code)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump({"code": code, "fields": [f.to_dict() for f in fields]},
                  handle, ensure_ascii=False, indent=2)
    return target


def missing_columns(fields):
    """還沒有被指派的輸出欄位。

    身分證、門牌、公文文號是最重要的三欄，缺了會在編輯器上明顯提示。
    """
    assigned = {field.column for field in fields}
    return [key for key in COLUMNS if key not in assigned]

# -*- coding: utf-8 -*-
"""判斷每一頁是哪一種表格、是正面還是背面，並把整份 PDF 切成一件一件的申請案。

實際作業時，一個 PDF 裡會混著各種表格、沒有順序，所以不能靠頁次或檔名推斷，
只能逐頁認。做法是拿每一頁去跟所有樣板比對特徵點，取最像的那一個。

實測的分離度非常清楚（150dpi、同一種表格的正面）：
    對到自己的樣板   內點 1100 ~ 1800
    對到別的樣板     內點    9 ~   19

切分錯比認錯字嚴重得多 —— 認錯字頂多改一格，切錯會讓整件資料張冠李戴，
而且從輸出的表格上完全看不出來。所以分類結果一定要在複核介面上讓人先確認。
"""

import json
import os

import cv2
import numpy as np

from . import render, resources

# 一頁在文件中的角色
FRONT = "front"      # 表格正面，看到它就是新的一件
BACK = "back"        # 續頁／背面，附在前一件後面
BLANK = "blank"      # 幾乎空白，通常是沒印東西的背面
UNKNOWN = "unknown"  # 認不出來，交給人判斷

# 墨跡低於這個比例就當成空白頁
BLANK_INK_RATIO = 0.004

# 內點數低於這個值就不算認出來
MIN_INLIERS = 60

# 最像的與第二像的差距不到這個倍數，代表兩個樣板太接近，不敢下結論
MIN_MARGIN = 1.8

# 判定成需要旋轉之前，角度得離 90 的倍數夠近。歪斜的掃描件角度會有幾度誤差，
# 但不會差到十幾度；差太多代表對位本身就不可信。
ROTATION_TOLERANCE = 20.0

_ORB = cv2.ORB_create(3000)
_MATCHER = cv2.BFMatcher(cv2.NORM_HAMMING)


def _features(gray):
    return _ORB.detectAndCompute(gray, None)


def _match(features_a, features_b):
    """把 a 對到 b，回傳 (通過幾何一致性檢定的配對數, 變換矩陣)。

    ORB 的描述子本身有旋轉不變性，所以躺著的頁面一樣配得上正立的樣板 ——
    旋轉量要從變換矩陣裡讀出來，不能靠「把頁面轉四個方向各比一次」，
    那樣四個方向會拿到差不多的分數。
    """
    kp_a, desc_a = features_a
    kp_b, desc_b = features_b
    if desc_a is None or desc_b is None or len(kp_a) < 10 or len(kp_b) < 10:
        return 0, None
    pairs = _MATCHER.knnMatch(desc_a, desc_b, k=2)
    good = [m for m, n in pairs if m.distance < 0.75 * n.distance]
    if len(good) < 15:
        return 0, None
    src = np.float32([kp_a[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_b[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    matrix, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if matrix is None or mask is None:
        return 0, None
    return int(mask.sum()), matrix


def _rotation_of(matrix):
    """從變換矩陣讀出頁面要轉多少度才會跟樣板同方向。

    回傳 90 的倍數；角度離 90 的倍數太遠就回傳 None，代表這個對位不可信。
    """
    if matrix is None:
        return 0
    angle = np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0]))
    snapped = int(round(angle / 90.0) * 90) % 360
    if abs(((angle - snapped + 180) % 360) - 180) > ROTATION_TOLERANCE:
        return None
    return snapped


class Template:
    """一種表格的一個版本，的一個頁面角色。

    例如「地價稅自用住宅用地申請書（新版）的正面」就是一個 Template。
    同一種表格的新舊版分開建，因為改版會讓欄位位置位移。
    """

    def __init__(self, code, name, role, image):
        self.code = code
        self.name = name
        self.role = role
        self.image = image
        self.features = _features(image)

    def __repr__(self):
        return "<Template %s/%s %s>" % (self.code, self.role, self.name)


class TemplateSet:
    """所有樣板的集合，負責分類。"""

    def __init__(self, templates=()):
        self.templates = list(templates)

    def __len__(self):
        return len(self.templates)

    @classmethod
    def load(cls, directory):
        """從資料夾載入樣板。

        每一種表格一個子資料夾，裡面放 index.json 與各角色的參考影像：

            <directory>/
                A/
                    index.json      {"code": "A", "name": "稅務入口網", "pages": {"front": "front.png"}}
                    front.png
        """
        templates = []
        for entry in sorted(os.listdir(directory)):
            folder = os.path.join(directory, entry)
            index = os.path.join(folder, "index.json")
            if not os.path.isfile(index):
                continue
            with open(index, encoding="utf-8") as handle:
                meta = json.load(handle)
            for role, filename in meta["pages"].items():
                image = resources.imread(os.path.join(folder, filename), cv2.IMREAD_GRAYSCALE)
                if image is None:
                    # 不要用 SystemExit —— 它繼承自 BaseException，會直接穿過
                    # except Exception，整個程式無聲無息地關掉，連紀錄都沒有。
                    raise ValueError(
                        "樣板 %s 的影像讀不到：%s\n"
                        "這個樣板沒有建完整，請重新執行「新增表格」。"
                        % (meta.get("code", entry), os.path.join(folder, filename)))
                templates.append(Template(meta["code"], meta["name"], role, image))
        return cls(templates)

    def classify(self, gray):
        """判斷一頁是什麼。回傳 (code, role, rotation, inliers, margin)。

        rotation 是「這一頁要順時針轉幾度才會跟樣板同方向」，
        橫式的系統報表掃進來是躺著的，會得到 90 或 270。
        """
        if render.ink_ratio(gray) < BLANK_INK_RATIO:
            return (None, BLANK, 0, 0, 0.0)

        features = _features(gray)
        scored = []
        for template in self.templates:
            count, matrix = _match(features, template.features)
            scored.append((count, template, matrix))

        scored.sort(key=lambda item: item[0], reverse=True)
        best_inliers, best_template, best_matrix = scored[0]

        # 第二名要來自不同的表格 —— 同一種表格的正反面本來就會有點像
        runner_up = next((count for count, template, _ in scored[1:]
                          if template.code != best_template.code), 0)
        margin = best_inliers / max(runner_up, 1)

        rotation = _rotation_of(best_matrix)
        if best_inliers < MIN_INLIERS or margin < MIN_MARGIN or rotation is None:
            return (None, UNKNOWN, 0, best_inliers, margin)
        return (best_template.code, best_template.role, rotation, best_inliers, margin)


class Page:
    def __init__(self, source, index, code, role, rotation, inliers, margin):
        self.source = source
        self.index = index          # 0-based
        self.code = code
        self.role = role
        self.rotation = rotation
        self.inliers = inliers
        self.margin = margin

    @property
    def label(self):
        if self.role == BLANK:
            return "空白"
        if self.role == UNKNOWN:
            return "無法辨識"
        return "%s %s" % (self.code, "正面" if self.role == FRONT else "背面")

    def __repr__(self):
        return "<Page %s p%d %s>" % (os.path.basename(self.source), self.index + 1, self.label)


# 一件申請書固定幾頁。
#
# 承辦人的作業方式是「一份申請書一定掃正反兩面，背面空白也掃」。
# 那是一條硬規則，比「認不認得出正面」可靠得多 —— 所以切分直接照頁數配對。
PAGES_PER_DOCUMENT = 2


class Document:
    """一件申請案。固定兩頁：第一頁是正面，第二頁是背面（可能空白）。"""

    def __init__(self, pages):
        self.pages = list(pages)

    @property
    def front(self):
        return self.pages[0] if self.pages else None

    @property
    def back(self):
        return self.pages[1] if len(self.pages) > 1 else None

    @property
    def code(self):
        """這一件是哪一種表格。

        以正面為準，但正面認不出來時退而用背面 —— 背面認得出來就代表這一件
        是那一種表格，總比整件丟掉好。反過來說，兩面都認不出來才是真的沒救。
        """
        for page in self.pages:
            if page.code:
                return page.code
        return None

    @property
    def complete(self):
        """頁數對、而且至少有一面認得出是哪一種表格。"""
        return len(self.pages) == PAGES_PER_DOCUMENT and self.code is not None

    @property
    def problem(self):
        """不完整的原因，給人看的。完整就回 None。"""
        if len(self.pages) != PAGES_PER_DOCUMENT:
            return ("只有 %d 頁，一件應該是 %d 頁（正反面都要掃，背面空白也要掃）"
                    % (len(self.pages), PAGES_PER_DOCUMENT))
        if self.code is None:
            return "兩面都認不出是哪一種表格"
        return None

    def __repr__(self):
        return "<Document %s %d 頁>" % (self.code or "?", len(self.pages))


def classify_pages(paths, templates, dpi=render.CLASSIFY_DPI, progress=None):
    """把一批 PDF 的每一頁都分類。"""
    pages = []
    for path in paths:
        for index in range(render.page_count(path)):
            gray = render.render(path, index, dpi=dpi)
            code, role, rotation, inliers, margin = templates.classify(gray)
            page = Page(path, index, code, role, rotation, inliers, margin)
            pages.append(page)
            if progress:
                progress(page)
    return pages


def split_documents(pages, per_document=PAGES_PER_DOCUMENT):
    """把頁面串切成一件一件：**同一個檔案內每兩頁一件**。

    切分不跨 PDF —— 換一個檔案一定重新開始配對。

    以前的規則是「看到表格正面就開新的一件」，那有一個很難發現的破口：
    正面認不出來的時候（掃歪了、太淡、蓋章蓋掉特徵），那一頁會被當成續頁
    併進**前一件**，於是兩件併成一件 —— 十五件掃進來變成十四筆，而且
    件數上完全看不出少了誰。空白背面認不出來則是正常的，兩種情形從
    分類結果上長得一模一樣，程式分不出來。

    改成照頁數配對之後，這個破口就不存在了：頁數是硬事實。承辦人的作業
    規則是「一份申請書一定掃正反兩面，背面空白也掃」，所以第 1、2 頁是
    第一件、第 3、4 頁是第二件，以此類推。認不出來只影響「這是哪一種表格」，
    不再影響「這是第幾件」。

    檔案頁數是奇數的話，最後一件會只有一頁而被標成不完整 —— 那正是
    「有一面忘了掃」的訊號，該讓人看到，不該默默補齊。
    """
    documents = []
    start = 0
    for index in range(1, len(pages) + 1):
        crossed = index == len(pages) or pages[index].source != pages[start].source
        if not crossed:
            continue
        group = pages[start:index]
        for offset in range(0, len(group), per_document):
            documents.append(Document(group[offset:offset + per_document]))
        start = index
    return documents

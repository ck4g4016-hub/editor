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

from . import render

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
                image = cv2.imread(os.path.join(folder, filename), cv2.IMREAD_GRAYSCALE)
                if image is None:
                    raise SystemExit("樣板影像讀不到: %s/%s" % (folder, filename))
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


class Document:
    """一件申請案。第一頁是表格正面，後面接著它的續頁與空白背面。"""

    def __init__(self, pages):
        self.pages = list(pages)

    @property
    def code(self):
        return self.pages[0].code if self.pages else None

    @property
    def complete(self):
        """有正面才算完整。沒有正面的多半是切分出了問題，或掃描漏了首頁。"""
        return bool(self.pages) and self.pages[0].role == FRONT

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


def split_documents(pages):
    """把頁面串切成一件一件。

    規則很簡單：看到表格正面就開新的一件，其餘的頁附在目前這一件後面。
    這樣連續兩件同款表格也能正確切開，因為第二件的第一頁一樣會被認成正面。

    切分不跨 PDF —— 換一個檔案一定重新開始。
    """
    documents = []
    current = None
    current_source = None

    for page in pages:
        starts_new = page.role == FRONT or page.source != current_source
        if starts_new or current is None:
            if current:
                documents.append(Document(current))
            current = [page]
            current_source = page.source
        else:
            current.append(page)

    if current:
        documents.append(Document(current))
    return documents

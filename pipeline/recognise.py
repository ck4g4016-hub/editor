# -*- coding: utf-8 -*-
"""欄位辨識。

用 RapidOCR（PP-OCRv4 模型，純 ONNX Runtime）。模型隨套件一起帶著，
執行時不連任何網路，這是離線要求的硬條件。

## 為什麼是逐欄位裁切，不是整頁辨識

整頁辨識會漏欄位。實測 F 表：整頁辨識漏掉門牌的「147」，
逐欄位裁切之後同一塊就讀得到。原因是偵測階段會把稀疏的手寫併成大區塊或直接忽略，
先把範圍限定住，辨識模型才有機會把那幾個字看清楚。

## 逐格辨識：曾經放棄，後來重測推翻

原本的結論是「逐字辨識是反效果，F 表身分證欄切成九格有五格回傳空白」，
所以一律整行辨識。後來拿真實件跑出 NL30477LZ（9 碼）、PUY3N（5 碼）
這種結果，重新量了一次單字辨識：A0-9 共 11 個字元，10 個讀得對。

所以逐格是可行的，之前那個量測不知道是被什麼影響（放大倍率、補白、
或是當時的裁切品質）。現在的做法是**兩種都讀**，讓檢查碼決定用哪一個 ——
身分證有檢查碼這件事，讓我們可以直接驗證哪一種讀法是對的，
不必猜。這是這個欄位獨有的優勢，別的欄位沒有。
"""

import threading

import cv2
import numpy as np

# 放大倍率。欄位裁下來通常只有幾十像素高，放大後辨識明顯較穩。
UPSCALE = 2

# 但放大有上限。太寬的影像偵測階段會把相鄰的字併進重疊的框，
# 同一個字被讀兩次 —— 實測 1600×150 的身分證欄位：
#
#     放大 0.5x → 寬  848px，偵測 10 段，A123456789      ✓
#     放大 1.0x → 寬 1648px，偵測  7 段，A1 2 34556 78899 ✗
#     放大 2.0x → 寬 3248px，偵測  5 段，A1 234556 7889   ✗
#
# 所以寬的欄位要壓下來，不是放大。
MAX_WIDTH = 1200

# 四周補白，避免筆畫貼著邊緣被截掉
MARGIN = 24

_engine = None
_lock = threading.Lock()


def engine():
    """第一次用到才載入模型 —— 載入要一兩秒，沒用到就不要付這個代價。"""
    global _engine
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
    return _engine


def scale_for(width):
    """這麼寬的欄位該用幾倍。小的放大，寬的壓下來，見 MAX_WIDTH 的說明。"""
    if width <= 0:
        return 1.0
    return max(0.4, min(float(UPSCALE), MAX_WIDTH / float(width)))


def prepare(crop):
    """把裁下來的欄位整理成辨識模型好處理的樣子。"""
    if crop is None or crop.size == 0:
        return None
    if crop.ndim == 2:
        crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
    scale = scale_for(crop.shape[1])
    if abs(scale - 1.0) > 0.01:
        crop = cv2.resize(crop, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    return cv2.copyMakeBorder(crop, MARGIN, MARGIN, MARGIN, MARGIN,
                              cv2.BORDER_CONSTANT, value=(255, 255, 255))


# 兩個偵測框水平方向重疊超過這個比例，就當成同一段文字的重複偵測
OVERLAP = 0.5


def _drop_overlaps(ordered):
    """丟掉跟前一段重疊的偵測框。

    偵測階段偶爾會對同一塊文字給出好幾個互相重疊的框，接起來就變成
    「A1232345667889」這種每個字讀兩次的字串 —— 長度和內容都像模像樣，
    只是錯的。這種錯比讀不出來危險得多。
    """
    kept = []
    for item in ordered:
        x0, x1 = _span(item)
        clash = False
        for other in kept:
            a0, a1 = _span(other)
            overlap = min(x1, a1) - max(x0, a0)
            if overlap > 0 and overlap >= OVERLAP * min(x1 - x0, a1 - a0):
                clash = True
                break
        if not clash:
            kept.append(item)
    return kept


def _span(item):
    xs = np.array(item[0])[:, 0]
    return float(xs.min()), float(xs.max())


def _vspan(item):
    ys = np.array(item[0])[:, 1]
    return float(ys.min()), float(ys.max())


# 兩段文字垂直重疊超過這個比例就算同一行
ROW_OVERLAP = 0.5


def rows(items):
    """把偵測到的段落分行，回傳 [[同一行的段落…]]，由上而下、每行由左而右。

    欄位框常常會多框到上下相鄰的一行 —— 公文文號那一格尤其明顯，
    收文戳上有戳記、文號、收文日期、條碼號好幾行。以前只照 x 排序，
    結果是把好幾行的字**橫著交錯接起來**，讀出「CR酮收文…115/08/25」
    這種東西。更糟的是去重複那一步只看水平重疊，於是「正上方那一行」
    會被當成重複整段丟掉 —— 實測兩行對齊的欄位，第一行整個消失。

    去重複只能在同一行裡做。不同行的段落水平重疊是正常的，不是重複。
    """
    ordered = sorted(items, key=lambda item: _vspan(item)[0])
    groups = []
    for item in ordered:
        top, bottom = _vspan(item)
        height = max(bottom - top, 1.0)
        placed = False
        for group in groups:
            g_top, g_bottom = group["top"], group["bottom"]
            overlap = min(bottom, g_bottom) - max(top, g_top)
            if overlap > 0 and overlap >= ROW_OVERLAP * min(height,
                                                           max(g_bottom - g_top, 1.0)):
                group["items"].append(item)
                group["top"] = min(g_top, top)
                group["bottom"] = max(g_bottom, bottom)
                placed = True
                break
        if not placed:
            groups.append({"top": top, "bottom": bottom, "items": [item]})

    groups.sort(key=lambda g: g["top"])
    return [_drop_overlaps(sorted(g["items"], key=lambda item: _span(item)[0]))
            for g in groups]


def read(crop):
    """辨識一個欄位，回傳 (文字, 信心)。

    同一個欄位可能被切成好幾段（中間有空格，或框到了相鄰的行）。
    由上而下、每行由左而右接起來，順序才跟人看到的一樣。
    信心取最低的那一段 —— 一串裡只要有一個字沒把握，整串就不能算有把握。
    """
    image = prepare(crop)
    if image is None:
        return "", 0.0

    result, _ = engine()(image)
    if not result:
        return "", 0.0

    ordered = [item for line in rows(result) for item in line]
    if not ordered:
        return "", 0.0
    text = "".join(item[1] for item in ordered).strip()
    confidence = min(float(item[2]) for item in ordered)
    return text, confidence


def crop_field(page, box):
    """依欄位定義把區域裁下來。box 是 (x, y, w, h)。"""
    x, y, w, h = box
    height, width = page.shape[:2]
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(width, int(x + w)), min(height, int(y + h))
    if x1 <= x0 or y1 <= y0:
        return None
    return page[y0:y1, x0:x1]


# 兩塊墨跡之間的空白超過這個比例就算是分開的格子。
# 比例是相對於**欄位高度**，不是總寬 —— 字跟字的間距是跟著字的大小走的，
# 跟這一欄總共有多長沒關係。用總寬當基準的話，欄位愈寬門檻愈高，
# 實測 1600×150 的身分證整條只切出 1 格，等於逐格辨識完全沒作用。
GAP_RATIO = 0.18


def cells(crop):
    """依墨跡之間的空白，把一個欄位切成一格一格。

    一字一格的欄位（身分證、稅籍編號）字跟字之間有明顯空白，
    照著切開就能一格一格讀 —— 整行讀的時候漏掉的字，這樣有機會補回來。
    """
    if crop is None or crop.size == 0:
        return []
    gray = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    ink = (gray < 128).sum(axis=0) > 0
    if not ink.any():
        return []

    gap = max(3, int(crop.shape[0] * GAP_RATIO))
    groups, start = [], None
    blanks = 0
    for index, has_ink in enumerate(ink):
        if has_ink:
            if start is None:
                start = index
            blanks = 0
        elif start is not None:
            blanks += 1
            if blanks >= gap:
                groups.append((start, index - blanks + 1))
                start = None
    if start is not None:
        groups.append((start, len(ink)))

    pad = 4
    out = []
    for x0, x1 in groups:
        if x1 - x0 < 3:
            continue
        out.append(crop[:, max(0, x0 - pad):min(crop.shape[1], x1 + pad)])
    return out


def read_cells(crop):
    """逐格辨識，回傳 (接起來的文字, 最低信心)。格子讀不到就跳過。"""
    return read_pieces(cells(crop))


def trim(crop, pad_ratio=0.25):
    """把一格裁到只剩有寫字的地方。

    格子本身比字大得多（本來就該這樣，字才不會頂到格線）。整格丟給辨識模型，
    模型會先把整張影像縮到固定高度，字佔的比例愈小就被縮得愈小 ——
    等於自己把解析度丟掉。實測乾淨的十個字元：整格 9/10、緊裁之後 10/10。

    留一圈相對於字本身大小的白邊，不是固定像素 —— 字大白邊就大。
    """
    if crop is None or crop.size == 0:
        return None
    gray = crop if crop.ndim == 2 else cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    ink = gray < 128
    if not ink.any():
        return None
    ys, xs = np.where(ink)
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    pad = int(max(y1 - y0, x1 - x0) * pad_ratio) + 2
    return crop[max(0, y0 - pad):min(crop.shape[0], y1 + pad),
                max(0, x0 - pad):min(crop.shape[1], x1 + pad)]


def read_char(cell):
    """讀一格裡的單一個字，回傳所有可能的讀法（去重、去空）。

    給兩種讀法，因為它們錯的地方不一樣：

    只做辨識    跳過偵測直接認。**真實掃描件上明顯較好**：拿使用者的
                手寫樣本量，同一排十個字，只做辨識 8/10、偵測+辨識 3/10。
                （在乾淨的合成字上剛好相反，所以以前排在後面 ——
                合成的字騙了我，真實件才算數。）
    偵測+辨識   一般路徑，救得回只做辨識失手的那幾格。

    兩種都給出來，讓上層用檢查碼去挑 —— 身分證有檢查碼，不必猜哪一種對。
    """
    tight = trim(cell)
    source = cell if tight is None else tight
    out = []
    text = read_only(source)
    if text:
        out.append(text)
    text, _score = read(source)
    if text:
        out.append(text.strip())
    seen, unique = set(), []
    for text in out:
        if text not in seen:
            seen.add(text)
            unique.append(text)
    return unique


def read_pieces(pieces):
    """把切好的格子一格一格讀，接起來。回傳 (文字, 最低信心)。"""
    out, scores = [], []
    for cell in pieces:
        text, confidence = read(cell)
        text = text.strip()
        if text:
            out.append(text)
            scores.append(confidence)
    return "".join(out), (min(scores) if scores else 0.0)


# ---------------------------------------------------------------------------
# 照原稿印好的方格切
#
# 承辦人講得很直接：「又要一格一個圈出來嗎？原稿就有格子了。」
# 他是對的。身分證、稅籍編號這種欄位，表格上本來就印好了一格一個字的方格，
# 那些格線就在底圖上，位置精確而且每一份都一樣 —— 拿它來切是最準的，
# 也不必要求人去框十個小方塊。
#
# 靠墨跡空白切（上面的 cells）只在沒有格線時才需要：格線本身就是墨跡，
# 「字—線—字」之間根本沒有空白可言，整條會被當成一格，等於沒切。
# ---------------------------------------------------------------------------

# 一條直格線要有多長才算數。
#
# 原本是「佔欄位高度的 55%」，那個門檻在真實件上完全沒有觸發過 ——
# 使用者框選時通常框得比那排方格高（本來就該框寬鬆一點，字才不會被切掉），
# 格線只佔框高的一小部分，於是一條都找不到，逐格辨識形同沒做。
# 診斷報告上看不出來這件事，因為我當初沒把「切出幾格」記進去。
#
# 改成相對於「這個框裡最長的那一條垂直筆畫」：格線一定是框裡最長的，
# 跟框開多大無關。同時保留一個絕對下限，免得整框都是雜訊時亂切。
# 一條直線至少要多長才可能是格線。單位是像素，**不跟欄位框的高度連動**。
#
# 這個門檻踩過兩次坑。先是寫成「欄位高度的 55%」，框畫大一點就永遠達不到；
# 改成「相對於框裡最長的那條」之後，又留了一個「欄位高度的 20%」當下限，
# 等於把同一個問題原封不動搬回來 —— 實測同一排格子，框高 110/160/240
# 抓得到、300 抓不到、380 又抓得到。這種時好時壞的錯最難查。
#
# 現在只留絕對值，真正的判斷交給 grid_lines 的「一整排」條件。
# 300dpi 下印刷格子至少 40px 高，抓 20px 當下限很安全。
MIN_LINE_PIXELS = 20

# 至少要切出這麼多格才當作「這是一個一字一格的欄位」
MIN_CELLS = 4

# 每一格往內縮幾個像素，避開格線本身留下的殘影
INSET = 3


def _run_bounds(mask):
    """每一欄最長的連續 True 有多長、從第幾列開始。"""
    height, width = mask.shape
    best = np.zeros(width, dtype=np.int32)
    best_start = np.zeros(width, dtype=np.int32)
    current = np.zeros(width, dtype=np.int32)
    start = np.zeros(width, dtype=np.int32)
    for row in range(height):
        line = mask[row]
        start = np.where(line & (current == 0), row, start)
        current = np.where(line, current + 1, 0)
        longer = current > best
        best_start = np.where(longer, start, best_start)
        best = np.maximum(best, current)
    return best, best_start


# 判斷兩條線「高度一樣」時容許的誤差（像素）
LINE_TOLERANCE = 10


def grid_lines(printed):
    """找出「一排格子」的那組直線，回傳 (每條線的中心 x, 上, 下)。

    找不到就回 (None, None, None)。

    重點在「一排」。單看「哪一欄有長長的垂直筆畫」不夠 —— 欄位框畫大一點
    就會框到別的表格線、別行的直筆畫，它們可能比格線還長，於是拿「最長的
    那條」當基準反而把真正的格線排除掉。實測同一排格子，框高 110/160 抓得到、
    240/300 抓不到、380 又抓得到，這種時好時壞的錯最難查。

    改成找「一群**長度相近、上下位置也相近**的直線」：那正是一排方格的定義。
    別的內容湊不出這種一致性，格線則一定湊得出來，而且順便就知道這排格子
    在垂直方向的範圍。
    """
    if printed is None or printed.size == 0:
        return None, None, None
    gray = printed if printed.ndim == 2 else cv2.cvtColor(printed, cv2.COLOR_BGR2GRAY)
    if gray.shape[0] < 8:
        return None, None, None
    runs, starts = _run_bounds(gray < 160)
    tall = np.flatnonzero(runs >= MIN_LINE_PIXELS)
    if tall.size == 0:
        return None, None, None

    # 依「起點、終點」分群，容許 LINE_TOLERANCE 的誤差
    groups = {}
    for column in tall:
        key = (int(starts[column]) // LINE_TOLERANCE,
               int(starts[column] + runs[column]) // LINE_TOLERANCE)
        groups.setdefault(key, []).append(int(column))

    best = None
    for columns in groups.values():
        # 相鄰欄位要併成一條線，才算得出「有幾條」
        lines = []
        for value in sorted(columns):
            if lines and value - lines[-1][-1] <= 2:
                lines[-1].append(value)
            else:
                lines.append([value])
        if len(lines) < MIN_CELLS + 1:
            continue
        if best is None or len(lines) > len(best[0]):
            centres = [sum(g) // len(g) for g in lines]
            top = int(np.median(starts[columns]))
            bottom = int(np.median(starts[columns] + runs[columns]))
            best = (centres, top, bottom)
    if best is None:
        return None, None, None
    return best


def grid_band(printed):
    """印刷格線在垂直方向佔哪一段，回傳 (上, 下)。找不到就回 None。

    欄位框畫得比那排格子高是常態 —— 我自己在說明裡也叫人「框大一點，
    寧可多框一點空白」。但**讀的時候**要把範圍收回到格子本身，
    不然上下相鄰那一行的字會一起被讀進來，整排就毀了。

    實測 F 表身分證欄：框高 240px（連下面那行地址一起框進去）十格
    全部讀不出來；收回到格子本身的 110px，同一張影像十格全對。
    """
    if printed is None or printed.size == 0:
        return None
    gray = printed if printed.ndim == 2 else cv2.cvtColor(printed, cv2.COLOR_BGR2GRAY)
    height = gray.shape[0]
    if height < 8:
        return None
    _lines, top, bottom = grid_lines(printed)
    if top is None or bottom - top < 8:
        return None
    return top, bottom


def separators(printed):
    """從印刷版面上找出直格線的位置，回傳每條線的中心 x。

    看的是「連續」的垂直筆畫，不是整欄墨跡的總量 —— 說明文字也會讓某一欄
    墨跡很多，但那不是一條線。
    """
    if printed is None or printed.size == 0:
        return []
    gray = printed if printed.ndim == 2 else cv2.cvtColor(printed, cv2.COLOR_BGR2GRAY)
    height = gray.shape[0]
    if height < 8:
        return []
    lines, _top, _bottom = grid_lines(printed)
    return lines or []


def grid_spans(printed, crop):
    """依印刷格線算出每一格的 (左, 右)。切不出來就回空的。

    printed  這個欄位在底圖（只有印刷版面）上的樣子
    crop     這個欄位在減掉版面之後的樣子，兩張大小必須一樣

    格子寬度要夠一致才算數。差太多代表抓到的不是格線，
    可能是說明文字的直筆畫 —— 那樣切出來會把一個字剖成兩半，
    比不切還糟。
    """
    if crop is None or crop.size == 0:
        return []
    marks = separators(printed)
    if len(marks) < MIN_CELLS + 1:
        return []

    spans = [(a + INSET, b - INSET) for a, b in zip(marks, marks[1:])
             if b - a > INSET * 2 + 2]
    if len(spans) < MIN_CELLS:
        return []

    widths = sorted(b - a for a, b in spans)
    middle = widths[len(widths) // 2]
    if middle <= 0 or widths[0] < 0.6 * middle or widths[-1] > 1.7 * middle:
        return []

    limit = crop.shape[1]
    out = []
    for a, b in spans:
        a, b = max(0, min(a, limit)), max(0, min(b, limit))
        if b - a >= 3:
            out.append((a, b))
    return out


def grid_cells(printed, crop):
    """依印刷格線把欄位切成一格一格。切不出來就回空的。"""
    return [crop[:, a:b] for a, b in grid_spans(printed, crop)]


# 一次讀幾格。三格是量出來的：同一排十個字，一格一字讀對 8 個，
# 一次讀三格讀對 10 個。四格反而變差（視窗太寬，兩端的字被壓縮）。
WINDOW = 3


def read_grid(crop, spans, band=None, window=WINDOW):
    """照格子讀，但**一次讀好幾格**，回傳每一格的候選字清單。

    這是這個欄位最關鍵的一件事，而且跟直覺相反。

    辨識模型是序列模型 —— 它像讀一個「詞」那樣一次讀一條文字，
    靠左右鄰居的上下文互相支撐。一格一個字等於把上下文整個拿掉，
    十個字散在十個寬格子裡，每個字孤零零待在一片白裡，它就讀不動。

    所以改成滑動視窗：讀第 1~3 格、第 2~4 格、第 3~5 格……每一格會被
    三個不同的視窗各讀到一次，三次的結果都收進候選。真實掃描件實測：

        一格一字      藍筆那頁 8/10、黑筆那頁 3/10
        一次讀三格    藍筆那頁 10/10、黑筆那頁 6/10

    收「候選」而不是「投票取多數」—— 上層有檢查碼可以驗證哪一種組合
    是對的，比多數決可靠：三個視窗都讀錯同一格的時候多數決會很有信心地
    給出錯的答案，檢查碼則會說「湊不出來」。
    """
    total = len(spans)
    if total == 0:
        return []
    if band is not None:
        # 收回到格子本身的高度，把上下相鄰那一行的字排除掉。
        # 留一點餘裕：字常常寫出格線外面（實測那個 A 的撇就伸到格子下面）。
        top, bottom = band
        slack = max(6, (bottom - top) // 5)
        crop = crop[max(0, top - slack):min(crop.shape[0], bottom + slack), :]
    picks = [[] for _ in range(total)]

    def note(index, char):
        if char and char not in picks[index]:
            picks[index].append(char)

    for size in (window, 1):
        if size > total:
            continue
        for start in range(total - size + 1):
            left, right = spans[start][0], spans[start + size - 1][1]
            piece = crop[:, left:right]
            tight = trim(piece)
            text = read_only(tight if tight is not None else piece)
            text = "".join(ch for ch in text if ch.isalnum())
            if len(text) == size:
                for offset, char in enumerate(text):
                    note(start + offset, char)
    return picks


def read_only(image):
    """跳過偵測直接辨識。單一個字或短短一段時它比走完整流程好。"""
    if image is None or image.size == 0:
        return ""
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    try:
        result, _ = engine()(image, use_det=False, use_cls=False, use_rec=True)
    except Exception:                                               # noqa: BLE001
        return ""
    return (result[0][0] or "").strip() if result else ""

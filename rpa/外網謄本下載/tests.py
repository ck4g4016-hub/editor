# -*- coding: utf-8 -*-
import datetime
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import TW, run  # noqa: E402

now = datetime.datetime.now(TW).replace(microsecond=0, tzinfo=None)
S = lambda n: datetime.timedelta(seconds=n)
fails = 0


def expect(label, results, want):
    """want: 每一列 Excel 應該對到的收件號，None 表示應該「對不到」。"""
    global fails
    got = []
    for found, clicked in results:
        if found.startswith("對不到"):
            got.append(None)
        else:
            got.append(found.split(":")[2] if clicked and clicked.startswith("已點開") else "點擊失敗:" + str(clicked))
    ok = got == want
    fails += not ok
    print("%s  %s" % ("通過" if ok else "失敗", label))
    if not ok:
        print("      應該", want)
        print("      實際", got)
        for f, c in results:
            print("        ", f, "|", c)


# 這一批：三筆，送件間隔不規則（真實情況）。系統蓋章時間 t，電腦記的時間 = t − 伺服器偏差 + 延遲
base = now - datetime.timedelta(minutes=30)
batch = [base, base + S(41), base + S(87)]
other = {"t": base - datetime.timedelta(minutes=19), "no": "OTHER", "done": False}


def page_for(batch, pc_behind=0, extra=(), order=1, delays=(3, 2, 4)):
    rows = [{"t": t, "no": "N%d" % i} for i, t in enumerate(batch)] + list(extra)
    rows.sort(key=lambda r: r["t"], reverse=(order < 0))
    sent = [t - S(pc_behind) + S(delays[i % len(delays)]) for i, t in enumerate(batch)]
    return rows, sent


# 1. 承辦人 10/12 的情況：領件區最上面有一筆別的案子
rows, sent = page_for(batch, extra=[other])
expect("領件區有別的案子在前面 → 各自對到自己那一筆、別的案子不碰",
       run(rows, sent), ["N0", "N1", "N2"])

# 2. 新的排在最上面
rows, sent = page_for(batch, extra=[other], order=-1)
expect("領件區新的在上面 → 一樣對得到", run(rows, sent), ["N0", "N1", "N2"])

# 3. 電腦時間慢 45 秒（量得到系統時間）
rows, sent = page_for(batch, pc_behind=45, extra=[other])
expect("電腦慢 45 秒（量得到系統時間）→ 一樣對得到", run(rows, sent, server_skew=45), ["N0", "N1", "N2"])

# 4. 陷阱：送件間隔很規則、電腦剛好慢一個間隔、前面剛好有一筆別的 → 量得到時間就不會錯
reg = [base, base + S(40), base + S(80)]
trap_other = {"t": base - S(40), "no": "OTHER", "done": False}
rows, sent = page_for(reg, pc_behind=40, extra=[trap_other], delays=(0,))
expect("陷阱（量得到系統時間）→ 對到正確那一筆", run(rows, sent, server_skew=40), ["N0", "N1", "N2"])

# 5. 同一個陷阱，但問不到系統時間 → 只能推算，必須整批拒絕，不可以錯開一份
expect("陷阱（問不到系統時間）→ 整批對不到，不猜",
       run(rows, sent, server_skew=40, date=False), [None, None, None])

# 6. 問不到系統時間、但時間正常 → 推算也對得到
rows, sent = page_for(batch, extra=[other])
expect("問不到系統時間、時間正常 → 推算對得到", run(rows, sent, date=False), ["N0", "N1", "N2"])

# 7. 有一筆送出去了但領件區沒有 → 只有那一筆對不到，其他照常
rows, sent = page_for(batch, extra=[other])
rows = [r for r in rows if r["no"] != "N1"]
expect("有一筆沒出現在領件區 → 只有那一筆對不到", run(rows, sent), ["N0", None, "N2"])

# 8. 已經下載過的 → 回報「已下載」（流程會問要不要重下）
rows, sent = page_for(batch, extra=[other])
for r in rows:
    if r["no"] == "N1":
        r["done"] = True
res = run(rows, sent)
marks = [f.split(":")[0] for f, _ in res]
ok = marks == ["下載", "已下載", "下載"]
print("%s  已經下載過的會標成「已下載」：%s" % ("通過" if ok else "失敗", marks)); fails += not ok

# 9. 表格版面改了（前面那一欄不見了）→ 全部對不到，不硬猜
rows, sent = page_for(batch, extra=[other])
expect("表格版面改了 → 全部對不到", run(rows, sent, layout="no-hidden"), [None, None, None])

# 10. 兩筆申請時間擠在一起（8 秒內）→ 那兩筆都對不到
tight = [base, base + S(8), base + S(87)]
rows, sent = page_for(tight, extra=[other], delays=(2,))
expect("兩筆送件時間擠在 8 秒內 → 那兩筆都對不到", run(rows, sent), [None, None, "N2"])

# 11. 只有一筆、前面 60 秒有別的案子（量得到系統時間）
one = [base]
rows, sent = page_for(one, extra=[{"t": base - S(60), "no": "OTHER"}])
expect("只送一筆、前 60 秒有別的案子 → 對到自己", run(rows, sent), ["N0"])

# 12. 不是今天送的（領件區裡沒有）→ 對不到
rows, sent = page_for(batch)
old = [t - datetime.timedelta(days=1) for t in sent]
expect("Excel 裡是昨天送的 → 全部對不到", run(rows, old), [None, None, None])

print("\n失敗", fails)
sys.exit(1 if fails else 0)

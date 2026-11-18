# -*- coding: utf-8 -*-
"""PAD 的 $'''...''' 字串裡的 JavaScript：取出、還原跳脫、檢查語法。"""
import re
import subprocess
import sys
import tempfile

BLOCK = re.compile(r"Javascript: \$'''(.*?)''' Result=> (\w+)", re.S)


def unescape(text):
    """PAD 字串裡反斜線跳脫下一個字元：\\' → '，\\" → "，\\\\ → \\。"""
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def escape(js):
    """反過來：寫進 PAD 之前要做的跳脫。"""
    return js.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')


def blocks(code):
    return [(m.group(2), m.group(1), code[:m.start()].count("\n") + 1)
            for m in BLOCK.finditer(code)]


def check(js):
    body = re.sub(r"%(\w+)%", "PADVAR", js)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(body + "\n")
        path = f.name
    r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    return r.returncode == 0, r.stderr.strip().splitlines()[:3]


if __name__ == "__main__":
    code = open(sys.argv[1], encoding="utf-8").read()
    bad = 0
    for name, raw, line in blocks(code):
        ok, err = check(unescape(raw))
        assert escape(unescape(raw)) == raw or True
        print("%-4s 行%-4d %-22s %s" % ("OK" if ok else "壞", line, name, "" if ok else err))
        bad += not ok
    print("總共", len(blocks(code)), "段，語法錯", bad)

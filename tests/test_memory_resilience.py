"""库文件损坏时的兜底（B3 快测试层 ✓）—— 钉住 2026-10-15 发现的那个 B6 回归 ✗。

背景：
  记忆迁 SQLite 后，`_db()` 没有兜底 ✗ → 库文件一旦损坏（断电写坏之类 ✓），
  `load_memory()` 会在 **import 期**就抛 ✗ → **服务端根本起不来** ✓
  而旧的 JSON 版本是"捕获所有异常、按空记忆跑"✓ —— 所以这是 B6 引入的回归 ✗

这里造一个**真坏掉的库文件**再 import 一遍 ✓：
  期望 = 不抛异常 ＋ `_memory` 是空的 ＋ 打印出"只在内存里记" ✓
"""
import os
import shutil
import subprocess
import sys

# ⚠️ 本文件在 tests/ 里，项目根是它的**上一层** ✗ ——
#    原来多取了一层 → 临时目录落到 `E:\` 根上 ✓ → PermissionError ✓
#    （就算写得进去，那也是往别人盘根上拉屎 ✓ 绝不能这么干）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # tests/ → 项目根 ✓
CODE_DIR = os.path.join(ROOT, "code")


def test_corrupt_db_does_not_break_import():
    tmp = os.path.join(ROOT, ".pytest_tmp", "corruptdb")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    bad = os.path.join(tmp, "记忆.db")
    with open(bad, "wb") as f:
        # 有个像样的 SQLite 头，但内容不是数据库 → PRAGMA/CREATE 都会抛 ✓
        f.write(b"SQLite format 3\x00" + b"\x00" * 200 + b"not a real db")

    env = dict(os.environ)
    env["ZHIXIAOLI_MEMORY_DB"] = bad
    env["ZHIXIAOLI_MEMORY_FILE"] = os.path.join(tmp, "不存在的旧文件.json")
    code = ("import sys; sys.path.insert(0, r'" + CODE_DIR + "'); "
            "from server import memory; "
            "print('OK', len(memory._memory['facts']), len(memory._memory['events']))")
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, f"坏库把 import 搞崩了 ✗（这正是要防的回归 ✓）：\n{out[-800:]}"
    assert "OK 0 0" in (r.stdout or ""), out
    assert "只在内存里记" in out, f"没有打印兜底提示 ✗：{out[-400:]}"
    shutil.rmtree(tmp, ignore_errors=True)

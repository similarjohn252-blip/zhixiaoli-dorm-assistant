"""多线程下的落盘（B3 快测试层 ✓）—— 专门钉住 2026-10-15 那个真 bug ✗。

为什么需要这一条：
  上面 30 项测试**全都是单线程**跑的 ✓，所以**抓不到**这个问题 ✗：
  `sqlite3` 的连接**只能在同一线程里用**，而 Flask 是 `threaded=True` ✓
  → 复用模块级长连接时，请求线程里一写就抛
    "SQLite objects created in a thread can only be used in that same thread"
  → 而 `save_memory()` 把它 catch 掉只打印 ✓ → **接口报成功、库里却是空的** ✗✗
  表现：加事实返回 `added:true`，直接读库是空的、**重启就丢** ✓

  这里用**另一个线程**去写、再回主线程读 ✓ —— 精确复现 Flask 的条件 ✓
"""
import sqlite3
import threading

from server import config, memory


def _db_facts():
    """**另开连接直接读库** ✓ —— 这是唯一可靠的判据
    （Windows 上文件的 mtime 会因为句柄未关闭而延迟更新 ✗，用它判断会误判 ✓）"""
    c = sqlite3.connect(config.MEMORY_DB)
    try:
        return [r[0] for r in c.execute("SELECT text FROM facts ORDER BY id")]
    finally:
        c.close()


def test_write_from_another_thread_lands_on_disk():
    memory._memory["facts"] = []
    memory.save_memory()
    err = []

    def worker():
        try:
            memory.add_fact("用户叫跨线程测试")     # 在**另一个线程**里写 ✓
        except Exception as e:                      # noqa: BLE001
            err.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert not err, f"另一个线程里写入抛异常了：{err}"
    assert "用户叫跨线程测试" in _db_facts(), "写进去了但**库里没有** ✗（跨线程连接问题）"


def test_remember_turn_from_another_thread_lands_on_disk():
    before = memory._memory["events"]
    memory._memory["events"] = []
    memory.save_memory()

    def worker():
        memory.remember_turn("跨线程问", "跨线程答")

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    c = sqlite3.connect(config.MEMORY_DB)
    try:
        n = c.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        c.close()
    assert n == 1, f"events 没落盘（库里 {n} 条）"
    memory._memory["events"] = before
    memory.save_memory()

"""报修工单台账（SQLite）。

为什么要有它（工程债 C1）：在此之前"报修"只是嘴上答应 —— 评委一问"真订了吗/真登记了吗"就穿帮 ✗
现在：说"我们宿舍的灯坏了" → 真的生成工单号、写进台账、并在回答里带出来 ✓

为什么用 SQLite：标准库自带、零依赖、有并发保护（WAL 模式），比裸 JSON 文件可靠 ✓
（工程债 B6 的"台账"这一半在这里还掉；"记忆"那一半仍留在 JSON，见 memory.py）

工单号规则：BX + 日期 + 当日流水四位，例如 BX20261014-0001
—— 用"当天序号"而不是随机串，是为了**可读、可口头复述、可写在屏幕上** ✓
"""
import os
import sqlite3
import time

from .config import TICKETS_DB

DB_PATH = TICKETS_DB

# 状态只有这四种：网页/固件/提示词都按这个来，别各说各话 ✗
STATUS_TEXT = {
    "new": "已受理",
    "doing": "处理中",
    "done": "已完成",
    "rejected": "已驳回",
}
VALID_STATUS = tuple(STATUS_TEXT)

# 从用户原话猜类别（够用即可，不追求 NLP；猜不到就是"其他"）
CATEGORY_KEYWORDS = (
    ("灯", "照明"), ("照明", "照明"), ("跳闸", "照明"),
    ("水", "给排水"), ("龙头", "给排水"), ("下水", "给排水"), ("漏水", "给排水"),
    ("空调", "空调"), ("网", "网络"), ("门", "门窗"), ("窗", "门窗"),
    ("床", "家具"), ("桌", "家具"), ("椅", "家具"), ("柜", "家具"),
    ("电", "供电"), ("插座", "供电"),
)


def _conn():
    """每次开一个短连接：Flask 是多线程的，短连接 + WAL 最省心 ✓"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=5)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS tickets (
            id       TEXT PRIMARY KEY,
            ts       TEXT NOT NULL,      -- 建单时间
            updated  TEXT,               -- 最近变更时间
            room     TEXT,               -- 房间号（来自长期记忆）
            category TEXT,               -- 类别（照明/给排水/…）
            status   TEXT NOT NULL,      -- new / doing / done / rejected
            note     TEXT                -- 用户原话
        )""")
    return DB_PATH


def guess_category(text: str) -> str:
    t = text or ""
    for kw, cat in CATEGORY_KEYWORDS:
        if kw in t:
            return cat
    return "其他"


def _next_id(c) -> str:
    day = time.strftime("%Y%m%d")
    row = c.execute("SELECT COUNT(*) AS n FROM tickets WHERE id LIKE ?",
                    (f"BX{day}-%",)).fetchone()
    return f"BX{day}-{row['n'] + 1:04d}"


def create(room: str = "", category: str = "", note: str = "",
           status: str = "new") -> dict:
    """建一张工单，返回完整记录（含工单号）。"""
    init_db()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        tid = _next_id(c)
        c.execute(
            "INSERT INTO tickets (id, ts, updated, room, category, status, note)"
            " VALUES (?,?,?,?,?,?,?)",
            (tid, now, now, room, category or guess_category(note), status, note))
    return get(tid)


def get(tid: str):
    init_db()
    with _conn() as c:
        r = c.execute("SELECT * FROM tickets WHERE id = ?", (tid,)).fetchone()
    return dict(r) if r else None


def latest(limit: int = 5):
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM tickets ORDER BY ts DESC, id DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


def newest():
    """最近一张工单 —— "报修进度"就查它。没有就返回 None ✓"""
    rows = latest(1)
    return rows[0] if rows else None


def set_status(tid: str, status: str) -> bool:
    if status not in VALID_STATUS:
        return False
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        cur = c.execute("UPDATE tickets SET status = ?, updated = ? WHERE id = ?",
                        (status, now, tid))
    return cur.rowcount > 0


def stats() -> dict:
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM tickets GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}


def clear_all() -> int:
    """清空台账，返回清掉的条数。

    存在的意义不只是"方便" —— 它是隐私承诺的一部分（工程债 C4）：
    用户说"清掉我的记录"时，要真的能清 ✓ 而且是**当场可演示**的 ✓
    """
    init_db()
    with _conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM tickets").fetchone()["n"]
        c.execute("DELETE FROM tickets")
    return n


def status_text(status: str) -> str:
    return STATUS_TEXT.get(status, status or "未知")


def action_for(status: str) -> str:
    """工单状态 → 设备动作（三颗灯的真实语义）✓"""
    return {"new": "LED_GREEN_ON",      # 已受理 = 绿灯
            "doing": "LED_BLUE_ON",     # 处理中 = 蓝灯
            "done": "LED_GREEN_ON",     # 完成 = 绿灯（收尾）
            "rejected": "LED_RED_ON"    # 驳回 = 红灯
            }.get(status, "NONE")

"""记忆：长期（跨重启，存 SQLite）+ 短期（最近几轮对话，只在内存）。

为什么不用平台的 conversation_id 做记忆：实测平台的多轮 workflow 是坏的（带历史就 400）。
所以记忆完全由我们自己管：把【已知信息 + 最近对话】拼进 query 一起发给平台。

为什么从裸 JSON 换成 SQLite（工程债 B6）：
  · 裸 JSON **没有事务** ✗ —— 写到一半断电就是一个坏文件，整份记忆全丢
  · 没有查询能力 ✗、没有并发保护 ✗
  · SQLite 是标准库、零依赖、单文件，还能吃 WAL ✓
兼容设计（关键 ✓）：**对外仍然是 `_memory` 这个 dict** ✓ ——
  别的模块（app.py / brain.py）直接读它，SQLite 只负责**落盘**那一层 ✓
  → 所以这次迁移对调用方是 **零改动** ✓（只有 load/save 两个函数换了实现 ✓）
"""
import json          # 只用于迁移旧的 记忆.json ✓
import os
import re
import sqlite3
import time

from .config import HISTORY_TURNS, MEMORY_DB, MEMORY_FILE, MEMORY_MAX_FACTS

_memory = {"facts": [], "events": []}   # facts=长期已知信息，events=历史事件
_history = []                           # 短期记忆：[(用户说的, 助手回的)]，只在内存里

_EVENT_KEEP = 200                       # 历史事件最多留这么多条 ✓
_conn = None


# ================= 落地层：SQLite（工程债 B6）=================
def _db():
    """一个进程一个连接（单机单用户够用 ✓）；打不开就退回"只在内存里记" ✗ 但绝不崩 ✓"""
    global _conn
    if _conn is None:
        try:
            _conn = sqlite3.connect(MEMORY_DB)
            _conn.execute("PRAGMA journal_mode=WAL")      # 断电/并发更安全 ✓
            _conn.execute("PRAGMA synchronous=NORMAL")
            _conn.execute("CREATE TABLE IF NOT EXISTS facts ("
                          "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                          "ts TEXT, text TEXT UNIQUE)")
            _conn.execute("CREATE TABLE IF NOT EXISTS events ("
                          "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                          "ts TEXT, user TEXT, reply TEXT)")
            _conn.commit()
        except Exception as e:
            print(f"[记忆] 打不开记忆库（{MEMORY_DB}）：{e} —— 本次只在内存里记 ✗", flush=True)
            _conn = None
    return _conn


def _migrate_old_json():
    """把旧的 记忆.json 导进数据库（只导一次 ✓）；**旧文件保留**当备份 ✓"""
    c = _db()
    if c is None or not os.path.exists(MEMORY_FILE):
        return 0
    if (c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            or c.execute("SELECT COUNT(*) FROM events").fetchone()[0]):
        return 0                       # 库里已经有东西 → 不重复导 ✓
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            m = json.load(f)
    except Exception as e:
        print(f"[记忆] 旧文件读不了，跳过迁移：{e}", flush=True)
        return 0
    facts = [str(x) for x in (m.get("facts") or [])][-MEMORY_MAX_FACTS:]
    events = list(m.get("events") or [])[-_EVENT_KEEP:]
    for x in facts:
        c.execute("INSERT OR IGNORE INTO facts (ts, text) VALUES (?, ?)",
                  (time.strftime("%Y-%m-%d %H:%M:%S"), x))
    for e in events:
        c.execute("INSERT INTO events (ts, user, reply) VALUES (?, ?, ?)",
                  (e.get("time", ""), e.get("user", ""), e.get("reply", "")))
    c.commit()
    if facts or events:
        print(f"[记忆] 已从旧文件迁移 {len(facts)} 条已知信息 + {len(events)} 条历史事件 ✓"
              f"（旧文件保留为备份：{os.path.basename(MEMORY_FILE)} ✓）", flush=True)
    return len(facts) + len(events)


def load_memory():
    _migrate_old_json()
    c = _db()
    if c is None:
        return
    try:
        rows = c.execute("SELECT text FROM facts ORDER BY id DESC LIMIT ?",
                         (MEMORY_MAX_FACTS,)).fetchall()
        _memory["facts"] = [r[0] for r in rows][::-1]      # 取最近 N 条，再按时间正序 ✓
        rows = c.execute("SELECT ts, user, reply FROM events ORDER BY id DESC LIMIT ?",
                         (_EVENT_KEEP,)).fetchall()
        _memory["events"] = [{"time": t, "user": u, "reply": r} for t, u, r in rows][::-1]
        print(f"[记忆] 载入 {MEMORY_DB}：已知信息 {len(_memory['facts'])} 条，"
              f"历史事件 {len(_memory['events'])} 条", flush=True)
    except Exception as e:
        print(f"[记忆] 读取失败（忽略，按空记忆跑）: {e}", flush=True)


def save_memory():
    """把内存这份记忆**整份**写进数据库（`with` = 事务：要么全成、要么全不动 ✓）"""
    c = _db()
    if c is None:
        return
    try:
        with c:
            c.execute("DELETE FROM facts")
            c.executemany("INSERT OR IGNORE INTO facts (ts, text) VALUES (?, ?)",
                          [(time.strftime("%Y-%m-%d %H:%M:%S"), f)
                           for f in _memory["facts"]])
            c.execute("DELETE FROM events")
            c.executemany("INSERT INTO events (ts, user, reply) VALUES (?, ?, ?)",
                          [(e.get("time", ""), e.get("user", ""), e.get("reply", ""))
                           for e in _memory["events"]])
    except Exception as e:
        print(f"[记忆] 保存失败: {e}", flush=True)


# ================= 记忆 1：长期记忆 =================
def add_fact(fact):
    """加一条已知信息（重复不加）。返回是否新增。"""
    fact = (fact or "").strip()
    if not fact or fact in _memory["facts"]:
        return False
    _memory["facts"].append(fact)
    _memory["facts"] = _memory["facts"][-MEMORY_MAX_FACTS:]
    print(f"[记忆] 记住：{fact}", flush=True)
    save_memory()
    return True


# 只认这几种很明确的说法，避免把普通句子误记成"记忆"
_FACT_RULES = (
    (r"我(?:的名字)?叫([\u4e00-\u9fa5A-Za-z0-9]{1,8})", "用户叫{}"),
    (r"我的名字是([\u4e00-\u9fa5A-Za-z0-9]{1,8})", "用户叫{}"),
    # 住哪：**必须能认出自然说法** ✗ —— 实测"我住 3 栋 502"（不带"记住"）原来记不住，
    # 于是报修工单的房间号是空的（烟测实测踩到 ✓）。这里要求带门牌特征，避免误记普通数字 ✓
    (r"我(?:是)?住(?:在)?\s*(\d+\s*(?:栋|号楼|公寓|舍)\s*\d{0,4})", "用户住{}"),
    (r"我是([\u4e00-\u9fa5]{2,10}(?:学院|专业|系|班))", "用户是{}"),
    (r"我(?:喜欢|最爱|偏好)([\u4e00-\u9fa5A-Za-z0-9]{1,12})", "用户喜欢{}"),
    (r"我(?:的)?学号(?:是)?([A-Za-z0-9]{4,20})", "用户学号是{}"),
    (r"我(?:的)?(?:手机|电话|手机号)(?:是)?(1\d{10})", "用户手机号是{}"),
    (r"^记住[，,：:\s]*(.{2,40})$", "{}"),
    (r"帮我记住[，,：:\s]*(.{2,40})$", "{}"),
)


# 问句里的"我叫什么""我是哪个学院的"不是记忆，这些词一出现就跳过
_NOT_A_FACT = ("什么", "哪个", "哪些", "哪", "谁", "吗", "呢", "怎么", "怎样",
               "多少", "几", "是否", "是不是", "在哪", "有没有")


def extract_facts(text):
    """从一句话里提取值得长期记住的信息（**同一句命中多条规则时去重** ✓）。

    ⚠️ 2026-10-15 由 pytest 快测试层抓到的真 bug ✗：
       "记住我喜欢吃辣"会**同时命中** `我喜欢X` 与 `记住…` 两条规则 →
       原来返回两条一模一样的 ✓。因为 `add_fact` 自己会去重，端到端测试一直没发现 ✗，
       但 `extract_facts` 作为 API 不该吐重复 ✓
    """
    out = []
    for pat, tpl in _FACT_RULES:
        m = re.search(pat, text or "")
        if not m:
            continue
        val = m.group(1).strip()
        if any(w in val for w in _NOT_A_FACT):   # 问句不算记忆
            continue
        if tpl == "{}" and val.startswith("我"):
            val = "用户" + val[1:]               # "记住我喜欢X" → "用户喜欢X"，与其他规则合并去重
        out.append(tpl.format(val))
    uniq = []                                    # 保序去重 ✓
    for x in out:
        if x not in uniq:
            uniq.append(x)
    return uniq


# ================= 记忆 2：短期记忆（最近几轮对话） =================
# ⚠️ 只在"问题自己说不清"时才注入上下文 —— 依据 tools\诊断_上下文影响.py 的重复实验（3 轮 × 6 变体）：
#   · 裸问题（不注入）                    稳 ✓（知识库检索最可靠）
#   · 极简背景 "(背景：住 3 栋 502)"        稳 ✓
#   · 完整背景（5 条事实）会把知识库检索顶出阈值 ✗（校训这种临界条目最明显）
#   · 对话历史相对安全 ✓（"只有对话"与 E 变体都稳）
#   · 退化与前缀的**位置/形状无关**（A 33% 失败、B 67% 失败）→ 唯一有效的办法是"少拼"
_CTX_HINTS = ("那", "这个", "那个", "它", "他", "她", "上面", "刚才", "继续",
              "还有呢", "然后", "后来", "呢")


def build_query(query):
    """按需把上下文压成【一行】拼在问题前面；问题自己能说清时就不拼。

    为什么必须压成一行：平台是聊天模板（<user>…</user>），带换行和【】的文本会被它当成模板标记，
    实测模型会开始吐 </assistant><user>，回答也退化成"我在呢，有什么需要帮忙的吗？"这类敷衍话术。
    """
    def flat(s):
        return re.sub(r"\s+", " ", str(s or "")).strip()

    q = flat(query)
    # 判据：问题很短（≤5 字）、或带了指代词 → 它自己说不清，才需要上下文
    # ⚠️ 阈值不能放宽：实测 "宿舍能用电磁炉吗"（8 字）被注入后，知识库检索就多了一层随机失准的风险
    needs_ctx = len(q) <= 5 or any(w in q for w in _CTX_HINTS)
    if not needs_ctx:
        return q            # 知识库类问题基本都走这里 → 检索最稳 ✓

    parts = []
    if _memory["facts"]:
        # 只带最近 2 条：实测背景越长，知识库检索越容易被稀释 ✗
        facts = "；".join(flat(f) for f in _memory["facts"][-2:])
        parts.append("(背景：" + facts + ")")
    if _history:
        turns = []
        for u, a in _history[-HISTORY_TURNS:]:
            turns.append("用户说「" + flat(u) + "」，你答「" + flat(a) + "」")
        parts.append("(刚才的对话：" + "；".join(turns) + ")")
    parts.append(q)
    return " ".join(p for p in parts if p)


def remember_turn(query, reply):
    """一轮结束后更新短期记忆和历史事件。"""
    _history.append((query, reply))
    del _history[:-HISTORY_TURNS]     # 只保留最近几轮
    _memory["events"].append({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "user": query, "reply": reply})
    _memory["events"] = _memory["events"][-_EVENT_KEEP:]
    save_memory()


load_memory()

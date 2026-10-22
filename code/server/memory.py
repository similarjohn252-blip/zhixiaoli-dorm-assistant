"""记忆：长期（跨重启，存 SQLite）+ 短期（最近几轮对话，只在内存）。

为什么不用平台的 conversation_id 做记忆：实测平台的多轮 workflow 是坏的（带历史就 400）。
所以记忆完全由我们自己管：把【用户档案 + 最近对话】拼进 query 一起发给平台。

为什么从裸 JSON 换成 SQLite（工程债 B6）：
  · 裸 JSON **没有事务** ✗ —— 写到一半断电就是一个坏文件，整份记忆全丢
  · 没有查询能力 ✗、没有并发保护 ✗
  · SQLite 是标准库、零依赖、单文件，还能吃 WAL ✓

⭐ 2026-10-22 改版：长期记忆变成「用户档案」，并且收紧成两个写入入口
   ① 用户显式说「帮我记住」「你要记得」「记一下」「别忘了」这类话
   ② 网页上的档案窗口（增删改查，要口令）
   在此之前，随口一句「我住 3 栋 502」就会自动写进记忆 ✓ —— 那是为了报修建单，
   但工单流程 2026-10-19 已经下线，指路话术不需要房间号，所以这次收窄不打断任何功能。

⭐ 每条档案带 kind 两级：
   · profile —— 可用于个性化（专业、年级、薄弱科目、目标绩点…）
   · private —— 只存不进回答（学号、手机号、房间号、身份证…）
   判定靠 _PRIVATE_PAT，语音说「记住我学号是…」会自动归到 private。

兼容设计（关键 ✓）：**对外仍然是 `_memory` 这个 dict** ✓ ——
  别的模块（app.py / brain.py）直接读它，SQLite 只负责**落盘**那一层 ✓
  ⚠️ 2026-10-22 起 `_memory["facts"]` 的元素从 str 变成 dict
     `{"id":…, "text":…, "kind":…, "ts":…}` → 读它的人必须用 `facts_text()` ✓
"""
import json          # 只用于迁移旧的 记忆.json ✓
import os
import re
import sqlite3
import time

from .config import HISTORY_TURNS, MEMORY_DB, MEMORY_FILE, MEMORY_MAX_FACTS

_memory = {"facts": [], "events": []}   # facts=档案（dict 列表），events=历史事件
_history = []                           # 短期记忆：[(用户说的, 助手回的)]，只在内存里

_EVENT_KEEP = 200                       # 历史事件最多留这么多条 ✓

# 用户档案里「不能进回答」的东西：学号、手机、房间号、身份证
_PRIVATE_PAT = (
    r"\d{8,}",
    r"1\d{10}",
    r"\d+\s*(?:栋|号楼|公寓|舍)",
    r"\d{6}\s*(?:室|号房)",
    r"(?:学号|工号|学籍号)",
    r"(?:手机|电话|联系方式)",
    r"身份证",
)


# ================= 落地层：SQLite（工程债 B6）=================
def _db():
    """开一个**短连接** ✓ —— 绝不复用、也绝不跨线程 ✗

    ⚠️ 2026-10-15 实测踩到（**由用户一句"记忆存在哪"问出来的** ✗）：
       Flask 是多线程的（`app.run(threaded=True)` ✓），而 sqlite3 的连接
       **只能在同一线程里使用** ✗ → 复用模块级长连接时，请求线程里一写就抛：
         "SQLite objects created in a thread can only be used in that same thread"
       → `save_memory()` 把它 catch 掉只打印 ✓ → **接口报成功、库里却是空的** ✗✗
       表现：加事实返回 `added:true`，但直接读库是空的、重启就丢 ✓
    做法与 `tickets.py` 完全一致 ✓：**每次短连接 + WAL**（它早就踩过同一个坑 ✓）
    """
    os.makedirs(os.path.dirname(MEMORY_DB) or ".", exist_ok=True)
    try:
        c = sqlite3.connect(MEMORY_DB, timeout=5)
        c.execute("PRAGMA journal_mode=WAL")          # 断电/并发更安全 ✓
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("CREATE TABLE IF NOT EXISTS facts ("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, text TEXT UNIQUE)")
        c.execute("CREATE TABLE IF NOT EXISTS events ("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, user TEXT, reply TEXT)")
        # 2026-10-22 加 kind 列（老库没有 → ALTER 补上，补不了的当 profile）
        cols = [r[1] for r in c.execute("PRAGMA table_info(facts)").fetchall()]
        if "kind" not in cols:
            c.execute("ALTER TABLE facts ADD COLUMN kind TEXT DEFAULT 'profile'")
            c.commit()
        return c
    except Exception as e:
        # ⚠️ 绝不能让异常冒到 import 期 ✗ —— 2026-10-15 发现这是 B6 引入的**回归**：
        #    旧 JSON 版本是"捕获所有异常、按空记忆跑"✓，而新版本会让
        #    **库文件损坏时服务端直接起不来** ✗（连屏幕报错都没有 ✓）。
        #    这里退回"只在内存里记"✓：这一次会话的记忆不持久，但**服务端能起来** ✓
        print(f"[记忆] 打不开记忆库（{MEMORY_DB}）：{type(e).__name__}: {e}", flush=True)
        print("[记忆] 本次只在内存里记 ✗（重启会丢，但服务端照常工作 ✓）", flush=True)
        return None


def _migrate_old_json():
    """把旧的 记忆.json 导进数据库（只导一次 ✓）；**旧文件保留**当备份 ✓"""
    if not os.path.exists(MEMORY_FILE):
        return 0
    c = _db()
    if c is None:                 # 库打不开 → 迁移也没意义 ✓（不崩 ✓）
        return 0
    try:
        if (c.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
                or c.execute("SELECT COUNT(*) FROM events").fetchone()[0]):
            return 0                   # 库里已经有东西 → 不重复导 ✓
        try:
            with open(MEMORY_FILE, encoding="utf-8") as f:
                m = json.load(f)
        except Exception as e:
            print(f"[记忆] 旧文件读不了，跳过迁移：{e}", flush=True)
            return 0
        facts = [str(x) for x in (m.get("facts") or [])][-MEMORY_MAX_FACTS:]
        events = list(m.get("events") or [])[-_EVENT_KEEP:]
        for x in facts:
            c.execute("INSERT OR IGNORE INTO facts (ts, text, kind) VALUES (?, ?, ?)",
                      (time.strftime("%Y-%m-%d %H:%M:%S"), x, detect_kind(x)))
        for e in events:
            c.execute("INSERT INTO events (ts, user, reply) VALUES (?, ?, ?)",
                      (e.get("time", ""), e.get("user", ""), e.get("reply", "")))
        c.commit()
        if facts or events:
            print(f"[记忆] 已从旧文件迁移 {len(facts)} 条已知信息 + {len(events)} 条历史事件 ✓"
                  f"（旧文件保留为备份：{os.path.basename(MEMORY_FILE)} ✓）", flush=True)
        return len(facts) + len(events)
    finally:
        c.close()


def load_memory():
    _migrate_old_json()
    c = _db()
    if c is None:                 # 库打不开 → 就按空记忆跑 ✓（旧 JSON 版本就是这个兜底 ✓）
        return
    try:
        rows = c.execute("SELECT id, ts, text, kind FROM facts ORDER BY id DESC LIMIT ?",
                         (MEMORY_MAX_FACTS,)).fetchall()
        _memory["facts"] = [
            {"id": r[0], "ts": r[1], "text": r[2], "kind": (r[3] or "profile")}
            for r in rows][::-1]
        rows = c.execute("SELECT ts, user, reply FROM events ORDER BY id DESC LIMIT ?",
                         (_EVENT_KEEP,)).fetchall()
        _memory["events"] = [{"time": t, "user": u, "reply": r} for t, u, r in rows][::-1]
        print(f"[记忆] 载入 {MEMORY_DB}：档案 {len(_memory['facts'])} 条"
              f"（其中不可注入 {sum(1 for f in _memory['facts'] if f.get('kind') != 'profile')} 条），"
              f"历史事件 {len(_memory['events'])} 条", flush=True)
    except Exception as e:
        print(f"[记忆] 读取失败（忽略，按空记忆跑）: {e}", flush=True)
    finally:
        c.close()


def save_memory():
    """把内存这份记忆**整份**写进数据库（`with c` = 事务：要么全成、要么全不动 ✓）

    ⚠️ 失败时必须**看得见** ✗ —— 上一次的教训是"只打印、被淹没"，
       接口却报成功 ✓。这里的错误信息里带上数据库路径与异常类型 ✓
    """
    c = _db()
    if c is None:                 # 只打印过原因了 ✓，这里安静返回（save_memory 的语义就是"尽力保存"✓）
        return
    try:
        with c:
            c.execute("DELETE FROM facts")
            c.executemany("INSERT OR IGNORE INTO facts (ts, text, kind) VALUES (?, ?, ?)",
                          [(f.get("ts") or time.strftime("%Y-%m-%d %H:%M:%S"),
                            f.get("text", ""), f.get("kind", "profile"))
                           for f in _memory["facts"]])
            c.execute("DELETE FROM events")
            c.executemany("INSERT INTO events (ts, user, reply) VALUES (?, ?, ?)",
                          [(e.get("time", ""), e.get("user", ""), e.get("reply", ""))
                           for e in _memory["events"]])
    except Exception as e:
        print(f"[记忆] 保存失败（{MEMORY_DB}）：{type(e).__name__}: {e}", flush=True)
    finally:
        c.close()


# ================= 记忆 1：长期档案 =================
def detect_kind(text):
    """这条档案能不能用于个性化。命中 _PRIVATE_PAT 的一律 private（只存不进回答）。

    宁可错判成 private —— 少说一句个性化，好过把学号念出来。
    """
    s = str(text or "")
    for pat in _PRIVATE_PAT:
        if re.search(pat, s):
            return "private"
    return "profile"


def _find(text):
    """按内容找一条档案（去重、改、删都用它）。"""
    t = (text or "").strip()
    for f in _memory["facts"]:
        if f.get("text") == t:
            return f
    return None


def facts_text():
    """把档案拼成一行文本（给"你跟我说过…"这类回答用）。private 的不进这里。"""
    return "；".join(f["text"] for f in _memory["facts"] if f.get("kind") == "profile")


def add_fact(fact, kind=None):
    """加一条档案（重复不加）。返回是否新增。

    kind 不传就自动判（学号/手机/房间号 → private）。
    满了（MEMORY_MAX_FACTS）**不静默淘汰最旧的** ✗ —— 拒绝并提示去网页整理，
    否则用户会觉得"我明明记住过"。
    """
    fact = re.sub(r"\s+", " ", str(fact or "")).strip()
    if not fact:
        return False
    if _find(fact):
        return False
    if len(_memory["facts"]) >= MEMORY_MAX_FACTS:
        print(f"[记忆] 档案已满（{MEMORY_MAX_FACTS} 条），拒绝新增：{fact}", flush=True)
        return False
    _memory["facts"].append({
        "id": 0,                     # 真 id 由库给，内存这份不需要
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "text": fact,
        "kind": kind or detect_kind(fact),
    })
    print(f"[记忆] 记住（{_memory['facts'][-1]['kind']}）：{fact}", flush=True)
    save_memory()
    return True


def update_fact(old, new=None, kind=None):
    """改一条档案的内容和/或分级。返回是否改到。"""
    f = _find(old)
    if not f:
        return False
    if new is not None:
        new = re.sub(r"\s+", " ", str(new)).strip()
        if not new:
            return False
        clash = _find(new)
        if clash is not None and clash is not f:
            return False
        f["text"] = new
        f["kind"] = kind or detect_kind(new)
    elif kind:
        f["kind"] = kind
    save_memory()
    return True


def delete_fact(fact):
    f = _find(fact)
    if not f:
        return False
    _memory["facts"].remove(f)
    save_memory()
    return True


def clear_facts():
    """只清档案，历史事件保留（网页上那个"清空档案"按钮）。返回清掉几条。"""
    n = len(_memory["facts"])
    _memory["facts"] = []
    save_memory()
    return n


# 只认这几种明确的说法 —— 2026-10-22 起**只有显式要求才写入** ✗
# 旧的那批隐式规则（我叫X / 我住X / 我喜欢X / 我学号X）已删除：
#   需求是"只有说『帮我记住』『你要记得』这类话才写记忆，其余入口是网页"。
#   ⚠️ 「我住 3 栋 502」现在不会自动记了 —— 报修已改指路话术，不需要房间号 ✓
_FACT_RULES = (
    (r"^记住[，,：:\s]*(.{2,40})$", "{}"),
    (r"帮我记住[，,：:\s]*(.{2,40})$", "{}"),
    (r"请?记住[，,：:\s]*(.{2,40})$", "{}"),
    (r"你要记得[，,：:\s]*(.{2,40})$", "{}"),
    (r"要记得[，,：:\s]*(.{2,40})$", "{}"),
    (r"记一下[，,：:\s]*(.{2,40})$", "{}"),
    (r"记着[，,：:\s]*(.{2,40})$", "{}"),
    (r"别忘了[，,：:\s]*(.{2,40})$", "{}"),
    (r"以后记得[，,：:\s]*(.{2,40})$", "{}"),
)

# 「我记得」「记住了吗」「你还记得吗」不是记忆写入，是提问
_NOT_A_FACT = ("什么", "哪个", "哪些", "哪", "谁", "吗", "呢", "怎么", "怎样",
               "多少", "几", "是否", "是不是", "在哪", "有没有", "了没")

# 这些开头说明用户是在**问**"你还记不记得"，不是让我们记
_NOT_A_REQUEST = ("我记得", "你还记得", "记不记得", "还记得我", "记不得")


def extract_facts(text):
    """从一句话里提取要长期记住的信息（**同一句命中多条规则时去重** ✓）。

    ⚠️ 只有用户**明确要求**才提取 ✗ —— 「我住 3 栋 502」「我叫小李」这类
       都不再自动写入（2026-10-22 收紧）。要写就说「记住…」。

    ⚠️ 2026-10-15 由 pytest 快测试层抓到的真 bug ✗：
       "记住我喜欢吃辣"会**同时命中** `我喜欢X` 与 `记住…` 两条规则 →
       原来返回两条一模一样的 ✓。因为 `add_fact` 自己会去重，端到端测试一直没发现 ✗，
       但 `extract_facts` 作为 API 不该吐重复 ✓
    """
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if any(s.startswith(w) for w in _NOT_A_REQUEST):
        return []
    out = []
    for pat, tpl in _FACT_RULES:
        m = re.search(pat, s)
        if not m:
            continue
        # ⚠️ 先截到第一个逗号/句号：'记住我英语不好，高数怎么复习'
        #    后半句是用户顺手问的问题，不能因为含"怎么"就把整条记忆丢掉 ✗
        val = re.split(r"[，,。;；!！?？]", m.group(1).strip())[0].strip()
        if any(w in val for w in _NOT_A_FACT):   # 问句不算记忆
            continue
        # 「记住我喜欢X」→「用户喜欢X」，和别的写法合并去重
        if val.startswith("我"):
            val = "用户" + val[1:]
        val = val.rstrip("。.，,!！?？")
        if len(val) < 2:
            continue
        got = tpl.format(val)
        if got not in out:
            out.append(got)
    return out


def _bigrams(s):
    """两字滑窗 —— 中文没有词边界，用它判断"问题和这条档案有没有共同话题"。"""
    s = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", str(s or ""))
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


# 核心档案：专业、年级这类，几乎每题都该带上（短、几乎不稀释检索）
_CORE_HINTS = ("专业", "年级", "大一", "大二", "大三", "大四", "学院", "系", "方向")


def profile_for_query(query, limit=3):
    """挑出这轮要注入的档案：核心 1 条 + 与问题相关的补充，最多 limit 条。

    ⚠️ 为什么不是全塞：memory.py 里记着一条实测结论 —— 背景越长，知识库检索越容易
       被稀释（5 条完整背景能把"校训"这种临界条目顶出阈值）。所以：
       · 核心档案（专业/年级）总是带，但只带 1 条
       · 其余靠"和问题有共同两字词"来挑，没有共同话题就不带
       · private（学号/手机/房间号）一条都不带
    """
    prof = [f for f in _memory["facts"] if f.get("kind") == "profile"]
    if not prof:
        return []
    core, rest = [], []
    for f in prof:
        (core if any(h in f["text"] for h in _CORE_HINTS) else rest).append(f)
    picked = core[:1]
    qb = _bigrams(query)
    scored = []
    for f in rest:
        overlap = len(qb & _bigrams(f["text"]))
        if overlap:
            scored.append((overlap, f))
    scored.sort(key=lambda x: -x[0])
    for _n, f in scored:
        if len(picked) >= limit:
            break
        picked.append(f)
    return picked[:limit]


def facts_for_display(mask=True):
    """给网页看的档案列表。学号手机号房间号一律打码（后四位保留）。"""
    def mask(t):
        if not mask:
            return t
        digits = re.sub(r"\D", "", t)
        if len(digits) >= 6 and digits in t:
            return t.replace(digits, digits[:3] + "****" + digits[-2:])
        return t
    return [{
        "text": f["text"],
        "shown": mask(f["text"]),
        "kind": f.get("kind", "profile"),
        "ts": f.get("ts", ""),
    } for f in _memory["facts"]]


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
    """把【用户档案 + 按需的对话历史】压成一行拼在问题前面。

    2026-10-22 起档案是**有选择地**注入（见 profile_for_query），最多 3 条；
    对话历史仍然只在问题说不清时才拼。

    为什么必须压成一行：平台是聊天模板（<user>…</user>），带换行和【】的文本会被它当成模板标记，
    实测模型会开始吐 </assistant><user>，回答也退化成"我在呢，有什么需要帮忙的吗？"这类敷衍话术。
    """
    def flat(s):
        return re.sub(r"\s+", " ", str(s or "")).strip()

    q = flat(query)
    # 判据：问题很短（≤5 字）、或带了指代词 → 它自己说不清，才需要对话历史
    # ⚠️ 阈值不能放宽：实测 "宿舍能用电磁炉吗"（8 字）被注入后，知识库检索就多了一层随机失准的风险
    needs_ctx = len(q) <= 5 or any(w in q for w in _CTX_HINTS)

    parts = []
    picked = profile_for_query(q, 3)
    if picked:
        line = "；".join(flat(f["text"]) for f in picked)
        if len(line) > 90:                     # 太长就截断，宁少不多
            line = line[:90]
        parts.append("(用户档案：" + line + ")")
    if needs_ctx and _history:
        turns = []
        for u, a in _history[-HISTORY_TURNS:]:
            turns.append("用户说「" + flat(u) + "」，你答「" + flat(a) + "」")
        parts.append("(刚才的对话：" + "；".join(turns) + ")")
    parts.append(q)
    return " ".join(p for p in parts if p)


def memory_input():
    """给平台 inputs.memory 用的档案文本（平台那边如果配了这个变量，就用它注入）。

    返回空字符串表示这轮没有可注入的档案 —— 调用方据此决定要不要传这个键。
    """
    picked = [f for f in _memory["facts"] if f.get("kind") == "profile"]
    if not picked:
        return ""
    line = "；".join(str(f["text"]) for f in picked[:5])
    return line[:160]


def remember_turn(query, reply):
    """一轮结束后更新短期记忆和历史事件。"""
    _history.append((query, reply))
    del _history[:-HISTORY_TURNS]     # 只保留最近几轮
    _memory["events"].append({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "user": query, "reply": reply})
    _memory["events"] = _memory["events"][-_EVENT_KEEP:]
    save_memory()


load_memory()

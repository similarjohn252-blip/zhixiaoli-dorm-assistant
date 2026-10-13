# -*- coding: utf-8 -*-
"""记忆：长期（跨重启，存 记忆.json）+ 短期（最近几轮对话，只在内存）。

为什么不用平台的 conversation_id 做记忆：实测平台的多轮 workflow 是坏的（带历史就 400）。
所以记忆完全由我们自己管：把【已知信息 + 最近对话】拼进 query 一起发给平台。
"""
import json
import re
import time

from .config import HISTORY_TURNS, MEMORY_FILE, MEMORY_MAX_FACTS

_memory = {"facts": [], "events": []}   # facts=长期已知信息，events=历史事件
_history = []                           # 短期记忆：[(用户说的, 助手回的)]

# ================= 记忆 1：长期记忆（跨重启，存 记忆.json） =================
# 为什么不用平台的 conversation_id 做记忆：实测平台的多轮 workflow 是坏的（带历史就 400）。
# 所以记忆完全由我们自己管：把【已知信息 + 最近对话】拼进 query 一起发给平台，它照样能懂上下文。
_memory = {"facts": [], "events": []}   # facts=长期已知信息，events=历史事件
_history = []                           # 短期记忆：[(用户说的, 助手回的)]，只在内存里


def load_memory():
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            m = json.load(f)
        _memory["facts"] = [str(x) for x in
                            (m.get("facts") or [])][-MEMORY_MAX_FACTS:]
        _memory["events"] = list(m.get("events") or [])[-200:]
        print(f"[记忆] 载入 {MEMORY_FILE}："
              f"已知信息 {len(_memory['facts'])} 条，"
              f"历史事件 {len(_memory['events'])} 条", flush=True)
    except FileNotFoundError:
        print(f"[记忆] 还没有 {MEMORY_FILE}，从零开始（跟它说“我叫…”就会自动记下来）", flush=True)
    except Exception as e:
        print(f"[记忆] 读取失败（忽略，按空记忆跑）: {e}", flush=True)


def save_memory():
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(_memory, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[记忆] 保存失败: {e}", flush=True)


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
    """从一句话里提取值得长期记住的信息。"""
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
    return out


# ================= 记忆 2：短期记忆（最近几轮对话） =================
def build_query(query):
    """把记忆和最近对话压成【一行】背景，拼在问题前面一起发给平台。

    为什么必须压成一行：平台是聊天模板（<user>…</user>），带换行和【】的文本会被它当成模板标记，
    实测模型会开始吐 </assistant><user>，回答也退化成"我在呢，有什么需要帮忙的吗？"这类敷衍话术。
    """
    def flat(s):
        return re.sub(r"\s+", " ", str(s or "")).strip()

    parts = []
    if _memory["facts"]:
        _facts = "；".join(flat(f) for f in _memory["facts"][-5:])
        parts.append("(背景：" + _facts + ")")
    if _history:
        turns = []
        for u, a in _history[-HISTORY_TURNS:]:
            turns.append("用户说「" + flat(u) + "」，你答「" + flat(a) + "」")
        parts.append("(刚才的对话：" + "；".join(turns) + ")")
    parts.append(flat(query))
    return " ".join(p for p in parts if p)


def remember_turn(query, reply):
    """一轮结束后更新短期记忆和历史事件。"""
    _history.append((query, reply))
    del _history[:-HISTORY_TURNS]     # 只保留最近几轮
    _memory["events"].append({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                              "user": query, "reply": reply})
    _memory["events"] = _memory["events"][-200:]
    save_memory()


load_memory()
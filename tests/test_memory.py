"""记忆的纯逻辑：事实提取、上下文拼装、SQLite 落盘（B3 快测试层 ✓ 不联网 ✓）。

⚠️ 2026-10-22 记忆改版后重写：
   · 只有用户**明确要求**（「记住…」「帮我记住…」「你要记得…」）才写入，
     随口一句「我叫小李」「我住 3 栋 502」不再自动记 ✗
   · 前缀从「(背景：…」改成「(用户档案：…」，且改成**有选择地挑**（最多 3 条）
   · 学号/手机号/房间号归 private，**永不注入**
"""
from server import memory


# ================= 事实提取（正则规则） =================
def test_extract_name_requires_explicit_request():
    """2026-10-22 起：随口说名字不写记忆，要明确说「记住」 ✓"""
    assert memory.extract_facts("我叫小李") == []
    assert memory.extract_facts("记住我叫小李") == ["用户叫小李"]


def test_extract_room_requires_explicit_request():
    """老版本靠「我住 3 栋 502」自动记（为了报修建单）；工单下线后收紧了 ✓"""
    assert memory.extract_facts("我住在3栋502") == []
    assert memory.extract_facts("记住我住 3 栋 502") == ["用户住 3 栋 502"]


def test_extract_question_is_not_a_fact():
    """问句不能当记忆 ✓（否则'我叫什么'会被记成'用户叫什么' ✗）"""
    assert memory.extract_facts("我叫什么") == []
    assert memory.extract_facts("我是哪个学院的") == []
    assert memory.extract_facts("你还记得我叫什么吗") == []


def test_extract_remember_rule_prefixes_user():
    assert memory.extract_facts("记住我喜欢吃辣") == ["用户喜欢吃辣"]


def test_extract_nothing_from_smalltalk():
    assert memory.extract_facts("今天天气不错") == []
    assert memory.extract_facts("今天天气怎么样") == []


# ================= 上下文拼装（按需注入） =================
def _fact(text, kind="profile"):
    return {"id": 0, "ts": "2026-10-22 00:00:00", "text": text, "kind": kind}


def test_build_query_no_profile_no_prefix():
    """没有任何可注入档案 → 原样发问题 ✓"""
    memory._memory["facts"] = []
    memory._history.clear()
    assert memory.build_query("宿舍能用电磁炉吗") == "宿舍能用电磁炉吗"


def test_build_query_injects_core_profile():
    """核心档案（专业、年级）总是带 ✓ —— 这是个性化的前提"""
    memory._memory["facts"] = [_fact("用户是大二计算机专业")]
    memory._history.clear()
    out = memory.build_query("宿舍能用电磁炉吗")
    assert out.startswith("(用户档案：")
    assert out.endswith("宿舍能用电磁炉吗")


def test_build_query_never_injects_private():
    """房间号/学号是 private → 一个字都不能进问题 ✗（那会发给学校平台）"""
    memory._memory["facts"] = [_fact("用户住3栋502", "private"),
                               _fact("用户学号是12500000001", "private")]
    memory._history.clear()
    out = memory.build_query("那要多少钱")
    assert "3栋502" not in out
    assert "12500000001" not in out


def test_build_query_injects_history_when_short():
    """问题说不清（短句）→ 才拼对话历史 ✓"""
    memory._memory["facts"] = []
    memory._history[:] = [("我叫小李", "好的，记住了")]
    out = memory.build_query("那要多少钱")
    assert out.startswith("(刚才的对话：")
    assert out.endswith("那要多少钱")


def test_build_query_is_single_line():
    """必须是**一行** ✗ —— 带换行会被平台当成模板标记，回答会退化成敷衍话术 ✓"""
    memory._memory["facts"] = [_fact("用户是大二计算机专业"), _fact("用户英语基础较弱")]
    memory._history[:] = [("我叫小李", "好的，记住了"), ("那水电费呢", "宿舍水电按度计费")]
    out = memory.build_query("那要多少钱")
    assert "\n" not in out and "\r" not in out


def test_build_query_picks_at_most_three():
    """最多 3 条：背景越长，知识库检索越容易被稀释 ✗"""
    memory._memory["facts"] = [_fact(f"用户课程{i}的偏好") for i in range(6)]
    out = memory.build_query("用户课程偏好")
    # 注意别把问题原文里的"用户课程"也数进来 → 数档案自己的特征词
    assert out.count("的偏好") <= 3


# ================= 落盘（SQLite，工程债 B6） =================
def test_save_then_load_roundtrip():
    """写进去 → 清空内存 → 读回来 ✓（这就是"跨重启记得住"的最小证明 ✓）"""
    memory._memory["facts"] = [_fact("用户叫落盘测试")]
    memory._memory["events"] = [{"time": "2026-10-15 00:00:00", "user": "问", "reply": "答"}]
    memory.save_memory()
    memory._memory["facts"] = []
    memory._memory["events"] = []
    memory.load_memory()
    assert [f["text"] for f in memory._memory["facts"]] == ["用户叫落盘测试"]
    assert memory._memory["facts"][0]["kind"] == "profile"
    assert memory._memory["events"][0]["reply"] == "答"


def test_add_fact_dedup_and_cap(monkeypatch):
    memory._memory["facts"] = []
    assert memory.add_fact("用户叫去重测试") is True
    assert memory.add_fact("用户叫去重测试") is False      # 重复不加 ✓

    # 2026-10-22 起满了是**拒绝**，不是静默淘汰最旧的 ✓
    monkeypatch.setattr(memory, "MEMORY_MAX_FACTS", 3)
    memory._memory["facts"] = []
    for i in range(3):
        memory.add_fact(f"用户叫编号{i}")
    assert memory.add_fact("用户叫编号99") is False
    assert [f["text"] for f in memory._memory["facts"]] == [
        "用户叫编号0", "用户叫编号1", "用户叫编号2"]

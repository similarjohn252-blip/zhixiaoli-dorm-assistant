"""记忆的纯逻辑：事实提取、上下文拼装、SQLite 落盘（B3 快测试层 ✓ 不联网 ✓）。"""
from server import memory


# ================= 事实提取（正则规则） =================
def test_extract_name():
    assert memory.extract_facts("我叫小李") == ["用户叫小李"]


def test_extract_room_plain_speech():
    """实测踩过的回归：'我住 3 栋 502'（不带'记住'）必须记得住 ✓"""
    assert memory.extract_facts("我住在3栋502") == ["用户住3栋502"]
    assert memory.extract_facts("我住 5 号楼 301") == ["用户住5 号楼 301"]


def test_extract_question_is_not_a_fact():
    """问句不能当记忆 ✓（否则'我叫什么'会被记成'用户叫什么' ✗）"""
    assert memory.extract_facts("我叫什么") == []
    assert memory.extract_facts("我是哪个学院的") == []


def test_extract_remember_rule_prefixes_user():
    assert memory.extract_facts("记住我喜欢吃辣") == ["用户喜欢吃辣"]


def test_extract_nothing_from_smalltalk():
    assert memory.extract_facts("今天天气不错") == []


# ================= 上下文拼装（按需注入） =================
def test_build_query_no_prefix_when_self_contained():
    """问题自己能说清 → 不拼前缀 ✓（实测：拼了会让知识库检索随机失准 ✗）"""
    memory._memory["facts"] = ["用户叫小李"]
    memory._history.clear()
    out = memory.build_query("宿舍能用电磁炉吗")
    assert out == "宿舍能用电磁炉吗"


def test_build_query_injects_when_too_short():
    memory._memory["facts"] = ["用户住3栋502"]
    memory._history.clear()
    out = memory.build_query("那要多少钱")
    assert out.startswith("(背景：")
    assert out.endswith("那要多少钱")


def test_build_query_is_single_line():
    """必须是**一行** ✗ —— 带换行会被平台当成模板标记，回答会退化成敷衍话术 ✓"""
    memory._memory["facts"] = ["用户叫小李", "用户住3栋502"]
    memory._history[:] = [("我叫小李", "好的，记住了"), ("那水电费呢", "宿舍水电按度计费")]
    out = memory.build_query("那要多少钱")
    assert "\n" not in out and "\r" not in out


def test_build_query_keeps_only_two_facts():
    """只带最近 2 条：背景越长，知识库检索越容易被稀释 ✗"""
    memory._memory["facts"] = ["用户叫甲", "用户叫乙", "用户叫丙"]
    out = memory.build_query("那我是谁")
    assert "用户叫丙" in out and "用户叫乙" in out
    assert "用户叫甲" not in out


# ================= 落盘（SQLite，工程债 B6） =================
def test_save_then_load_roundtrip():
    """写进去 → 清空内存 → 读回来 ✓（这就是"跨重启记得住"的最小证明 ✓）"""
    memory._memory["facts"] = ["用户叫落盘测试"]
    memory._memory["events"] = [{"time": "2026-10-15 00:00:00", "user": "问", "reply": "答"}]
    memory.save_memory()
    memory._memory["facts"] = []
    memory._memory["events"] = []
    memory.load_memory()
    assert memory._memory["facts"] == ["用户叫落盘测试"]
    assert memory._memory["events"][0]["reply"] == "答"


def test_add_fact_dedup_and_cap():
    memory._memory["facts"] = []
    assert memory.add_fact("用户叫去重测试") is True
    assert memory.add_fact("用户叫去重测试") is False      # 重复不加 ✓
    memory._memory["facts"] = []
    for i in range(30):
        memory.add_fact(f"用户叫编号{i}")
    assert len(memory._memory["facts"]) <= 20              # 上限 ✓

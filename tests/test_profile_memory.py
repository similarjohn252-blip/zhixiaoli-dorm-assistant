"""用户档案（长期记忆）的测试（2026-10-22）

覆盖三块：
  ① 写入入口收紧：只有「记住」类显式要求才写，普通句子和提问都不写
  ② 分级：学号/手机/房间号归 private，只存不注入
  ③ 注入挑选：核心 1 条 + 相关补充，最多 3 条，private 永不出现
"""
import pytest

from server import memory as M


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """每个测试一个空库，别碰真实记忆。"""
    monkeypatch.setattr(M, "MEMORY_DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(M, "MEMORY_FILE", str(tmp_path / "t.json"))
    monkeypatch.setattr(M, "_memory", {"facts": [], "events": []})
    monkeypatch.setattr(M, "_history", [])
    yield


# ---------------- ① 写入入口 ----------------

@pytest.mark.parametrize("q", [
    "我住 3 栋 502",          # 以前会自动记，现在不记
    "我叫小李",
    "我喜欢吃辣",
    "我学号是12500000001",
    "今天天气怎么样",
])
def test_plain_sentence_does_not_write(q):
    assert M.extract_facts(q) == []


@pytest.mark.parametrize("q,want", [
    ("记住我英语基础不好", "用户英语基础不好"),
    ("帮我记住我是大二计算机专业", "用户是大二计算机专业"),
    ("你要记得我目标是绩点 4.5", "用户目标是绩点 4.5"),
    ("记一下我周三下午没课", "用户周三下午没课"),
    ("别忘了下周二交大作业", "下周二交大作业"),
])
def test_explicit_request_writes(q, want):
    assert M.extract_facts(q) == [want]


@pytest.mark.parametrize("q", [
    "我记得我住哪来着",
    "你还记得我叫什么吗",
    "记住了吗",
    "记得我说过什么吗",
])
def test_question_form_does_not_write(q):
    assert M.extract_facts(q) == []


def test_no_duplicate_from_same_sentence():
    # 老 bug：一句话同时命中两条规则会吐重复
    got = M.extract_facts("记住我喜欢吃辣")
    assert len(got) == len(set(got))


# ---------------- ② 分级 ----------------

@pytest.mark.parametrize("text,kind", [
    ("用户学号是12500000001", "private"),
    ("用户手机号是13800138000", "private"),
    ("用户住3栋502", "private"),
    ("用户是大二计算机专业", "profile"),
    ("用户英语基础较弱", "profile"),
    ("用户目标绩点4.5", "profile"),
])
def test_detect_kind(text, kind):
    assert M.detect_kind(text) == kind


def test_add_fact_autokind():
    M.add_fact("用户学号是12500000001")
    M.add_fact("用户英语基础较弱")
    kinds = {f["text"]: f["kind"] for f in M._memory["facts"]}
    assert kinds["用户学号是12500000001"] == "private"
    assert kinds["用户英语基础较弱"] == "profile"


# ---------------- ③ 注入挑选 ----------------

def test_profile_for_query_core_plus_related():
    M.add_fact("用户是大二计算机专业")
    M.add_fact("用户英语基础较弱")
    M.add_fact("用户目标绩点 4.5")

    got = [f["text"] for f in M.profile_for_query("英语怎么过四级", 3)]
    assert "用户是大二计算机专业" in got        # 核心总在
    assert "用户英语基础较弱" in got            # 相关带进来

    # 跟英语无关的问题，不该带英语
    got2 = [f["text"] for f in M.profile_for_query("今天几号", 3)]
    assert "用户英语基础较弱" not in got2


def test_profile_for_query_never_includes_private():
    M.add_fact("用户是大二计算机专业")
    M.add_fact("用户学号是12500000001")
    for q in ("学号是多少", "高数怎么复习", "我是谁"):
        picked = [f["text"] for f in M.profile_for_query(q, 3)]
        assert not any("学号" in t for t in picked)


def test_profile_for_query_limit():
    for i in range(6):
        M.add_fact(f"用户课程{i}相关偏好")
    assert len(M.profile_for_query("课程", 3)) <= 3


def test_profile_for_query_empty():
    assert M.profile_for_query("高数怎么复习", 3) == []


# ---------------- ④ 拼进问题的形态 ----------------

def test_build_query_injects_profile():
    M.add_fact("用户是大二计算机专业")
    q = M.build_query("高数怎么复习")
    assert q.startswith("(用户档案：")
    assert "大二计算机专业" in q
    assert q.endswith("高数怎么复习")


def test_build_query_without_profile_is_bare():
    assert M.build_query("高数怎么复习") == "高数怎么复习"


def test_build_query_never_leaks_private():
    M.add_fact("用户学号是12500000001")
    q = M.build_query("高数怎么复习")
    assert "12500000001" not in q


# ---------------- ⑤ 增删改清 ----------------

def test_update_and_delete():
    M.add_fact("用户英语基础较弱")
    assert M.update_fact("用户英语基础较弱", "用户英语基础一般") is True
    assert M._memory["facts"][0]["text"] == "用户英语基础一般"
    assert M.delete_fact("用户英语基础一般") is True
    assert M._memory["facts"] == []


def test_dedupe():
    assert M.add_fact("用户英语基础较弱") is True
    assert M.add_fact("用户英语基础较弱") is False
    assert len(M._memory["facts"]) == 1


def test_capacity_refuses_instead_of_dropping(monkeypatch):
    monkeypatch.setattr(M, "MEMORY_MAX_FACTS", 2)
    M.add_fact("第一条内容")
    M.add_fact("第二条内容")
    assert M.add_fact("第三条内容") is False
    # 关键：老的没被静默淘汰
    assert [f["text"] for f in M._memory["facts"]] == ["第一条内容", "第二条内容"]


def test_clear_facts_keeps_events():
    M.add_fact("用户英语基础较弱")
    M._memory["events"].append({"time": "", "user": "a", "reply": "b"})
    assert M.clear_facts() == 1
    assert M._memory["facts"] == []
    assert len(M._memory["events"]) == 1


# ---------------- ⑥ 展示打码 ----------------

def test_display_masks_private():
    M.add_fact("用户学号是12500000001")
    shown = M.facts_for_display()[0]["shown"]
    assert "12500000001" not in shown
    assert "125" in shown


def test_facts_text_skips_private():
    M.add_fact("用户是大二计算机专业")
    M.add_fact("用户学号是12500000001")
    assert "学号" not in M.facts_text()
    assert "计算机" in M.facts_text()

"""定闹钟解析的测试（2026-10-22）"""
from datetime import datetime

import pytest

from server import alarm, brain, reminders

NOW = datetime(2026, 10, 22, 14, 0, 0)


# ---------- 中文数字 ----------

@pytest.mark.parametrize("raw,want", [
    ("10", 10), ("十", 10), ("二十", 20), ("八", 8), ("两", 2),
    ("二十五", 25), ("半", None), ("", None), ("abc", None),
])
def test_cn_num(raw, want):
    assert alarm.cn_num(raw) == want


# ---------- 相对时间 ----------

def test_seconds():
    at, say = alarm.parse("十秒后提醒我喝水", NOW)
    assert at == datetime(2026, 10, 22, 14, 0, 10)
    assert say == "喝水"


def test_minutes():
    at, say = alarm.parse("二十分钟后提醒我拿快递", NOW)
    assert at == datetime(2026, 10, 22, 14, 20, 0)
    assert say == "拿快递"


def test_half_hour():
    at, _ = alarm.parse("半小时后叫我", NOW)
    assert at == datetime(2026, 10, 22, 14, 30, 0)


def test_one_and_half_hours():
    # 「一个半小时」不能被「半小时」抢走
    at, say = alarm.parse("一个半小时后提醒我收衣服", NOW)
    assert at == datetime(2026, 10, 22, 15, 30, 0)
    assert say == "收衣服"


def test_two_and_half_hours():
    at, _ = alarm.parse("两个半小时后提醒我烧水", NOW)
    assert at == datetime(2026, 10, 22, 16, 30, 0)


def test_quarter():
    at, _ = alarm.parse("一刻钟后提醒我关火", NOW)
    assert at == datetime(2026, 10, 22, 14, 15, 0)


# ---------- 绝对时刻 ----------

def test_clock_rolls_to_tomorrow():
    # 现在 14:00，说"八点"应该当成明天早上
    at, say = alarm.parse("八点提醒我开会", NOW)
    assert at == datetime(2026, 10, 23, 8, 0, 0)
    assert say == "开会"


def test_clock_half():
    at, _ = alarm.parse("八点半提醒我吃药", NOW)
    assert at == datetime(2026, 10, 23, 8, 30, 0)


def test_clock_later_today():
    at, _ = alarm.parse("二十点二十提醒我关火", NOW)
    assert at == datetime(2026, 10, 22, 20, 20, 0)


def test_tomorrow():
    at, _ = alarm.parse("明天八点提醒我交材料", NOW)
    assert at == datetime(2026, 10, 23, 8, 0, 0)


def test_no_time_returns_none():
    assert alarm.parse("定个闹钟", NOW) is None
    assert alarm.parse("提醒我喝水", NOW) is None
    assert alarm.parse("今天天气怎么样", NOW) is None


# ---------- 提醒内容 ----------

@pytest.mark.parametrize("q,want", [
    ("十秒后提醒我喝水", "喝水"),
    ("八点提醒我开会", "开会"),
    ("半小时后叫我", ""),
    ("帮我定个闹钟，八点，开会", "开会"),
    ("提醒我一下", ""),
])
def test_extract_say(q, want):
    assert alarm.extract_say(q) == want


def test_extract_say_strips_particles():
    assert alarm.extract_say("八点提醒我开会吧") == "开会"


# ---------- 触发判断 ----------

@pytest.mark.parametrize("q,want", [
    ("十秒后提醒我喝水", True),
    ("帮我定个闹钟", True),
    ("八点叫我", True),
    ("提醒我一下请假流程", False),      # ⭐ 有时间词才算
    ("提醒我喝水", False),
    ("今天天气怎么样", False),
    ("校训是什么", False),
    ("明天考试提醒我", True),           # "明天"里没有时间词，但"提醒我"+"点"？没有 → 看下一行
])
def test_looks_like_alarm(q, want):
    got = alarm.looks_like_alarm(q)
    if q == "明天考试提醒我":
        # 这句没有时间词，当前实现判 False —— 记录实际行为，避免以后悄悄改
        assert got is False
    else:
        assert got is want


# ---------- 回答文本 ----------

def test_answer_confirms():
    text = alarm.answer("十秒后提醒我喝水", NOW)
    assert "10 秒后" in text
    assert "喝水" in text


def test_answer_asks_when_no_time():
    text = alarm.answer("定个闹钟", NOW)
    assert "几点" in text


# ---------- 接进 brain ----------

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(reminders, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(brain, "make_tts", lambda text: "")
    return tmp_path


def test_brain_sets_alarm(isolated):
    text = brain.handle_query("十秒后提醒我喝水")["voice_text"]
    assert "喝水" in text
    got = reminders.upcoming(1)
    assert len(got["items"]) == 1
    assert got["items"][0]["kind"] == "alarm"
    assert got["items"][0]["say"] == "喝水"


def test_brain_does_not_hijack_normal_question(isolated):
    # 「提醒我一下请假流程」不该被当成定闹钟
    text = brain.handle_query("提醒我一下请假流程")["voice_text"]
    assert "几点" not in text


# ---------- 2026-10-29 板子上实测出来的两组问题 ----------

@pytest.mark.parametrize("raw,secs", [
    ("定一个15秒的闹钟。", 15),      # ← 板子上识别出来的原话，当时没解析出来，反问"几点"
    ("定一个15秒的闹钟", 15),
    ("十五秒的闹钟", 15),            # 中文数字
    ("15秒闹钟", 15),                # 没有"的"
    ("定个20分钟的闹钟", 20 * 60),   # 分钟同理
    ("半小时的闹钟", 30 * 60),
    ("一个半小时的闹钟", 90 * 60),
])
def test_relative_without_hou(raw, secs):
    """「X 秒的闹钟」不带"后"也要认 —— 原来只认「X 秒后」，实测漏了。"""
    got = alarm.parse(raw, now=NOW)
    assert got is not None, f"没解析出来：{raw}"
    at, _say = got
    assert (at - NOW).total_seconds() == secs


@pytest.mark.parametrize("raw,want", [
    ("八点二十分提醒我开会", (8, 20)),   # 回归：曾被当成"20 分钟后"
    ("20点20分提醒我", (20, 20)),        # 回归：曾被当成"当前时刻"
    ("八点提醒我开会", (8, 0)),
])
def test_clock_time_is_not_mistaken_for_relative(raw, want):
    """带"点"的是绝对时刻，不能被相对时间抢走。

    ⚠️ 这条是 10-29 放宽松「后」之后立刻暴露的回归：
       正则能从数字中间开始匹配（"八点二十分"里的"十"、"20点20分"里的第二个"0"）。
    """
    got = alarm.parse(raw, now=NOW)
    assert got is not None
    at, _say = got
    assert (at.hour, at.minute) == want


@pytest.mark.parametrize("raw", [
    "半个小时后提醒我",      # 抽出"我"
    "20点20分提醒我",        # 抽出"我"
    "提醒我二十三分钟",      # 抽出"二十三分钟"
])
def test_say_falls_back_when_nothing_meaningful(raw):
    """提醒内容只剩代词或只剩时间 → 退回默认的「时间到了」，别念「提醒你：我」。"""
    got = alarm.parse(raw, now=NOW)
    assert got is not None
    _at, say = got
    assert say == "时间到了"


def test_say_still_keeps_real_content():
    """别为了过滤把真内容也弄丢。"""
    at, say = alarm.parse("十秒后提醒我喝水", now=NOW)
    assert say == "喝水"
    at, say = alarm.parse("二十分钟后提醒我拿快递", now=NOW)
    assert say == "拿快递"

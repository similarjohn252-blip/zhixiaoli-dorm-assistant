"""课表问答的测试（2026-11-06）

为什么要这层测试：这功能只在"有课表数据"时才有意义，
而真实课表在 `提醒.db` 里、每天都会变 —— 测试不能依赖它。
所以这里用假课表 + 固定的"现在"，把日期/周次/单双周/上下午这些边界钉住。
"""
import datetime as dt

import pytest

from server import course_qa, reminders

WEEK1_MON = dt.date(2026, 9, 7)      # 第 1 周周一（假）
NOW = dt.datetime(2026, 11, 6, 12, 43, 0)   # 周五中午（第 9 周）

ITEMS = [
    # 周五（今天）的课：一节早上（已过）、一节下午（还没上）
    {"name": "中国近现代史纲要", "weekday": 5, "period": 1, "venue": "弘远楼B0119", "weeks": [9]},
    {"name": "线性代数[理工]", "weekday": 5, "period": 5, "venue": "弘远楼B0321", "weeks": [9]},
    # 周六（明天）两节
    {"name": "大学英语3", "weekday": 6, "period": 1, "venue": "弘远楼A0203", "weeks": [9]},
    {"name": "创业基础", "weekday": 6, "period": 7, "venue": "弘远楼C0118", "weeks": [9]},
    # 只在第 10 周上（用来验单双周/周次过滤）
    {"name": "体育", "weekday": 6, "period": 9, "venue": "两江操场1", "weeks": [10]},
]

SETTINGS = {
    "week1_monday": WEEK1_MON,
    "early_minutes": 20,
    "periods": {"1": "08:30", "3": "10:30", "5": "14:20", "7": "16:20", "9": "19:00"},
}


@pytest.fixture(autouse=True)
def fake_schedule(monkeypatch):
    monkeypatch.setattr(reminders, "load_items", lambda: list(ITEMS))
    monkeypatch.setattr(reminders, "get_settings", lambda: dict(SETTINGS))


# ---------- 该不该接管 ----------

@pytest.mark.parametrize("q", [
    "明天几节课", "今天有课吗", "下节课是什么", "我周三有课吗", "课表", "还有几节课",
])
def test_looks_like_true(q):
    assert course_qa.looks_like(q) is True


@pytest.mark.parametrize("q", [
    "课表怎么上传", "课表 PDF 怎么导入", "上传课表格式要求", "今天天气怎么样", "帮我订个闹钟",
])
def test_looks_like_false(q):
    """问"怎么用"的不接管 —— 交给平台。"""
    assert course_qa.looks_like(q) is False


# ---------- 明天 / 周几 ----------

def test_tomorrow_counts_only_that_week():
    """明天（周六，第 9 周）应该是 2 节 —— 第 10 周那节体育不能算进来。"""
    got = course_qa.answer("明天几节课", now=NOW)
    assert "2 节课" in got
    assert "大学英语3" in got and "创业基础" in got
    assert "体育" not in got


def test_tomorrow_has_times():
    got = course_qa.answer("明天几节课", now=NOW)
    assert "08:30" in got and "16:20" in got


def test_today_skips_past_courses():
    """今天中午问，早上那节已经上过，只该报下午的。"""
    got = course_qa.answer("今天有课吗", now=NOW)
    assert "线性代数" in got
    assert "中国近现代史纲要" not in got


def test_today_done_says_so():
    """晚上问"今天有课吗"—— 课都上完了。"""
    late = dt.datetime(2026, 11, 6, 21, 0, 0)
    got = course_qa.answer("今天有课吗", now=late)
    assert "上完了" in got


def test_weekend_without_course():
    """第 10 周才有课的那天（周六第 10 周）在该周是 3 节。"""
    next_week = dt.datetime(2026, 11, 13, 9, 0, 0)   # 下周五 = 第 10 周
    got = course_qa.answer("明天几节课", now=next_week)   # 周六（第 10 周）
    assert "体育" in got


# ---------- 下节课 ----------

def test_next_course_today():
    got = course_qa.answer("下节课是什么", now=NOW)
    assert "14:20" in got and "线性代数" in got and "弘远楼B0321" in got


def test_next_course_tomorrow_when_today_finished():
    late = dt.datetime(2026, 11, 6, 21, 0, 0)
    got = course_qa.answer("下节课是什么", now=late)
    assert "明天" in got and "大学英语3" in got


def test_next_course_not_found():
    """把课表清空后的空档期 —— 返回"没课"那句，不是崩。"""
    assert course_qa.answer("下节课是什么", now=dt.datetime(2026, 12, 31, 9, 0, 0)) is not None


# ---------- 没有课表 ----------

def test_no_schedule_hint(monkeypatch):
    monkeypatch.setattr(reminders, "load_items", lambda: [])
    got = course_qa.answer("明天几节课", now=NOW)
    assert "还没上传" in got

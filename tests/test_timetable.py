"""课表解析与提醒生成的测试（2026-10-22）

只测纯函数（不依赖 pdfplumber）；带真 PDF 的那条会用 pytest.mark.skipif 跳过。
"""
from datetime import date, datetime

import pytest

from server import timetable


# ---------- parse_weeks ----------

def test_parse_weeks_ranges():
    assert sorted(timetable.parse_weeks("2-8周,10-12周,14-18周")) == [
        2, 3, 4, 5, 6, 7, 8, 10, 11, 12, 14, 15, 16, 17, 18]


def test_parse_weeks_odd():
    assert sorted(timetable.parse_weeks("3-5周(单)")) == [3, 5]


def test_parse_weeks_even():
    assert sorted(timetable.parse_weeks("10-16周(双)")) == [10, 12, 14, 16]


def test_parse_weeks_fullwidth_parens():
    # 教务处偶尔用全角括号
    assert sorted(timetable.parse_weeks("3-7周（单）")) == [3, 5, 7]


def test_parse_weeks_single_week():
    assert timetable.parse_weeks("6周") == {6}


def test_parse_weeks_empty_and_garbage():
    assert timetable.parse_weeks("") == set()
    assert timetable.parse_weeks(None) == set()
    assert timetable.parse_weeks("没有周次") == set()


# ---------- parse_cell ----------

CELL = "线性代数[理工]★ (1-2节)4-8周,10-12周/校区:两江校区/场地:弘远楼B0321/教师:张喜花"


def test_parse_cell_basic():
    name, period, weeks, venue = timetable.parse_cell(CELL)
    assert name == "线性代数[理工]"
    assert period == 1
    assert sorted(weeks) == [4, 5, 6, 7, 8, 10, 11, 12]
    assert venue == "弘远楼B0321"


def test_parse_cell_strips_marks():
    name, _, _, _ = timetable.parse_cell("大学英语3☆ (3-4节)2-9周")
    assert name == "大学英语3"


def test_parse_cell_no_period_returns_none():
    assert timetable.parse_cell("实践课程：工程训练") is None
    assert timetable.parse_cell("") is None
    assert timetable.parse_cell(None) is None


def test_parse_cell_without_venue():
    name, period, weeks, venue = timetable.parse_cell("形势与政策3 (9-10节)11-12周")
    assert name == "形势与政策3"
    assert period == 9
    assert venue == ""


# ---------- period_start ----------

def test_period_start_defaults():
    assert timetable.period_start(1) == (8, 30)
    assert timetable.period_start(3) == (10, 30)
    assert timetable.period_start(5) == (14, 20)
    assert timetable.period_start(7) == (16, 20)
    assert timetable.period_start(9) == (19, 0)


def test_period_start_custom():
    periods = {"1": "08:15", "9": "18:45"}
    assert timetable.period_start(1, periods) == (8, 15)
    assert timetable.period_start(9, periods) == (18, 45)
    # 没给的就回默认
    assert timetable.period_start(5, periods) == (14, 20)


# ---------- weekday_date ----------

def test_weekday_date():
    w1 = date(2026, 8, 31)  # 第 1 周周一
    assert timetable.weekday_date(w1, 1, 1) == date(2026, 8, 31)
    assert timetable.weekday_date(w1, 3, 1) == date(2026, 9, 2)
    # 第 8 周周三 = 10/21（和手机截图一致）
    assert timetable.weekday_date(w1, 3, 8) == date(2026, 10, 21)
    assert timetable.weekday_date(w1, 7, 8) == date(2026, 10, 25)


# ---------- make_say ----------

def test_make_say_with_venue():
    begin = datetime(2026, 10, 21, 8, 30)
    say = timetable.make_say("线性代数", "弘远楼B0321", begin, 20)
    assert say == "20 分钟后你有线性代数，在弘远楼B0321，08:30 开始"


def test_make_say_without_venue():
    begin = datetime(2026, 10, 21, 14, 20)
    say = timetable.make_say("形势与政策3", "", begin, 20)
    assert say == "20 分钟后你有形势与政策3，14:20 开始"


# ---------- expand ----------

def test_expand_picks_only_window():
    items = [{
        "weekday": 3, "period": 1, "name": "线性代数",
        "venue": "弘远楼B0321", "weeks": [8],
    }]
    w1 = date(2026, 8, 31)
    got = timetable.expand(items, w1, 20,
                          since=datetime(2026, 10, 21, 0, 0),
                          until=datetime(2026, 10, 21, 23, 59))
    assert len(got) == 1
    assert got[0]["at"] == datetime(2026, 10, 21, 8, 10)
    assert got[0]["begin"] == datetime(2026, 10, 21, 8, 30)
    assert got[0]["kind"] == "class"


def test_expand_excludes_outside_window():
    items = [{
        "weekday": 3, "period": 1, "name": "线性代数",
        "venue": "", "weeks": [8],
    }]
    w1 = date(2026, 8, 31)
    got = timetable.expand(items, w1, 20,
                          since=datetime(2026, 10, 22, 0, 0),
                          until=datetime(2026, 10, 22, 23, 59))
    assert got == []


def test_expand_sorted_and_limited():
    items = [
        {"weekday": 3, "period": 9, "name": "晚上的课", "venue": "", "weeks": [8]},
        {"weekday": 3, "period": 1, "name": "早上的课", "venue": "", "weeks": [8]},
    ]
    w1 = date(2026, 8, 31)
    got = timetable.expand(items, w1, 20,
                          since=datetime(2026, 10, 21, 0, 0),
                          until=datetime(2026, 10, 21, 23, 59))
    assert [x["name"] if "name" in x else x["say"] for x in got] != []
    # 时间升序
    assert got[0]["at"] < got[1]["at"]
    assert "早上的课" in got[0]["say"]


def test_expand_early_minutes_zero():
    items = [{"weekday": 3, "period": 1, "name": "线性代数", "venue": "", "weeks": [8]}]
    w1 = date(2026, 8, 31)
    got = timetable.expand(items, w1, 0,
                          since=datetime(2026, 10, 21, 0, 0),
                          until=datetime(2026, 10, 21, 23, 59))
    assert got[0]["at"] == datetime(2026, 10, 21, 8, 30)


# ---------- week_of / today_weekday ----------

def test_week_of():
    w1 = date(2026, 8, 31)
    assert timetable.week_of(w1, datetime(2026, 8, 31, 10, 0)) == 1
    assert timetable.week_of(w1, datetime(2026, 10, 21, 10, 0)) == 8


def test_today_weekday():
    assert timetable.today_weekday(date(2026, 10, 21)) == 3  # 周三
    assert timetable.today_weekday(date(2026, 10, 25)) == 7  # 周日


# ---------- 真 PDF（文件不在就跳过） ----------

import os  # noqa: E402

REAL_PDF = r"C:\Users\ASUS\Downloads\王鹏超(2026-2027-1)课表 (1).pdf"


@pytest.mark.skipif(not os.path.exists(REAL_PDF), reason="真课表 PDF 不在本机")
def test_parse_real_pdf():
    items, _warnings = timetable.parse_pdf(REAL_PDF)
    assert len(items) >= 15
    names = {it["name"] for it in items}
    assert "线性代数[理工]" in names
    assert "大学英语3" in names
    # 10/21 周三四场
    got = timetable.expand(items, date(2026, 8, 31), 20,
                           since=datetime(2026, 10, 21, 0, 0),
                           until=datetime(2026, 10, 21, 23, 59))
    assert len(got) == 4
    assert "线性代数" in got[0]["say"]
    assert got[0]["at"] == datetime(2026, 10, 21, 8, 10)

"""课表解析与提醒生成（2026-10-22）

设计见 code\\课表与提醒_设计.md。

一句话：把教务处导出的课表 PDF 变成"几点几分、在哪个教室、播报什么话"。

⭐ 为什么叫 timetable 不叫 schedule：schedule 是常见库名，会撞。

⭐ 已知缺陷（靠网页校对兜住，不指望这里 100% 准）：
   合并单元格会让列错位。实测那张真实课表漏了周五一门「数字电子技术」，
   周六周日多出两门不存在的课。
"""
import re
from datetime import date, datetime, timedelta

WEEKDAY_NAMES = "一二三四五六日"

# 起始节次 → 钟点（每节 45 分钟，节间 10 分钟）
PERIOD_START = {
    1: (8, 30),
    3: (10, 30),
    5: (14, 20),
    7: (16, 20),
    9: (19, 0),
}

PERIOD_MINUTES = 45
DEFAULT_EARLY_MINUTES = 20

_WEEK_CHUNK = re.compile(r"(\d+)\s*-\s*(\d+)\s*周(?:\s*[（(]\s*([单双])\s*[）)])?")
_WEEK_SINGLE = re.compile(r"(\d+)\s*周(?!\s*-)")
_PERIOD_RANGE = re.compile(r"[（(]\s*(\d+)\s*-\s*(\d+)\s*节\s*[）)]")
_VENUE = re.compile(r"场地\s*[:：]\s*([^/／]+)")


def parse_weeks(text):
    """'2-8周,10-12周,14-18周' → {2..8, 10..12, 14..18}；带(单)/(双) 的按奇偶过滤。"""
    weeks = set()
    s = str(text or "")
    for m in _WEEK_CHUNK.finditer(s):
        a, b = int(m.group(1)), int(m.group(2))
        if b < a:
            a, b = b, a
        parity = m.group(3)
        for w in range(a, b + 1):
            if parity == "单" and w % 2 == 0:
                continue
            if parity == "双" and w % 2 == 1:
                continue
            weeks.add(w)
    for m in _WEEK_SINGLE.finditer(s):
        weeks.add(int(m.group(1)))
    return weeks


def clean_name(text):
    """去掉 ★☆◆■〇 这类标记和多余空白。"""
    s = str(text or "")
    for ch in "★☆◆■〇":
        s = s.replace(ch, "")
    return re.sub(r"\s+", "", s).strip()


def parse_cell(text):
    """一格里的文字 → (课名, 起始节次, 周次集合, 场地)；认不出返回 None。

    典型输入：
        中国近现代史纲要★ (1-2节)2-8周,10-12周,14-18周/校区:两江校区/场地:弘远楼B0119
    """
    if not text:
        return None
    t = str(text).replace("\n", "")
    m = _PERIOD_RANGE.search(t)
    if not m:
        return None
    period = int(m.group(1))
    name = clean_name(t[:m.start()])
    if not name:
        return None
    rest = t[m.end():]
    venue_m = _VENUE.search(rest)
    venue = venue_m.group(1).strip() if venue_m else ""
    weeks = parse_weeks(rest)
    return name, period, weeks, venue


def parse_pdf(path):
    """整份 PDF → [{'weekday':1-7, 'period':起始节, 'name':…, 'venue':…, 'weeks':[...]}]

    pdfplumber 是可选依赖：只做纯计算（parse_weeks/parse_cell/expand）时不需要它。
    """
    try:
        import pdfplumber
    except ImportError:
        return [], ["服务端没装 pdfplumber，暂时解析不了 PDF"]

    items = []
    warnings = []
    with pdfplumber.open(path) as pdf:
        for page_no, page in enumerate(pdf.pages, 1):
            for table in page.extract_tables():
                for row in table:
                    if not row or len(row) < 9:
                        continue
                    period = None
                    if row[1]:
                        pm = re.search(r"\d+", str(row[1]))
                        if pm:
                            period = int(pm.group(0))
                    if period is None:
                        continue
                    for col in range(2, 9):
                        cell = row[col] if col < len(row) else None
                        got = parse_cell(cell)
                        if not got:
                            continue
                        name, p0, weeks, venue = got
                        if not weeks:
                            warnings.append(f"第 {page_no} 页「{name}」没解析出周次，请手工补")
                            continue
                        items.append({
                            "weekday": col - 1,
                            "period": p0,
                            "name": name,
                            "venue": venue,
                            "weeks": sorted(weeks),
                        })
    if not items:
        warnings.append("这份 PDF 里没解析出任何课程，可能不是教务处的课表格式")
    return items, warnings


def period_start(period, periods=None):
    """起始节次 → (时, 分)。periods 可传入自定义作息表，覆盖默认值。"""
    if periods and str(period) in periods:
        raw = str(periods[str(period)])
        m = re.match(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", raw)
        if m:
            return int(m.group(1)), int(m.group(2))
    base = period if period % 2 == 1 else period - 1
    return PERIOD_START.get(base, (8, 30))


def weekday_date(week1_monday, weekday, week_no):
    """第 week_no 周、星期 weekday(1-7) 是哪一天。week1_monday 是第 1 周的周一。"""
    return week1_monday + timedelta(days=(week_no - 1) * 7 + (weekday - 1))


def make_say(name, venue, begin, early_minutes):
    """播报稿。例：20 分钟后你有线性代数[理工]，在弘远楼B0321，08:30 开始"""
    where = f"在{venue}，" if venue else ""
    return (f"{early_minutes} 分钟后你有{name}，{where}"
            f"{begin.strftime('%H:%M')} 开始")


def expand(items, week1_monday, early_minutes=DEFAULT_EARLY_MINUTES,
           since=None, until=None, periods=None, limit=60):
    """课表条目 → 具体提醒。

    返回 [{'at': 提醒时刻, 'begin': 上课时刻, 'say': 播报稿, 'kind': 'class'}]
    只保留 since <= at <= until 的，按时间排序。
    """
    if since is None:
        since = datetime.now()
    if until is None:
        until = since + timedelta(days=2)

    out = []
    for it in items:
        start = period_start(it.get("period", 1), periods)
        for w in it.get("weeks", []):
            d = weekday_date(week1_monday, it["weekday"], w)
            begin = datetime(d.year, d.month, d.day, start[0], start[1])
            at = begin - timedelta(minutes=early_minutes)
            if not (since <= at <= until):
                continue
            out.append({
                "at": at,
                "begin": begin,
                "say": make_say(it.get("name", ""), it.get("venue", ""), begin, early_minutes),
                "kind": "class",
            })
    out.sort(key=lambda x: x["at"])
    return out[:limit]


def week_of(monday, when):
    """when 属于第几周（第 1 周从 monday 算起）。"""
    return (when.date() - monday).days // 7 + 1


def today_weekday(when=None):
    """1=周一 … 7=周日"""
    return (when or date.today()).isoweekday()

"""课表与提醒的存储、以及"未来有哪些提醒"的计算（2026-10-22）

设计见 code\\课表与提醒_设计.md。

⭐ 两件事放这里：
   ① 课表（解析结果 + 学期参数）存哪儿、怎么读
   ② 把课表展开成"未来 N 小时的提醒"，加上语音定的闹钟，一起按时间排好

⭐ 为什么整份课表存成一个 JSON 而不是建一张课程表：
   一学期也就二十来个时段，读的时候反正要整体读出来算提醒，
   拆成表反而要写一堆 SQL。存 JSON 简单、够用、改结构不迁移。

⭐ 闹钟一次性：算未来提醒时把已经过去的丢掉，顺手删掉。
"""
import json
import os
import sqlite3
import threading
from datetime import date, datetime, timedelta

from . import timetable

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "提醒.db")

DEFAULT_SETTINGS = {
    "week1_monday": "2026-08-31",
    "early_minutes": timetable.DEFAULT_EARLY_MINUTES,
    "periods": {str(k): f"{v[0]:02d}:{v[1]:02d}" for k, v in timetable.PERIOD_START.items()},
}

_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(DB_PATH, timeout=5)
    c.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    return c


def _get(key, default=None):
    with _lock:
        c = _conn()
        try:
            row = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default
        finally:
            c.close()


def _set(key, value):
    with _lock:
        c = _conn()
        try:
            c.execute("INSERT INTO kv(k,v) VALUES(?,?) "
                      "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                      (key, json.dumps(value, ensure_ascii=False)))
            c.commit()
        finally:
            c.close()


# ---------- 学期参数 ----------

def get_settings():
    """返回 dict：week1_monday(date) / early_minutes(int) / periods(dict)"""
    raw = _get("settings", {}) or {}
    merged = dict(DEFAULT_SETTINGS)
    merged.update(raw)
    try:
        w1 = datetime.strptime(str(merged["week1_monday"])[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        w1 = datetime.strptime(DEFAULT_SETTINGS["week1_monday"], "%Y-%m-%d").date()
    try:
        early = int(merged.get("early_minutes", timetable.DEFAULT_EARLY_MINUTES))
    except (ValueError, TypeError):
        early = timetable.DEFAULT_EARLY_MINUTES
    if early < 0 or early > 120:
        early = timetable.DEFAULT_EARLY_MINUTES
    periods = merged.get("periods") or DEFAULT_SETTINGS["periods"]
    return {"week1_monday": w1, "early_minutes": early, "periods": periods}


def set_settings(week1_monday=None, early_minutes=None, periods=None):
    cur = get_settings()
    today = {
        "week1_monday": cur["week1_monday"].isoformat(),
        "early_minutes": cur["early_minutes"],
        "periods": cur["periods"],
    }
    if week1_monday:
        try:
            datetime.strptime(str(week1_monday)[:10], "%Y-%m-%d")
            today["week1_monday"] = str(week1_monday)[:10]
        except (ValueError, TypeError):
            pass
    if early_minutes is not None:
        try:
            v = int(early_minutes)
            if 0 <= v <= 120:
                today["early_minutes"] = v
        except (ValueError, TypeError):
            pass
    if periods:
        today["periods"] = periods
    _set("settings", today)
    return get_settings()


# ---------- 课表 ----------

def save_items(items):
    """items 来自 timetable.parse_pdf 或网页校对后的结果。返回保存条数。"""
    clean = []
    for it in items or []:
        try:
            clean.append({
                "weekday": int(it.get("weekday", 0)),
                "period": int(it.get("period", 0)),
                "name": str(it.get("name", "") or ""),
                "venue": str(it.get("venue", "") or ""),
                "weeks": sorted(int(w) for w in (it.get("weeks") or [])),
            })
        except (ValueError, TypeError):
            continue
    clean = [c for c in clean if 1 <= c["weekday"] <= 7 and c["period"] in timetable.PERIOD_START
             and c["name"] and c["weeks"]]
    _set("items", clean)
    return len(clean)


def load_items():
    return _get("items", []) or []


def has_schedule():
    return bool(load_items())


# ---------- 闹钟 ----------

def add_alarm(at, say, kind="alarm"):
    """at 可以是 datetime 或 'YYYY-MM-DD HH:MM'。返回新闹钟的 id。"""
    if isinstance(at, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M"):
            try:
                at = datetime.strptime(at, fmt)
                break
            except ValueError:
                continue
    if not isinstance(at, datetime):
        return None
    alarms = _get("alarms", []) or []
    nid = max([a.get("id", 0) for a in alarms], default=0) + 1
    alarms.append({"id": nid, "at": at.isoformat(timespec="seconds"),
                   "say": str(say or ""), "kind": kind})
    _set("alarms", alarms)
    return nid


def _drop_past_alarms(now):
    """一次性闹钟：响过就删。这里按时间判断（不需要板子回报）。"""
    alarms = _get("alarms", []) or []
    keep = []
    for a in alarms:
        try:
            at = datetime.fromisoformat(a.get("at", ""))
        except ValueError:
            continue
        if at > now - timedelta(minutes=2):      # 留 2 分钟余量，给板子反应时间
            keep.append(a)
    if len(keep) != len(alarms):
        _set("alarms", keep)
    return keep


# ---------- 未来提醒 ----------

def upcoming(hours=48, now=None, limit=60):
    """未来 N 小时内的提醒，按时间升序。返回 {"now":…, "items":[…]}"""
    now = now or datetime.now()
    until = now + timedelta(hours=max(1, min(int(hours or 48), 24 * 14)))
    st = get_settings()

    out = []

    for r in timetable.expand(load_items(), st["week1_monday"], st["early_minutes"],
                              since=now, until=until, periods=st["periods"], limit=limit):
        out.append({"id": "c" + r["begin"].strftime("%Y%m%d%H%M"),
                    "at": r["at"], "say": r["say"], "kind": "class"})

    for a in _drop_past_alarms(now):
        try:
            at = datetime.fromisoformat(a.get("at", ""))
        except ValueError:
            continue
        if now <= at <= until:
            out.append({"id": "a" + str(a.get("id")),
                        "at": at, "say": a.get("say", ""), "kind": a.get("kind", "alarm")})

    out.sort(key=lambda x: x["at"])
    return {
        "now": now.isoformat(timespec="seconds"),
        "items": [{
            "id": x["id"],
            "at": x["at"].isoformat(timespec="seconds"),
            "in_seconds": max(0, int((x["at"] - now).total_seconds())),
            "say": x["say"],
            "kind": x["kind"],
        } for x in out[:limit]],
    }


def week_info(now=None):
    """给网页/调试看的：现在是第几周、周几。"""
    now = now or datetime.now()
    st = get_settings()
    return {
        "week": timetable.week_of(st["week1_monday"], now),
        "weekday": timetable.WEEKDAY_NAMES[now.isoweekday() - 1],
        "week1_monday": st["week1_monday"].isoformat(),
        "early_minutes": st["early_minutes"],
        "periods": st["periods"],
        "class_count": len(load_items()),
    }


def clear_all():
    _set("items", [])
    _set("alarms", [])


def today_str():
    return date.today().isoformat()

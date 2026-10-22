"""语音定闹钟的解析（2026-10-22）

用户说一句，我们认出"什么时候"和"提醒什么"。

支持这些说法：
    十秒后提醒我喝水
    二十分钟后提醒我拿快递
    半小时后叫我
    八点提醒我开会
    八点半提醒我吃药
    二十点二十提醒我关火
    明天八点提醒我交材料
    帮我定个闹钟，八点，开会
    定个闹钟

⭐ 为什么放服务端而不是平台：
   定闹钟是"确定可复现"的动作，不该让大模型去理解。
   平台那边一行字都可能被它解读成别的，而这里失败最多是"没听懂，再问一遍"，
   不会把用户的闹钟定到错误的时刻。

⭐ 只做一次性闹钟（设计文档第四节第 5 条：不做"每天早上七点"）。
"""
import re
from datetime import datetime, timedelta

_CN_NUM = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

# 十秒后 / 20 分钟后 / 半小时后 / 一个小时后
_REL = re.compile(
    r"([0-9]+|[零一二两三四五六七八九十]+)\s*个?\s*(秒钟|秒|分钟|分|小时|钟头|刻钟)\s*(?:之)?后")
_REL_HALF_HOUR = re.compile(r"(?<!个)(?:半\s*个?\s*小时|一?刻钟)(?:之)?后")

# 八点 / 八点半 / 八点二十 / 20:20 / 八点一刻
_AT = re.compile(
    r"(?<![\d点:])([0-9]{1,2}|[零一二两三四五六七八九十]{1,3})\s*[点:：]\s*"
    r"(半|[0-9]{1,2}|[零一二两三四五六七八九十]{1,3})?\s*分?")

_SAY = re.compile(r"(?:提醒我|提醒一下|提醒|叫我|让我)(.+)", re.S)
_TOMORROW = ("明天", "明早", "明晚", "明日")

_TAG_WORDS = ("闹钟", "闹表", "定时")
_TRIGGER = ("提醒我", "提醒一下", "叫我", "闹钟", "定个闹", "定时提醒", "让我记得")


def cn_num(s):
    """'十' → 10，'二十' → 20，'八' → 8，'20' → 20；认不出返回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if s.startswith("十"):
        n = _CN_NUM.get(s[1:])
        return 10 + n if n is not None else None
    if s.endswith("十"):
        n = _CN_NUM.get(s[:-1])
        return n * 10 if n is not None else None
    if "十" in s:
        a, _, b = s.partition("十")
        na, nb = _CN_NUM.get(a), _CN_NUM.get(b)
        if na is not None and nb is not None:
            return na * 10 + nb
        return None
    if len(s) == 1:
        return _CN_NUM.get(s)
    return None


_STRONG_TRIGGER = ("闹钟", "闹表", "定个闹", "叫我", "定时提醒")
_TIME_WORD = ("秒", "分钟", "小时", "点", "刻钟")


def looks_like_alarm(text):
    """这句是不是在定闹钟。宁可少认，认错了会答得很奇怪。

    · 「闹钟」「叫我」这类词，出现就算
    · 「提醒我」必须同时有时间词 —— 否则「提醒我一下请假流程」会被误认
    """
    t = text or ""
    if any(w in t for w in _STRONG_TRIGGER):
        return True
    if "提醒我" in t or "提醒一下" in t:
        return any(w in t for w in _TIME_WORD)
    return False


def extract_say(text):
    """'十秒后提醒我喝水' → '喝水'；认不出返回空串。"""
    m = _SAY.search(text or "")
    if not m:
        return _tail_after_comma(text)
    s = m.group(1).strip()
    s = re.sub(r"^[，,、。.！!？?\s]+", "", s)
    s = re.sub(r"[。.！!？?\s]+$", "", s)
    # 去掉句尾的"吧""哦""啊"
    s = re.sub(r"[吧哦啊呀嘛呢]+$", "", s)
    # "提醒我一下"里的"一下"不是内容
    if s in ("一下", "一声", "下", "一下下"):
        return ""
    return s


def _tail_after_comma(text):
    """兜底：'帮我定个闹钟，八点，开会' → '开会'

    只取最后一个逗号之后、且不像时间的那些字。
    """
    parts = [p.strip() for p in re.split(r"[，,]", str(text or "")) if p.strip()]
    if len(parts) < 2:
        return ""
    tail = parts[-1]
    # 如果最后一段还是时间（"八点"），往前找一段
    if re.fullmatch(r"[0-9零一二两三四五六七八九十点:：半分\s]+", tail):
        if len(parts) >= 3:
            tail = parts[-2]
        else:
            return ""
    return re.sub(r"[吧哦啊呀嘛呢。.！!？?\s]+$", "", tail)


def parse(text, now=None):
    """返回 (提醒时刻, 提醒内容) 或 None。

    相对时间（X 秒/分/小时后）优先；其次绝对时刻（八点 / 20:20）；
    都没有就返回 None，由上层问一句"几点提醒你"。
    """
    now = now or datetime.now()
    t = text or ""

    # ---- 相对 ----
    m = _REL.search(t)
    if m:
        n = cn_num(m.group(1))
        unit = m.group(2)
        if n is not None:
            if unit in ("秒钟", "秒"):
                delta = timedelta(seconds=n)
            elif unit in ("分钟", "分"):
                delta = timedelta(minutes=n)
            elif unit == "刻钟":
                delta = timedelta(minutes=15 * n)
            else:
                delta = timedelta(hours=n)
            return (now + delta).replace(microsecond=0), extract_say(t) or "时间到了"
    m = _REL_HALF_HOUR.search(t)
    if m:
        delta = timedelta(minutes=15 if "刻" in m.group(0) else 30)
        return (now + delta).replace(microsecond=0), extract_say(t) or "时间到了"

    # 一个半小时 / 两个半小时 → 1.5 小时 / 2.5 小时
    m = re.search(r"([0-9]+|[零一二两三四五六七八九十]+)\s*个?\s*半\s*小时", t)
    if m:
        n = cn_num(m.group(1))
        if n is not None:
            delta = timedelta(minutes=int(n * 60 + 30))
            return (now + delta).replace(microsecond=0), extract_say(t) or "时间到了"

    # ---- 绝对时刻 ----
    m = _AT.search(t)
    if m:
        hour = cn_num(m.group(1))
        minute_raw = m.group(2)
        if hour is not None:
            if hour == 0:
                hour = 0
            if minute_raw is None:
                minute = 0
            elif minute_raw.strip() in ("半",):
                minute = 30
            else:
                minute = cn_num(minute_raw)
            if minute is not None and 0 <= hour <= 23 and 0 <= minute <= 59:
                at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if any(w in t for w in _TOMORROW):
                    at += timedelta(days=1)
                elif at <= now:
                    # 说的是今天已过去的时刻 → 当成明天（"八点提醒我"说在下午时）
                    at += timedelta(days=1)
                return at, extract_say(t) or "时间到了"

    return None


def describe(at, say, now=None):
    """把'提醒时刻'说成人话：'10 秒后' / '今天 08:00' / '明天 08:00'"""
    now = now or datetime.now()
    diff = (at - now).total_seconds()
    if -2 <= diff <= 90:
        return f"{max(1, int(round(diff)))} 秒后"
    if diff < 3600:
        return f"{int(round(diff / 60))} 分钟后"
    if at.date() == now.date():
        return at.strftime("今天 %H:%M")
    if at.date() == (now + timedelta(days=1)).date():
        return at.strftime("明天 %H:%M")
    return at.strftime("%m月%d日 %H:%M")


def answer(query, now=None):
    """一句话进、一句话出。定好了返回确认语，听不懂返回追问。"""
    now = now or datetime.now()
    got = parse(query, now)
    if not got:
        return ("好的，几点提醒你呢？你比如说「八点提醒我开会」，"
                "或者「十秒后提醒我喝水」。")
    at, say = got
    when = describe(at, say, now)
    return f"好的，{when}提醒你{say}。"


def make_alarm(query, now=None):
    """给 brain 用：定成了返回 (at, say)，没定成返回 (None, 追问语)。"""
    now = now or datetime.now()
    got = parse(query, now)
    if not got:
        return None, None
    at, say = got
    if at <= now - timedelta(seconds=1):
        return None, None
    return at, say

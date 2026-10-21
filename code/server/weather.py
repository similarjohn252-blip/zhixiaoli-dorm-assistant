"""高德天气（2026-10-21）

为什么换：平台那个天气插件只能查当天，而且它一挂就把整个工作流拖成 400。
         现在改成服务端直接问高德，一次拿到今天 + 未来三天，平台那边可以整个删掉。

key 从环境变量读：ZHIXIAOLI_GAODE_KEY（写在 .env 里，不要写进代码）
城市默认重庆（adcode 500000）—— 高德要高德自己的城市编码，不是名字。

API：https://restapi.amap.com/v3/weather/weatherInfo
  extensions=base  实况（只有今天）
  extensions=all   预报（今天 + 未来三天）
返回里 casts 是列表，每条 {date, dayweather, nightweather, daytemp, nighttemp,
                          daywind, daypower, nightwind, nightpower}
"""
import os

import requests

AMAP_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
CITY = os.environ.get("ZHIXIAOLI_WEATHER_CITY", "500000")     # 500000 = 重庆
KEY = os.environ.get("ZHIXIAOLI_GAODE_KEY", "")
TIMEOUT = 6

_CN_NUM = {"1": "1", "2": "2", "3": "3", "4": "4", "5": "5", "6": "6"}


def enabled() -> bool:
    return bool(KEY)


def fetch(extensions="all"):
    """问高德要天气。成功返回 dict，失败返回 None（绝不抛 ✗）。"""
    if not KEY:
        print("[天气] 没配 ZHIXIAOLI_GAODE_KEY，跳过", flush=True)
        return None
    try:
        r = requests.get(AMAP_URL, params={
            "key": KEY, "city": CITY, "extensions": extensions,
            "output": "JSON",
        }, timeout=TIMEOUT)
        if r.status_code != 200:
            print(f"[天气] 高德 HTTP {r.status_code}", flush=True)
            return None
        j = r.json()
        if str(j.get("status")) != "1":
            # 常见：10001 key 不对 / 10009 请求超限 / 10003 日访问量超限
            print(f"[天气] 高德返回 status={j.get('status')} "
                  f"info={j.get('info')} infocode={j.get('infocode')}", flush=True)
            return None
        casts = j.get("casts") or []
        if not casts:
            print("[天气] 高德没给 casts", flush=True)
            return None
        return {"casts": casts, "city": (j.get("lives") or [{}])[0].get("city")
                or casts[0].get("city") or ""}
    except Exception as e:
        print(f"[天气] 高德调用失败: {type(e).__name__}: {e}", flush=True)
        return None


def _temp(cast):
    return f"{cast.get('nighttemp', '?')} 到 {cast.get('daytemp', '?')} 度"


def _day_line(cast, which):
    """一天说一句人话。which：'今天' / '明天' / 后天日期"""
    return (f"{which}{cast.get('dayweather') or ''}，{_temp(cast)}，"
            f"白天{cast.get('daywind') or ''}风 {cast.get('daypower') or ''} 级")


def answer(query=""):
    """把天气问句变成一句人话；拿不到数据就返回空串（让调用方去指路）。"""
    data = fetch("all")
    if not data:
        return ""
    casts = data["casts"]
    city = "重庆"                     # 目前固定重庆，问别的城市由提示词/分支把关
    want = 0
    if "后天" in query:
        want = 2
    elif "明天" in query or "明日" in query:
        want = 1
    if len(casts) <= want:
        want = 0
    head = _day_line(casts[want], ["今天", "明天", "后天"][want])
    if want != 0:
        return f"{city}{head}。"
    # 问今天 → 顺带给明天，信息更有用（用户抱怨的就是"只能查当天"）
    parts = [f"{city}{head}"]
    if len(casts) > 1:
        parts.append("明" + _day_line(casts[1], "天"))
    return "。".join(parts) + "。"

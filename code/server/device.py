"""板子（ESP32）代理：让网页只跟服务端打交道，不直接碰板子的私有地址。

为什么需要它：
  网页一旦部署到"评委能访问的地方"，它和板子就不在同一个网络了 ✗
  —— 私有地址 192.168.137.x 跨网络不可达，网页直连板子必然失败。
  所以统一由服务端转发（服务端和板子在同一个热点里 ✓）。

对外约定（见 网页前端_对接文档.md 第 11 节）：
  · 子路径与板子**完全一致**：/status、/volume、/profile
    → 前端只要把 base 从 http://<板子> 换成 http://<服务端>/v1/device 就行 ✓
  · 失败**不抛异常**，一律返回 200 + ok/online=false
    → 网页不必 try/catch 也能把状态渲染出来 ✓
"""
import requests

from .config import BOARD_HOST, BOARD_TIMEOUT


def _base() -> str:
    return "http://" + BOARD_HOST


def _err(e) -> str:
    return f"{type(e).__name__}: {str(e)[:80]}"


def board_status(timeout: float = None) -> dict:
    """板子在线吗。失败也返回结构化结果（online=False）。

    ⚠️ timeout 参数是给 /v1/health 用的 —— **健康检查必须快** ✗
    实测：板子不在线时，默认 4 秒超时会让 /v1/health 卡 4~5 秒，
    前端轮询会误以为"服务不可用"，部署自检也被拖慢（真踩过 ✓）
    """
    try:
        r = requests.get(_base() + "/status",
                         timeout=BOARD_TIMEOUT if timeout is None else timeout)
        return {"online": True, "board": BOARD_HOST,
                "detail": r.json() if r.content else {}}
    except Exception as e:
        return {"online": False, "board": BOARD_HOST, "error": _err(e)}


def board_volume_get() -> dict:
    """读板子当前音量。"""
    try:
        r = requests.get(_base() + "/volume", timeout=BOARD_TIMEOUT)
        j = r.json()
        return {"ok": True, "volume": j.get("volume"), "steps": j.get("steps")}
    except Exception as e:
        return {"ok": False, "error": _err(e)}


def board_volume_set(volume) -> dict:
    """设置板子音量。范围与固件保持一致：0~21，越界直接夹住。"""
    try:
        v = int(volume)
    except Exception:
        return {"ok": False, "error": "volume 必须是 0~21 的整数"}
    v = max(0, min(21, v))
    try:
        r = requests.post(_base() + "/volume", json={"volume": v},
                          timeout=BOARD_TIMEOUT)
        return {"ok": True, "volume": v,
                "detail": r.json() if r.content else {}}
    except Exception as e:
        return {"ok": False, "volume": v, "error": _err(e)}


def board_profile(name: str, room: str = "") -> dict:
    """把网页填的个人信息转给板子（板子只打印到串口）。"""
    try:
        r = requests.post(_base() + "/profile",
                          json={"name": name, "room": room},
                          timeout=BOARD_TIMEOUT)
        return {"ok": True, "detail": r.json() if r.content else {}}
    except Exception as e:
        return {"ok": False, "error": _err(e)}

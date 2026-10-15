"""音频地址的纯逻辑（B3 快测试层 ✓）。

这一组直接对应 2026-10-15 那个真 bug ✗：
  校园网 IP 一变（10.103.7.74 → 10.203.24.251），板子拿到的就是**死地址** → 屏幕"播放失败" ✓
  修法 = 音频地址**跟着请求的 Host 走** ✓ → 于是"谁问就给谁能打开的那个地址" ✓
"""
from server import config, music, trace


def test_base_follows_request_host():
    trace.set_base("http://192.168.137.1:5000")
    assert music._base() == "http://192.168.137.1:5000"


def test_base_follows_another_host():
    """同一个进程里换一个 Host，地址也要跟着换 ✓（老代码两次都会返回同一个配置值 ✗）"""
    trace.set_base("http://10.203.24.251:5000")
    assert music._base() == "http://10.203.24.251:5000"


def test_base_falls_back_to_config():
    trace.set_base("")
    assert music._base() == f"http://{config.PUBLIC_HOST}:{config.PUBLIC_PORT}"

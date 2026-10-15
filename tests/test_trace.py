"""链路追踪的纯逻辑：id 生成、请求基地址跟随、分段计时（B3 快测试层 ✓）。"""
import re

from server import trace


def test_new_id_is_short_hex():
    i = trace.new_id()
    assert re.fullmatch(r"[0-9a-f]{6}", i), i


def test_start_sets_rid():
    rid = trace.start()
    assert trace.rid() == rid
    assert len(rid) == 6


def test_start_accepts_caller_id():
    """调用方带了 X-Request-Id 就用它的 ✓"""
    trace.start("abc123")
    assert trace.rid() == "abc123"


def test_base_url_follows_request_and_strips_slash():
    """2026-10-15 修的那个 bug：音频地址要**跟着请求走** ✓"""
    trace.set_base("http://192.168.137.1:5000/")
    assert trace.base_url("http://fallback") == "http://192.168.137.1:5000"


def test_base_url_falls_back_when_no_request():
    trace.set_base("")
    assert trace.base_url("http://10.0.0.1:5000") == "http://10.0.0.1:5000"


def test_timed_collects_stages():
    trace.start()
    with trace.timed("甲"):
        pass
    with trace.timed("乙"):
        pass
    t = trace.timings()
    assert set(t) == {"甲", "乙"}
    assert all(isinstance(v, float) and v >= 0 for v in t.values())
    assert trace.total() >= 0

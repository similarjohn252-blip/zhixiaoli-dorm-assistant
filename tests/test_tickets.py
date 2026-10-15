"""工单台账的纯逻辑：类别猜测、建单/改状态、状态文案与灯色映射（B3 快测试层 ✓）。"""
import re

from server import tickets


def test_guess_category_from_speech():
    assert tickets.guess_category("我们宿舍的灯坏了") == "照明"
    assert tickets.guess_category("宿舍跳闸了") == "照明"
    assert tickets.guess_category("水龙头一直滴水") == "给排水"
    assert tickets.guess_category("空调不制冷") == "空调"
    assert tickets.guess_category("宿舍网断了") == "网络"
    assert tickets.guess_category("门关不上") == "门窗"
    assert tickets.guess_category("床板断了") == "家具"
    assert tickets.guess_category("插座没电") == "供电"


def test_guess_category_fallback():
    assert tickets.guess_category("随便说点什么") == "其他"


def test_create_get_and_id_format():
    t = tickets.create(room="3栋502", category="照明", note="灯坏了")
    assert re.fullmatch(r"BX\d{8}-\d{4}", t["id"]), t["id"]      # BX20261015-0001 形式 ✓
    assert t["status"] == "new"
    got = tickets.get(t["id"])
    assert got and got["room"] == "3栋502" and got["category"] == "照明"


def test_set_status_and_led_mapping():
    t = tickets.create(room="", category="给排水", note="漏水")
    assert tickets.set_status(t["id"], "doing") is True
    assert tickets.status_text("doing") == "处理中"
    assert tickets.action_for("doing") == "LED_BLUE_ON"          # 处理中 = 蓝灯 ✓
    assert tickets.set_status(t["id"], "done") is True
    assert tickets.action_for("done") == "LED_GREEN_ON"          # 已完成 = 绿灯 ✓
    assert tickets.set_status(t["id"], "rejected") is True
    assert tickets.action_for("rejected") == "LED_RED_ON"        # 驳回 = 红灯 ✓


def test_invalid_status_rejected():
    t = tickets.create(room="", category="其他", note="x")
    assert tickets.set_status(t["id"], "不存在的状态") is False
    assert tickets.get(t["id"])["status"] == "new"               # 没被改坏 ✓


def test_status_text_fallback():
    assert tickets.status_text("new") == "已受理"
    assert tickets.status_text("") == "未知"


def test_clear_all():
    tickets.create(room="", category="其他", note="清空前")
    assert tickets.clear_all() >= 1
    assert tickets.newest() is None

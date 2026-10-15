"""补槽位追问（2026-10-15 新增 ✓）—— 报修缺房间号时**先问**，下一句补上再建单 ✓

为什么要这条 ✓：
  `_guess_room()` 只在**长期记忆**里找房间 ✓ → 记忆是空的时候，
  原来会直接建一张**房间为空**的工单 ✗（用户说半天，单上没地址 ✓）→ 修法：缺什么就问什么 ✓

为什么它**能离线跑** ✓：
  追问与补单这两条路，都在【调云端平台之前】就 `return` 了 ✓（纯本地决定 ✓ 断网也能用 ✓✓）
"""
from server import brain, memory


def test_room_in_extracts():
    """提房间号的正则 ✓（历史遗留坑：中间有空格的那次只截出"3 栋" ✗）"""
    assert brain._room_in("3 栋 502") == "3 栋 502"          # 允许空格 ✓
    assert brain._room_in("我住 12 号楼 501") == "12 号楼 501"
    assert brain._room_in("在 502 室") == "502 室"
    assert brain._room_in("灯坏了") == ""
    assert brain._room_in("") == ""


def test_ask_back_then_fill(monkeypatch):
    """① 缺房间 → 先问、不建单 ② 补房间 → 建单、回答带工单号 ✓"""
    monkeypatch.setattr(brain, "make_tts", lambda text: "")   # 不碰 TTS 网络 ✓
    memory._memory["facts"] = []                              # 确保记忆里没有房间 ✓
    brain._pending.update(kind="", note="", ts=0.0)

    # ① 报修但没房间号 → 必须【先问】✓ 且【不建单】✓
    r1 = brain.handle_query("我们宿舍的灯坏了")
    assert "哪个房间" in r1["voice_text"], r1["voice_text"]
    assert r1["device_action"] == "NONE"           # 还没办事，别亮绿灯 ✗
    assert brain._pending["kind"] == "repair"      # 记住了"待补房间" ✓

    # ② 用户补上房间号 → 建单 ✓ 回答里带房间与单号 ✓
    r2 = brain.handle_query("3 栋 502")
    assert "3 栋 502" in r2["voice_text"], r2["voice_text"]
    assert ("工单号" in r2["voice_text"]) or ("报修单" in r2["voice_text"]), r2["voice_text"]
    assert brain._pending["kind"] == ""            # 状态已清 ✓ 不会重复建单 ✓


def test_pending_expires(monkeypatch):
    """超时后不该把一句无关的话当成"补房间" ✗（120 秒 TTL ✓）

    ⚠️ 这一条会走到**平台那一支** ✓ 所以必须把云调用也 mock 掉 ✗
       （否则测试会真的发网络请求 ✓ 既慢又可能偶发失败 ✓）
    """
    monkeypatch.setattr(brain, "make_tts", lambda text: "")
    monkeypatch.setattr(brain, "_cloud_request",
                        lambda query, conv_id: ("我在呢，有什么可以帮你的吗？", "NONE"))
    memory._memory["facts"] = []
    brain._pending.update(kind="repair", note="灯坏了", ts=0.0)   # ts=0 → 早就过期 ✓

    r = brain.handle_query("3 栋 502")
    assert "工单号" not in r["voice_text"], r["voice_text"]        # 不该建单 ✓

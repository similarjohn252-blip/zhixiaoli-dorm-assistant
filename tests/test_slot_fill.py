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


def test_is_repair_covers_real_phrasings():
    """报修判定：**16 句真实说法全要认出来** ✓，而规定类问题**不能误判** ✗

    ⚠️ 2026-10-15 实测：原来靠 12 个整词，16 句里只触发 6 句（37%）✗
       → 改成「设备词 + 故障特征」组合后，应**全覆盖** ✓
    """
    should = ["我们宿舍的灯坏了", "空调不制冷", "空调坏了", "水龙头漏水", "厕所堵了",
              "网连不上", "插座没电", "门锁坏了", "窗户关不上", "有点吵",
              "宿舍太脏了", "热水器不出热水", "风扇不转", "灯泡闪", "床板断了",
              "我想报修"]
    for q in should:
        assert brain._is_repair(q), f"漏掉了：{q}"        # 漏 = 少建一张单 ✗

    must_not = ["宿舍能用大功率电器吗", "违规怎么处理", "校训是什么",
                "宿舍几点关门", "我想订会议室", "我有点不舒服"]
    for q in must_not:
        assert not brain._is_repair(q), f"误判了：{q}"     # 误判 = 凭空建垃圾单 ✗


def test_ask_back_feature_is_gone(monkeypatch):
    """2026-10-19：补槽位整条已停用（报修改成固定话术，不再追问房间号）"""
    monkeypatch.setattr(brain, "make_tts", lambda text: "")   # 不碰 TTS 网络 ✓
    memory._memory["facts"] = []                              # 确保记忆里没有房间 ✓
    brain._pending.update(kind="", note="", ts=0.0)

    r = brain.handle_query("我们宿舍的灯坏了")
    assert "哪个房间" not in r["voice_text"], r["voice_text"]
    assert "知行理工" in r["voice_text"], r["voice_text"]
    assert brain._pending["kind"] == "", "不该再记录待补房间"


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

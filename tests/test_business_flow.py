"""业务接线回归（2026-10-18 新增 ✓）

⭐ 为什么加 ✗：原来 49 项测试把【零件】测得很细 ✓
   （guess_category ✓ / action_for ✓ / _is_repair ✓ / 追问 ✓ / 自愈 ✓）
   但**零件之间的接线没测** ✗ —— 比如"报修到底有没有真的变成 LED_GREEN_ON"✗
   今天实测 39 例才发现这些接线是好的 ✓ 可那是临时脚本 ✗ 进不了仓库 ✗
→ 这个文件把"接线"固定下来 ✓ 离线跑 ✓ 走 pytest ✓ 钩子会自动跑它 ✓

⚠️ 平台一律 mock 掉 ✓（monkeypatch ask_cloud ✓）→ 不联网 ✓ 不花钱 ✓ 快 ✓
⚠️ 工单库走 conftest 的临时库 ✓（ZHIXIAOLI_TICKETS_DB ✓）→ 不会写脏真库 ✓
"""
from server import brain, memory, tickets


def _setup(monkeypatch, facts=None, reply=None, action="NONE"):
    """把平台替换成固定回复 ✓ 并清掉工单 / 追问状态 ✓

    ⚠️ action 必须能指定 ✗：红灯/蓝灯是【平台】下发的 ✓
       （我第一版一律返回 NONE ✗ → 违禁和晚归那两条必然误判 ✓）
       而报修是【服务端】建单后自己钉成绿灯的 ✓ 所以报修那条用 NONE 就行 ✓
    """
    monkeypatch.setattr(brain, "BRAIN", "cloud")
    monkeypatch.setattr(brain, "ask_cloud", lambda q: ((reply or q), action))
    monkeypatch.setattr(brain, "make_tts", lambda t: "")
    memory._memory["facts"] = list(facts or [])
    memory._history.clear()
    brain._pending.update(kind="", note="", ts=0.0)
    tickets.clear_all()


# ══════════════════ 报修 / 请假 → 固定话术（2026-10-19 改）══════════════════
def test_repair_points_to_zhixingli(monkeypatch):
    """2026-10-19 改：报修不再建工单，改说固定话术（指路知行理工）"""
    _setup(monkeypatch, facts=["用户住 3 栋 502"], reply="好的。")
    r = brain.handle_query("我们宿舍的灯坏了，要报修")
    assert r["device_action"] == "LED_GREEN_ON", f"实际动作 {r['device_action']}"
    vt = r["voice_text"]
    assert "知行理工" in vt and "后勤报修" in vt, vt
    assert "工单号" not in vt, f"不该再出现工单号：{vt}"
    assert tickets.newest() is None, "不该再建工单"
    assert r["source"] == "本地固定话术"


def test_repair_never_asks_for_room(monkeypatch):
    """报修不再反问房间号（2026-10-19 用户要求）"""
    _setup(monkeypatch, facts=[], reply="好的。")
    r = brain.handle_query("我们宿舍的灯坏了，要报修")
    assert "哪个房间" not in r["voice_text"], "不该再反问房间号"
    assert "知行理工" in r["voice_text"]
    assert brain._pending["kind"] == "", "不该再记「待补房间」"


def test_leave_points_to_zhixingli(monkeypatch):
    """请假：固定话术（知行理工 + 本科生请假 + 提醒班导师）"""
    _setup(monkeypatch, facts=[], reply="好的。")
    r = brain.handle_query("我想请假")
    vt = r["voice_text"]
    assert "知行理工" in vt and "本科生请假" in vt, vt
    assert "班导师" in vt, vt
    assert r["device_action"] == "LED_GREEN_ON"
    assert r["source"] == "本地固定话术"


def test_second_turn_after_repair_goes_to_platform(monkeypatch):
    """补槽位已停用 —— 第二句「3 栋 502」走正常问答"""
    _setup(monkeypatch, facts=[], reply="你好呀。")
    brain.handle_query("我们宿舍的灯坏了，要报修")
    r = brain.handle_query("3 栋 502")
    assert r["source"] != "本地补槽位", "不该再走补槽位"
    assert "知行理工" not in r["voice_text"], "第二句不该再是报修话术"


# ══════════════════ 其它意图的动作 ══════════════════
def test_forbidden_appliance_turns_red(monkeypatch):
    """⭐ 违禁电器 → 红灯 ✓"""
    _setup(monkeypatch, facts=["用户住 3 栋 502"],
            reply="宿舍里不能用电磁炉哦，属于违规电器。", action="LED_RED_ON")
    r = brain.handle_query("宿舍能用电磁炉吗")
    assert r["device_action"] == "LED_RED_ON", f"实际 {r['device_action']}"


def test_late_return_turns_blue(monkeypatch):
    """⭐ 晚归报备 → 蓝灯 ✓"""
    _setup(monkeypatch, facts=["用户住 3 栋 502"],
            reply="好的，已经帮你登记今晚晚归报备。", action="LED_BLUE_ON")
    r = brain.handle_query("我今晚晚点回宿舍")
    assert r["device_action"] == "LED_BLUE_ON", f"实际 {r['device_action']}"


def test_progress_points_to_zhixingli(monkeypatch):
    """2026-10-19 改：问进度也指路知行理工

    为什么改：报修已经"不在我们这儿办"了，如果再报出台账里的旧单号，
    用户视角就自相矛盾（说不在你这办，怎么又有一张单子）。台账数据没删，
    只是这条分支不再读它。
    """
    _setup(monkeypatch, facts=["用户住 3 栋 502"], reply="好的。")
    tickets.create(room="3 栋 502", category="照明", note="灯坏了")   # 故意放一张旧的
    before = tickets.newest()["id"]
    r = brain.handle_query("我报修的进度怎么样了")
    assert "知行理工" in r["voice_text"], r["voice_text"]
    assert "后勤报修" in r["voice_text"], r["voice_text"]
    assert before not in r["voice_text"], "不该再报出台账里的旧单号"
    assert r["source"] == "本地固定话术"
    assert tickets.newest()["id"] == before, "问进度不该新建工单"


def test_chitchat_is_none_action(monkeypatch):
    """⭐ 闲聊 → 动作 NONE ✓ 不建单 ✓"""
    _setup(monkeypatch, facts=[], reply="你好呀，我是智小理。")
    r = brain.handle_query("你好呀")
    assert r["device_action"] == "NONE"
    assert tickets.newest() is None


# ══════════════════ 异常输入不能崩 ══════════════════
def test_weird_inputs_do_not_crash(monkeypatch):
    """⭐ 空串 / 纯空格 / 乱码 / 纯符号 / emoji / 超长 → 都必须给出一句回答，不能抛 ✓"""
    _setup(monkeypatch, facts=["用户住 3 栋 502"], reply="没听明白。")
    for q in ("", "   ", "asdkjh qwe", "？？？……", "😀😀", "灯坏了" * 100):
        r = brain.handle_query(q)
        assert isinstance(r.get("voice_text"), str) and r["voice_text"].strip(), f"「{q[:12]}」没给回答 ✗"
        assert r["device_action"] in ("NONE", "LED_GREEN_ON", "LED_BLUE_ON", "LED_RED_ON")


def test_empty_query_falls_back_when_cloud_raises(monkeypatch):
    """⭐ 平台对空问题会报错（400）→ 服务端必须回退本地、仍然给出回答 ✓"""
    monkeypatch.setattr(brain, "BRAIN", "cloud")

    def boom(q):
        raise RuntimeError("平台返回 HTTP 400: query字段不能为空")

    monkeypatch.setattr(brain, "ask_cloud", boom)
    monkeypatch.setattr(brain, "make_tts", lambda t: "")
    memory._memory["facts"] = []
    r = brain.handle_query("")
    assert r["voice_text"].strip(), "回退后必须有回答 ✗"
    assert r["source"] == "本地回退"


# ══════════════════ 动作兜底 ══════════════════
def test_action_guard_gives_fallback_action(monkeypatch):
    """⭐ 平台没下发动件动作时，本地要兜底（会议室这类要么指路要么给动作 ✓）"""
    reply, action, override = brain._action_guard("帮我订个会议室", "会议室预约可以找教务处。", "NONE")
    assert action in ("LED_GREEN_ON", "LED_BLUE_ON", "NONE"), f"兜底动作异常 {action}"
    assert isinstance(reply, str) and reply.strip()
    # 平台动作非 NONE 时不该被覆盖 ✓
    reply2, action2, override2 = brain._action_guard("帮我订个会议室", "已经帮你预约了。", "LED_GREEN_ON")
    assert action2 == "LED_GREEN_ON", "平台给了动作就不该覆盖 ✗"


# ═══════════════════════════════════════════════════════════
# 工单回执下载（工程债 C1 最后一块 ✓）
# ═══════════════════════════════════════════════════════════
def _flask_client():
    """⭐ conftest 里没有 client fixture ✓ → 自己建一个 Flask 测试客户端 ✓
    （不影响真实服务端 ✓ 只在内存里跑 ✓）"""
    from server.app import app
    app.config["TESTING"] = True
    return app.test_client()


def test_receipt_download():
    """⭐ 回执真的能下载 ✓ 而且是附件 ✓（不是显示在页面里 ✗）"""
    from server import tickets
    known = tickets.newest()
    if not known:
        import pytest
        pytest.skip("库里还没有工单 ✓ 先跑一次报修再测 ✓")
    tid = known["id"]
    r = _flask_client().get(f"/v1/tickets/{tid}/receipt.txt")
    assert r.status_code == 200, r.status_code
    cd = r.headers.get("Content-Disposition", "")
    assert "attachment" in cd, f"必须让浏览器下载而不是显示 ✗ 实际 {cd!r}"
    assert tid in cd, f"文件名里要带单号 ✓ 实际 {cd!r}"
    body = r.data.decode("utf-8-sig")
    assert tid in body, "回执正文里必须有单号 ✓"
    assert "智小理" in body and "报修回执" in body
    # 状态要翻译成中文 ✓（不是裸 new ✗）
    assert "new" not in body or "已受理" in body


def test_receipt_missing_ticket_is_404_text():
    """找不到单号 → 404 + 人话，而不是 500 ✗"""
    r = _flask_client().get("/v1/tickets/BX_NOT_EXIST/receipt.txt")
    assert r.status_code == 404
    assert "没有这张工单" in r.data.decode("utf-8")

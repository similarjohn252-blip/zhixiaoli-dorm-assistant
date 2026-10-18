"""用户最直接感知的那半：板子代理 / 语音 / 放歌（2026-10-18 新增 ✓）

⭐ 为什么补这三个 ✗：实测覆盖率 51%，而且**偏在数据层**（tickets 94% / memory 74% ✓）
   真正被用户感知的这几块几乎没测 ✗：
     speech.py 18%（有没有声音、听不听得懂）
     device.py 22%（灯亮不亮、音量改不改得动）
     music.py  23%（放歌）
→ 这个文件只测【不联网也能测】的部分 ✓：纯函数 + 失败路径 + 降级 ✓

⭐ 判据一律测【形状和契约】✓ 不测模型的随机文本 ✗
   例：只断言 `ok is False` 和 `online is False` ✓ 不断言错误文案 ✗
"""
from server import device, music, speech

# ═══════════════════════════════════════════════════════════
# 一、device.py —— 板子代理（契约：失败也不抛 ✗）
# ═══════════════════════════════════════════════════════════

def test_device_volume_set_clamps(monkeypatch):
    """⭐ 音量必须夹到 0~21（固件的范围 ✓）—— 越界要夹住而不是报错 ✓"""
    monkeypatch.setattr(device, "BOARD_HOST", "127.0.0.1:1")
    sent = {}

    class R:
        content = b"{}"

        def json(self):
            return {}

    monkeypatch.setattr(device.requests, "post", lambda url, **kw: (sent.update(kw), R())[1])

    assert device.board_volume_set(-5)["volume"] == 0
    assert device.board_volume_set(99)["volume"] == 21
    assert device.board_volume_set(15)["volume"] == 15
    assert sent["json"]["volume"] == 15, "发给板子的值应该是夹取后的 ✓"


def test_device_volume_set_bad_input(monkeypatch):
    """⭐ 非数字 → ok=False ✓ **不许抛** ✗（网页不做 try/catch ✓）"""
    monkeypatch.setattr(device, "BOARD_HOST", "127.0.0.1:1")
    r = device.board_volume_set("大声点")
    assert r["ok"] is False
    assert "error" in r


def test_device_never_raises_on_network_error(monkeypatch):
    """⭐ 板子不在线时，四个函数都必须返回结构化失败 ✓ 一个都不许抛 ✗"""
    monkeypatch.setattr(device, "BOARD_HOST", "127.0.0.1:1")

    def boom(*a, **kw):
        raise ConnectionError("板子没连")

    monkeypatch.setattr(device.requests, "get", boom)
    monkeypatch.setattr(device.requests, "post", boom)

    assert device.board_status()["online"] is False
    assert device.board_volume_get()["ok"] is False
    assert device.board_volume_set(15)["ok"] is False
    assert device.board_profile("小李", "3栋502")["ok"] is False


def test_device_status_timeout_is_passed(monkeypatch):
    """⭐ 健康检查必须快 ✓ —— timeout 参数要真的传给 requests ✓（否则 /v1/health 会卡 4 秒 ✗）"""
    seen = {}

    class R:
        content = b"{}"

        def json(self):
            return {}

    def fake_get(url, **kw):
        seen.update(kw)
        return R()

    monkeypatch.setattr(device, "BOARD_HOST", "127.0.0.1:1")
    monkeypatch.setattr(device.requests, "get", fake_get)
    device.board_status(timeout=1.5)
    assert seen.get("timeout") == 1.5, f"timeout 没传下去 ✗ 实际 {seen}"


# ═══════════════════════════════════════════════════════════
# 二、speech.py —— 语音（契约：按顺序降级，全失败才抛 ✗）
# ═══════════════════════════════════════════════════════════

def test_engine_chain_parsing(monkeypatch):
    """⭐ 引擎链是【有序】的 ✓（edge,baidu = 先 edge 后百度 ✓）"""
    monkeypatch.setattr(speech, "TTS_ENGINE", "baidu")
    assert [f.__name__ for f in speech._engine_chain()] == ["baidu_tts"]

    monkeypatch.setattr(speech, "TTS_ENGINE", "edge,baidu")
    assert [f.__name__ for f in speech._engine_chain()] == ["edge_tts_generate", "baidu_tts"]

    monkeypatch.setattr(speech, "TTS_ENGINE", "edge")
    assert [f.__name__ for f in speech._engine_chain()] == ["edge_tts_generate"]


def test_engine_chain_falls_back_on_typo(monkeypatch):
    """⭐ 写错了 → 两条都试 ✓（不许变成"没有引擎" ✗ 那样就彻底没声音 ✓）"""
    monkeypatch.setattr(speech, "TTS_ENGINE", "baiduuu")
    chain = [f.__name__ for f in speech._engine_chain()]
    assert len(chain) >= 2, f"写错配置时应该退回默认两条 ✗ 实际 {chain}"


def test_synthesize_falls_back_to_second_engine(monkeypatch):
    """⭐⭐ 第一个引擎失败 → 自动用第二个 ✓ 并返回真正成功那个的名字 ✓
    （这条是演示当天"edge 突然失效"的保命逻辑 ✓）"""
    calls = []

    def bad(text, path):
        calls.append("bad")
        raise RuntimeError("403 被微软拒")

    def good(text, path):
        calls.append("good")
        return path

    monkeypatch.setattr(speech, "TTS_ENGINE", "edge,baidu")
    monkeypatch.setattr(speech, "_engine_chain", lambda: [bad, good])
    assert speech.synthesize("你好", "x.wav") == "good"
    assert calls == ["bad", "good"]


def test_synthesize_raises_when_all_fail(monkeypatch):
    """⭐ 全失败 → 必须抛 ✗（上层要能报"没声音"✓ 不能静默成功 ✓）"""
    def bad(text, path):
        raise RuntimeError("挂了")

    monkeypatch.setattr(speech, "_engine_chain", lambda: [bad, bad])
    try:
        speech.synthesize("你好", "x.wav")
    except RuntimeError as e:
        assert "所有 TTS 引擎都失败" in str(e)
    else:
        raise AssertionError("全失败时应该抛异常 ✗")


def test_baidu_token_is_cached(monkeypatch):
    """⭐ token 必须缓存 ✓（有效期 30 天 ✓ 每次问都取一次会把额度打光 ✗）"""
    n = {"get": 0}

    class R:
        def json(self):
            return {"access_token": "tok", "expires_in": 2592000}

    def fake_get(url, **kw):
        n["get"] += 1
        return R()

    speech._baidu_token.update(value=None, expire_at=0.0)
    monkeypatch.setattr(speech.requests, "get", fake_get)
    assert speech._get_baidu_token() == "tok"
    assert speech._get_baidu_token() == "tok"
    assert n["get"] == 1, f"第二次应该走缓存 ✗ 实际请求了 {n['get']} 次"


def test_baidu_token_missing_raises(monkeypatch):
    """⭐ 没拿到 token → 必须抛（否则后面每个字都会静默失败 ✓）"""
    class R:
        def json(self):
            return {"error": "invalid_client"}

    speech._baidu_token.update(value=None, expire_at=0.0)
    monkeypatch.setattr(speech.requests, "get", lambda url, **kw: R())
    try:
        speech._get_baidu_token()
    except RuntimeError as e:
        assert "取 token 失败" in str(e)
    else:
        raise AssertionError("没 token 时应该抛 ✗")


def test_baidu_tts_rejects_non_audio(monkeypatch):
    """⭐ 百度返回的不是音频（额度用完/参数错）→ 必须抛 ✓ 不能把错误页写成 .wav ✗"""
    speech._baidu_token.update(value="tok", expire_at=1e12)

    class R:
        # ⚠️ 假对象要造【代码真正用到的全部字段】✗ —— 我漏了 .text ✗
        #    而 speech.py:67 的报错信息里用了 f"...{r.text[:200]}" ✓ → 生产环境真 Response 有它 ✓
        content = b'{"err_msg":"quota"}'
        text = '{"err_msg":"quota"}'
        headers = {"Content-Type": "application/json"}

        def json(self):
            return {"access_token": "tok", "expires_in": 2592000}

    monkeypatch.setattr(speech.requests, "get", lambda url, **kw: R())
    try:
        speech.baidu_tts("你好", "x.wav")
    except RuntimeError as e:
        assert "非音频" in str(e)
    else:
        raise AssertionError("非音频时应该抛 ✗")


def test_baidu_asr_raises_on_error_code(monkeypatch):
    """⭐ ASR 返回 err_no≠0（没听清/鉴权失败）→ 必须抛 ✓（/v1/asr 会兜成 ok:false ✓）"""
    speech._baidu_token.update(value="tok", expire_at=1e12)

    class R:
        def json(self):
            return {"err_no": 3301, "err_msg": "音质太差"}

    monkeypatch.setattr(speech.requests, "post", lambda url, **kw: R())
    try:
        speech.baidu_asr(b"fake")
    except RuntimeError as e:
        assert "3301" in str(e)
    else:
        raise AssertionError("err_no≠0 时应该抛 ✗")


# ═══════════════════════════════════════════════════════════
# 三、music.py —— 放歌（只在能离线测的纯函数上 ✓）
# ═══════════════════════════════════════════════════════════

def test_music_request_detects_song_request():
    """⭐ "放周杰伦的七里香" → 要认出是放歌 ✓ 并且提取出歌名 ✓"""
    term, strict = music.music_request("放周杰伦的七里香")
    # ⚠️ 别断言 strict ✗ —— 实测「放周杰伦的七里香」走的是【宽松】分支（首字"放"✓ 长度≤14 ✓）
    #    strict=False 但**照样认出来了** ✓ → 真正的契约是「term 里要有歌名」✓
    assert term and "七里香" in term, f"歌名没提取出来 ✗ 实际 {term!r}"
    assert isinstance(strict, bool)


def test_music_request_only_song_no_title():
    """⭐ "放首歌" → 认出是放歌 ✓ 但没歌名（term 为空 ✓ 上层会随机来一首 ✓）"""
    term, strict = music.music_request("放首歌")
    assert isinstance(strict, bool)
    assert term == "", f"只说了放首歌时不该有歌名 ✗ 实际 {term!r}"


def test_music_request_ignores_normal_question():
    """⭐ 普通问题不能误判成放歌 ✗（否则问"宿舍管理规定"会去搜歌 ✓）"""
    assert music.music_request("宿舍管理规定是什么")[0] is None
    assert music.music_request("我们宿舍的灯坏了")[0] is None
    assert music.music_request("你好呀")[0] is None


def test_music_artist_alias():
    """⭐ 中文歌手名要能对上英文名 ✓（否则搜到的是翻唱 ✓）"""
    hits = music._artist_alias_hits("放周杰伦的歌")
    assert "Jay Chou" in hits, f"没对上英文名 ✗ 实际 {hits}"


def test_music_score_prefers_original_over_cover():
    """⭐⭐ 打分必须让【原唱】排在【翻唱/钢琴版】前面 ✓（这是选曲质量的关键 ✓）"""
    term = "七里香 周杰伦 Jay Chou"
    original = {"trackName": "七里香", "artistName": "Jay Chou"}
    cover = {"trackName": "七里香 (钢琴版)", "artistName": "Some Pianist"}
    assert music._score_result(original, term) > music._score_result(cover, term), \
        f"原唱应高于翻唱 ✗ 原唱={music._score_result(original, term)} 翻唱={music._score_result(cover, term)}"

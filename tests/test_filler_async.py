"""填充语 + 异步语音受理的测试（2026-10-27）

两块：
  ① 填充语音频管线：裁静音真的变短、清单幂等（第二次不重新生成）、改名后清孤儿
  ② /v1/voice?async=1 的受理与轮询：accepted → working → done，以及过期/野号

⚠️ 一律不联网 ✗：TTS 用注入的假 maker，ASR 和大脑都 monkeypatch 掉 ✓
"""
import array
import json
import os
import wave

import pytest

from server import filler


def _write_wav(path, frames):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(array.array("h", frames).tobytes())


def _read_secs(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


@pytest.fixture
def audio_dir(tmp_path, monkeypatch):
    d = str(tmp_path / "audio")
    os.makedirs(d, exist_ok=True)
    monkeypatch.setattr(filler, "AUDIO_DIR", d)
    monkeypatch.setattr(filler, "MANIFEST", os.path.join(d, "filler_manifest.json"))
    return d


# ---------------- ① 裁静音 ----------------

def test_trim_silence_shortens(tmp_path):
    """0.5 秒静音 + 0.4 秒声音 + 0.5 秒静音 → 应该明显变短。"""
    p = str(tmp_path / "x.wav")
    frames = [0] * int(24000 * 0.5) + [8000] * int(24000 * 0.4) + [0] * int(24000 * 0.5)
    _write_wav(p, frames)
    before, after = filler.trim_silence(p)
    assert before > 1.3
    assert after < 0.7
    assert abs(after - _read_secs(p)) < 0.02


def test_trim_keeps_all_silent(tmp_path):
    """整段都是静音（比如 TTS 出错）→ 原样保留，不能删成空文件。"""
    p = str(tmp_path / "silent.wav")
    _write_wav(p, [0] * 24000)
    before, after = filler.trim_silence(p)
    assert before == pytest.approx(1.0, abs=0.01)
    assert after == before
    assert _read_secs(p) == pytest.approx(1.0, abs=0.01)


def test_trim_handles_broken_file(tmp_path):
    p = str(tmp_path / "bad.wav")
    open(p, "wb").write(b"not a wav")
    before, after = filler.trim_silence(p)      # 不抛异常就赢
    assert (before, after) == (0.0, 0.0)


# ---------------- ② 生成与清单 ----------------

def _fake_maker(calls):
    def maker(text, path):
        calls.append(text)
        # 半秒静音 + 0.3 秒声音 + 半秒静音：顺带验证裁静音在 ensure 里生效
        frames = [0] * 12000 + [6000] * 7200 + [0] * 12000
        _write_wav(path, frames)
        return path
    return maker


def test_ensure_generates_and_is_idempotent(audio_dir):
    calls = []
    got = filler.ensure(tts_maker=_fake_maker(calls))
    n = len(filler.FILLER_HI_TEXT) + len(filler.FILLER_FILL_TEXT)
    assert len(got) == n
    assert len(calls) == n
    for x in got:
        assert x["duration"] < 0.9          # 裁过静音了（原长 1.3 秒）
        assert os.path.exists(os.path.join(audio_dir, x["file"]))
        assert x["url"] == "/audio/" + x["file"]

    calls.clear()
    got2 = filler.ensure(tts_maker=_fake_maker(calls))
    assert calls == [], "第二次不该重新生成"
    assert [x["id"] for x in got2] == [x["id"] for x in got]


def test_ensure_regenerates_when_text_changes(audio_dir, monkeypatch):
    calls = []
    filler.ensure(tts_maker=_fake_maker(calls))

    monkeypatch.setattr(filler, "FILLER_FILL_TEXT", ("改过的文案",))
    monkeypatch.setattr(filler, "FILLER_HI_TEXT", ("好的",))
    calls.clear()
    got = filler.ensure(tts_maker=_fake_maker(calls))
    assert calls == ["改过的文案"]
    assert len(got) == 2
    # 孤儿文件（旧的三句填充语）应该被清掉
    left = [f for f in os.listdir(audio_dir) if f.startswith("filler_") and f.endswith(".wav")]
    assert len(left) == 2


def test_manifest_is_json_on_disk(audio_dir):
    """清单落盘必须是 JSON，且带上生成本次音频用的语速。

    2026-10-29 改：格式从"纯数组"变成 {"rate":…, "items":[…]}。
    为什么要记 rate：改了语速如果音频不重生成，会以为"改了没生效"——
    记下来之后 ensure() 就能发现不匹配、自动重做。
    """
    filler.ensure(tts_maker=_fake_maker([]))
    data = json.load(open(filler.MANIFEST, encoding="utf-8"))
    assert isinstance(data, dict), "清单应该是 {rate, items} 结构"
    assert data["rate"] == filler.FILLER_RATE
    items = data["items"]
    assert isinstance(items, list) and items
    assert items[0]["kind"] == "hi"


def test_manifest_rate_change_regenerates(audio_dir, monkeypatch):
    """语速变了 → 已有音频算过期，必须重生成。

    不这么做的话：改了 FILLER_RATE，音频还是旧的，听起来"改了没生效"。
    """
    calls = []
    filler.ensure(tts_maker=_fake_maker(calls))
    assert len(calls) == len(filler._plan())   # 首次全生成

    calls.clear()
    filler.ensure(tts_maker=_fake_maker(calls))
    assert calls == []                               # 语速没变 → 一条都不重做

    monkeypatch.setattr(filler, "FILLER_RATE", filler.FILLER_RATE + 20)
    calls.clear()
    filler.ensure(tts_maker=_fake_maker(calls))
    assert len(calls) == len(filler._plan())    # 语速变了 → 全部重做
    assert filler.manifest()["rate"] == filler.FILLER_RATE   # 清单跟上新语速

def test_items_empty_before_generate(audio_dir):
    assert filler.items() == []


def test_summary_text_counts(audio_dir):
    filler.ensure(tts_maker=_fake_maker([]))
    s = filler.summary_text()
    assert "语气词" in s and "填充语" in s


def test_default_phrases_have_no_lone_en():
    """2026-10-27 定：单个「嗯」太短，去掉了；「嗯嗯」保留。"""
    assert "嗯" not in filler.FILLER_HI_TEXT
    assert "嗯嗯" in filler.FILLER_HI_TEXT
    assert len(filler.FILLER_FILL_TEXT) == 3


# ---------------- ③ 异步受理 + 轮询 ----------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    from server import app as app_mod
    monkeypatch.setattr(app_mod, "MIC_LAST_WAV", str(tmp_path / "mic.wav"))
    calls = {"n": 0}

    def fake_asr(wav):
        calls["n"] += 1
        return "你好"

    def fake_query(text):
        calls["n"] += 1
        return {"voice_text": "你好呀", "device_action": "NONE", "source": "测试"}

    monkeypatch.setattr(app_mod, "baidu_asr", fake_asr)
    monkeypatch.setattr(app_mod, "handle_query", fake_query)
    app_mod.app.config["TESTING"] = True
    with app_mod.app.test_client() as c:
        yield c, app_mod


def _wait_done(c, app_mod, job_id, tries=60):
    import time as _t
    last = None
    for _ in range(tries):
        r = c.get(f"/v1/voice/result?id={job_id}")
        last = r.get_json()
        if last.get("state") != "working":
            return last
        _t.sleep(0.05)
    return last


def _wav_bytes(n=4000):
    return b"RIFF" + b"\x00" * n          # 长度够就行（ASR 被替换掉了）


def test_async_accepted_then_done(client):
    c, app_mod = client
    r = c.post("/v1/voice?async=1", data=_wav_bytes())
    j = r.get_json()
    assert r.status_code == 200
    assert j["ok"] is True and j["state"] == "accepted" and j["id"]
    assert j["poll"].startswith("/v1/voice/result?id=")

    final = _wait_done(c, app_mod, j["id"])
    assert final["state"] == "done"
    assert final["voice_text"] == "你好呀"
    assert final["asr_text"] == "你好"
    assert "elapsed" in final


def test_async_accept_returns_fast(client):
    """受理必须立刻返回（这正是板子能开口说话的前提）。"""
    import time as _t
    c, _ = client
    t0 = _t.time()
    c.post("/v1/voice?async=1", data=_wav_bytes())
    assert _t.time() - t0 < 1.0


def test_sync_unchanged_by_default(client):
    """不带 async 时，行为与原来完全一样（一次往返拿全部字段）。"""
    c, _ = client
    j = c.post("/v1/voice", data=_wav_bytes()).get_json()
    assert j["voice_text"] == "你好呀"
    assert j["asr_text"] == "你好"


def test_result_unknown_id(client):
    c, _ = client
    j = c.get("/v1/voice/result?id=nope").get_json()
    assert j["ok"] is False and j["state"] == "gone"


def test_result_expires(client, monkeypatch):
    c, app_mod = client
    jid = c.post("/v1/voice?async=1", data=_wav_bytes()).get_json()["id"]
    _wait_done(c, app_mod, jid)
    # 把时间推到 TTL 之后，再问一次：应该被清掉
    with app_mod._JOBS_LOCK:
        app_mod._VOICE_JOBS[jid]["t"] -= app_mod._JOB_TTL + 1
    c.post("/v1/voice?async=1", data=_wav_bytes())      # 触发一次 prune
    assert c.get(f"/v1/voice/result?id={jid}").get_json()["state"] == "gone"


def test_jobs_capped(client):
    c, app_mod = client
    for _ in range(app_mod._JOB_MAX + 4):
        c.post("/v1/voice?async=1", data=_wav_bytes())
    with app_mod._JOBS_LOCK:
        assert len(app_mod._VOICE_JOBS) <= app_mod._JOB_MAX


def test_asr_error_is_graceful(client, monkeypatch):
    """ASR 失败不是崩，而是回一句人话（同步/异步都该如此）。"""
    c, app_mod = client

    def boom(_wav):
        raise RuntimeError("识别服务挂了")

    monkeypatch.setattr(app_mod, "baidu_asr", boom)
    jid = c.post("/v1/voice?async=1", data=_wav_bytes()).get_json()["id"]
    final = _wait_done(c, app_mod, jid)
    assert final["state"] == "done"
    assert "再说一遍" in final["voice_text"]


def test_pipeline_error_becomes_error_state(client, monkeypatch):
    """大脑那一步抛出去（pipeline 内部没兜住的），要变成 error 状态而不是 500。"""
    c, app_mod = client

    def boom(_text):
        raise RuntimeError("模拟崩了")

    monkeypatch.setattr(app_mod, "handle_query", boom)
    jid = c.post("/v1/voice?async=1", data=_wav_bytes()).get_json()["id"]
    final = _wait_done(c, app_mod, jid)
    assert final["state"] == "error"
    assert "模拟崩了" in final["error"]


def test_slug_is_stable_across_processes():
    """文件名里的数字必须跨进程稳定 —— 这条是真 bug 换来的。

    ⚠️ 原来回退用的是 Python 的 hash()，而 str 的 hash **每个进程都不同**
    （PYTHONHASHSEED 随机化）。后果（2026-10-29 板子上实测）：
      · 服务端重启 → 同一句话的文件名变了 → 板子手上旧 URL 全 404 → 一句都不响
      · 服务端自己也总算出"磁盘上没有的名字" → 反复重生成 → /v1/fillers 500
    """
    import zlib

    # 同一进程内稳定
    assert filler._slug("好的") == filler._slug("好的")
    # 算法钉死成 crc32（换回任何"随机化"的实现都会红）
    assert filler._slug("好的") == str(zlib.crc32("好的".encode()) % 100000)
    # 有英文的仍然直接用英文（中文文件名在 HTTP 头里会被转义）
    assert filler._slug("ok good") == "okgood"
    # 不同文本不能撞同一个名字
    assert filler._slug("好的") != filler._slug("嗯嗯")
